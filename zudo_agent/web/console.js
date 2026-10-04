"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let targets = [], csrf = "", lease = null, deadline = 0, generation = 0, pending = false, controller = null, mutationBusy = false, deferred = null;
  let allowed = false, control = false, sequence = 1, queued = "", composing = false, lastPoll = 0;
  let mode = "compose", collapsed = false, discardComposition = false, latestFrame = null, following = true;
  let preferredHeight = 230, drag = null, restoreFocus = null, layoutFrame = 0;
  const viewport = $("console-screen"), dock = $("console-dock"), divider = $("console-divider");
  const selection = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname);
  const keys = {enter: "\r", tab: "\t", escape: "\x1b", interrupt: "\x03", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D"};
  const keyboardKeys = {Enter: "\r", Tab: "\t", Escape: "\x1b", Backspace: "\x7f", Delete: "\x1b[3~", ArrowUp: keys.up, ArrowDown: keys.down, ArrowRight: keys.right, ArrowLeft: keys.left, Home: "\x1b[H", End: "\x1b[F", PageUp: "\x1b[5~", PageDown: "\x1b[6~"};
  function text(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
  function status(message) { text("console-status", message); }
  function cancelComposition() { if (composing) discardComposition = true; composing = false; $("console-keyboard").value = ""; }
  function directActive() { return !!lease && control && mode === "direct" && !collapsed; }
  function target() { return targets.find(t => t.id === $("console-run").value && t.project === $("console-project").value); }
  function buttons() {
    $("console-connect").disabled = mutationBusy || !!lease || !target() || !csrf;
    $("console-refresh").disabled = mutationBusy || !lease;
    $("console-close").disabled = !lease && !pending;
    $("console-control").disabled = mutationBusy || !lease || !allowed;
    if (!mutationBusy) $("console-control").checked = control;
    for (const id of ["console-input", "console-keyboard", "console-send"]) $(id).disabled = !lease || !control;
    $("console-resize").disabled = mutationBusy || !!queued || !lease || !control;
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
    generation++; controller?.abort(); controller = null; lease = null; deadline = 0; pending = false; mutationBusy = false; deferred = null;
    control = false; queued = ""; cancelComposition(); latestFrame = null; following = true; restoreFocus = null;
    for (const id of ["console-input", "console-keyboard"]) $(id).value = "";
    $("console-screen").textContent = ""; text("console-sampled", ""); viewState(); $("console-foreground").textContent = "Foreground: disconnected";
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
  function selectedText() {
    const selection = window.getSelection();
    return selection && !selection.isCollapsed && (viewport.contains(selection.anchorNode) || viewport.contains(selection.focusNode));
  }
  function viewState() {
    text("console-view-state", latestFrame !== null ? "Selection held · Latest resumes updates" : following ? "Visible pane snapshot · no scrollback" : "Reading snapshot · follow paused");
    text("console-latest", following && latestFrame === null ? "Following" : "Latest");
  }
  function renderFrame(value) {
    if (value === viewport.textContent) { latestFrame = null; viewState(); return; }
    // At most one bounded snapshot is held, never a history of captures.
    if (selectedText()) { latestFrame = value; viewState(); return; }
    const top = viewport.scrollTop, left = viewport.scrollLeft;
    latestFrame = null;
    viewport.textContent = value;
    screenHeight = viewport.scrollHeight; screenClientHeight = viewport.clientHeight;
    viewport.scrollTop = following ? viewport.scrollHeight : top;
    viewport.scrollLeft = left;
    viewState();
  }
  async function screen(epoch, signal) {
    const result = await request("screen", {lease}, signal);
    if (epoch !== generation || !lease) return;
    renderFrame(result.screen);
    const t = target();
    t.foreground = result.foreground;
    const label = `${t.pane} · ${t.foreground} · ${t.machine}`;
    if ($("console-run").selectedOptions[0].textContent !== label) $("console-run").selectedOptions[0].textContent = label;
    text("console-foreground", `Foreground: ${result.foreground} · ${result.cols} × ${result.rows} · Pane ${t.pane}`);
    text("console-sampled", `Sampled ${new Date(result.sampled_at * 1000).toLocaleTimeString()}`);
  }
  async function operate(action, extra = {}) {
    if (action !== "open" && !lease) return;
    if (pending) {
      // Human actions during a poll/send are accepted once, in this generation.
      // Control-off clears local input immediately in its change handler.
      if ((action === "control" || action === "resize") && !deferred) {
        deferred = {action, extra, epoch: generation}; mutationBusy = true; buttons();
      }
      return;
    }
    const epoch = generation;
    pending = true;
    mutationBusy = action === "open" || action === "control" || action === "resize";
    if (action !== "screen") buttons();
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try {
      if (action === "open") {
        const t = target();
        const result = await request("open", {project: t.project, machine: t.machine, id: t.id}, active.signal);
        if (epoch !== generation) { void request("close", {lease: result.lease}, AbortSignal.timeout(4000)).catch(() => {}); return; }
        lease = result.lease; deadline = Date.now() + result.expires_in * 1000; sequence = result.sequence; control = false;
        text("console-expiry", `Lease expires in ${result.expires_in}s. Reconnect explicitly after expiry.`);
        status("Connected · Read-only. Enable control to send to the selected pane.");
        await screen(epoch, active.signal);
      } else if (action === "screen") await screen(epoch, active.signal);
      else {
        const body = {lease, ...extra};
        if (action === "send" || action === "resize") body.sequence = sequence;
        const result = await request(action, body, active.signal);
        if (epoch !== generation) return;
        if (action === "control") {
          control = result.control;
          text("console-delivery", control ? "Control enabled for this pane, including its shell." : "Read-only. Input cleared.");
          status(control ? "Control enabled · Input goes to the selected pane, including its shell." : "Connected · Read-only.");
        } else {
          sequence = result.sequence;
          text("console-delivery", "Sent to pane. This acknowledges delivery, not command success.");
        }
      }
    } catch {
      if (epoch === generation) clear("Disconnected or target changed. Delivery may be uncertain; inspect before sending again. No input will be replayed.");
    } finally {
      clearTimeout(timeout);
      if (epoch === generation) {
        pending = false; controller = null;
        const next = deferred; deferred = null; mutationBusy = false;
        if (next && next.epoch === generation) void operate(next.action, next.extra);
        else { if (action !== "screen") buttons(); pump(); }
      }
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
    if (!enabled) { control = false; $("console-input").value = ""; cancelComposition(); buttons(); }
    void operate("control", {enabled});
  });
  $("console-send").addEventListener("click", () => {
    const text = $("console-input").value + ($("console-enter").checked ? "\r" : "");
    $("console-input").value = ""; enqueue(text);
  });
  document.querySelectorAll("[data-key]").forEach(button => button.addEventListener("click", () => enqueue(keys[button.dataset.key])));
  $("console-resize").addEventListener("click", () => operate("resize", {cols: Number($("console-cols").value), rows: Number($("console-rows").value)}));
  const keyboard = $("console-keyboard");
  // A new paste or tap is explicit fresh intent after a cancelled IME session.
  // Merely restoring focus must not release late composition/input events.
  keyboard.addEventListener("paste", () => { if (directActive() && !composing) discardComposition = false; });
  keyboard.addEventListener("pointerdown", () => { if (directActive() && !composing) discardComposition = false; });
  keyboard.addEventListener("compositionstart", () => {
    if (!directActive()) { cancelComposition(); return; }
    composing = true; discardComposition = false;
  });
  keyboard.addEventListener("compositionend", () => {
    const valid = composing && directActive() && !discardComposition;
    composing = false; const value = keyboard.value; keyboard.value = "";
    if (valid) enqueue(value);
  });
  keyboard.addEventListener("input", event => {
    if (composing || event.isComposing) return;
    const value = keyboard.value; keyboard.value = "";
    if (directActive() && !discardComposition && event.inputType !== "insertCompositionText") enqueue(value);
  });
  keyboard.addEventListener("beforeinput", event => {
    if (!directActive() || composing || event.isComposing || discardComposition) return;
    if (event.inputType === "deleteContentBackward" || event.inputType === "insertLineBreak") { event.preventDefault(); enqueue(event.inputType === "deleteContentBackward" ? "\x7f" : "\r"); }
  });
  keyboard.addEventListener("keydown", event => {
    if (!directActive() || event.isComposing || composing || event.metaKey) return;
    if (event.isTrusted) discardComposition = false;
    let value = keyboardKeys[event.key];
    if (event.ctrlKey && /^[a-zA-Z@[\\\]^_]$/.test(event.key)) value = String.fromCharCode(event.key.toUpperCase().charCodeAt(0) & 31);
    if (value) { event.preventDefault(); enqueue(value); }
  });
  keyboard.addEventListener("blur", () => { if (composing) cancelComposition(); });
  // Capture all outside pointer transitions, including a checkbox label's
  // forwarded click, before focus loss can commit a direct IME candidate.
  document.addEventListener("pointerdown", event => {
    if (composing && event.target !== keyboard) cancelComposition();
  }, true);
  // Remember input focus before a panel action takes it.
  for (const button of document.querySelectorAll("[data-mode], #console-toggle, #console-info, #console-project, #console-run, #console-close, #console-control")) {
    button.addEventListener("pointerdown", () => {
      if (!collapsed && dock.contains(document.activeElement)) restoreFocus = document.activeElement;
    });
  }
  function setMode(next) {
    if (mode === next) return;
    cancelComposition(); mode = next;
    for (const button of document.querySelectorAll("[data-mode]")) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    $("console-input").hidden = mode !== "compose"; keyboard.hidden = mode !== "direct";
    $("console-send").hidden = mode !== "compose"; $("console-enter-label").hidden = mode !== "compose";
    text("console-mode-hint", mode === "compose" ? "Send submits the draft. Key buttons send immediately, including to the shell." : "Typing and keys send immediately. IME waits for commit; hiding cancels unfinished input.");
    const input = mode === "compose" ? $("console-input") : keyboard;
    restoreFocus = input;
    if (!collapsed && !input.disabled) input.focus({preventScroll: true});
    fit();
  }
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
  let screenHeight = viewport.scrollHeight, screenClientHeight = viewport.clientHeight;
  viewport.addEventListener("scroll", () => {
    if (screenHeight !== viewport.scrollHeight || screenClientHeight !== viewport.clientHeight) {
      screenHeight = viewport.scrollHeight; screenClientHeight = viewport.clientHeight;
      if (following) viewport.scrollTop = viewport.scrollHeight;
      return;
    }
    following = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 8;
    viewState();
  });
  $("console-latest").addEventListener("click", () => {
    if (selectedText()) window.getSelection().removeAllRanges();
    following = true;
    if (latestFrame !== null) renderFrame(latestFrame);
    viewport.scrollTop = viewport.scrollHeight; viewState();
  });
  $("console-info").addEventListener("click", () => { cancelComposition(); $("console-dialog").showModal(); });
  $("console-info-close").addEventListener("click", () => $("console-dialog").close());
  function limits() {
    const short = window.innerHeight < 600, layout = document.querySelector(".console-layout");
    const chrome = document.querySelector(".console-terminal").clientHeight - viewport.clientHeight;
    const max = Math.max(80, layout.clientHeight - divider.offsetHeight - chrome - (short ? 70 : 150));
    const style = getComputedStyle(dock);
    const fixed = [...dock.children].filter(el => !el.classList.contains("console-entry"));
    const need = fixed.reduce((sum, el) => sum + el.getBoundingClientRect().height, 0) + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + parseFloat(style.gap) * 3 + 46;
    return {min: Math.min(need, max), max};
  }
  function applyHeight(value, remember = false) {
    if (collapsed) return;
    const bounds = limits();
    const height = Math.round(Math.max(bounds.min, Math.min(bounds.max, value)));
    if (remember) preferredHeight = height;
    dock.style.setProperty("--dock-height", height + "px");
    screenHeight = viewport.scrollHeight; screenClientHeight = viewport.clientHeight;
    for (const [key, value] of Object.entries({min: Math.round(bounds.min), max: Math.round(bounds.max), now: height, text: height + " pixels high"})) divider.setAttribute("aria-value" + key, String(value));
    if (following) viewport.scrollTop = viewport.scrollHeight;
  }
  function fit() { cancelAnimationFrame(layoutFrame); layoutFrame = requestAnimationFrame(() => applyHeight(preferredHeight)); }
  function collapse(next) {
    if (next) {
      if (dock.contains(document.activeElement)) restoreFocus = document.activeElement;
      cancelComposition(); drag = null; document.body.classList.remove("console-dragging");
    }
    collapsed = next; dock.hidden = next; divider.hidden = next;
    $("console-toggle").setAttribute("aria-expanded", String(!next));
    $("console-toggle").querySelector("span").textContent = next ? "Show input" : "Hide input";
    if (!next) {
      applyHeight(preferredHeight);
      if (restoreFocus && !restoreFocus.disabled && !restoreFocus.hidden) restoreFocus.focus({preventScroll: true});
    } else $("console-toggle").focus({preventScroll: true});
  }
  $("console-toggle").addEventListener("click", () => collapse(!collapsed));
  divider.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); drag = {id: event.pointerId, y: event.clientY, height: dock.getBoundingClientRect().height};
    divider.setPointerCapture(event.pointerId); document.body.classList.add("console-dragging");
  });
  divider.addEventListener("pointermove", event => { if (drag && drag.id === event.pointerId) applyHeight(drag.height + drag.y - event.clientY, true); });
  function endDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null; document.body.classList.remove("console-dragging");
    if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
  }
  divider.addEventListener("pointerup", endDrag); divider.addEventListener("pointercancel", endDrag);
  divider.addEventListener("lostpointercapture", () => { drag = null; document.body.classList.remove("console-dragging"); });
  divider.addEventListener("keydown", event => {
    let value = dock.getBoundingClientRect().height, step = event.shiftKey ? 32 : 16;
    if (event.key === "ArrowUp") value += step;
    else if (event.key === "ArrowDown") value -= step;
    else if (event.key === "Home") value = limits().min;
    else if (event.key === "End") value = limits().max;
    else return;
    event.preventDefault(); applyHeight(value, true);
  });
  new ResizeObserver(fit).observe(document.querySelector(".console-layout"));
  function fitViewport() {
    if (window.visualViewport) document.querySelector(".console-app").style.height = visualViewport.height + "px";
    fit();
  }
  window.addEventListener("resize", fitViewport); window.visualViewport?.addEventListener("resize", fitViewport); fitViewport();
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
