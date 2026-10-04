'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'zudo_agent/web/index.html'),'utf8');
const preferences=fs.readFileSync(path.join(root,'zudo_agent/web/preferences.js'),'utf8');
const app=fs.readFileSync(path.join(root,'zudo_agent/web/app.js'),'utf8');
const snapshot={schema_version:1,mode:'local',generated_at:1,projects:[{id:'project',repository:'repo',completion:'unknown',runs:[{id:'current',machine:'device',source:'agent',state:'working',reachability:'present',evidence:'current run'},{id:'old',machine:'device',source:'agent',state:'ended',reachability:'absent',evidence:'previous run'}]}],collectors:[{machine:'device',status:'offline',collector_status:'stopped',panes:0,unmatched_panes:0,checked_at:1}]};
function setup(stored){
 const dom=new JSDOM(html,{url:'http://127.0.0.1/',runScripts:'outside-only',pretendToBeVisual:true});const w=dom.window;
 w.matchMedia=()=>({matches:false,addEventListener(){}});w.AbortSignal.timeout=()=>undefined;w.setInterval=()=>1;
 if(stored!==undefined)w.localStorage.setItem('zudo-agent-dashboard-workspace-v1',stored);
 w.fetch=async url=>{if(url==='/api/snapshot')return {ok:true,json:async()=>snapshot};if(url==='/api/workflow')return {ok:true,json:async()=>({items:{},sessions:[],run_keys:[]})};if(url==='/api/console/status')return {ok:true,json:async()=>({enabled:false})};throw Error(url)};
 w.eval(preferences);return {dom,w};
}
(async()=>{
 const bad=setup('{broken');assert.deepEqual(JSON.parse(JSON.stringify(bad.w.WorkspacePreferences.get())),{explorer:false,overview:false,view:'board'});assert.equal(bad.w.document.documentElement.dataset.workspaceExplorer,'false');bad.w.close();
 const {w}=setup();w.eval(app);await new Promise(resolve=>setTimeout(resolve,25));
 const d=w.document,controller=w.WorkspaceController,frame=d.querySelector('#console-frame');
 assert.equal(d.querySelector('#view-board').getAttribute('aria-pressed'),'true');assert.equal(d.querySelectorAll('.board-lane').length,4);
 assert.equal(d.querySelector('#sidebar').inert,true);assert.equal(d.querySelector('#overview-panel').inert,true);
 assert.match(d.querySelector('#project-history').textContent,/Previous runs \(1\)/);
 assert.match(d.querySelector('#attention-indicator').textContent,/disconnected/);
 controller.setExplorer(true);assert.equal(d.querySelector('#sidebar').inert,false);assert.equal(d.querySelector('#tree-toggle').getAttribute('aria-expanded'),'true');
 d.querySelector('#all-sessions').focus();controller.setExplorer(false);assert.equal(d.activeElement,d.querySelector('#tree-toggle'));assert.equal(d.querySelector('#sidebar').inert,true);
 controller.setOverview(true);d.querySelector('#search').focus();controller.setOverview(false);assert.equal(d.activeElement,d.querySelector('#overview-toggle'));assert.equal(d.querySelector('#overview-panel').inert,true);
 controller.focusSearch();assert.equal(d.activeElement,d.querySelector('#search'));d.querySelector('#search').value='missing';d.querySelector('#search').dispatchEvent(new w.Event('input',{bubbles:true}));assert.match(d.querySelector('#filter-indicator').textContent,/Search: missing/);
 controller.clearFilters();assert.equal(d.querySelector('#filter-indicator').hidden,true);
 controller.setView('gallery');assert.equal(d.querySelector('#view-gallery').getAttribute('aria-pressed'),'true');assert.equal(d.querySelector('#console-frame'),frame);
 assert.deepEqual(JSON.parse(w.localStorage.getItem('zudo-agent-dashboard-workspace-v1')),{explorer:false,overview:true,view:'gallery'});
 assert.equal(w.localStorage.getItem('zudo-agent-dashboard-settings-v1'),null);
 w.ThemeSettings.commit({theme:'dark',vim:true,wrap:false,lineNumbers:true,fontSize:16});assert.equal(w.WorkspacePreferences.get().overview,true);
 assert.equal(w.localStorage.getItem('zudo-agent-dashboard-workspace-v1').includes('missing'),false);
 w.close();console.log('round09 compact workspace check passed');
})().catch(error=>{console.error(error);process.exitCode=1});
