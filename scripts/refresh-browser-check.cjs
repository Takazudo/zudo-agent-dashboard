/* Repeated real polling against disposable tmux only; never enables or sends input. */
'use strict';
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright-core');
(async()=>{
 const fixture=spawn('python3',['scripts/console-browser-fixture.py',...(process.env.ZUDO_BROWSER_PROXY?['--proxy']:[])],{stdio:['ignore','pipe','inherit']});
 let browser,page;
 try{
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),15000);fixture.stdout.on('data',b=>{const m=b.toString().match(/https?:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}});fixture.on('exit',()=>{clearTimeout(timer);reject(Error('Fixture exited'))})});
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  const context=await browser.newContext({ignoreHTTPSErrors:!!process.env.ZUDO_BROWSER_PROXY,httpCredentials:{username:'fixture',password:'public-fixture-password'},viewport:{width:1100,height:700}});
  page=await context.newPage();let failure=null,slowRemaining=0,held=false,reads=0,navigations=0;const errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++});
  page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/api/console/workflow')reads++;if(['/api/console/open','/api/console/control','/api/console/send'].includes(path))mutations.push(path)});
  await page.route('**/api/console/workflow',async route=>{
   if(failure==='denied'){await route.fulfill({status:403,contentType:'application/json',body:'{}'});return}
   if(failure==='http-error'){await route.fulfill({status:503,contentType:'application/json',body:'{}'});return}
   if(failure==='unavailable'){
    const response=await route.fetch(),data=await response.json();data.sessions=[];data.discovery={status:'unavailable',targets:[]};await route.fulfill({response,json:data});return;
   }
   await route.continue();
  });
  await page.route('**/api/console/preview',async route=>{if(slowRemaining>0){slowRemaining--;held=true;await new Promise(resolve=>setTimeout(resolve,3100));held=false}await route.continue()});
  await page.goto(base);await page.locator('#authenticate-previews').waitFor({state:'visible'});await page.waitForLoadState('networkidle');await page.locator('#authenticate-previews').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.capture')].filter(x=>x.textContent.includes('SYNTHETIC SCREEN ONLY')).length===2);await page.waitForLoadState('networkidle');
  const count=await page.locator('.session-card').count();assert.equal(count,2);
  await page.evaluate(()=>{
   window.fixtureCard=document.querySelector('.session-card');window.fixtureCapture=fixtureCard.querySelector('.capture');window.fixtureSelect=fixtureCard.querySelector('select');fixtureSelect.focus();
   const lane=document.querySelector('[data-scroll-lane=inbox]');lane.scrollTop=80;window.fixtureScroll=lane.scrollTop;
   const range=document.createRange();range.selectNodeContents(fixtureCapture);getSelection().removeAllRanges();getSelection().addRange(range);window.fixtureSelection=getSelection().toString();
  });
  slowRemaining=2;await page.evaluate(()=>{window.fixtureCycle=WorkspaceController.refresh()});
  for(let i=0;!held&&i<100;i++)await page.waitForTimeout(20);assert.equal(held,true);
  const before=reads;await page.waitForTimeout(5200);assert.equal(held,true,'second individually bounded capture is still pending after the five-second tick');assert.equal(reads,before,'real five-second timer must join slow capture cycle, not rediscover targets');
  await page.evaluate(()=>fixtureCycle);await page.waitForLoadState('networkidle');
  const afterSlow=reads;await page.waitForTimeout(11000);await page.waitForLoadState('networkidle');assert.ok(reads>=afterSlow+2,'at least two further real background cycles completed');
  assert.equal(await page.locator('.session-card').count(),count);
  assert.equal(await page.evaluate(()=>document.querySelector('.session-card')===fixtureCard),true);
  assert.equal(await page.evaluate(()=>getSelection().toString()===fixtureSelection),true);
  assert.equal(await page.evaluate(()=>document.activeElement===fixtureSelect),true);
  assert.equal(await page.evaluate(()=>document.querySelector('[data-scroll-lane=inbox]').scrollTop===fixtureScroll),true);
  for(const transient of ['unavailable','http-error']){
   failure=transient;await page.evaluate(()=>WorkspaceController.refresh());
   assert.equal(await page.locator('.session-card').count(),count,'transient failure retains session aggregation');
   assert.equal(await page.evaluate(()=>document.querySelector('.session-card')===fixtureCard),true);
   assert.equal(await page.locator('.card-preview').first().isDisabled(),true);
   assert.match(await page.locator('.session-card').first().textContent(),/STALE · last capture/);
   assert.match(await page.locator('.capture').first().textContent(),/SYNTHETIC SCREEN ONLY/);
   failure=null;await page.evaluate(()=>WorkspaceController.refresh());assert.equal(await page.locator('.card-preview').first().isDisabled(),false);
  }
  failure='denied';await page.evaluate(()=>WorkspaceController.refresh());
  assert.equal(await page.locator('.capture').filter({hasText:'SYNTHETIC SCREEN ONLY'}).count(),0,'authorization denial clears even selected cached output');
  assert.match(await page.locator('#session-surface').textContent(),/authorization failed or expired/);
  assert.deepEqual(mutations,[],'home cycles never open panes, enable control or send input');assert.deepEqual(errors,[]);assert.equal(navigations,1,'no full-page reload occurred');
  console.log(`Repeated refresh browser checks passed (${process.env.ZUDO_BROWSER_PROXY?'proxy':'direct'}): capture batch >5s, multiple real timer cycles, transient discovery/HTTP failures, stable card/selection/scroll, revocation clears captures, no input.`);
 }finally{
  if(page)await page.unrouteAll({behavior:'ignoreErrors'});if(browser)await browser.close();fixture.kill('SIGTERM');await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));
 }
})().catch(e=>{console.error(e);process.exitCode=1});
