/* All screenshots and requests use the synthetic local fixture, never user panes. */
const { chromium } = require('playwright-core');
const { spawn } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
  const server = spawn('python3', ['scripts/console-browser-fixture.py', ...(process.env.ZUDO_BROWSER_PROXY ? ['--proxy'] : [])], {stdio: ['ignore', 'pipe', 'inherit']});
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Fixture startup timed out')), 10000);
      server.stdout.on('data', bytes => { const match = bytes.toString().match(/https?:\/\/127\.0\.0\.1:\d+/); if (match) { clearTimeout(timer); resolve(match[0]); } });
      server.on('exit', () => { clearTimeout(timer); reject(Error('Fixture exited')); });
    });
    browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const context = await browser.newContext({ignoreHTTPSErrors: Boolean(process.env.ZUDO_BROWSER_PROXY), httpCredentials: {username: 'fixture', password: 'public-fixture-password'}, viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', req => { if (req.url().endsWith('/send')) writes.push(JSON.parse(req.postData()).text); });
    await page.goto(base + '/console.html#project=example&run=stale-fixture-run');
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('requested run'));
    assert.equal(await page.locator('#console-connect').isDisabled(), true);
    assert.equal(await page.locator('#console-run').inputValue(), '');
    assert.equal(writes.length, 0);
    await page.goto(base);
    await page.locator('.run > summary').first().click();
    await page.locator('.pane-link').first().click();
    await page.waitForFunction(() => !document.querySelector('#console-connect').disabled);
    assert.match(await page.locator('#console-target').textContent(), /Project: example.*Machine: fixture.*Server: .*Pane: %\d+.*Initial run: [a-f0-9]{64}/);
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.includes('SYNTHETIC'));
    assert.equal(await page.locator('#console-screen img').count(), 0);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    await page.locator('#console-control').check();
    await page.waitForFunction(() => !document.querySelector('#console-send').disabled);
    // Enter ends the disposable agent-like child and returns to its same shell.
    await page.locator('[data-key="enter"]').click();
    await page.waitForFunction(() => document.querySelector('#console-foreground').textContent.includes('Foreground: sh'));
    await page.locator('#console-input').fill("printf 'BROWSER-SHELL-OK\\n'");
    await page.locator('#console-send').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.split('\n').includes('BROWSER-SHELL-OK'));
    await require('./console-ui-check.cjs')(page, writes);
    await page.locator('[data-mode="direct"]').click();
    await page.locator('#console-keyboard').focus();
    const beforeComposition = writes.length;
    await page.locator('#console-keyboard').evaluate(el => {
      el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      for (const inputType of ['deleteContentBackward', 'insertLineBreak']) el.dispatchEvent(new InputEvent('beforeinput', {inputType, isComposing: true, bubbles: true, cancelable: true}));
      el.value = '日本語';
      el.dispatchEvent(new InputEvent('input', {inputType: 'insertCompositionText', data: '日本語', isComposing: true, bubbles: true}));
    });
    await page.waitForTimeout(250);
    assert.equal(writes.length, beforeComposition, 'IME edits must not reach the pane before commit');
    const committed = page.waitForResponse(response => response.url().endsWith('/send') && response.request().postData().includes('日本語'));
    await page.locator('#console-keyboard').evaluate(el => {
      el.dispatchEvent(new CompositionEvent('compositionend', {data: '日本語', bubbles: true}));
      el.dispatchEvent(new InputEvent('input', {inputType: 'insertText', data: '日本語', bubbles: true}));
    });
    assert.equal((await committed).status(), 200);
    await page.waitForTimeout(150);
    assert.equal(writes.slice(beforeComposition).join(''), '日本語', 'Commit exactly once, with no premature control keys');
    await page.locator('[data-key="interrupt"]').click();
    await page.locator('#console-keyboard').pressSequentially("printf 'KEYBOARD-OK\\n'", {delay: 25});
    await page.locator('#console-keyboard').press('Enter');
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.split('\n').includes('KEYBOARD-OK'));
    await page.locator('[data-key="up"]').click();
    await page.locator('[data-key="interrupt"]').click();
    await page.locator('#console-info').click();
    await page.locator('summary').filter({hasText: 'Resize'}).click();
    await page.locator('#console-cols').fill('60');
    await page.locator('#console-rows').fill('20');
    await page.locator('#console-resize').click();
    await page.locator('#console-info-close').click();
    await page.locator('[data-mode="compose"]').click();
    assert.ok(writes.some(text => text.includes('BROWSER-SHELL-OK')));
    mkdirSync('test-results', {recursive: true});
    await page.screenshot({path: `test-results/console${process.env.ZUDO_BROWSER_PROXY ? '-proxy' : ''}-desktop.png`, fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: `test-results/console${process.env.ZUDO_BROWSER_PROXY ? '-proxy' : ''}-mobile.png`, fullPage: true});
    const first = await page.locator('#console-run').inputValue();
    const second = await page.locator('#console-run option').evaluateAll(nodes => nodes.map(n => n.value));
    await page.locator('#console-run').selectOption(second.find(value => value !== first));
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.locator('#console-refresh').isDisabled(), true);
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.includes('SYNTHETIC'));
    // Automatic polling may fail before a manual click can occur. Assert the
    // disconnect itself instead of racing that poll for an enabled button.
    await page.route('**/api/console/screen', route => route.abort());
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Disconnected'));
    assert.equal(await page.locator('#console-screen').innerText(), '');
    assert.equal(await page.locator('#console-connect').isEnabled(), true);
    await page.unroute('**/api/console/screen');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#console-screen').innerText(), '');
    // Expiry comes from the server; shorten it only in this synthetic response.
    await page.route('**/api/console/open', async route => {
      const response = await route.fetch(); const data = await response.json(); data.expires_in = 1;
      await route.fulfill({response, json: data});
    });
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.includes('SYNTHETIC'));
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Lease expired'));
    assert.equal(await page.locator('#console-screen').innerText(), '');
    await page.unroute('**/api/console/open');
    // A response from the previous selection must never populate a new target.
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/console/screen', async route => {
      const response = await route.fetch(); await gate; await route.fulfill({response});
    });
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => !document.querySelector('#console-close').disabled);
    await page.locator('#console-run').selectOption(first);
    release();
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#console-screen').innerText(), '');
    await page.unroute('**/api/console/screen');
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.includes('SYNTHETIC'));
    await page.locator('#console-close').click();
    assert.equal(await page.locator('#console-screen').innerText(), '');
    // The server may have accepted this input before the response disappears.
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.length > 0);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    await page.locator('#console-control').check();
    await page.waitForFunction(() => !document.querySelector('#console-send').disabled);
    await page.route('**/api/console/send', async route => { await route.fetch(); await route.abort(); });
    await page.locator('#console-input').fill('UNIQUE-NO-REPLAY');
    await page.locator('#console-send').click();
    await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Delivery may be uncertain'));
    const count = writes.length;
    await page.unroute('**/api/console/send');
    await page.locator('#console-connect').click();
    await page.waitForFunction(() => document.querySelector('#console-screen').textContent.length > 0);
    assert.equal(await page.locator('#console-send').isDisabled(), true);
    await page.waitForTimeout(700);
    assert.equal(writes.length, count);
    assert.equal(writes.filter(text => text.includes('UNIQUE-NO-REPLAY')).length, 1);
    await page.locator('#console-close').click();
    assert.deepEqual(errors, []);
    console.log('Pane console QA passed: real private tmux fixture, shell continuation, explicit control, text/keyboard/special keys/resize, desktop/mobile, IME commit isolation, stale-link rejection, escaped output, reconnect read-only, stale-response clearing, uncertain delivery without replay.');
  } finally { if (browser) await browser.close(); server.kill('SIGTERM'); }
})().catch(error => {console.error(error); process.exitCode = 1;});
