/* Interaction checks use only the caller's disposable tmux fixture. */
const assert = require('node:assert/strict');
const {mkdirSync} = require('node:fs');
module.exports = async function checkConsoleUI(page, writes) {
  const screen = page.locator('#console-screen'), input = page.locator('#console-input');
  assert.equal(await page.title(), 'zudo-agent-dashboard');
  assert.equal(await page.locator('h1').textContent(), 'zudo-agent-dashboard');
  assert.equal(await page.locator('#console-keyboard').isVisible(), false);
  const frame = tag => Array.from({length: 100}, (_, i) => `${tag} fixture row ${i}`).join('\n');
  let value = frame('INITIAL'), polls = 0;
  await page.route('**/api/console/screen', async route => {
    const response = await route.fetch(); polls++;
    await route.fulfill({response, json: {...await response.json(), screen: value}});
  });
  await page.waitForFunction(() => document.querySelector('#console-screen').textContent.startsWith('INITIAL'));
  await input.fill('retained draft'); await input.focus();
  await input.evaluate(el => el.setSelectionRange(2, 7));
  await page.evaluate(() => {
    window.uiMutations = 0;
    window.uiObserver = new MutationObserver(records => window.uiMutations += records.length);
    uiObserver.observe(document.querySelector('#console-screen'), {childList: true, characterData: true, subtree: true});
    window.uiDisabled = 0;
    window.uiControlObserver = new MutationObserver(records => { window.uiDisabled += records.filter(r => r.target.disabled).length; });
    uiControlObserver.observe(document.querySelector('#console-control'), {attributes: true, attributeFilter: ['disabled']});
  });
  const startPolls = polls; await page.waitForTimeout(1200);
  assert.ok(polls >= startPolls + 2);
  assert.deepEqual(await page.evaluate(() => [uiMutations, uiDisabled, document.activeElement.id, document.querySelector('#console-input').selectionStart]), [0, 0, 'console-input', 2]);
  // Changed captures must not destroy selected text; Latest explicitly resumes.
  await screen.evaluate(el => {
    const range = document.createRange(); range.setStart(el.firstChild, 0); range.setEnd(el.firstChild, 7);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  value = frame('UPDATED');
  await page.waitForFunction(() => document.querySelector('#console-view-state').textContent.includes('Selection held'));
  assert.equal(await page.evaluate(() => getSelection().toString()), 'INITIAL');
  assert.ok((await screen.textContent()).startsWith('INITIAL'));
  await page.locator('#console-latest').click();
  assert.ok((await screen.textContent()).startsWith('UPDATED'));
  await screen.evaluate(el => { el.scrollTop = 75; });
  await page.waitForFunction(() => document.querySelector('#console-view-state').textContent.includes('Reading'));
  value = frame('READING');
  await page.waitForFunction(() => document.querySelector('#console-screen').textContent.startsWith('READING'));
  assert.equal(await screen.evaluate(el => el.scrollTop), 75);
  value = frame('MORE') + '\n' + frame('EXTRA');
  await page.waitForFunction(() => document.querySelector('#console-screen').textContent.startsWith('MORE'));
  assert.equal(await screen.evaluate(el => el.scrollTop), 75);
  await screen.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForFunction(() => document.querySelector('#console-latest').textContent === 'Following');
  await page.locator('#console-latest').click();
  // Desktop mouse and keyboard divider: textarea grows, terminal stays usable.
  const divider = page.locator('#console-divider'), dock = page.locator('#console-dock');
  let before = await input.boundingBox(), grip = await divider.boundingBox();
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2); await page.mouse.down();
  await page.mouse.move(grip.x + grip.width / 2, grip.y - 85, {steps: 8}); await page.mouse.up();
  assert.ok((await input.boundingBox()).height > before.height + 60);
  const preferred = (await dock.boundingBox()).height;
  await input.focus(); await input.evaluate(el => el.setSelectionRange(2, 7));
  await page.locator('#console-toggle').click(); assert.equal(await dock.isVisible(), false);
  await page.locator('#console-toggle').click();
  assert.equal(await input.inputValue(), 'retained draft');
  assert.deepEqual(await input.evaluate(el => [document.activeElement === el, el.selectionStart, el.selectionEnd]), [true, 2, 7]);
  assert.equal((await dock.boundingBox()).height, preferred);
  mkdirSync('test-results', {recursive: true});
  const suffix = process.env.ZUDO_BROWSER_PROXY ? '-proxy' : '';
  for (const [width, height] of [[1440,1000], [390,844], [390,500], [320,500]]) {
    await page.setViewportSize({width,height}); await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1), true, `${width}x${height} viewport bounds`);
    assert.ok((await screen.boundingBox()).height >= 65, 'reserve a visible terminal');
    assert.ok((await input.boundingBox()).height >= 40, 'visible textarea');
    assert.equal(await page.locator('#console-enter-label').isVisible(), true);
    const append = await page.locator('#console-enter-label').boundingBox(); assert.ok(append.x + append.width <= width);
    const box = await dock.boundingBox(); assert.ok(box.y + box.height <= height);
    await page.screenshot({path: `test-results/console-ui${suffix}-${width}x${height}.png`});
  }
  await page.setViewportSize({width:1440,height:1000}); await page.waitForTimeout(100);
  assert.equal((await dock.boundingBox()).height, preferred, 'viewport clamp retains preferred height');
  await divider.focus(); await divider.press('Home');
  assert.equal(Number(await divider.getAttribute('aria-valuenow')), Number(await divider.getAttribute('aria-valuemin')));
  before = await input.boundingBox(); await divider.press('Shift+ArrowUp');
  assert.ok((await input.boundingBox()).height >= before.height + 30);
  await divider.press('End');
  assert.equal(Number(await divider.getAttribute('aria-valuenow')), Number(await divider.getAttribute('aria-valuemax')));
  await divider.press('Home');
  // A real touch pointer goes through the same capture path on a phone viewport.
  await page.setViewportSize({width:390,height:844}); await page.waitForTimeout(100);
  const cdp = await page.context().newCDPSession(page); grip = await divider.boundingBox(); before = await input.boundingBox();
  const x = Math.round(grip.x + grip.width / 2), y = Math.round(grip.y + grip.height / 2);
  await cdp.send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:[{x,y}]});
  await cdp.send('Input.dispatchTouchEvent', {type:'touchMove',touchPoints:[{x,y:y-55}]});
  await cdp.send('Input.dispatchTouchEvent', {type:'touchEnd',touchPoints:[]});
  assert.ok((await input.boundingBox()).height > before.height + 30);
  await cdp.detach();
  await page.setViewportSize({width:1440,height:1000});
  await page.unroute('**/api/console/screen');
  await page.evaluate(() => { uiObserver.disconnect(); uiControlObserver.disconnect(); });

  // Hold one poll after its server reply. Mutations must queue exactly once.
  async function duringPoll(action, after) {
    let release, reached;
    const gate = new Promise(resolve => release = resolve), ready = new Promise(resolve => reached = resolve);
    await page.route('**/api/console/screen', async route => {
      const response = await route.fetch(); reached(); await gate; await route.fulfill({response});
    });
    await ready;
    try { await action(); } finally { release(); }
    await after(); await page.unroute('**/api/console/screen');
  }
  const controls = [], sizes = [];
  const listen = req => {
    if (req.url().endsWith('/control')) controls.push(JSON.parse(req.postData()));
    if (req.url().endsWith('/resize')) sizes.push(JSON.parse(req.postData()));
  };
  page.on('request', listen);
  const beforeWrites = writes.length;
  await duringPoll(async () => {
    await input.fill('QUEUED-MUST-BE-DROPPED'); await page.locator('#console-send').click();
    await page.locator('#console-control').uncheck();
    assert.equal(await input.isDisabled(), true);
  }, async () => { await page.waitForFunction(() => !document.querySelector('#console-control').disabled); });
  assert.equal(writes.length, beforeWrites); assert.equal(controls.length, 1); assert.equal(controls[0].enabled, false);
  await duringPoll(async () => { await page.locator('#console-control').check(); }, async () => { await page.waitForFunction(() => !document.querySelector('#console-input').disabled); });
  assert.equal(controls.length, 2); assert.equal(controls[1].enabled, true);
  await page.locator('#console-info').click(); await page.locator('summary').filter({hasText:'Resize'}).click();
  await page.locator('#console-cols').fill('80'); await page.locator('#console-rows').fill('24');
  await duringPoll(async () => { await page.locator('#console-resize').click(); }, async () => { await page.waitForFunction(() => !document.querySelector('#console-resize').disabled); });
  assert.equal(sizes.length, 1);
  await page.locator('summary').filter({hasText:'Resize'}).click(); await page.locator('#console-info-close').click();
  page.off('request', listen);

  // Fresh mobile-style input without keydown and explicit paste must work.
  await page.locator('[data-mode="direct"]').click();
  const mobileText = 'printf "NO-KEYDOWN-OK\\n"\n';
  let delivered = page.waitForResponse(r => r.url().endsWith('/send'));
  await page.locator('#console-keyboard').evaluate((el, value) => {
    el.value = value; el.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertText',data:value}));
  }, mobileText);
  assert.equal((await delivered).status(), 200);
  assert.equal(writes.at(-1), mobileText);
  await page.locator('[data-mode="compose"]').click();
  // Hiding / changing modes cancels uncommitted IME, including late browser events.
  const keyboard = page.locator('#console-keyboard');
  async function startComposition() {
    await page.locator('[data-mode="direct"]').click(); await keyboard.focus();
    await keyboard.evaluate(el => { el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true})); el.value = '未確定'; });
  }
  async function lateComposition() {
    await keyboard.evaluate(el => {
      el.value = '未確定'; el.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true,data:'未確定'}));
      el.value = '未確定'; el.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertText',data:'未確定'}));
    });
  }
  const beforeIME = writes.length;
  // The boundary must cancel on pointerdown, before browser blur commits.
  for (const id of ['console-project','console-run','console-close','console-control']) {
    await startComposition();
    await page.locator('#' + id).dispatchEvent('pointerdown');
    await lateComposition();
    assert.equal(writes.length, beforeIME, id + ' cancels before blur');
  }
  await startComposition(); await page.locator('label').filter({has: page.locator('#console-control')}).dispatchEvent('pointerdown'); await lateComposition();
  assert.equal(writes.length, beforeIME, 'control label cancels before forwarded click');
  await startComposition(); await page.locator('#console-toggle').click(); await lateComposition();
  await page.locator('#console-toggle').click(); await lateComposition();
  await startComposition(); await page.locator('[data-mode="compose"]').click(); await lateComposition();
  await page.waitForTimeout(200); assert.equal(writes.length, beforeIME);
  await input.fill('DRAFT-TO-CLEAR');
  // Buffer an old selected frame, then change targets while collapsed. Neither
  // the pending frame nor the draft may reappear on the new target.
  value = frame('OLD-TARGET');
  await page.route('**/api/console/screen', async route => {
    const response = await route.fetch(); await route.fulfill({response, json:{...await response.json(),screen:value}});
  });
  await page.waitForFunction(() => document.querySelector('#console-screen').textContent.startsWith('OLD-TARGET'));
  await screen.evaluate(el => { const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r); });
  value = frame('BUFFERED-OLD');
  await page.waitForFunction(() => document.querySelector('#console-view-state').textContent.includes('Selection held'));
  const current = await page.locator('#console-run').inputValue();
  const other = await page.locator('#console-run option').evaluateAll(nodes => nodes.map(n => n.value));
  await startComposition(); await page.locator('#console-toggle').click();
  await page.locator('#console-run').selectOption(other.find(v => v !== current));
  await lateComposition(); await page.locator('#console-latest').click();
  assert.equal(await screen.textContent(), ''); assert.equal(await input.inputValue(), '');
  await page.unroute('**/api/console/screen');
  await page.locator('#console-run').selectOption(current);
  await page.locator('#console-connect').click();
  await page.waitForFunction(() => !document.querySelector('#console-control').disabled);
  await page.locator('#console-toggle').click(); await page.locator('[data-mode="compose"]').click();
  await page.locator('#console-control').check(); await page.waitForFunction(() => !document.querySelector('#console-input').disabled);
  await lateComposition(); await page.waitForTimeout(200); assert.equal(writes.length, beforeIME);
  await page.locator('[data-mode="direct"]').click();
  delivered = page.waitForResponse(r => r.url().endsWith('/send'));
  await keyboard.evaluate(el => {
    el.dispatchEvent(new ClipboardEvent('paste', {bubbles:true}));
    el.value = 'printf "PASTE-OK\\n"\r';
    el.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertFromPaste'}));
  });
  assert.equal((await delivered).status(), 200);
  assert.match(writes.at(-1), /PASTE-OK/);
  await page.locator('[data-mode="compose"]').click();
  console.log('Console UI QA passed: refresh stability, changed-frame selection/scroll, draft/caret/focus, viewport clamp, mouse/touch/keyboard resize, deferred control/resize, control-off queue drop, cancelled IME and target buffer clearing.');
};
