/* All pages use the isolated synthetic private tmux fixture, never live panes. */
const {chromium} = require('playwright-core');
const {spawn} = require('node:child_process');
const {mkdirSync} = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const server = spawn('python3', ['scripts/console-browser-fixture.py', ...(process.env.ZUDO_BROWSER_PROXY ? ['--proxy'] : [])], {stdio: ['ignore', 'pipe', 'inherit']});
  let browser, page;
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Fixture startup timed out')), 15000);
      server.stdout.on('data', bytes => { const match = bytes.toString().match(/https?:\/\/127\.0\.0\.1:\d+/); if (match) { clearTimeout(timer); resolve(match[0]); } });
      server.on('exit', () => { clearTimeout(timer); reject(Error('Fixture exited')); });
    });
    browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const context = await browser.newContext({ignoreHTTPSErrors: Boolean(process.env.ZUDO_BROWSER_PROXY), httpCredentials: {username: 'fixture', password: 'public-fixture-password'}, viewport: {width: 1440, height: 1000}});
    page = await context.newPage();
    mkdirSync('test-results', {recursive: true});
    const errors = [], consoleErrors = [], calls = [], sendTexts = [], targetLists = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|Refused to|unsafe-eval/i.test(message.text())) consoleErrors.push(message.text()); });
    page.on('request', request => {
      const url = new URL(request.url());
      if (!url.pathname.startsWith('/api/')) return;
      let body = null;
      try { body = request.postData() ? JSON.parse(request.postData()) : null; } catch {}
      calls.push({path: url.pathname, method: request.method(), body});
      if (url.pathname.endsWith('/send') && typeof body?.text === 'string') sendTexts.push(body.text);
    });
    page.on('response', async response => {
      if (response.url().includes('/api/console/') && !response.ok()) console.error('Fixture response', new URL(response.url()).pathname, response.status());
      if (!response.url().includes('/api/console/targets')) return;
      try { const data = await response.json(); if (Array.isArray(data.targets)) targetLists.push(data.targets); } catch {}
    });

    // A requested but stale run must not silently fall back to any other pane.
    let begin = calls.length;
    await page.goto(base + '/console.html#project=example&run=stale-fixture-run');
    await page.waitForFunction(() => document.querySelector('#console-status')?.textContent.includes('Requested pane or session is stale'));
    assert.equal(await page.locator('#console-connect').isDisabled(), true);
    assert.equal(await page.locator('#console-run').inputValue(), '');
    assert.equal(await page.locator('#console-dock').isVisible(), false);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    const staleCalls = calls.slice(begin);
    assert.equal(staleCalls.some(call => ['/api/console/open', '/api/console/screen', '/api/console/send', '/api/console/control'].includes(call.path)), false,
      'stale links issue no pane open, capture, send, or control request');

    // The home page starts with observation and workflow metadata. Authentication
    // is an explicit action before it loads actual pane previews.
    begin = calls.length;
    await page.goto(base + '/');
    await page.locator('#authenticate-previews').waitFor({state: 'visible'});
    assert.equal(await page.locator('#project-overview #project-history').count(), 1);
    assert.match(await page.locator('#project-overview').innerText(), /Project health and run history/);
    assert.equal(await page.locator('#project-history').count(), 1);
    assert.equal(calls.slice(begin).some(call => call.path === '/api/console/open'), false, 'home observation never connects a pane');
    assert.equal(await page.locator('.session-card .capture').filter({hasText: /Authenticate previews|Capture unavailable/}).count() > 0, true);
    await page.locator('#authenticate-previews').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.session-card .capture')].some(el => el.textContent.includes('SYNTHETIC SCREEN ONLY')));
    const homeCards = page.locator('.session-card');
    assert.ok(await homeCards.count() >= 2, 'same-session panes group into one session card');
    const sharedCard = homeCards.filter({hasText: '2 panes'}).first();
    await sharedCard.locator('.card-preview').click();
    const embedded = page.frameLocator('#console-frame');
    await embedded.locator('#console-screen').waitFor();
    await page.waitForFunction(() => {
      const child = document.querySelector('#console-frame')?.contentDocument;
      return child?.querySelector('#console-screen')?.textContent.includes('SYNTHETIC SCREEN ONLY');
    });
    assert.equal(await embedded.locator('.console-pickers').evaluate(el => el.hidden), true, 'embedded pane selection belongs to the parent inspector');
    assert.equal(await embedded.locator('#console-expand').evaluate(el => el.hidden), true, 'embedded detail expansion belongs to the parent inspector');
    assert.equal(await embedded.locator('#console-dock').evaluate(el => el.hidden), true, 'inspection opens read-only');
    assert.equal(await embedded.locator('#console-send').isDisabled(), true);
    assert.equal(calls.slice(begin).some(call => call.path === '/api/console/send' || (call.path === '/api/console/control' && call.body?.enabled === true)), false);

    // The authenticated inspector exposes two panes from one session. Switching
    // with the parent tabs cannot inherit terminal input or desync a child picker.
    const panes = await page.locator('#pane-tabs [data-pane]').evaluateAll(nodes => nodes.map(node => node.dataset.pane));
    assert.equal(panes.length, 2);
    const beforePaneChange = calls.length;
    await page.locator('#pane-tabs [data-pane]').nth(1).click();
    await page.waitForFunction(id => document.querySelector('#console-frame')?.contentDocument?.querySelector('#console-target')?.textContent.includes(id), panes[1]);
    assert.equal(calls.slice(beforePaneChange).some(call => call.path === '/api/console/send' || (call.path === '/api/console/control' && call.body?.enabled === true)), false);

    // At a 1000×460 inspector viewport, an explicitly opened control dock can
    // scroll to its footer and keyboard buttons instead of clipping them.
    await page.setViewportSize({width: 1000, height: 460});
    await embedded.locator('#console-toggle').click();
    await embedded.locator('#console-send').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#console-frame')?.contentDocument?.querySelector('#console-send')?.disabled === false);
    const embeddedMetrics = await embedded.locator('.console-app').evaluate(app => {
      app.scrollTop = app.scrollHeight;
      const footer = app.querySelector('.console-foot').getBoundingClientRect();
      return {scrollHeight: app.scrollHeight, clientHeight: app.clientHeight, scrollTop: app.scrollTop,
        width: app.scrollWidth, viewport: innerWidth, footTop: footer.top, footBottom: footer.bottom, height: innerHeight,
        keysVisible: app.querySelector('[data-key="interrupt"]').getBoundingClientRect().bottom <= innerHeight};
    });
    assert.ok(embeddedMetrics.scrollHeight > embeddedMetrics.clientHeight, 'short embedded app owns vertical scrolling');
    assert.equal(embeddedMetrics.scrollTop, embeddedMetrics.scrollHeight - embeddedMetrics.clientHeight);
    assert.ok(embeddedMetrics.footTop >= 0 && embeddedMetrics.footBottom <= embeddedMetrics.height, 'footer is reachable at scroll end');
    assert.equal(embeddedMetrics.keysVisible, true, 'keyboard controls are reachable at scroll end');
    assert.ok(embeddedMetrics.width <= embeddedMetrics.viewport, 'embedded console has no horizontal page overflow');
    await embedded.locator('#composer-close').click();
    await page.waitForFunction(() => document.querySelector('#console-frame')?.contentDocument?.querySelector('#console-toggle')?.disabled === false);
    await page.locator('#inspector-close').click();

    // Manual workflow changes only the dashboard metadata. They cannot open a
    // pane, enable control, or send text.
    const moveSelect = sharedCard.locator('select[data-move]');
    const oldLane = await moveSelect.inputValue();
    const otherLane = await moveSelect.locator('option').evaluateAll(options => options.find(option => option.value !== options.find(item => item.selected)?.value)?.value);
    assert.ok(otherLane);
    const beforeMove = calls.length, beforeSends = sendTexts.length;
    const moveRequest = page.waitForRequest(request => request.url().endsWith('/api/console/workflow') && request.method() === 'POST');
    await moveSelect.selectOption(otherLane);
    const moved = await moveRequest;
    assert.equal(JSON.parse(moved.postData()).lane, otherLane);
    assert.notEqual(oldLane, otherLane);
    assert.equal(calls.slice(beforeMove).some(call => ['/api/console/open', '/api/console/control', '/api/console/send'].includes(call.path)), false);
    assert.equal(sendTexts.length, beforeSends);

    // Direct standalone mode selects the first authorized pane but still waits
    // for the explicit Connect action before reading any output.
    begin = calls.length;
    await page.goto(base + '/console.html#project=example&id=' + panes[0]);
    await page.waitForFunction(() => document.querySelector('#console-status')?.textContent.includes('Connect explicitly'));
    assert.equal(await page.locator('#console-connect').isEnabled(), true);
    assert.equal(await page.locator('#console-run option').count(), 3);
    assert.equal(await page.locator('#console-dock').isVisible(), false);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    assert.equal(await page.evaluate(() => window.composerEditor?.state().enabled), false);
    assert.equal(calls.slice(begin).some(call => ['/api/console/open', '/api/console/screen', '/api/console/send', '/api/console/control'].includes(call.path)), false,
      'target selection does not open a lease or start capture');
    assert.equal(targetLists.at(-1)?.length, 3, 'fixture has two panes in one session and one pane in another');
    const targets = targetLists.at(-1);
    const first = targets.find(target => targets.filter(item => item.session_id === target.session_id).length === 2);
    const sameSession = targets.filter(target => target.session_id === first.session_id), otherSession = targets.find(target => target.session_id !== first.session_id);
    assert.equal(sameSession.length, 2);
    assert.ok(otherSession);

    let screenOverride = null;
    await page.route('**/api/console/screen', async route => {
      try {
        const response = await route.fetch();
        const body = await response.json();
        if (screenOverride !== null) {
          body.screen = screenOverride;
          body.lines = screenOverride.split('\n').length;
          body.limit = 500;
          body.truncated = body.lines >= 500;
          body.byte_truncated = false;
        }
        await route.fulfill({response, json: body});
      } catch (error) {
        if (!String(error).includes('Target closed') && !String(error).includes('aborted')) throw error;
      }
    });
    const firstScreenResponse = page.waitForResponse(response => response.url().endsWith('/api/console/screen'));
    await page.locator('#console-connect').click();
    const initialScreen = await (await firstScreenResponse).json();
    await page.waitForFunction(() => document.querySelector('#console-screen')?.textContent.includes('SYNTHETIC SCREEN ONLY'));
    assert.equal(initialScreen.lines, 500, 'recent capture is capped at 500 lines');
    assert.ok(Buffer.byteLength(initialScreen.screen, 'utf8') <= 131072, 'recent capture is capped at 128 KiB');
    assert.equal(initialScreen.limit, 500);
    assert.equal(initialScreen.truncated, true);
    assert.equal(await page.locator('#console-screen img').count(), 0, 'terminal markup remains escaped text');
    const firstHistoryName = initialScreen.screen.match(/HISTORY-(shared-[ab])-\d{4}/)?.[1];
    assert.ok(firstHistoryName, 'first shared fixture pane emitted unique history lines');
    assert.match(await page.locator('#console-screen').innerText(), new RegExp(`HISTORY-${firstHistoryName}-\\d{4}`));
    assert.doesNotMatch(await page.locator('#console-screen').innerText(), new RegExp(`HISTORY-${firstHistoryName}-0000`));
    const secondHistoryName = firstHistoryName === 'shared-a' ? 'shared-b' : 'shared-a';
    assert.match(await page.locator('#console-view-state').innerText(), /500 lines.*no deep history/);
    assert.match(await page.locator('#console-sampled').innerText(), /Sampled/);
    assert.equal(await page.locator('#console-dock').isVisible(), false);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    assert.equal(await page.evaluate(() => window.composerEditor.state().enabled), false);
    assert.equal(await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => value.includes('SYNTHETIC SCREEN ONLY'))), false,
      'captured output stays in memory rather than browser storage');

    // Selecting, pausing and resuming long output holds the operator's place.
    const oldLines = initialScreen.screen.split('\n');
    const shifted = oldLines.slice(-400).concat(Array.from({length: 100}, (_, index) => `NEW-OUTPUT-${index}`));
    await page.locator('#console-screen').evaluate(el => { el.scrollTop = Math.floor(el.scrollHeight / 2); });
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Reading; follow paused'));
    screenOverride = shifted.join('\n');
    await page.waitForFunction(value => document.querySelector('#console-screen')?.textContent === value, screenOverride);
    assert.match(await page.locator('#console-view-state').innerText(), /Reading; follow paused/);
    await page.locator('#console-screen').evaluate(el => {
      const node = el.firstChild, selection = window.getSelection(), range = document.createRange();
      range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range);
    });
    const heldScreen = await page.locator('#console-screen').innerText();
    screenOverride = shifted.slice(100).concat(Array.from({length: 100}, (_, index) => `SELECTION-NEW-${index}`)).join('\n');
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Selection held'));
    await page.waitForTimeout(650);
    assert.equal(await page.locator('#console-screen').innerText(), heldScreen, 'new captures never replace selected text');
    await page.locator('#console-latest').click();
    await page.waitForFunction(value => document.querySelector('#console-screen')?.textContent === value, screenOverride);
    await page.locator('#console-screen').evaluate(el => { el.scrollTop = 0; });
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Reading; follow paused'));
    const droppedAnchor = await page.locator('#console-screen').innerText();
    screenOverride = droppedAnchor.split('\n').slice(100).concat(Array.from({length: 100}, (_, index) => `DROPPED-ANCHOR-${index}`)).join('\n');
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Position uncertain'));
    assert.equal(await page.locator('#console-screen').innerText(), droppedAnchor, 'an expired reading anchor freezes even when newer lines overlap');
    await page.locator('#console-latest').click();
    await page.waitForFunction(value => document.querySelector('#console-screen')?.textContent === value, screenOverride);
    await page.locator('#console-screen').evaluate(el => { el.scrollTop = 0; });
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Reading; follow paused'));
    const beforeUncertain = await page.locator('#console-screen').innerText();
    screenOverride = 'REWRITTEN-OUTPUT-ONE\nREWRITTEN-OUTPUT-TWO\nREWRITTEN-OUTPUT-THREE';
    await page.waitForFunction(() => document.querySelector('#console-view-state')?.textContent.includes('Position uncertain'));
    assert.equal(await page.locator('#console-screen').innerText(), beforeUncertain, 'ambiguous anchors freeze output until Latest');
    await page.locator('#console-latest').click();
    await page.waitForFunction(value => document.querySelector('#console-screen')?.textContent === value, screenOverride);
    screenOverride = null;

    // Browser tests cover CodeMirror as the actual editor, serialized lease
    // transitions, settings and IME delivery. It never submits the draft.
    await require('./console-ui-check.cjs')(page, sendTexts);
    assert.equal(sendTexts.some(text => text.includes('draft survives closing')), false);
    assert.equal(await page.locator('#console-dock').isVisible(), true);
    assert.equal(await page.locator('#console-send').isDisabled(), false);

    // Every pane in a session has an independent unsent draft and selection.
    const composer = page.locator('.cm-content[contenteditable="true"]');
    await composer.fill('pane A draft');
    await composer.press('End');
    await page.waitForTimeout(600); // Separate real typing gestures in CodeMirror history.
    await composer.pressSequentially(' + undo');
    await page.evaluate(() => window.composerEditor.undo());
    assert.equal(await page.evaluate(() => window.composerEditor.value), 'pane A draft', 'CodeMirror undo changes only the draft');
    await composer.press('Home');
    for (let i = 0; i < 2; i++) await composer.press('ArrowRight');
    for (let i = 0; i < 6; i++) await composer.press('Shift+ArrowRight');
    assert.deepEqual(await page.evaluate(() => [window.composerEditor.selectionStart, window.composerEditor.selectionEnd]), [2, 8]);
    await page.locator('#console-run').selectOption(sameSession[1].id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), sameSession[1].id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    assert.equal(await page.evaluate(() => window.composerEditor.value), '');
    assert.equal(await page.locator('#console-send').isDisabled(), true, 'a newly selected pane is read-only');
    if (await page.locator('#console-dock').isVisible()) await page.locator('#composer-close').click();
    await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === false);
    await page.locator('#console-toggle').click();
    await page.waitForFunction(() => document.querySelector('#console-send').disabled === false);
    await page.locator('.cm-content[contenteditable="true"]').fill('pane B draft');
    await page.locator('#console-run').selectOption(first.id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), first.id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    assert.equal(await page.evaluate(() => window.composerEditor.value), 'pane A draft');
    assert.deepEqual(await page.evaluate(() => [window.composerEditor.selectionStart, window.composerEditor.selectionEnd]), [2, 8]);
    await page.locator('#console-run').selectOption(sameSession[1].id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), sameSession[1].id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    assert.equal(await page.evaluate(() => window.composerEditor.value), 'pane B draft');

    // In-progress IME composition defers retargeting and is discarded after a
    // switch, with no partial byte or replay to the newly selected pane.
    if (await page.locator('#console-dock').isVisible()) await page.locator('#composer-close').click();
    await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === false);
    await page.locator('#console-toggle').click();
    await page.waitForFunction(() => document.querySelector('#console-send').disabled === false);
    await page.locator('[data-mode="direct"]').click();
    const keyboard = page.locator('#console-keyboard');
    await keyboard.focus();
    const beforeRetarget = sendTexts.length;
    await keyboard.evaluate(el => {
      el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      el.value = '未確定';
      el.dispatchEvent(new InputEvent('input', {inputType: 'insertCompositionText', data: '未確定', isComposing: true, bubbles: true}));
    });
    await page.locator('#console-run').selectOption(first.id);
    // Native blur may cancel Direct composition before selection; otherwise
    // the controller waits for compositionend. Both paths must discard bytes.
    await page.waitForTimeout(100);
    assert.equal(sendTexts.length, beforeRetarget, 'IME composition is not replayed before commit');
    await keyboard.evaluate(el => {
      el.dispatchEvent(new CompositionEvent('compositionend', {data: '未確定', bubbles: true}));
      el.dispatchEvent(new InputEvent('input', {inputType: 'insertText', data: '未確定', bubbles: true}));
    });
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), first.id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    await page.waitForTimeout(150);
    assert.equal(sendTexts.length, beforeRetarget, 'retargeted composition is discarded without replay');
    assert.equal(await page.evaluate(() => window.composerEditor.value), 'pane A draft');
    await page.locator('[data-mode="compose"]').click();

    // Moving to another tmux session clears all retained drafts and editor/Vim
    // history. Returning to the original session starts with no old text.
    await page.locator('#console-run').selectOption(otherSession.id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), otherSession.id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    assert.equal(await page.evaluate(() => window.composerEditor.value), '');
    assert.equal(await page.evaluate(() => window.composerEditor.state().undoDepth), 0);
    await page.locator('#console-run').selectOption(first.id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), first.id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    assert.equal(await page.evaluate(() => window.composerEditor.value), '', 'session change permanently clears pane drafts');
    assert.equal(await page.evaluate(() => window.composerEditor.state().vim === window.ThemeSettings.get().vim), true, 'session boundaries retain preferences while discarding draft history');

    // The fixture's pane can return to its shell, and an interrupted response
    // is uncertain: reconnect read-only and never replay the accepted input.
    if (await page.locator('#console-dock').isVisible()) await page.locator('#composer-close').click();
    await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === false);
    if (await page.locator('#console-screen').innerText() === '') await page.locator('#console-refresh').click().catch(() => {});
    await page.locator('#console-toggle').click();
    await page.waitForFunction(() => document.querySelector('#console-send').disabled === false);
    // Preserve the existing real-shell, direct-key, special-key and resize coverage.
    await page.locator('[data-key="enter"]').click();
    await page.waitForFunction(() => document.querySelector('#console-foreground').textContent.includes('Foreground: sh'));
    await page.locator('.cm-content[contenteditable="true"]').fill("printf 'BROWSER-SHELL-OK\\n'");
    await page.locator('#console-send').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.split('\n').includes('BROWSER-SHELL-OK'));
    await page.locator('[data-mode="direct"]').click();
    await page.locator('#console-keyboard').pressSequentially("printf 'KEYBOARD-OK\\n'", {delay: 25});
    await page.locator('#console-keyboard').press('Enter');
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.split('\n').includes('KEYBOARD-OK'));
    await page.locator('[data-key="up"]').click();
    await page.locator('[data-key="interrupt"]').click();
    await page.locator('#console-info').click();
    await page.locator('#console-dialog summary').click();
    await page.locator('#console-cols').fill('70');
    await page.locator('#console-rows').fill('18');
    const resized = page.waitForResponse(response => response.url().endsWith('/api/console/resize'));
    await page.locator('#console-resize').click();
    assert.equal((await resized).status(), 200);
    await page.locator('#console-info-close').click();
    await page.locator('[data-mode="compose"]').click();
    await page.locator('[data-open-settings]').first().click();
    await page.locator('#settings-dialog [name="theme"]').selectOption('dark');
    await page.locator('#settings-dialog').getByRole('button', {name: 'Apply settings'}).click();
    assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark');
    for (const [name, width, height] of [['desktop-dark', 1440, 1000], ['mobile-dark', 390, 844], ['short-dark', 1000, 460]]) {
      await page.setViewportSize({width, height});
      await page.locator('.console-foot').scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: `test-results/console${process.env.ZUDO_BROWSER_PROXY ? '-proxy' : ''}-${name}.png`, fullPage: true, animations: 'disabled'});
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await page.route('**/api/console/send', async route => { await route.fetch(); await route.abort(); });
    await page.locator('.cm-content[contenteditable="true"]').fill('UNIQUE-NO-REPLAY');
    await page.locator('#console-send').click();
    await page.waitForFunction(() => document.querySelector('#console-status')?.textContent.includes('Delivery may be uncertain'));
    assert.equal(await page.locator('#console-screen').innerText(), '', 'interrupted delivery clears captured output');
    const uniqueRequests = sendTexts.filter(text => text.includes('UNIQUE-NO-REPLAY')).length;
    assert.equal(uniqueRequests, 1);
    await page.unroute('**/api/console/send');
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen')?.textContent.length > 0);
    assert.equal(await page.locator('#console-send').isDisabled(), true, 'manual reconnect is read-only');
    await page.waitForTimeout(700);
    assert.equal(sendTexts.filter(text => text.includes('UNIQUE-NO-REPLAY')).length, 1, 'accepted or uncertain input is never replayed');
    assert.equal(await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => value.includes('UNIQUE-NO-REPLAY'))), false);

    // Failed polling and server-reported expiry both require manual reconnect.
    const beforeFailure = sendTexts.length;
    await page.route('**/api/console/screen', route => route.abort());
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Disconnected'));
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.evaluate(() => window.composerEditor.value), '');
    await page.unroute('**/api/console/screen');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#console-screen').innerText(), '', 'failed polling never auto-reconnects');
    await page.route('**/api/console/open', async route => {
      const response = await route.fetch(), data = await response.json();
      data.expires_in = 1;
      await route.fulfill({response, json: data});
    });
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.length > 0);
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Lease expired'));
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    await page.unroute('**/api/console/open');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#console-screen').innerText(), '', 'expiry never auto-reconnects');
    assert.equal(sendTexts.length, beforeFailure, 'failure and expiry never replay input');

    // A pane switch while a prior screen response is pending cannot paint old
    // text into the newly selected pane.
    let releaseStale, staleResponseStarted;
    const staleGate = new Promise(resolve => { releaseStale = resolve; });
    const staleStarted = new Promise(resolve => { staleResponseStarted = resolve; });
    let holdNextScreen = true;
    await page.route('**/api/console/screen', async route => {
      try {
        const response = await route.fetch(), body = await response.json();
        if (holdNextScreen) { holdNextScreen = false; staleResponseStarted(); await staleGate; }
        await route.fulfill({response, json: body});
      } catch {}
    });
    await page.locator('#console-connect').click();
    await staleStarted;
    await page.locator('#console-run').selectOption(sameSession[1].id);
    await page.waitForFunction(id => document.querySelector('#console-target')?.textContent.includes(id), sameSession[1].id);
    await page.waitForFunction(() => document.querySelector('#console-refresh').disabled === false);
    releaseStale();
    await page.waitForFunction(name => document.querySelector('#console-screen')?.textContent.includes(`HISTORY-${name}-`), secondHistoryName);
    await page.waitForTimeout(200);
    assert.match(await page.locator('#console-screen').innerText(), new RegExp(`HISTORY-${secondHistoryName}-`));
    assert.doesNotMatch(await page.locator('#console-screen').innerText(), new RegExp(`HISTORY-${firstHistoryName}-`));
    await page.unroute('**/api/console/screen');

    await page.locator('#console-close').click();
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.evaluate(() => window.composerEditor.value), '', 'disconnect clears drafts');
    assert.equal(await page.evaluate(() => window.composerEditor.state().enabled), false);
    assert.equal(await page.locator('#console-dock').isVisible(), false);
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile detail has no horizontal page overflow');
    mkdirSync('test-results', {recursive: true});
    await page.screenshot({path: `test-results/console${process.env.ZUDO_BROWSER_PROXY ? '-proxy' : ''}-mobile.png`, fullPage: true, animations: 'disabled'});
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, [], 'no CSP or browser console security errors');
    console.log('Pane console QA passed: authenticated home previews and metadata-only moves, same-session target panes, isolated synthetic tmux capture capped at 500 lines/128 KiB, output selection/scroll safety, explicit read-only Connect/control, CodeMirror settings/drafts/undo, serialized control revocation, IME retarget isolation, short embedded/mobile scroll, stale-target/output rejection, and no-replay uncertain delivery.');
  } catch (error) {
    if (page && !page.isClosed()) console.error('Synthetic fixture UI state', await page.evaluate(() => ({
      status: document.querySelector('#console-status')?.textContent,
      inputHidden: document.querySelector('#console-dock')?.hidden,
      connectDisabled: document.querySelector('#console-connect')?.disabled,
      refreshDisabled: document.querySelector('#console-refresh')?.disabled,
      toggleDisabled: document.querySelector('#console-toggle')?.disabled,
      sendDisabled: document.querySelector('#console-send')?.disabled
    })).catch(() => null));
    throw error;
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
