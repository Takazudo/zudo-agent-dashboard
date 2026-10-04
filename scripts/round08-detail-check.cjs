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
const targets = [
  {id:"a",project:"p",machine:"m",session_id:"s",pane:"%1",foreground:"sh",run:"r",server:[1,1]},
  {id:"b",project:"p",machine:"m",session_id:"s",pane:"%2",foreground:"sh",run:null,server:[1,1]},
  {id:"c",project:"p",machine:"m",session_id:"other",pane:"%3",foreground:"sh",run:null,server:[1,1]}
];
async function fixture(hash) {
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
  let nextLease = 0, pauseScreen = false, releaseScreen;
  w.fetch = async (url, options={}) => {
    const action = url.split("/").at(-1); const body = options.body ? JSON.parse(options.body) : {};
    calls.push({action,body});
    if (action === "screen" && pauseScreen) { pauseScreen = false; await new Promise(resolve => { releaseScreen = resolve; }); }
    const data = action === "bootstrap" ? {csrf:"token",identity:"tester",allow_input:true} :
      action === "targets" ? {targets} : action === "open" ? {lease:`lease-${++nextLease}`,expires_in:120,sequence:1} :
      action === "screen" ? {screen:"hello",lines:1,limit:500,sampled_at:1,truncated:false,byte_truncated:false,foreground:"sh",cols:80,rows:24} :
      action === "control" ? {control:body.enabled} : action === "send" ? {sequence:2} : {closed:true};
    return {ok:true,json:async()=>data};
  };
  w.eval(script); await wait();
  return {dom,w,editor,calls,editorCallbacks,holdScreen:()=>{pauseScreen=true},releaseScreen:()=>releaseScreen?.()};
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
  const stale = await fixture("project=p&id=missing&session=s");
  assert.equal(stale.w.document.getElementById("console-connect").disabled,true,"stale exact target cannot fall back");
  stale.dom.window.close();
  console.log("round08 detail checks passed: activation, poll interruption, revocation, pane drafts, IME guard, byte boundary, reconnect, stale link");
})().catch(error=>{console.error(error);process.exitCode=1});
