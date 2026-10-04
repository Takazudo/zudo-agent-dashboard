/* Pure mock dashboard state: never talks to a terminal. */
(function (root) {
  'use strict';
  const workflows=[{id:'inbox',label:'Inbox'},{id:'progress',label:'In progress'},{id:'review',label:'Review'},{id:'done',label:'Done'}];
  const activities={working:{label:'Working',short:'Working',icon:'activity'},waiting:{label:'Waiting for input',short:'Waiting for input',icon:'message'},completed:{label:'Task completed',short:'Task completed',icon:'check'},idle:{label:'Idle',short:'Idle',icon:'moon'},'no-agent':{label:'No agent running',short:'No agent running',icon:'terminal'},unknown:{label:'Unknown',short:'Unknown',icon:'help'}};
  function primary(session){return session.panes.find(p=>p.id===session.primaryPane)||session.panes[0]}
  function visible(sessions,{scope={},query='',activity='all'}={}) {const q=query.trim().toLocaleLowerCase();return sessions.filter(s=>(!scope.device||s.device===scope.device)&&(!scope.project||s.project===scope.project)&&(!scope.session||s.id===scope.session)&&(activity==='all'||primary(s).observation.activity===activity)&&(!q||[s.device,s.project,s.name,s.title,...s.panes.map(p=>p.id+' '+p.agent)].join(' ').toLocaleLowerCase().includes(q)))}
  function move(sessions,id,workflow){if(!workflows.some(w=>w.id===workflow))throw new Error('Invalid workflow');const s=sessions.find(s=>s.id===id);if(!s)throw new Error('Unknown session');const previous=s.workflow;s.workflow=workflow;return {session:s,previous,workflow}}
  root.DashboardModel={workflows,activities,primary,visible,move};
})(typeof window!=='undefined'?window:globalThis);
