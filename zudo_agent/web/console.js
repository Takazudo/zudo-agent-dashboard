"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let targets = [], csrf = "", lease = null, deadline = 0, generation = 0, pending = false, controller = null;
  let allowed = false, control = false, sequence = 1, queued = "", composing = false, lastPoll = 0;
  const selection = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname);
  const keys = {enter: "\r", tab: "\t", escape: "\x1b", interrupt: "\x03", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D"};
  const keyboardKeys = {Enter: "\r", Tab: "\t", Escape: "\x1b", Backspace: "\x7f", Delete: "\x1b[3~", ArrowUp: keys.up, ArrowDown: keys.down, ArrowRight: keys.right, ArrowLeft: keys.left, Home: "\x1b[H", End: "\x1b[F", PageUp: "\x1b[5~", PageDown: "\x1b[6~"};
  function status(message) { $("console-status").textContent = message; }
  function target() { return targets.find(t => t.id === $("console-run").value && t.project === $("console-project").value); }
  function buttons() {
    $("console-connect").disabled = pending || !!lease || !target() || !csrf;
    $("console-refresh").disabled = pending || !lease;
    $("console-close").disabled = !lease && !pending;
    $("console-control").disabled = pending || !lease || !allowed;
    if (!pending) $("console-control").checked = control;
    for (const id of ["console-input", "console-keyboard", "console-send"]) $(id).disabled = !lease || !control;
    $("console-resize").disabled = pending || !!queued || !lease || !control;
    document.querySelectorAll("[data-key]").forEach(button => { button.disabled = !lease || !control; });
  }
  async function request(action, body, signal) {
    const response = await fetch(`/api/console/${action}`, {method: "POST", cache: "no-store", credentials: "same-origin", signal,
      headers: {"Content-Type": "application/json", "X-Console-CSRF": csrf}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error("Unavailable");
    return response.json();
  }
  function clear(message) {
    const old = lease;
    generation++; controller?.abort(); controller = null; lease = null; deadline = 0; pending = false;
    control = false; queued = ""; composing = false;
    for (const id of ["console-input", "console-keyboard"]) $(id).value = "";
    $("console-screen").textContent = ""; $("console-foreground").textContent = "Foreground: disconnected";
    $("console-expiry").textContent = "Disconnected. Reconnection is always manual.";
    $("console-delivery").textContent = "Input queue cleared. Nothing will be replayed on reconnect.";
    if (message) status(message);
    buttons();
    if (old) void request("close", {lease: old}, AbortSignal.timeout(4000)).catch(() => {});
  }
  function selected() {
    clear("Pane selected. Connect explicitly; foreground changes stay in this pane.");
    const t = target();
    $("console-target").textContent = t ? `Project: ${t.project} · Machine: ${t.machine} · Server: ${t.server.join("/")} · Pane: ${t.pane} · Identity: ${t.id}${t.run ? ` · Initial run: ${t.run}` : " · Shell / no observed agent"}` : "No allowed pane available. Reload to refresh pane discovery.";
    buttons();
  }
  function panes() {
    $("console-run").replaceChildren();
    for (const t of targets.filter(t => t.project === $("console-project").value)) $("console-run").add(new Option(`${t.pane} · ${t.foreground} · ${t.machine}`, t.id));
    $("console-run").disabled = !$("console-run").options.length;
    selected();
  }
  async function screen(epoch, signal) {
    const result = await request("screen", {lease}, signal);
    if (epoch !== generation || !lease) return;
    $("console-screen").textContent = result.screen;
    const t = target();
    t.foreground = result.foreground;
    $("console-run").selectedOptions[0].textContent = `${t.pane} · ${t.foreground} · ${t.machine}`;
    $("console-foreground").textContent = `Foreground: ${result.foreground} · ${result.cols} × ${result.rows} · Pane ${target().pane}`;
    status(`${control ? "Control enabled" : "Read-only"} · screen sampled ${new Date(result.sampled_at * 1000).toLocaleTimeString()}`);
  }
  async function operate(action, extra = {}) {
    if (pending || (action !== "open" && !lease)) return;
    const epoch = generation;
    pending = true; buttons();
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try {
      if (action === "open") {
        const t = target();
        const result = await request("open", {project: t.project, machine: t.machine, id: t.id}, active.signal);
        if (epoch !== generation) { void request("close", {lease: result.lease}, AbortSignal.timeout(4000)).catch(() => {}); return; }
        lease = result.lease; deadline = Date.now() + result.expires_in * 1000; sequence = result.sequence; control = false;
        $("console-expiry").textContent = `Lease expires in ${result.expires_in}s. Reconnect explicitly after expiry.`;
        await screen(epoch, active.signal);
      } else if (action === "screen") await screen(epoch, active.signal);
      else {
        const body = {lease, ...extra};
        if (action === "send" || action === "resize") body.sequence = sequence;
        const result = await request(action, body, active.signal);
        if (epoch !== generation) return;
        if (action === "control") {
          control = result.control;
          $("console-delivery").textContent = control ? "Control enabled for this pane, including its shell." : "Read-only. Input cleared.";
        } else {
          sequence = result.sequence;
          $("console-delivery").textContent = "Sent to pane. This acknowledges delivery, not command success.";
        }
      }
    } catch {
      if (epoch === generation) clear("Disconnected or target changed. Delivery may be uncertain; inspect before sending again. No input will be replayed.");
    } finally {
      clearTimeout(timeout);
      if (epoch === generation) { pending = false; buttons(); pump(); }
    }
  }
  function pump() {
    if (pending || !lease || !control || !queued) return;
    const text = queued; queued = "";
    void operate("send", {text});
  }
  function enqueue(text) {
    if (!lease || !control || !text) return;
    if (new TextEncoder().encode(queued + text).length > 4096) { clear("Input buffer limit reached. Unsent input discarded; reconnect manually."); return; }
    queued += text; pump();
  }
  $("console-project").addEventListener("change", panes);
  $("console-run").addEventListener("change", selected);
  $("console-connect").addEventListener("click", () => operate("open"));
  $("console-refresh").addEventListener("click", () => operate("screen"));
  $("console-close").addEventListener("click", () => clear("Closed. Screen and unsent input cleared."));
  $("console-control").addEventListener("change", () => {
    const enabled = $("console-control").checked;
    queued = "";
    if (!enabled) { control = false; $("console-input").value = ""; $("console-keyboard").value = ""; }
    void operate("control", {enabled});
  });
  $("console-send").addEventListener("click", () => {
    const text = $("console-input").value + ($("console-enter").checked ? "\r" : "");
    $("console-input").value = ""; enqueue(text);
  });
  document.querySelectorAll("[data-key]").forEach(button => button.addEventListener("click", () => enqueue(keys[button.dataset.key])));
  $("console-resize").addEventListener("click", () => operate("resize", {cols: Number($("console-cols").value), rows: Number($("console-rows").value)}));
  const keyboard = $("console-keyboard");
  keyboard.addEventListener("compositionstart", () => { composing = true; });
  keyboard.addEventListener("compositionend", () => { composing = false; const value = keyboard.value; keyboard.value = ""; enqueue(value); });
  keyboard.addEventListener("input", () => { if (!composing) { const value = keyboard.value; keyboard.value = ""; enqueue(value); } });
  keyboard.addEventListener("beforeinput", event => {
    if (composing || event.isComposing) return;
    if (event.inputType === "deleteContentBackward" || event.inputType === "insertLineBreak") { event.preventDefault(); enqueue(event.inputType === "deleteContentBackward" ? "\x7f" : "\r"); }
  });
  keyboard.addEventListener("keydown", event => {
    if (event.isComposing || composing || event.metaKey) return;
    let text = keyboardKeys[event.key];
    if (event.ctrlKey && /^[a-zA-Z@[\\\]^_]$/.test(event.key)) text = String.fromCharCode(event.key.toUpperCase().charCodeAt(0) & 31);
    if (text) { event.preventDefault(); enqueue(text); }
  });
  window.addEventListener("pagehide", () => clear());
  window.addEventListener("offline", () => clear("Offline. Delivery may be uncertain. Input cleared; reconnect manually."));
  setInterval(() => {
    if (!lease) return;
    const seconds = Math.ceil((deadline - Date.now()) / 1000);
    if (seconds <= 0) { clear("Lease expired. Screen and unsent input cleared; reconnect manually."); return; }
    $("console-expiry").textContent = `Lease expires in ${seconds}s. Refresh does not extend it.`;
    if (!pending && !queued && Date.now() - lastPoll >= 500) { lastPoll = Date.now(); void operate("screen"); }
  }, 100);
  (async () => {
    try {
      const auth = await fetch("/api/console/bootstrap", {cache: "no-store", signal: AbortSignal.timeout(5000)});
      if (!auth.ok) throw new Error();
      const info = await auth.json(); csrf = info.csrf; allowed = info.allow_input === true;
      $("operator").textContent = `Authenticated operator: ${info.identity} · ${allowed ? "Control permitted by policy; each connection starts read-only" : "Server policy: read-only"}`;
      const result = await request("targets", {}, AbortSignal.timeout(5000)); targets = result.targets;
      for (const project of new Set(targets.map(t => t.project))) $("console-project").add(new Option(project, project));
      $("console-project").disabled = !targets.length;
      if (targets.some(t => t.project === selection.get("project"))) $("console-project").value = selection.get("project");
      panes();
      const initial = targets.find(t => t.project === selection.get("project") && t.run === selection.get("run"));
      if (initial) $("console-run").value = initial.id;
      const unavailable = selection.has("run") && !initial;
      if (unavailable) {
        const placeholder = new Option("Select a pane explicitly", "", true, true);
        placeholder.disabled = true;
        $("console-run").add(placeholder, 0);
        $("console-run").value = "";
      }
      selected();
      if (unavailable) status("The requested run is no longer present. Select an existing pane explicitly; no replacement was selected.");
    } catch { clear("Pane console unavailable. Enablement requires a separately approved local policy."); }
  })();
})();
