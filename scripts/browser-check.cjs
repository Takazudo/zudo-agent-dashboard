/* Development-only dashboard QA. All fixtures are synthetic and local. */
'use strict';
const { chromium } = require('playwright-core');
const { spawn } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const assert = require('node:assert/strict');

function startSample() {
  return new Promise((resolve, reject) => {
    const server = spawn('python3', ['-m', 'zudo_agent', 'serve', '--sample', '--port', '0'], {stdio: ['ignore', 'pipe', 'inherit']});
    const timer = setTimeout(() => reject(new Error('Sample server startup timed out')), 10000);
    server.stdout.on('data', bytes => {
      const match = bytes.toString().match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) { clearTimeout(timer); resolve({server, base: match[0]}); }
    });
    server.on('exit', () => { clearTimeout(timer); reject(new Error('Sample server exited')); });
  });
}

(async () => {
  const {server, base} = await startSample();
  let browser;
  try {
    browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const errors = [], cspErrors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && /content security policy|refused to apply/i.test(message.text())) cspErrors.push(message.text()); });
    await page.goto(base);
    await page.waitForSelector('.session-card');
    assert.equal(await page.title(), 'zudo-agent-dashboard');
    assert.equal((await page.locator('h1').innerText()).replace(/\s+/g, ' '), 'Session library 5');
    assert.match(await page.locator('#notice').innerText(), /Sample observations are synthetic/);
    assert.match(await page.locator('.session-card').first().innerText(), /Capture unavailable|Authenticate previews/);
    assert.equal(await page.locator('#authenticate-previews').isVisible(), false, 'sample data cannot authenticate a live preview');

    // Real browser settings are transactional and remain available without a terminal.
    await page.emulateMedia({colorScheme: 'light'});
    await page.locator('[data-open-settings]').first().click();
    await page.locator('#settings-dialog [name=theme]').selectOption('dark');
    assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'light');
    await page.locator('#settings-dialog [data-cancel]').last().click();
    assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'light');
    await page.locator('[data-open-settings]').first().click();
    await page.locator('#settings-dialog [name=theme]').selectOption('dark');
    await page.locator('#settings-dialog [type=submit]').click();
    assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark');
    await page.emulateMedia({colorScheme: 'light'});
    assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark', 'fixed theme ignores OS changes');
    await page.locator('[data-open-settings]').first().click();
    await page.locator('#settings-dialog [name=theme]').selectOption('system');
    await page.locator('#settings-dialog [type=submit]').click();
    await page.emulateMedia({colorScheme: 'dark'});
    await page.waitForFunction(() => document.documentElement.dataset.colorMode === 'dark');
    await page.emulateMedia({colorScheme: 'light'});
    await page.waitForFunction(() => document.documentElement.dataset.colorMode === 'light');
    const device = page.locator('[data-node^="device:"]').first();
    const deviceId = await device.getAttribute('data-node');
    await page.locator('[data-expand]').first().click();
    assert.equal(await page.locator('#session-count').innerText(), '5', 'trailing expansion does not select scope');
    await device.focus();
    await device.press('ArrowRight');
    assert.match(await page.evaluate(() => document.activeElement.dataset.node), /^project:/);
    await page.keyboard.press('ArrowLeft');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.node), deviceId);
    await page.keyboard.press('End');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.node), await page.locator('[data-node]:visible').last().getAttribute('data-node'));
    await page.keyboard.press('Home');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.node), deviceId);
    const heights = [];
    for (const size of ['s', 'm', 'l']) {
      await page.locator(`[data-size="${size}"]`).click();
      heights.push(await page.locator('.capture').first().evaluate(el => el.getBoundingClientRect().height));
    }
    assert.ok(heights[0] < heights[1] && heights[1] < heights[2], 'thumbnail sizes change bounded capture height');
    await page.locator('[data-size="m"]').click();
    await page.locator('#search').fill('atlas');
    assert.equal(await page.locator('.session-card').count(), 1);
    await page.locator('#search').fill('');
    await page.locator('#activity-filter').selectOption('waiting');
    assert.equal(await page.locator('.session-card').count(), 1);
    await page.locator('#activity-filter').selectOption('all');
    await page.locator('#view-board').click();
    assert.equal(await page.locator('.board-lane').count(), 4);
    assert.equal(await page.locator('#project-overview #project-history').count(), 1, 'project overview contains the history region');
    assert.equal(await page.locator('#project-overview').getAttribute('open'), null);
    mkdirSync('test-results', {recursive: true});
    await page.screenshot({path: 'test-results/dashboard-sample-desktop.png', fullPage: true, animations: 'disabled'});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile dashboard has no horizontal document overflow');
    await page.locator('#tree-toggle').click();
    assert.equal(await page.locator('#tree-toggle').getAttribute('aria-expanded'), 'true');
    await page.locator('#tree-close').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'tree-toggle', 'closing mobile navigation restores opener focus');
    await page.screenshot({path: 'test-results/dashboard-sample-mobile.png', fullPage: true, animations: 'disabled'});

    const historyFixture = await page.evaluate(async () => (await fetch('/api/snapshot')).json());
    historyFixture.mode = 'sample';
    const project = {...historyFixture.projects[0], id: 'history-project'};
    const template = {...project.runs[0], machine: 'fixture-device', source: 'tmux', evidence: 'Fixture observation'};
    const current = {...template, id: 'a'.repeat(64), state: 'unknown', reachability: 'present'};
    const disconnected = {...template, id: 'd'.repeat(64), state: 'working', reachability: 'disconnected', freshness: 'stale', evidence: 'Last lifecycle event observed'};
    const completed = {...template, id: 'c'.repeat(64), state: 'completed', reachability: 'not-observed', evidence: 'Explicit task completion import'};
    const absent = {...template, id: 'b'.repeat(64), state: 'working', reachability: 'absent'};
    const ended = {...template, id: 'e'.repeat(64), state: 'ended', reachability: 'present'};
    project.runs = [current, disconnected, completed, absent, ended];
    historyFixture.projects = [project];
    historyFixture.collectors = [{machine: 'fixture-device', status: 'offline', collector_status: 'disconnected', checked_at: 1, panes: 3, unmatched_panes: 1, omitted_runs: 12}];
    let releaseRefresh;
    const refreshGate = new Promise(resolve => { releaseRefresh = resolve; });
    await page.route('**/api/snapshot', async route => {
      await refreshGate;
      await route.fulfill({json: historyFixture});
    });
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#refresh').click();
    assert.equal(await page.locator('#refresh').isDisabled(), true);
    assert.match(await page.locator('#refresh-status').innerText(), /Checking/);
    releaseRefresh();
    await page.waitForFunction(() => document.querySelector('#session-count').textContent === '3');
    assert.equal(await page.locator('.session-card').count(), 3, 'ended and absent observations stay in project history');
    assert.match(await page.locator('.session-card').allInnerTexts().then(text => text.join('\n')), /Unknown|Waiting for input|Task completed/);
    assert.match(await page.locator('#refresh-status').innerText(), /Updated/);
    await page.locator('#project-overview > summary').click();
    assert.equal(await page.locator('#project-overview').getAttribute('open'), '');
    assert.match(await page.locator('#project-history').innerText(), /Previous runs \(2\)/);
    await page.locator('.history > summary').click();
    assert.match(await page.locator('#project-history').innerText(), /No longer present/);
    assert.match(await page.locator('#project-history').innerText(), /Session ended/);
    assert.match(await page.locator('#session-surface').innerText(), /Explicit task completion import/);
    assert.match(await page.locator('#home-health').innerText(), /offline.*disconnected.*12 older runs omitted/s);
    historyFixture.projects[0].runs.push({...template, id: 'f'.repeat(64), state: 'ended', reachability: 'absent'});
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#project-history').textContent.includes('Previous runs (3)'));
    assert.equal(await page.locator('#project-overview').getAttribute('open'), '', 'expanded project overview survives refresh');
    assert.equal(await page.locator('.history[open]').count(), 1, 'history disclosure stays open across redraw');
    assert.equal(await page.locator('.session-card').count(), 3);
    await page.screenshot({path: 'test-results/dashboard-history-desktop.png', fullPage: true, animations: 'disabled'});

    const hubFixture = structuredClone(historyFixture);
    hubFixture.mode = 'hub';
    hubFixture.projects[0].id = '<img src=x onerror=window.fixtureInjection=true>';
    hubFixture.collectors[0].status = 'offline';
    hubFixture.collectors[0].collector_status = 'disconnected';
    await page.unroute('**/api/snapshot');
    await page.route('**/api/snapshot', route => route.fulfill({json: hubFixture}));
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#mode').textContent === 'Shared observations');
    assert.match(await page.locator('#notice').innerText(), /Remote capture and control are unavailable/);
    assert.equal(await page.locator('#authenticate-previews').isVisible(), false);
    assert.equal(await page.locator('.session-card .card-preview:not([disabled])').count(), 0, 'remote observation cards cannot open a terminal');
    assert.equal(await page.locator('.session-card img').count(), 0);
    assert.equal(await page.evaluate(() => Boolean(window.fixtureInjection)), false, 'untrusted project labels render as text');
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: 'test-results/dashboard-hub-mobile.png', fullPage: true, animations: 'disabled'});

    await page.unroute('**/api/snapshot');
    await page.route('**/api/snapshot', route => route.abort());
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Connection lost'));
    assert.match(await page.locator('#refresh-status').innerText(), /Refresh failed/);
    assert.equal(await page.locator('#refresh').isDisabled(), false);
    assert.equal(await page.locator('.session-card').count(), 3, 'last known observations remain visible with stale warning');
    assert.deepEqual(errors, []);
    assert.deepEqual(cspErrors, []);
    // Stress the real card rendering and per-lane scroller with synthetic model
    // data. Long labels and a short viewport must not compress card content.
    const boardPage = await context.newPage();
    const longProject = 'project-' + 'long-label-'.repeat(12);
    const runs = Array.from({length: 28}, (_, index) => ({
      id: index.toString(16).padStart(64, '0'), machine: 'workstation-' + 'device-name-'.repeat(4),
      source: 'codex', state: index % 3 ? 'working' : 'needs-attention', reachability: 'present',
      freshness: 'fresh', state_freshness: 'fresh', confidence: 'observed', evidence: 'Synthetic task and pane observation ' + 'long evidence label '.repeat(8),
      state_at: Date.now() / 1000, last_seen: Date.now() / 1000, recent_events: []
    }));
    const boardSnapshot = {schema_version: 1, mode: 'sample', generated_at: Date.now() / 1000, projects: [{id: longProject, repository: 'example/' + longProject, completion: 'unknown', runs}], collectors: [{machine: runs[0].machine, status: 'connected', collector_status: 'running', checked_at: Date.now() / 1000, panes: 28, unmatched_panes: 0, omitted_runs: 0}]};
    const boardItems = Object.fromEntries(runs.map(run => [run.id, {lane: 'inbox', revision: 0}]));
    const boardWorkflow = {csrf: 'synthetic-workflow-token', lanes: ['inbox', 'progress', 'review', 'done'], items: boardItems,
      run_keys: runs.map(run => ({id: run.id, canonical: run.id, project: longProject, machine: run.machine, run: run.id})), sessions: []};
    await boardPage.route('**/api/snapshot', route => route.fulfill({json: boardSnapshot}));
    await boardPage.route('**/api/workflow', async route => {
      if (route.request().method() === 'POST') {
        const move = JSON.parse(route.request().postData());
        const next = {lane: move.lane, revision: move.revision + 1};
        boardItems[move.id] = next;
        await route.fulfill({json: {id: move.id, ...next}});
      } else await route.fulfill({json: boardWorkflow});
    });
    const boardErrors = [];
    boardPage.on('pageerror', error => boardErrors.push(error.message));
    await boardPage.setViewportSize({width: 1000, height: 460});
    await boardPage.goto(base);
    await boardPage.waitForFunction(() => document.querySelectorAll('.session-card').length === 28);
    await boardPage.locator('#view-board').click();
    const inbox = boardPage.locator('.lane-cards[data-scroll-lane="inbox"]');
    assert.equal(await inbox.locator('.session-card').count(), 28);
    assert.ok(await inbox.locator('.session-card').first().evaluate(el => el.getBoundingClientRect().height > 250), 'cards do not shrink to fit the lane');
    assert.ok(await inbox.evaluate(el => el.scrollHeight > el.clientHeight), 'each lane owns vertical scrolling for many cards');
    assert.equal(await boardPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'short board avoids horizontal document overflow');
    const origin = await inbox.locator('.session-card .card-origin').first().innerText();
    assert.ok(origin.length > 60, 'long device and project labels remain present');
    // Focus a menu deeper in the lane before recording its position: Chromium
    // may scroll a native select into view as part of the selection gesture.
    const move = inbox.locator('[data-move]').nth(3);
    await move.scrollIntoViewIfNeeded();
    await move.focus();
    await move.evaluate(el => el.addEventListener('change', () => {
      window.__scrollAtMove = el.closest('[data-scroll-lane]').scrollTop;
    }, {capture: true, once: true}));
    const movedId = await move.getAttribute('data-move');
    const moved = boardPage.waitForResponse(response => response.url().endsWith('/api/workflow') && response.request().method() === 'POST');
    await move.selectOption('done');
    assert.equal((await moved).status(), 200);
    await boardPage.waitForFunction(id => document.querySelector(`[data-scroll-lane="done"] [data-move="${id}"]`), movedId);
    const scrollAtMove = await boardPage.evaluate(() => window.__scrollAtMove);
    const scrollAfterMove = await inbox.evaluate(el => el.scrollTop);
    assert.ok(scrollAtMove > 0, 'the move begins in a scrolled lane after native focus');
    assert.ok(Math.abs(scrollAfterMove - scrollAtMove) <= 1,
      `moving a card retains the source lane position: ${scrollAtMove} -> ${scrollAfterMove}`);
    assert.equal(await boardPage.locator('.lane-cards[data-scroll-lane="done"] [data-move="' + movedId + '"]').inputValue(), 'done');
    const dragCard = inbox.locator('.session-card').first();
    const dragId = await dragCard.getAttribute('data-session-id');
    const dropped = boardPage.waitForResponse(response => response.url().endsWith('/api/workflow') && response.request().method() === 'POST');
    await dragCard.dragTo(boardPage.locator('[data-lane="progress"]'));
    assert.equal((await dropped).status(), 200);
    await boardPage.waitForFunction(id => document.querySelector(`[data-scroll-lane="progress"] [data-session-id="${id}"]`), dragId);
    assert.equal(await boardPage.locator('[data-scroll-lane="review"]').evaluate(el => el.scrollTop), 0, 'scroll positions belong to each lane');
    await boardPage.screenshot({path: 'test-results/dashboard-board-short.png', fullPage: true, animations: 'disabled'});
    await boardPage.setViewportSize({width: 390, height: 844});
    assert.equal(await boardPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile board does not overflow the document horizontally');
    await boardPage.screenshot({path: 'test-results/dashboard-board-mobile.png', fullPage: true, animations: 'disabled'});
    assert.deepEqual(boardErrors, []);
    await browser.close();
    browser = null;
    console.log('Dashboard browser QA passed: sample filters, gallery/board, project health/history, stale failure state, hub limitations, safe text, long-label 28-card lane scrolling, scrolled move retention and desktop/mobile/short viewport bounds.');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
