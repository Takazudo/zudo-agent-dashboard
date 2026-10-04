'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'zudo_agent/web/index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'zudo_agent/web/app.js'), 'utf8');
const run = {id:'run-a',machine:'device',source:'codex',state:'needs-attention',reachability:'disconnected',freshness:'stale',state_freshness:'stale',confidence:'reported',evidence:'Input request observed',state_at:1,last_seen:1,recent_events:[]};
const complete = {...run,id:'run-complete',state:'completed',reachability:'present',freshness:'fresh',state_freshness:'fresh',evidence:'Completion event observed'};
const prior = {...run,id:'run-old',state:'ended',reachability:'absent'};
const snapshot = {schema_version:1,mode:'local',generated_at:1,projects:[{id:'project',repository:'example/project',completion:'unknown',runs:[run,complete,prior]}],collectors:[{machine:'device',status:'offline',collector_status:'stopped',panes:0,unmatched_panes:0,checked_at:1,omitted_runs:2}]};
let authenticated=false;
const workflow = {csrf:'token',items:{'run-key':{lane:'review',revision:2},'complete-key':{lane:'inbox',revision:0}},run_keys:[{id:'run-key',canonical:'run-key',project:'project',machine:'device',run:'run-a'},{id:'complete-key',canonical:'complete-key',project:'project',machine:'device',run:'run-complete'}],sessions:[]};
const dom = new JSDOM(html,{url:'http://127.0.0.1:8765/',runScripts:'outside-only',pretendToBeVisual:true});
const w = dom.window;
w.AbortSignal.timeout = () => undefined;
w.setInterval = () => 1;
w.fetch = async (url,options={}) => {
  if(url==='/api/snapshot') return {ok:true,json:async()=>snapshot};
  if(url==='/api/workflow' && !options.method) { if(authenticated){workflow.run_keys[0].canonical='session-key';workflow.items['session-key']={lane:'done',revision:3};workflow.sessions=[{id:'session-key',session_id:'session-id',project:'project',machine:'device',panes:['pane-id'],runs:['run-a']}]} return {ok:true,json:async()=>workflow}; }
  if(url==='/api/console/status') return {ok:true,json:async()=>({enabled:true})};
  if(url==='/api/console/bootstrap'){authenticated=true;snapshot.collectors[0].status='connected';return {ok:true,json:async()=>({csrf:'console-token',identity:'fixture',allow_input:false})}}
  if(url==='/api/console/targets')return {ok:true,json:async()=>({targets:[{project:'project',machine:'device',id:'pane-id',session_id:'session-id',workflow_id:'session-key',run:'run-a',pane:'%1',foreground:'agent'}]})};
  if(url==='/api/console/preview'){assert.equal(options.headers['X-Console-CSRF'],'console-token');return {ok:true,json:async()=>({screen:'fixture recent output',sampled_at:1,lines:1,limit:500})}}
  if(url==='/api/workflow' && options.method==='POST') {
    assert.deepEqual(JSON.parse(options.body),{id:'run-key',lane:'done',revision:2});
    assert.equal(options.headers['X-Workflow-CSRF'],'token');
    return {ok:true,status:200,json:async()=>({id:'run-key',lane:'done',revision:3})};
  }
  throw Error(`Unexpected fetch ${url}`);
};
w.eval(app);
(async()=>{
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.match(w.document.querySelector('#notice').textContent,/Local observations/);
  assert.equal(w.document.querySelectorAll('.session-card').length,2);
  assert.match(w.document.querySelector('#session-surface').textContent,/Task completed/);
  assert.match(w.document.querySelector('.card-origin').textContent,/device \/ project/);
  assert.match(w.document.querySelector('.observed').textContent,/Waiting for input/);
  assert.match(w.document.querySelector('.observed').textContent,/STALE/);
  assert.match(w.document.querySelector('.capture').textContent,/Capture unavailable/);
  assert.match(w.document.querySelector('#project-history').textContent,/Previous runs \(1\)/);
  assert.match(w.document.querySelector('#home-health').textContent,/offline/);
  w.document.querySelector('#view-board').click();
  assert.equal(w.document.querySelectorAll('.board-lane').length,4);
  assert.equal(w.document.querySelector('[data-scroll-lane="review"] .session-card')!==null,true);
  const select=w.document.querySelector('[data-move="run-key"]');select.value='done';select.dispatchEvent(new w.Event('change',{bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,15));
  assert.equal(workflow.items['run-key'].lane,'done');
  assert.equal(w.document.querySelector('[data-scroll-lane="done"] .session-card')!==null,true);
  assert.equal(run.state,'needs-attention');
  w.document.querySelector('#authenticate-previews').click();
  await new Promise(resolve=>setTimeout(resolve,35));
  assert.equal(w.document.querySelectorAll('.session-card').length,2,'canonical session deduplicates observed run while completed run remains current');
  assert.match(w.document.querySelector('[data-session-id="session-key"] .capture').textContent,/fixture recent output/);
  assert.equal(w.document.querySelectorAll('[data-node^=pane]').length,1);
  w.close();
  console.log('round08 home model check passed');
})().catch(error=>{console.error(error);process.exitCode=1;w.close()});
