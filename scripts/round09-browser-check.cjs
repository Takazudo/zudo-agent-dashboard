/* Native command-dialog QA using synthetic sample observations only. */
'use strict';
const {chromium}=require('playwright-core');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
async function sample(){return new Promise((resolve,reject)=>{const server=spawn('python3',['-m','zudo_agent','serve','--sample','--port','0'],{stdio:['ignore','pipe','inherit']});const timer=setTimeout(()=>{server.kill();reject(Error('Sample startup timeout'))},10000);server.stdout.on('data',data=>{const base=data.toString().match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];if(base){clearTimeout(timer);resolve({server,base})}});server.on('exit',()=>{clearTimeout(timer);reject(Error('Sample exited'))})})}
(async()=>{
 const {server,base}=await sample();let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForSelector('.session-card');
  const modifier=await page.evaluate(()=>/Mac|iPhone|iPad/.test(navigator.platform)?'Meta':'Control');
  const palette=page.locator('#command-palette'),search=page.locator('#command-search'),close=palette.locator('[data-close]');
  await page.locator('#commands-open').focus();await page.keyboard.press(`${modifier}+Shift+k`);assert.equal(await palette.isVisible(),false,'extra Shift does not launch');
  await page.keyboard.press(`${modifier}+k`);assert.ok(await palette.isVisible());
  await page.keyboard.press('Tab');assert.ok(await close.evaluate(el=>el===document.activeElement));
  await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'command-search');
  await page.keyboard.press('Shift+Tab');assert.ok(await close.evaluate(el=>el===document.activeElement));
  await page.keyboard.press('Enter');assert.equal(await palette.isVisible(),false,'Close Enter does not execute selected command');
  assert.equal(await page.locator('#sidebar').isVisible(),false);
  await page.locator('#commands-open').click();await search.fill('workflow');assert.equal(await page.locator('#command-results [aria-selected=true]').getAttribute('data-command-id'),'board','aliases find Kanban');
  await search.evaluate(el=>el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})));
  await search.dispatchEvent('keydown',{key:'Enter',isComposing:true});await search.dispatchEvent('keydown',{key:'Tab',isComposing:true});
  await palette.evaluate(el=>el.dispatchEvent(new Event('cancel',{cancelable:true})));assert.ok(await palette.isVisible(),'native cancel yields during composition');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'command-search');
  await search.evaluate(el=>el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
  await search.dispatchEvent('keydown',{key:'Enter',repeat:true});assert.ok(await palette.isVisible(),'repeat cannot execute');
  await page.keyboard.press('Enter');assert.equal(await palette.isVisible(),false);
  await page.locator('#commands-open').click();await search.fill('');
  for(let i=0;i<15;i++)await page.keyboard.press('ArrowDown');
  const selected=await page.locator('#command-results [aria-selected=true]').getAttribute('data-command-id');
  await page.evaluate(()=>window.WorkspaceController.refresh());
  assert.equal(await page.locator('#command-results [aria-selected=true]').getAttribute('data-command-id'),selected,'refresh preserves stable command identity');
  assert.ok(await page.locator('#command-results [aria-selected=true]').evaluate(el=>{const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return a.top>=b.top-1&&a.bottom<=b.bottom+1}),'selected command remains in view');
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'commands-open');
  await page.keyboard.press('?');assert.ok(await page.locator('#command-help').isVisible());
  await page.keyboard.press('Tab');assert.ok(await page.locator('#command-help [data-close]').evaluate(el=>el===document.activeElement));await page.keyboard.press('Escape');
  await page.locator('#overview-toggle').click();await page.locator('#search').focus();await page.keyboard.press(`${modifier}+k`);assert.equal(await palette.isVisible(),false,'search owns native editing shortcuts');
  await page.locator('#overview-close').click();
  await page.locator('[data-open-settings]').first().click();const settings=page.locator('#settings-dialog');
  await settings.evaluate(el=>el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})));
  await settings.evaluate(el=>el.dispatchEvent(new Event('cancel',{cancelable:true})));assert.ok(await settings.isVisible(),'settings native cancel yields during composition');
  await settings.evaluate(el=>el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));await page.keyboard.press('Escape');assert.equal(await settings.isVisible(),false);
  await page.setViewportSize({width:390,height:844});await page.locator('#tree-toggle').click();await page.locator('#all-sessions').focus();await page.keyboard.press(`${modifier}+k`);await search.fill('focus session');await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'search','mobile search command closes inert drawer before destination focus');assert.equal(await page.locator('#sidebar').isVisible(),false);
  await page.locator('#overview-close').click();await page.locator('#commands-open').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'test-results/round09-palette-mobile.png'});await page.keyboard.press('Escape');
  await page.emulateMedia({colorScheme:'dark'});await page.locator('#help-open').click();await page.screenshot({path:'test-results/round09-help-mobile-dark.png'});await page.keyboard.press('Escape');
  await page.setViewportSize({width:1000,height:460});await page.locator('#commands-open').click();assert.ok(await palette.evaluate(el=>el.getBoundingClientRect().height<=innerHeight));await page.screenshot({path:'test-results/round09-palette-short-dark.png'});
  assert.deepEqual(errors,[]);console.log('Round09 native commands passed: modal Tab/Enter, aliases, composition/cancel/repeat, stable refresh selection, editable ownership, mobile drawer destination, responsive light/dark.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM')}
})().catch(error=>{console.error(error);process.exitCode=1});
