"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {createRequire} = require("node:module");
const repo = path.resolve(__dirname, "..");
const {JSDOM} = createRequire(path.join(repo, "package.json"))("jsdom");
const html = fs.readFileSync(path.join(repo, "zudo_agent/web/console.html"), "utf8");
const script = fs.readFileSync(path.join(repo, "zudo_agent/web/console.js"), "utf8");
const wait = () => new Promise(resolve => setTimeout(resolve, 12));
async function waitFor(predicate, message, timeout = 1500) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeout) throw new Error(`Timed out waiting for ${message}`);
    await wait();
  }
}
const targets = [
  {id:"a",project:"p",machine:"m",session_id:"s",pane:"%1",foreground:"sh",run:"r",server:[1,1]},
  {id:"b",project:"p",machine:"m",session_id:"s",pane:"%2",foreground:"sh",run:null,server:[1,1]},
  {id:"c",project:"p",machine:"m",session_id:"other",pane:"%3",foreground:"sh",run:null,server:[1,1]}
];
async function fixture(hash, {manualIntervals = false} = {}) {
  const dom = new JSDOM(html, {url:`http://localhost/console.html#${hash}`, runScripts:"dangerously", pretendToBeVisual:true});
  const {window:w} = dom;
  w.ResizeObserver = class {observe() {}};
  w.AbortSignal.timeout = () => undefined;
  w.TextEncoder = TextEncoder;
  w.visualViewport = null;
  w.HTMLElement.prototype.getClientRects = function(){return [1]};
  w.HTMLElement.prototype.getBoundingClientRect = function(){return {width:100,height:100,top:0,left:0,bottom:100,right:100}};
  w.ThemeSettings = {get:()=>({vim:false}),effective:()=>"light"};
  const calls = [], drafts = new Map(); let pane = "", value = "", composing = false, enabled = false;
  const editor = {view:{},get value(){return value},set value(v){value=v},get composing(){return composing},set composing(v){composing=v},setEnabled(v){enabled=v},get enabled(){return enabled},focus(){},hasFocus(){return false},setLabel(){},selectPane(id){if(composing)return false;if(pane)drafts.set(pane,value);pane=id;value=drafts.get(id)||"";return true},measure(){},snapshot(){return {}},restore(){},reset(){drafts.clear();pane="";value=""},destroy(){}};
  let editorCallbacks;
  w.createComposerEditor = (_, callbacks) => { editorCallbacks = callbacks; return editor; };
  const intervals = [];
  if (manualIntervals) {
    w.setInterval = callback => { intervals.push(callback); return intervals.length; };
    w.clearInterval = () => {};
  }
  let nextLease = 0, pauseScreen = false, releaseScreen, pauseClose = false, releaseClose, busyNextScreen = false;
  w.fetch = async (url, options={}) => {
    const action = url.split("/").at(-1); const body = options.body ? JSON.parse(options.body) : {};
    calls.push({action,body});
    if (action === "close" && pauseClose) { pauseClose = false; await new Promise(resolve => { releaseClose = resolve; }); }
    if (action === "screen" && pauseScreen) { pauseScreen = false; await new Promise(resolve => { releaseScreen = resolve; }); }
    if (action === "screen" && busyNextScreen) { busyNextScreen = false; return {ok:false,status:429,json:async()=>({})}; }
    const data = action === "bootstrap" ? {csrf:"token",identity:"tester",allow_input:true} :
      action === "targets" ? {targets} : action === "open" ? {lease:`lease-${++nextLease}`,expires_in:120,sequence:1} :
      action === "screen" ? {screen:"hello",lines:1,limit:500,sampled_at:1,truncated:false,byte_truncated:false,foreground:"sh",cols:80,rows:24} :
      action === "control" ? {control:body.enabled} : action === "send" ? {sequence:2} : {closed:true};
    return {ok:true,json:async()=>data};
  };
  w.eval(script); await wait();
  return {
    dom,w,editor,calls,editorCallbacks,
    holdScreen:()=>{pauseScreen=true}, releaseScreen:()=>releaseScreen?.(),
    holdClose:()=>{pauseClose=true}, releaseClose:()=>releaseClose?.(),
    busyNextScreen:()=>{busyNextScreen=true}, tickIntervals:()=>intervals.forEach(callback=>callback())
  };
}
(async () => {
  const {dom,w,editor,calls,editorCallbacks} = await fixture("project=p&run=r");
  const $ = id => w.document.getElementById(id);
  assert.equal($("console-dock").hidden,true,"input starts hidden");
  assert.equal($("console-connect").disabled,false,"legacy run selects its exact pane");
  $("console-connect").click(); await wait();
  assert.equal(editor.enabled,false,"viewing does not enable editor");
  $("console-toggle").click(); await wait();
  assert.equal(editor.enabled,true,"Terminal input activates named lease");
  editor.value = "first draft";
  $("console-toggle").click(); await wait();
  assert.equal(editor.value,"first draft","closing input retains draft");
  assert.ok(calls.some(c=>c.action==="control"&&c.body.enabled===false),"closing revokes server control");
  editor.composing = true;
  $("console-run").value="b"; $("console-run").dispatchEvent(new w.Event("change")); await wait();
  assert.equal(editor.value,"first draft","IME blocks pane state switch");
  assert.equal(calls.filter(c=>c.action==="open").length,1,"IME does not open new target early");
  editor.composing = false; editorCallbacks.composition(false); await wait();
  assert.equal(editor.value,"","new pane has separate draft");
  $("console-run").value="a"; $("console-run").dispatchEvent(new w.Event("change")); await wait();
  assert.equal(editor.value,"first draft","returning to pane restores draft");
  $("console-toggle").click(); await wait();
  editor.value="x".repeat(4097);
  $("console-send").click(); await wait();
  assert.equal(editor.value.length,4097,"overlong compose draft stays available for editing");
  assert.equal(calls.filter(c=>c.action==="send").length,0,"overlong compose draft is never sent");
  $("console-close").click(); await wait();
  assert.equal(editor.value,"","disconnect wipes all drafts");
  assert.equal($("console-connect").disabled,false,"disconnect keeps explicit reconnect available");
  $("console-connect").click(); await wait();
  assert.equal(editor.enabled,false,"reconnect starts read-only");
  dom.window.close();
  const polling = await fixture("project=p&id=a&session=s");
  const panel = polling.w.document.getElementById("console-toggle");
  polling.w.document.getElementById("console-connect").click(); await wait();
  polling.holdScreen(); polling.w.document.getElementById("console-refresh").click(); await wait();
  assert.equal(panel.disabled,true,"activation during pending poll waits until safe");
  polling.releaseScreen(); await wait();
  panel.click(); await wait();
  polling.holdScreen(); polling.w.document.getElementById("console-refresh").click(); await wait();
  assert.equal(panel.disabled,false,"closing input remains available during pending poll");
  panel.click();
  assert.equal(polling.editor.enabled,false,"closing stops local input immediately");
  polling.releaseScreen(); await wait();
  polling.dom.window.close();
  const switching = await fixture("project=p&id=a&session=s",{manualIntervals:true});
  const switch$ = id => switching.w.document.getElementById(id);
  switch$("console-connect").click();
  await waitFor(() => switching.calls.some(c=>c.action==="open"), "initial pane connection");
  await waitFor(() => switch$("console-status").textContent.toLowerCase().includes("connected read-only"), "initial read-only status");
  switching.holdClose();
  switch$("console-run").value="b";
  switch$("console-run").dispatchEvent(new switching.w.Event("change"));
  await waitFor(() => switching.calls.some(c=>c.action==="close"), "old lease close to begin");
  assert.equal(switch$("console-connect").disabled,true,"Connect stays disabled while target selection waits for old lease close");
  switch$("console-connect").disabled=false;
  switch$("console-connect").dispatchEvent(new switching.w.MouseEvent("click",{bubbles:true}));
  await wait();
  assert.deepEqual(switching.calls.filter(c=>c.action==="open").map(c=>c.body.id),["a"],"forged Connect during a held close cannot reopen the old target");
  switching.releaseClose();
  await waitFor(() => switching.calls.filter(c=>c.action==="open").length===2, "new pane connection after close");
  assert.deepEqual(switching.calls.filter(c=>c.action==="open").map(c=>c.body.id),["a","b"],"only the newly selected target opens after old close completes");
  switching.dom.window.close();
  const busy = await fixture("project=p&id=a&session=s",{manualIntervals:true});
  const busy$ = id => busy.w.document.getElementById(id);
  busy$("console-connect").click();
  await waitFor(() => busy.calls.filter(c=>c.action==="open").length===1, "read-only pane connection");
  await waitFor(() => busy$("console-sampled").textContent.startsWith("Sampled "), "initial screen capture");
  busy$("console-toggle").click();
  await waitFor(() => busy.editor.enabled, "explicit input activation");
  busy.editor.value="draft survives temporary capture busy";
  busy.busyNextScreen();
  busy$("console-refresh").click();
  await waitFor(() => busy.calls.filter(c=>c.action==="screen").length===2, "capture returning temporary busy");
  await waitFor(() => busy$("console-sampled").textContent.includes("temporarily busy"), "temporary busy feedback");
  assert.equal(busy.calls.filter(c=>c.action==="open").length,1,"429 does not replace the connected lease");
  assert.equal(busy.calls.filter(c=>c.action==="close").length,0,"temporary busy response does not close the lease");
  assert.equal(busy.editor.enabled,true,"temporary busy response preserves explicit control state");
  assert.equal(busy.editor.value,"draft survives temporary capture busy","temporary busy response preserves the compose draft");
  busy.tickIntervals();
  await waitFor(() => busy.calls.filter(c=>c.action==="screen").length===3, "next automatic capture poll");
  await waitFor(() => busy$("console-sampled").textContent.startsWith("Sampled "), "capture to resume after busy response");
  assert.equal(busy.calls.filter(c=>c.action==="open").length,1,"recovered polling reuses the same lease");
  assert.equal(busy.calls.filter(c=>c.action==="close").length,0,"temporary busy response does not close the lease");
  assert.equal(busy.editor.enabled,true,"temporary busy response preserves explicit control state");
  assert.equal(busy.editor.value,"draft survives temporary capture busy","temporary busy response preserves the compose draft");
  busy.dom.window.close();
  const stale = await fixture("project=p&id=missing&session=s");
  assert.equal(stale.w.document.getElementById("console-connect").disabled,true,"stale exact target cannot fall back");
  stale.dom.window.close();
  console.log("round08 detail checks passed: activation, poll interruption, held-close target race, temporary-busy polling, revocation, pane drafts, IME guard, byte boundary, reconnect, stale link");
})().catch(error=>{console.error(error);process.exitCode=1});
