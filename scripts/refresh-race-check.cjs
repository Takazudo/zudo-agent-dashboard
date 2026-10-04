'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync('zudo_agent/web/index.html','utf8'),{url:'http://127.0.0.1:8765/',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window, d=w.document;
w.matchMedia=()=>({matches:false,addEventListener(){}});w.AbortSignal.timeout=()=>undefined;
let tick, discovery='ready', active=0, overlaps=0, previews=0, holdNext=false, release, reads=0;
w.setInterval=fn=>{tick=fn;return 1};
const targets=Array.from({length:22},(_,i)=>({id:`pane-${i}`,pane:`%${i}`,workflow_id:`session-${i}`,session_id:`session-${i}`,project:'project',machine:'fixture',run:i<9?`run-${i}`:null}));
const snapshot={schema_version:1,mode:'live',generated_at:Date.now()/1000,collectors:[],projects:[{id:'project',runs:targets.slice(0,9).map(t=>({id:t.run,machine:'fixture',source:'codex',state:'idle',evidence:'lifecycle',freshness:'fresh',state_freshness:'stale',state_at:1,last_seen:Date.now()/1000}))}]};
const metadata={csrf:'fixture',items:Object.fromEntries(targets.map(t=>[t.workflow_id,{lane:'inbox',revision:0}])),run_keys:targets.slice(0,9).map(t=>({id:`key-${t.run}`,canonical:t.workflow_id,project:'project',machine:'fixture',run:t.run})),sessions:targets.map(t=>({id:t.workflow_id,session_id:t.session_id,project:t.project,machine:t.machine,panes:[t.id]}))};
const response=(data,status=200)=>({ok:status===200,status,json:async()=>structuredClone(data)});
w.fetch=async(url)=>{
 if(url==='/api/snapshot')return response(snapshot);
 if(url==='/api/console/status')return response({enabled:true});
 if(url==='/api/console/bootstrap')return response({csrf:'fixture'});
 if(url==='/api/console/targets')return response({targets});
 if(url.endsWith('/workflow')){reads++;if(active)overlaps++;if(discovery==='denied')return response({},403);if(discovery==='http-error')return response({},503);if(discovery==='missing')return response({...metadata,sessions:[]});const status=active?'unavailable':discovery==='removed'?'ready':discovery;return response({...metadata,sessions:status==='ready'?metadata.sessions.slice(discovery==='removed'?1:0):[],discovery:{status,targets:status==='ready'?targets.slice(discovery==='removed'?1:0):[]}})}
 if(url==='/api/console/preview'){previews++;active++;try{if(holdNext){holdNext=false;await new Promise(resolve=>{release=resolve})}return response({screen:'CAPTURE fixture '+previews,sampled_at:Date.now()/1000})}finally{active--}}
 throw Error('Unexpected endpoint '+url);
};
w.eval(fs.readFileSync('zudo_agent/web/preferences.js','utf8'));
w.eval(fs.readFileSync(process.env.ZUDO_APP_SOURCE||'zudo_agent/web/app.js','utf8')+';window.testEval=code=>eval(code);');
const flush=()=>new Promise(r=>setTimeout(r,0));
const cards=()=>[...d.querySelectorAll('.session-card')];
(async()=>{
 await flush();await w.testEval('authenticatePreviews()');
 assert.equal(cards().length,22,'authenticated grouping includes panes without lifecycle runs');
 const first=cards()[0], capture=first.querySelector('.capture'), select=first.querySelector('select');
 select.focus();d.querySelector('[data-scroll-lane=inbox]').scrollTop=80;
 const selection=w.getSelection(),range=d.createRange();range.selectNodeContents(capture);selection.addRange(range);const selected=selection.toString();
 holdNext=true;const cycle=tick();await flush();assert.equal(active,1);
 const before=reads;const joined=[tick(),tick(),w.WorkspaceController.refresh()];await flush();
 assert.equal(reads,before,'timer/manual refresh cannot start discovery during a pending capture batch');
 assert.equal(overlaps,0);release();await Promise.all([cycle,...joined]);
 assert.equal(cards().length,22);assert.equal(cards()[0],first,'card DOM identity survives repeated captures');
 assert.equal(d.activeElement,select);assert.equal(selection.toString(),selected,'capture selection survives changed output');
 assert.equal(d.querySelector('[data-scroll-lane=inbox]').scrollTop,80);
 const normalFetch=w.fetch;w.fetch=async(url,options)=>options?.method==='POST'?response({},503):normalFetch(url,options);select.value='progress';await w.testEval("move('session-0','progress')");assert.equal(select.value,'inbox','failed metadata move restores the live select value without replacing it');w.fetch=normalFetch;
 w.fetch=async(url,options)=>url==='/api/snapshot'?response({},503):normalFetch(url,options);await tick();assert.equal(select.disabled,true,'snapshot failure disables workflow moves');assert.equal(cards().length,22);w.fetch=normalFetch;await tick();assert.equal(select.disabled,false);

 for(const failure of ['unavailable','http-error','missing']){
  discovery=failure;const before=previews;await tick();
  assert.equal(cards().length,22,'temporary discovery failure must not collapse 22 sessions into 9 observed runs');
  assert.equal(cards()[0],first);assert.match(first.textContent,/STALE · last capture/);
  assert.equal(first.querySelector('.card-preview').disabled,true);assert.equal(select.disabled,true);assert.equal(previews,before);
  assert.match(capture.textContent,/CAPTURE/);
  discovery='ready';await tick();assert.equal(first.querySelector('.card-preview').disabled,false);assert.equal(select.disabled,false);
 }
 // A stale cache expires even while selected; selected text cannot defeat removal.
 discovery='unavailable';w.testEval('for(const s of sessions)if(s.preview)s.preview.received_at=Date.now()-61000');await tick();
 assert.doesNotMatch(first.querySelector('.capture').textContent,/CAPTURE/);assert.equal(cards().length,22);
 discovery='ready';await tick();
 discovery='removed';await tick();assert.doesNotMatch(d.querySelector('[data-session-id="session-0"] .capture').textContent,/CAPTURE/,'successful target removal clears old capture');
 discovery='ready';await tick();
 // A 409 metadata recovery rebuilds sessions while an older capture body is pending.
 holdNext=true;const pendingCycle=tick();await flush();assert.equal(active,1);
 metadata.items['session-0']={lane:'review',revision:2};
 w.fetch=async(url,options)=>options?.method==='POST'?response({},409):url.endsWith('/workflow')?response({...metadata,discovery:{status:'ready',targets}}):normalFetch(url,options);
 await w.testEval("move('session-0','progress')");assert.equal(d.querySelector('[data-session-id="session-0"] select').value,'review');
 release();await pendingCycle;assert.equal(d.querySelector('[data-session-id="session-0"] select').value,'review','old capture cannot repaint pre-conflict workflow');
 w.fetch=normalFetch;
 discovery='denied';await tick();assert.equal(w.testEval('targets.length'),0);assert.equal(w.testEval('previewsAllowed'),false);
 assert.equal(cards().length,9);assert.doesNotMatch(d.querySelector('#session-surface').textContent,/CAPTURE/);
 // Old unauthenticated JSON cannot overwrite a subsequently authenticated mapping.
 let finish;w.fetch=async()=>({ok:true,status:200,json:()=>new Promise(r=>{finish=r})});
 const pending=w.testEval('loadWorkflow()');await flush();w.testEval('previewsAllowed=true;targets=[{id:"new-authorized"}]');finish({sessions:[],items:{}});await pending;
 assert.equal(w.testEval('targets[0].id'),'new-authorized');
 console.log('repeated refresh race checks passed: 22→22 transient, 22→9 revoked, DOM/selection/scroll retained');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>w.close());
