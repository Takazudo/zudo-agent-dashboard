/* Development-only QA. Uses installed playwright-core; the app has no JS dependencies. */
const { chromium } = require('playwright-core');
const { spawn } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const server = spawn('python3', ['-m', 'zudo_agent', 'serve', '--sample', '--port', '0'], { stdio: ['ignore', 'pipe', 'inherit'] });
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Server startup timed out')), 10000);
      server.stdout.on('data', (chunk) => {
        const match = chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) { clearTimeout(timeout); resolve(match[0]); }
      });
      server.on('exit', () => { clearTimeout(timeout); reject(new Error('Server exited')); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(base);
    await page.waitForSelector('.project');
    assert.equal(await page.locator('.project').count(), 4);
    assert.match(await page.locator('#notice').innerText(), /SAMPLE WORKSPACE/);
    await page.locator('#search').fill('atlas');
    assert.equal(await page.locator('.project').count(), 1);
    await page.locator('#search').fill('');
    await page.locator('#filter').selectOption('needs-attention');
    assert.equal(await page.locator('.project').count(), 1);
    await page.locator('#filter').selectOption('all');
    await page.locator('.run summary').first().click();
    assert.equal(await page.locator('details[open]').count(), 1);
    mkdirSync('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
    const historyFixture = await page.evaluate(async () => (await fetch('/api/snapshot')).json());
    historyFixture.mode = 'sample';
    historyFixture.projects = historyFixture.projects.slice(0, 1);
    const current = { ...historyFixture.projects[0].runs[0], state: 'unknown', source: 'tmux' };
    const absent = { ...current, id: 'a'.repeat(64), state: 'working', reachability: 'absent' };
    const ended = { ...current, id: 'b'.repeat(64), state: 'ended', reachability: 'absent' };
    historyFixture.projects[0].runs = [current, absent, ended];
    let releaseRefresh;
    const responseGate = new Promise(resolve => { releaseRefresh = resolve; });
    await page.route('**/api/snapshot', async route => {
      await responseGate;
      await route.fulfill({ json: historyFixture });
    });
    await page.locator('#refresh').click();
    assert.equal(await page.locator('#refresh').isDisabled(), true);
    assert.match(await page.locator('#refresh-status').innerText(), /Checking/);
    releaseRefresh();
    await page.waitForFunction(() => document.querySelector('#run-count').textContent === '1');
    assert.equal(await page.locator('.project-head .run-count').innerText(), '1 current run');
    assert.equal(await page.locator('.run:visible').count(), 1);
    assert.equal(await page.locator('.history').getAttribute('open'), null);
    assert.match(await page.locator('#refresh-status').innerText(), /Updated/);
    await page.locator('.history > summary').click();
    assert.equal(await page.locator('.run:visible').count(), 3);
    assert.match(await page.locator('.history').innerText(), /No longer present/);
    assert.equal(await page.locator('.history .badge.working').count(), 0);
    historyFixture.projects[0].runs = [{ ...current, id: 'c'.repeat(64) }, { ...current, reachability: 'absent' }, absent, ended];
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('.history > summary').textContent === 'Previous runs (3)');
    assert.equal(await page.locator('#run-count').innerText(), '1');
    assert.equal(await page.locator('.history').getAttribute('open'), '');
    historyFixture.projects[0].runs[0].reachability = 'disconnected';
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('.project > .run').textContent.includes('disconnected'));
    assert.equal(await page.locator('#run-count').innerText(), '1');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: 'test-results/history-mobile.png', fullPage: true });
    await page.unroute('**/api/snapshot');
    const hubFixture = await page.evaluate(async () => (await fetch('/api/snapshot')).json());
    hubFixture.mode = 'hub';
    hubFixture.projects[0].id = '<img src=x onerror=window.fixtureInjection=true>';
    hubFixture.collectors[0].status = 'offline';
    hubFixture.collectors[0].collector_status = 'disconnected';
    hubFixture.collectors[0].omitted_runs = 12;
    await page.route('**/api/snapshot', route => route.fulfill({ json: hubFixture }));
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#mode').textContent === 'SHARED OBSERVATIONS');
    assert.match(await page.locator('#connections').innerText(), /offline.*collector disconnected/);
    assert.match(await page.locator('#connections').innerText(), /12 older runs omitted/);
    assert.equal(await page.locator('.project img').count(), 0);
    assert.equal(await page.evaluate(() => Boolean(window.fixtureInjection)), false);
    await page.unroute('**/api/snapshot');
    await page.route('**/api/snapshot', route => route.abort());
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('connection lost'));
    assert.equal(await page.locator('#stale-count').innerText(), '4');
    assert.match(await page.locator('#refresh-status').innerText(), /Refresh failed/);
    assert.equal(await page.locator('#refresh').isDisabled(), false);
    assert.deepEqual(errors, []);
    console.log('Browser QA passed: desktop/mobile, filters, details, sample label, current/history counts, restarts, disconnect uncertainty, refresh feedback, hub health, text-only rendering, connection failure, no page errors.');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
