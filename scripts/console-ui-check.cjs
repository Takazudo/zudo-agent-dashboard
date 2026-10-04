const assert = require('node:assert/strict');

// Exercise the standalone console after the caller has connected read-only to a
// disposable fixture pane. All writes are observed and must be explicit.
module.exports = async function consoleUiCheck(page, writes) {
  const editor = page.locator('.cm-content[contenteditable="true"]');
  assert.equal(await page.locator('#console-dock').isVisible(), false, 'input starts hidden');
  assert.equal(await page.locator('#console-toggle').innerText(), 'Terminal input');
  assert.equal(await page.locator('#console-send').isDisabled(), true);
  assert.equal(await page.locator('#console-keyboard').isDisabled(), true);
  assert.equal(await page.locator('.cm-editor').count(), 1, 'one CodeMirror instance');
  assert.equal(await page.locator('textarea:not([hidden])').count(), 0, 'no visible fallback textarea');
  assert.equal(await page.evaluate(() => window.composerEditor?.state().enabled), false);
  assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => value.includes('SYNTHETIC'))), false,
    'captured terminal output is never stored');

  const controls = [];
  let holdEnabled = false, holdDisabled = false, releaseEnabled, releaseDisabled;
  let enabledGate = null, disabledGate = null;
  await page.route('**/api/console/control', async route => {
    const enabled = JSON.parse(route.request().postData()).enabled;
    controls.push(enabled);
    if (enabled && holdEnabled) { holdEnabled = false; enabledGate = new Promise(resolve => { releaseEnabled = resolve; }); await enabledGate; }
    if (!enabled && holdDisabled) { holdDisabled = false; disabledGate = new Promise(resolve => { releaseDisabled = resolve; }); await disabledGate; }
    const response = await route.fetch();
    await route.fulfill({response});
  });

  // Closing while enable is in flight must serialize an off request after the
  // enable response, leaving no late control grant behind.
  holdEnabled = true;
  const enableStarted = page.waitForRequest(request => request.url().endsWith('/api/console/control') && JSON.parse(request.postData()).enabled === true);
  await page.locator('#console-toggle').click();
  await page.waitForFunction(() => document.querySelector('#console-dock').hidden === false);
  await enableStarted;
  assert.deepEqual(controls, [true], 'no disable request can overtake the held enable');
  await page.locator('#composer-close').click();
  assert.equal(await page.locator('#console-dock').isVisible(), false);
  await page.waitForTimeout(100);
  assert.deepEqual(controls, [true], 'disable waits for the enable request');
  releaseEnabled();
  await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === false);
  await page.waitForFunction(() => document.querySelector('#console-status').textContent.includes('Connected read-only'));
  await page.waitForTimeout(50);
  assert.deepEqual(controls, [true, false]);
  assert.equal(await page.evaluate(() => window.composerEditor.state().enabled), false);

  // Establish an active lease, then ensure the close/revoke transaction gates
  // re-enabling until the server confirms it.
  await page.locator('#console-toggle').click();
  await page.waitForFunction(() => document.querySelector('#console-send').disabled === false);
  assert.equal(await page.evaluate(() => window.composerEditor.state().enabled), true);
  await editor.fill('draft survives closing');
  await editor.press('End');
  await page.evaluate(() => window.composerEditor.setSelectionRange(5, 12, 'forward'));
  const expected = await page.evaluate(() => ({
    value: window.composerEditor.value,
    selection: [window.composerEditor.selectionStart, window.composerEditor.selectionEnd],
    view: (window.__testEditorView = window.composerEditor.view) && true,
  }));
  const beforeWrites = writes.length;
  holdDisabled = true;
  await page.locator('#composer-close').click();
  await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === true);
  await page.locator('#console-toggle').dispatchEvent('click');
  await page.waitForTimeout(100);
  assert.deepEqual(controls, [true, false, true, false]);
  assert.equal(writes.length, beforeWrites, 'close and ignored re-enable never send draft text');
  releaseDisabled();
  await page.waitForFunction(() => document.querySelector('#console-toggle').disabled === false);
  await page.locator('#console-toggle').click();
  await page.waitForFunction(() => document.querySelector('#console-send').disabled === false);
  const after = await page.evaluate(() => ({
    value: window.composerEditor.value,
    selection: [window.composerEditor.selectionStart, window.composerEditor.selectionEnd],
    sameView: window.composerEditor.view === window.__testEditorView,
  }));
  assert.equal(after.value, expected.value);
  assert.deepEqual(after.selection, expected.selection);
  assert.equal(after.sameView, true, 'open/close retains the single EditorView');
  assert.equal(writes.length, beforeWrites, 'opening and retaining a draft never sends it');

  // Enter in Compose inserts a newline; only the explicit Send button submits.
  await editor.press('End');
  await editor.press('Enter');
  const withNewline = await page.evaluate(() => window.composerEditor.value);
  assert.ok(withNewline.endsWith('\n'));
  assert.equal(writes.length, beforeWrites);
  assert.equal(await page.locator('#console-send').isDisabled(), false);

  // Settings are transactional. Cancel discards pending edits; Apply changes
  // preferences in place without replacing the editor or losing draft state.
  const beforePrefs = await page.evaluate(() => ({prefs: window.ThemeSettings.get(), view: (window.__testEditorView = window.composerEditor.view) && true, value: window.composerEditor.value}));
  await page.locator('[data-open-settings]').first().click();
  const settings = page.locator('#settings-dialog');
  await settings.locator('[name="theme"]').selectOption('light');
  await settings.locator('[name="vim"]').check();
  await settings.locator('[data-cancel]').last().click();
  assert.deepEqual(await page.evaluate(() => window.ThemeSettings.get()), beforePrefs.prefs);
  await page.locator('[data-open-settings]').first().click();
  await settings.locator('[name="theme"]').selectOption('dark');
  await settings.locator('[name="vim"]').check();
  await settings.locator('[name="lineNumbers"]').check();
  await settings.getByRole('button', {name: 'Apply settings'}).click();
  assert.equal(await page.evaluate(() => window.ThemeSettings.get().theme), 'dark');
  assert.equal(await page.evaluate(() => window.ThemeSettings.get().vim), true);
  assert.equal(await page.evaluate(() => window.composerEditor.state().vim), true);
  assert.equal(await page.evaluate(() => window.composerEditor.view === window.__testEditorView), true);
  assert.equal(await page.evaluate(() => window.composerEditor.value), beforePrefs.value);
  assert.equal(await page.locator('.cm-editor').count(), 1);

  // Vim owns Escape in its normal mode; the parent detail stays expanded.
  await page.locator('#composer-expand').click();
  await editor.focus();
  await editor.press('Escape');
  assert.equal(await page.locator('body').evaluate(el => el.classList.contains('composer-expanded')), true);
  await page.locator('#composer-expand').click();
  assert.equal(await page.locator('body').evaluate(el => el.classList.contains('composer-expanded')), false);

  // System mode tracks OS changes; a fixed theme does not.
  await page.emulateMedia({colorScheme: 'dark'});
  await page.locator('[data-open-settings]').first().click();
  await settings.locator('[name="theme"]').selectOption('system');
  await settings.getByRole('button', {name: 'Apply settings'}).click();
  assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark');
  await page.emulateMedia({colorScheme: 'light'});
  await page.waitForFunction(() => document.documentElement.dataset.colorMode === 'light');
  await page.locator('[data-open-settings]').first().click();
  await settings.locator('[name="theme"]').selectOption('dark');
  await settings.getByRole('button', {name: 'Apply settings'}).click();
  await page.emulateMedia({colorScheme: 'light'});
  await page.waitForTimeout(40);
  assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark');

  // Restore the disposable browser profile so later direct-input checks begin
  // with ordinary Compose settings.
  await page.locator('[data-open-settings]').first().click();
  await settings.locator('[name="theme"]').selectOption('system');
  await settings.locator('[name="vim"]').uncheck();
  await settings.locator('[name="wrap"]').check();
  await settings.locator('[name="lineNumbers"]').uncheck();
  await settings.locator('[name="fontSize"]').selectOption('14');
  await settings.getByRole('button', {name: 'Apply settings'}).click();

  // Composition must not write partial IME text or control keys. It commits
  // exactly once when compositionend delivers the completed string.
  await page.locator('[data-mode="direct"]').click();
  const keyboard = page.locator('#console-keyboard');
  await keyboard.focus();
  const beforeComposition = writes.length;
  await keyboard.evaluate(el => {
    el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    for (const inputType of ['deleteContentBackward', 'insertLineBreak']) {
      el.dispatchEvent(new InputEvent('beforeinput', {inputType, isComposing: true, bubbles: true, cancelable: true}));
    }
    el.value = '日本語';
    el.dispatchEvent(new InputEvent('input', {inputType: 'insertCompositionText', data: '日本語', isComposing: true, bubbles: true}));
  });
  await page.waitForTimeout(100);
  assert.equal(writes.length, beforeComposition);
  const committed = page.waitForResponse(response => response.url().endsWith('/api/console/send') && response.request().postData().includes('日本語'));
  await keyboard.evaluate(el => {
    el.dispatchEvent(new CompositionEvent('compositionend', {data: '日本語', bubbles: true}));
    el.dispatchEvent(new InputEvent('input', {inputType: 'insertText', data: '日本語', bubbles: true}));
  });
  assert.equal((await committed).status(), 200);
  await page.waitForTimeout(100);
  assert.deepEqual(writes.slice(beforeComposition), ['日本語']);
  await page.locator('[data-mode="compose"]').click();

  // Geometry changes retain one live editor and its history. Use real mouse,
  // Chromium touch input, and keyboard resize paths in the native browser.
  await page.setViewportSize({width: 1440, height: 1000});
  await page.locator('[data-open-settings]').first().click();
  await settings.locator('[name="vim"]').uncheck();
  await settings.getByRole('button', {name: 'Apply settings'}).click();
  await editor.fill('geometry draft');
  await editor.press('End');
  const geometryWrites = writes.length;
  for (const key of ['Enter', 'Shift+Enter', 'Control+Enter', 'Alt+Enter', 'Meta+Enter']) await editor.press(key);
  assert.equal(writes.length, geometryWrites, 'modified Enter cannot submit Compose');
  await editor.fill('history before expansion');
  await editor.press('End');
  await editor.pressSequentially('!');
  const geometryState = await page.evaluate(() => ({value: window.composerEditor.value, view: (window.__geometryView = window.composerEditor.view) && true}));
  const divider = page.locator('#console-divider');
  await divider.scrollIntoViewIfNeeded();
  let beforeHeight = await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height);
  let bounds = await divider.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y - 45, {steps: 4});
  await page.mouse.up();
  assert.ok(await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height) > beforeHeight, 'mouse drag grows composer');
  await divider.focus();
  await divider.press('Home');
  beforeHeight = await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height);
  await divider.press('ArrowUp');
  assert.ok(await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height) > beforeHeight, 'keyboard grows composer');
  bounds = await divider.boundingBox();
  const touch = await page.context().newCDPSession(page);
  beforeHeight = await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height);
  const point = {x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2};
  await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [point]});
  await touch.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{x: point.x, y: point.y - 40}]});
  await touch.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
  await touch.detach();
  assert.ok(await page.locator('#console-dock').evaluate(el => el.getBoundingClientRect().height) > beforeHeight, 'touch drag grows composer');
  await page.locator('#composer-expand').click();
  assert.equal(await page.locator('.console-terminal').evaluate(el => el.inert), true);
  assert.equal(await page.locator('.console-top').evaluate(el => el.inert), true, 'standalone header is an inert peer');
  await page.locator('#console-enter').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.mode), 'compose', 'last input wraps to first composer control');
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'console-enter', 'checkbox participates in reverse focus trap');
  await page.locator('#composer-expand').click();
  assert.equal(await page.evaluate(() => window.composerEditor.view === window.__geometryView), true);
  assert.equal(await page.evaluate(() => window.composerEditor.value), geometryState.value);
  assert.equal(await page.evaluate(() => window.composerEditor.undo()), true, 'geometry keeps undo history');
  assert.notEqual(await page.evaluate(() => window.composerEditor.value), geometryState.value);
  assert.equal(writes.length, geometryWrites);
  // One app controls all text capture. Neither settings nor storage may contain
  // the sampled pane output.
  assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => value.includes('SYNTHETIC'))), false);
  await page.unroute('**/api/console/control');
};
