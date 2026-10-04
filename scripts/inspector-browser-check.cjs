'use strict';
/* Open detail during a real slow fixture preview, then keep it open across timers. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright-core');
(async()=>{
 const fixture=spawn('python3',['scripts/console-browser-fixture.py','--slow-previews','--stream-output',...(process.env.ZUDO_BROWSER_PROXY?['--proxy']:[])],{stdio:['ignore','pipe','inherit']});
 let browser,page;
 try{
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),15000);fixture.stdout.on('data',b=>{const m=b.toString().match(/https?:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}});fixture.on('exit',()=>{clearTimeout(timer);reject(Error('Fixture exited'))})});
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  const context=await browser.newContext({ignoreHTTPSErrors:!!process.env.ZUDO_BROWSER_PROXY,httpCredentials:{username:'fixture',password:'public-fixture-password'},viewport:{width:1200,height:800}});
  page=await context.newPage();const calls=[],errors=[];let previewPending=0,snapshots=0,frameNavigations=0,screenFailure=null;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/console.html'&&r.resourceType()==='document')frameNavigations++;if(!path.startsWith('/api/'))return;calls.push({path,body:r.postData()?JSON.parse(r.postData()):null});if(path==='/api/console/preview')previewPending++;if(path==='/api/snapshot')snapshots++});
  page.on('requestfinished',r=>{if(new URL(r.url()).pathname==='/api/console/preview')previewPending--});
  await page.route('**/api/console/screen',async route=>{if(screenFailure){const status=screenFailure;screenFailure=null;await route.fulfill({status,contentType:'application/json',body:'{}'});return}await route.continue()});
  await page.goto(base);await page.locator('#authenticate-previews').waitFor({state:'visible'});await page.waitForLoadState('networkidle');await page.locator('#authenticate-previews').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.session-card .capture')].some(e=>e.textContent.includes('SYNTHETIC SCREEN ONLY')));
  assert.ok(previewPending>0,'open while the second overview capture still holds the real backend lock');
  await page.locator('.session-card').filter({hasText:'SYNTHETIC SCREEN ONLY'}).first().locator('.card-preview').click();
  const embedded=page.frameLocator('#console-frame');
  await embedded.locator('#console-screen').waitFor();
  await page.waitForFunction(()=>document.querySelector('#console-frame')?.contentDocument?.querySelector('#console-screen')?.textContent.includes('FIXTURE TICK'),null,{timeout:15000});
  const detail=page.frames().find(f=>f.url().includes('/console.html'));
  assert(detail);const before=snapshots,callStart=calls.length;const identity=await embedded.locator('#console-target').textContent();
  await detail.evaluate(()=>{window.fixtureDocument=document;window.fixturePulses=0;window.fixtureObserver=new MutationObserver(records=>{fixturePulses+=records.filter(r=>r.attributeName==='disabled').length});for(const id of ['console-toggle','console-refresh'])fixtureObserver.observe(document.getElementById(id),{attributes:true,attributeFilter:['disabled']});document.querySelector('#console-toggle').focus();window.fixtureFocus=document.activeElement;const screen=document.querySelector('#console-screen');screen.scrollTop=100;screen.dispatchEvent(new Event('scroll'));window.fixtureTop=screen.scrollTop;const range=document.createRange();range.selectNodeContents(screen);getSelection().removeAllRanges();getSelection().addRange(range);window.fixtureSelection=getSelection().toString()});
  await page.waitForTimeout(26500);
  assert.ok(snapshots>=before+5,'parent performs at least five real overview timer cycles');
  assert.equal(await detail.evaluate(()=>fixturePulses),0,'background samples never pulse foreground disabled state');
  assert.equal(await detail.evaluate(()=>document.activeElement===fixtureFocus),true,'polling preserves foreground focus');
  assert.equal(frameNavigations,1,'parent polling does not reload detail');
  assert.equal(await detail.evaluate(()=>document===fixtureDocument),true);
  assert.equal(await detail.evaluate(()=>getSelection().toString()===fixtureSelection),true,'changing output retains selection');
  assert.equal(await detail.evaluate(()=>document.querySelector('#console-screen').scrollTop===fixtureTop),true,'reading position remains stable');
  assert.equal(await embedded.locator('#console-target').textContent(),identity);
  assert.equal(await embedded.locator('#console-connect').isDisabled(),true,'same live lease remains connected');
  assert.equal(calls.slice(callStart).some(c=>['/api/console/preview','/api/console/targets','/api/console/workflow','/api/console/open','/api/console/close'].includes(c.path)),false,'parent does not rediscover/capture/reopen behind active detail');
  screenFailure=429;await page.waitForTimeout(1200);assert.equal(await embedded.locator('#console-connect').isDisabled(),true,'temporary busy retains current lease');
  await embedded.locator('#console-latest').click();assert.match(await embedded.locator('#console-screen').textContent(),/FIXTURE TICK/);
  screenFailure=403;await page.waitForFunction(()=>document.querySelector('#console-frame')?.contentDocument?.querySelector('#console-connect')?.disabled===false);
  assert.equal(await embedded.locator('#console-screen').textContent(),'','real denial clears capture');
  assert.equal(calls.some(c=>c.path==='/api/console/send'||c.path==='/api/console/resize'||(c.path==='/api/console/control'&&c.body?.enabled)),false,'inspection never enables or sends input');
  assert.deepEqual(errors,[]);console.log(`Long-lived inspector passed (${process.env.ZUDO_BROWSER_PROXY?'proxy':'direct'}): open during pending home capture, five parent cycles, zero foreground-control pulses, stable frame/lease/selection/scroll, busy recovery, denial clears.`);
 }finally{if(page)await page.unrouteAll({behavior:'ignoreErrors'});if(browser)await browser.close();fixture.kill('SIGTERM');await new Promise(r=>fixture.exitCode!==null?r():fixture.once('exit',r))}
})().catch(e=>{console.error(e);process.exitCode=1});
