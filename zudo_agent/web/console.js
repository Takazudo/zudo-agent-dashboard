"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const viewport = $("console-screen"), dock = $("console-dock"), divider = $("console-divider"), keyboard = $("console-keyboard");
  const selection = new URLSearchParams(location.hash.slice(1));
  const embedded = selection.get("embed") === "1" && parent !== window;
  history.replaceState(null, "", location.pathname);
  document.body.classList.toggle("embedded", embedded);
  $("console-back").hidden = !embedded;
  document.querySelector(".console-pickers").hidden = embedded;
  $("console-expand").hidden = embedded;
  let editRevision = 0;
  const editor = window.createComposerEditor($("editor-host"), {change: () => { editRevision++; }, composition: active => { if (!active) setTimeout(settleSelection, 0); }});
  const keys = {enter: "\r", tab: "\t", escape: "\x1b", interrupt: "\x03", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D"};
  const keyboardKeys = {Enter: "\r", Tab: "\t", Escape: "\x1b", Backspace: "\x7f", Delete: "\x1b[3~", ArrowUp: keys.up, ArrowDown: keys.down, ArrowRight: keys.right, ArrowLeft: keys.left, Home: "\x1b[H", End: "\x1b[F", PageUp: "\x1b[5~", PageDown: "\x1b[6~"};
  let targets = [], csrf = "", lease = null, deadline = 0, generation = 0, pending = false, controller = null, sampling = false, sampleController = null;
  let allowed = false, control = false, sequence = 1, queued = "", composing = false, discardComposition = false;
  let mode = "compose", collapsed = true, expanded = false, composerExpanded = false, restoreFocus = null;
  let current = null, pendingSelection = null, session = null, lastPoll = 0, controlIntent = false, selectionSerial = 0, selecting = null, revoking = null;
  let transportRequests = Promise.resolve();
  let frame = null, latestFrame = null, following = true, uncertain = false, held = false, screenHeight = 0, screenClientHeight = 0, scrollRevision = 0;
  let preferredHeight = 230, drag = null, layoutFrame = 0;
  const post = payload => { if (embedded) parent.postMessage(payload, location.origin); };
  const label = t => `${t.project} · ${t.machine} · ${t.pane}`;
  const text = (id, value) => { if ($(id).textContent !== value) $(id).textContent = value; };
  const status = message => text("console-status", message);
  const isComposing = () => composing || editor.composing;
  function cancelComposition() { if (composing) discardComposition = true; composing = false; keyboard.value = ""; }
  function directActive() { return !!lease && control && mode === "direct" && !collapsed; }
  function target() { return current; }
  function buttons() {
    $("console-connect").disabled = pending || selecting !== null || !!lease || !target() || !csrf;
    $("console-refresh").disabled = pending || !lease;
    $("console-close").disabled = !lease && !pending;
    $("console-toggle").disabled = !lease || !allowed || selecting !== null || pendingSelection !== null || ((pending || revoking?.lease === lease) && collapsed);
    $("console-send").disabled = !lease || !control || pending || mode !== "compose";
    keyboard.disabled = !directActive();
    editor.setEnabled(!!lease && control && !collapsed && mode === "compose");
    $("console-resize").disabled = pending || !!queued || !lease || !control;
    document.querySelectorAll("[data-key]").forEach(button => { button.disabled = !lease || !control || pending; });
    text("console-input-target", lease ? (control ? `Input active · ${label(target())}` : `Viewing · ${label(target())}`) : "Read-only");
    text("editor-target", target() ? `Draft · ${label(target())}` : "No pane selected");
  }
  function request(action, body, signal, mayDispatch = () => true) {
    // Keep bounded requests ordered; aborted work never becomes a later input replay.
    const next = transportRequests.then(() => {
      if (!mayDispatch()) {
        const error = new Error("Input canceled before dispatch"); error.canceledBeforeDispatch = true; throw error;
      }
      return transport(action, body, signal);
    });
    transportRequests = next.catch(() => {});
    return next;
  }
  async function transport(action, body, signal) {
    // A queued operation must not reach fetch after a read invalidates its lease.
    if (signal?.aborted) throw new DOMException("Operation canceled", "AbortError");
    const response = await fetch(`/api/console/${action}`, {method: "POST", cache: "no-store", credentials: "same-origin", signal,
      headers: {"Content-Type": "application/json", "X-Console-CSRF": csrf}, body: JSON.stringify(body)});
    if (!response.ok) { const error = new Error(`Console ${action}: ${response.status}`); error.status = response.status; throw error; }
    return response.json();
  }
  // Close is idempotent. A busy capture may briefly hold the backend lock; only close/control-off may retry.
  async function closeLease(old) {
    if (!old) return;
    for (let attempt = 0; attempt < 4; attempt++) {
      try { await request("close", {lease: old}, AbortSignal.timeout(4000)); return; }
      catch (error) { if (error.status !== 429) return; await new Promise(resolve => setTimeout(resolve, 150 * (attempt + 1))); }
    }
  }
  function reset(message, wipe = true, invalidateSelection = true) {
    const old = lease;
    if (invalidateSelection) { selectionSerial++; selecting = null; }
    generation++; controller?.abort(); controller = null; sampleController?.abort(); sampleController = null; sampling = false; lease = null; deadline = 0; pending = false;
    control = false; controlIntent = false; queued = ""; cancelComposition(); frame = null; latestFrame = null; following = true; uncertain = false; held = false;
    if (wipe) {
      editor.reset(); keyboard.value = ""; pendingSelection = null;
      // A disconnected pane can be reconnected manually; its drafts and Vim state are gone.
      if (current) { session = current.session_id; editor.selectPane(current.id); }
      else session = null;
    }
    viewport.textContent = ""; text("console-sampled", ""); text("console-foreground", "Foreground: disconnected"); viewState();
    text("console-expiry", "Disconnected. Reconnection is always manual.");
    text("console-delivery", "Nothing queued for replay. Delivery may be uncertain after an interrupted send.");
    if (message) status(message);
    collapse(true); buttons(); return closeLease(old);
  }
  function controlOff(old, epoch) {
    if (!old || !allowed) return Promise.resolve();
    if (revoking?.lease === old) return revoking.promise;
    const operation = {lease: old, promise: null};
    revoking = operation;
    operation.promise = (async () => {
      for (let attempt = 0; attempt < 5 && epoch === generation && lease === old; attempt++) {
        try { await request("control", {lease: old, enabled: false}, AbortSignal.timeout(4000)); return; }
        catch (error) {
          if (error.status !== 429) break;
          await new Promise(resolve => setTimeout(resolve, 150 * (attempt + 1)));
        }
      }
      if (epoch === generation && lease === old) reset("Control revocation could not be confirmed. Disconnected; reconnect manually.");
    })().finally(() => { if (revoking === operation) { revoking = null; buttons(); } });
    buttons();
    return operation.promise;
  }
  function revoke(preserveComposition = false) {
    control = false; controlIntent = false; queued = "";
    if (preserveComposition) discardComposition = true; else cancelComposition();
    buttons();
    const old = lease;
    if (old) void controlOff(old, generation);
  }
  function populate() {
    $("console-run").replaceChildren();
    for (const t of targets.filter(t => t.project === $("console-project").value)) $("console-run").add(new Option(`${t.pane} · ${t.foreground} · ${t.machine}`, t.id));
    $("console-run").disabled = !$("console-run").options.length;
  }
  function setPicker(t) {
    if (!t) return;
    $("console-project").value = t.project; populate(); $("console-run").value = t.id;
  }
  function acknowledge(id, requestId) { post({type: "round08-pane-selected", id, requestId}); }
  async function choose(id, requestId = null) {
    const choice = ++selectionSerial;
    selecting = choice;
    try {
    // A new target may never inherit control, queued bytes, or a late composition event.
    revoke(true);
    const t = targets.find(item => item.id === id);
    if (!t) { status("Requested pane is unavailable. Select an authorized pane explicitly."); acknowledge(null, requestId); return; }
    if (isComposing()) { pendingSelection = {id, requestId}; status("Waiting for text composition to finish before selecting pane."); return; }
    if (session && t.session_id !== session) {
      await reset("Session changed. Drafts cleared; connect explicitly.", true, false);
      if (choice !== selectionSerial) return;
    }
    pendingSelection = null;
    const same = current?.id === t.id;
    if (!same) {
      const old = lease;
      generation++; controller?.abort(); controller = null; sampleController?.abort(); sampleController = null; sampling = false; lease = null; pending = false; queued = ""; control = false;
      if (old) await closeLease(old);
      if (choice !== selectionSerial) return;
      current = t; session = t.session_id; setPicker(t);
      editor.selectPane(t.id); editor.setLabel(`Compose draft for ${label(t)}. Enter edits; Send submits.`);
      frame = null; latestFrame = null; uncertain = false; held = false; following = true; viewport.textContent = ""; viewState();
      text("console-target", `Project: ${t.project} · Machine: ${t.machine} · Session: ${t.session_id} · Pane: ${t.pane} · Identity: ${t.id}${t.run ? ` · Observed run: ${t.run}` : " · Shell / no observed agent"}`);
      status("Pane selected · connecting read-only."); buttons();
      await open(t, true);
    } else if (!lease && !pending) await open(t, true);
    acknowledge(choice === selectionSerial && current === t && lease ? t.id : null, requestId);
    } finally { if (selecting === choice) { selecting = null; buttons(); } }
  }
  function settleSelection() { if (pendingSelection && !isComposing()) { const next = pendingSelection; pendingSelection = null; void choose(next.id, next.requestId); } }
  function selectedText() {
    const selection = window.getSelection();
    return !!selection && !selection.isCollapsed && (viewport.contains(selection.anchorNode) || viewport.contains(selection.focusNode));
  }
  const lines = value => value.split("\n");
  function overlap(oldText, newText) {
    const a = lines(oldText), b = lines(newText), max = Math.min(a.length, b.length);
    let match = 0, matches = 0;
    for (let n = 1; n <= max; n++) {
      if (a.slice(-n).join("\n") === b.slice(0, n).join("\n")) { match = n; matches++; }
    }
    // Repeated or rewritten output gives no reliable anchor.
    return matches === 1 ? {retained: match, shifted: a.length - match} : null;
  }
  function viewState() {
    const cap = frame ? `Recent ${frame.lines}/${frame.limit} lines${frame.truncated || frame.byte_truncated ? " · capped" : ""}` : "Recent output · up to 500 lines";
    text("console-view-state", `${cap} · ${uncertain ? "Position uncertain; newer output available" : latestFrame ? "Selection held; newer output available" : following ? "Latest" : "Reading; follow paused"} · no deep history`);
    text("console-latest", following && !latestFrame ? "Following" : "Latest");
  }
  function renderFrame(result, force = false) {
    if (result.screen === frame?.screen && !force) { viewState(); return; }
    if (!force && selectedText()) held = true;
    if ((held || uncertain) && !force) { latestFrame = result; viewState(); return; }
    const old = frame, top = viewport.scrollTop, left = viewport.scrollLeft;
    let nextTop = top;
    if (old && !following && !force) {
      const retained = overlap(old.screen, result.screen);
      if (!retained) { latestFrame = result; uncertain = true; viewState(); return; }
      const lineHeight = parseFloat(getComputedStyle(viewport).lineHeight) || 22;
      if (top < retained.shifted * lineHeight) { latestFrame = result; uncertain = true; viewState(); return; }
      nextTop = top - retained.shifted * lineHeight;
    }
    frame = result; latestFrame = null; uncertain = false; held = false;
    viewport.textContent = result.screen;
    screenHeight = viewport.scrollHeight; screenClientHeight = viewport.clientHeight;
    viewport.scrollTop = following || force ? viewport.scrollHeight : nextTop;
    viewport.scrollLeft = left; viewState();
  }
  async function screen(epoch, signal, forTarget) {
    let result;
    try { result = await request("screen", {lease}, signal); }
    catch (error) {
      if (error.status !== 429) {
        // Invalidate before the transport queue can dispatch a waiting input operation.
        if (epoch === generation && target() === forTarget) void reset("Disconnected: capture unavailable or target changed. Reconnect manually; no input replay.");
        throw error;
      }
      if (epoch === generation && target() === forTarget) text("console-sampled", "Capture temporarily busy · waiting for the next sample");
      return;
    }
    if (epoch !== generation || !lease || target() !== forTarget) return;
    renderFrame(result);
    forTarget.foreground = result.foreground;
    const option = $("console-run").selectedOptions[0];
    if (option) option.textContent = `${forTarget.pane} · ${result.foreground} · ${forTarget.machine}`;
    text("console-foreground", `Foreground: ${result.foreground} · ${result.cols} × ${result.rows} · Pane ${forTarget.pane}`);
    text("console-sampled", `Sampled ${new Date(result.sampled_at * 1000).toLocaleTimeString()}`);
  }
  async function open(t = target(), switched = false) {
    if (!t || lease || pending || (selecting !== null && !switched)) return;
    const epoch = generation; pending = true; buttons();
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try {
      const result = await request("open", {project: t.project, machine: t.machine, id: t.id}, active.signal);
      if (epoch !== generation || t !== target()) { void closeLease(result.lease); return; }
      lease = result.lease; deadline = Date.now() + result.expires_in * 1000; sequence = result.sequence; control = false; controlIntent = false;
      text("console-expiry", `Lease expires in ${result.expires_in}s. Reconnect explicitly after expiry.`);
      status(switched ? "Pane selected · connected read-only. Terminal input enables this pane only." : "Connected read-only. Terminal input enables this pane only.");
      await screen(epoch, active.signal, t);
    } catch { if (epoch === generation) reset("Connection unavailable or target changed. Reconnect manually."); }
    finally { clearTimeout(timeout); if (epoch === generation) { pending = false; controller = null; buttons(); } }
  }
  async function sample() {
    if (!lease || sampling || pending || selecting !== null) return;
    const epoch = generation, t = target(), active = new AbortController();
    sampling = true; sampleController = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try { await screen(epoch, active.signal, t); }
    catch { if (epoch === generation) void reset("Capture unavailable. Reconnect manually; no input replay."); }
    finally {
      clearTimeout(timeout);
      if (epoch === generation) { sampling = false; sampleController = null; }
    }
  }
  async function operate(action, extra = {}) {
    if (action === "screen") return sample();
    if (!lease || pending || selecting !== null || (action === "control" && extra.enabled && revoking?.lease === lease)) return;
    const epoch = generation, t = target(), thisLease = lease;
    pending = true; buttons();
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try {
      const body = {lease: thisLease, ...extra};
      if (action === "send" || action === "resize") body.sequence = sequence;
      const result = await request(action, body, active.signal, () =>
        epoch === generation && thisLease === lease && t === target() && controlIntent &&
        (action === "control" || control));
      if (epoch !== generation || t !== target()) return;
      if (action === "control") {
        control = result.control && controlIntent;
        if (result.control && !controlIntent) void controlOff(thisLease, epoch);
        text("console-delivery", control ? `Input active for ${label(t)}` : "Read-only. Draft retained.");
        status(control ? `Input active · ${label(t)}` : "Connected read-only.");
      } else { sequence = result.sequence; text("console-delivery", "Sent to pane. This acknowledges delivery, not command success."); }
    } catch (error) {
      if (epoch === generation) {
        if (error.canceledBeforeDispatch) text("console-delivery", "Input canceled before dispatch. Nothing queued for replay.");
        else reset("Disconnected or target changed. Delivery may be uncertain; inspect before sending again. No input replay.");
      }
    }
    finally {
      clearTimeout(timeout);
      if (epoch === generation) { pending = false; controller = null; buttons(); pump(); }
    }
  }
  function pump() { if (pending || !lease || !control || !queued) return; const value = queued; queued = ""; void operate("send", {text: value}); }
  function enqueue(value) {
    if (!lease || !control || !value) return false;
    if (new TextEncoder().encode(queued + value).length > 4096) { text("console-delivery", "Input exceeds 4096 UTF-8 bytes. Nothing sent; shorten the draft."); return false; }
    queued += value; pump(); return true;
  }
  function limits() {
    const layout = document.querySelector(".console-layout");
    const chrome = document.querySelector(".console-terminal").clientHeight - viewport.clientHeight;
    const max = Math.max(110, layout.clientHeight - divider.offsetHeight - chrome - (innerHeight < 600 ? 65 : 135));
    const style = getComputedStyle(dock);
    const fixed = [...dock.children].filter(el => !el.classList.contains("console-entry"));
    const need = fixed.reduce((sum, el) => sum + el.getBoundingClientRect().height, 0) + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + 64;
    return {min: Math.min(need, max), max};
  }
  function applyHeight(value, remember = false) {
    if (collapsed || composerExpanded) return;
    const bounds = limits(), height = Math.round(Math.max(bounds.min, Math.min(bounds.max, value)));
    if (remember) preferredHeight = height;
    dock.style.setProperty("--dock-height", height + "px");
    for (const [key, item] of Object.entries({min: Math.round(bounds.min), max: Math.round(bounds.max), now: height, text: height + " pixels high"})) divider.setAttribute("aria-value" + key, String(item));
    editor.measure();
  }
  function fit() { cancelAnimationFrame(layoutFrame); layoutFrame = requestAnimationFrame(() => applyHeight(preferredHeight)); }
  function collapse(next) {
    if (next === collapsed) return;
    if (next) {
      if (dock.contains(document.activeElement)) restoreFocus = document.activeElement;
      revoke(); cancelComposition(); drag = null; if (composerExpanded) enlarge(false, true);
    }
    collapsed = next; dock.hidden = next; divider.hidden = next;
    $("console-toggle").setAttribute("aria-expanded", String(!next));
    $("console-toggle").querySelector("span").textContent = next ? "Terminal input" : "Close input";
    if (!next) { applyHeight(preferredHeight); editor.measure(); if (restoreFocus && !restoreFocus.disabled) restoreFocus.focus({preventScroll: true}); }
    else $("console-toggle").focus({preventScroll: true});
    buttons(); post({type: "round08-layout-state", expanded, inputOpen: !collapsed});
  }
  function enlarge(next, force = false) {
    if (next === composerExpanded || (!force && isComposing()) || (next && (collapsed || mode !== "compose"))) return;
    const snap = editor.snapshot(); composerExpanded = next;
    document.body.classList.toggle("composer-expanded", next);
    for (const el of [document.querySelector(".console-top"), document.querySelector(".console-terminal"), divider, document.querySelector(".console-targetbar"), document.querySelector(".console-foot"), $("console-status")]) el.inert = next;
    $("composer-expand").setAttribute("aria-pressed", String(next));
    text("composer-expand", next ? "Restore editor" : "Enlarge editor");
    if (!next) applyHeight(preferredHeight);
    editor.measure(); editor.restore(snap);
    $("composer-expand").focus({preventScroll: true});
  }
  function expandDetail(next) {
    if (isComposing()) return;
    expanded = next; document.body.classList.toggle("detail-expanded", next);
    $("console-expand").setAttribute("aria-pressed", String(next)); text("console-expand", next ? "Restore detail" : "Expand detail");
    fit(); editor.measure(); post({type: "round08-layout-state", expanded, inputOpen: !collapsed});
  }
  function ownsEscape(targetElement = document.activeElement) { return isComposing() || composerExpanded || (window.ThemeSettings.get().vim && $("editor-host").contains(targetElement)); }
  window.consoleLayout = {
    ownsEscape, isComposing,
    captureLayout: () => ({top: viewport.scrollTop, left: viewport.scrollLeft, following, scrollRevision, editor: editor.snapshot(), editRevision, expanded, composerExpanded, inputOpen: !collapsed, focus: document.activeElement === keyboard ? "direct" : editor.hasFocus() ? "editor" : null, inputEpoch: generation}),
    restoreLayout: state => { if (!state) return; if (state.expanded !== expanded) expandDetail(state.expanded); if (state.editRevision === editRevision && state.inputEpoch === generation && state.editor) editor.restore(state.editor); requestAnimationFrame(() => requestAnimationFrame(() => { if (state.inputEpoch !== generation || state.scrollRevision !== scrollRevision) return; following = state.following; viewport.scrollTop = following ? viewport.scrollHeight : state.top; viewport.scrollLeft = state.left; if (state.inputEpoch === generation && !isComposing() && !collapsed) { if (state.focus === "editor") editor.focus(); else if (state.focus === "direct" && !keyboard.disabled) keyboard.focus({preventScroll: true}); } viewState(); })); }
  };
  $("console-project").addEventListener("change", () => { if (embedded) return; populate(); void choose($("console-run").value); });
  $("console-run").addEventListener("change", () => { if (!embedded) void choose($("console-run").value); });
  $("console-connect").addEventListener("click", () => { void open(); });
  $("console-refresh").addEventListener("click", () => { void operate("screen"); });
  $("console-close").addEventListener("click", () => reset("Disconnected. Drafts cleared; reconnect manually."));
  $("console-toggle").addEventListener("click", () => { if (collapsed) { if (revoking?.lease === lease) return; controlIntent = true; collapse(false); void operate("control", {enabled: true}); } else collapse(true); });
  $("composer-close").addEventListener("click", () => collapse(true));
  $("composer-expand").addEventListener("pointerdown", event => { if (editor.hasFocus()) event.preventDefault(); });
  $("composer-expand").addEventListener("click", () => enlarge(!composerExpanded));
  $("console-expand").addEventListener("click", () => expandDetail(!expanded));
  $("console-back").addEventListener("click", () => { reset(); post({type: "round08-close"}); });
  dock.addEventListener("keydown", event => {
    if (!composerExpanded || event.key !== "Tab" || event.isComposing) return;
    const items = [...dock.querySelectorAll("button:not([disabled]):not([hidden]),[contenteditable=true],textarea:not([disabled]),input:not([disabled])")].filter(item => item.getClientRects().length);
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
  });
  $("console-send").addEventListener("click", () => {
    const value = editor.value + ($("console-enter").checked ? "\r" : "");
    if (enqueue(value)) editor.value = "";
  });
  document.querySelectorAll("[data-key]").forEach(button => button.addEventListener("click", () => { enqueue(keys[button.dataset.key]); }));
  $("console-resize").addEventListener("click", () => { void operate("resize", {cols: Number($("console-cols").value), rows: Number($("console-rows").value)}); });
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
    if (isComposing()) return;
    if (composerExpanded) enlarge(false, true);
    mode = button.dataset.mode;
    for (const item of document.querySelectorAll("[data-mode]")) item.setAttribute("aria-pressed", String(item.dataset.mode === mode));
    $("editor-host").hidden = mode !== "compose"; keyboard.hidden = mode !== "direct";
    $("console-send").hidden = mode !== "compose"; $("console-enter-label").hidden = mode !== "compose";
    $("composer-expand").hidden = mode !== "compose"; $("editor-mode").hidden = mode !== "compose";
    text("console-mode-hint", mode === "compose" ? "Enter edits the draft. Send submits it; key buttons act immediately." : "Typing and keys send immediately. IME waits for commit.");
    buttons(); editor.measure(); if (!collapsed && control) (mode === "compose" ? editor : keyboard).focus(); fit();
  }));
  keyboard.addEventListener("paste", () => { if (directActive() && !composing) discardComposition = false; });
  keyboard.addEventListener("pointerdown", () => { if (directActive() && !composing) discardComposition = false; });
  keyboard.addEventListener("compositionstart", () => { if (!directActive()) { cancelComposition(); return; } composing = true; discardComposition = false; });
  keyboard.addEventListener("compositionend", () => {
    const valid = composing && directActive() && !discardComposition && !pendingSelection;
    composing = false; const value = keyboard.value; keyboard.value = "";
    if (valid) enqueue(value); setTimeout(settleSelection, 0);
  });
  keyboard.addEventListener("input", event => {
    if (composing || event.isComposing) return;
    const value = keyboard.value; keyboard.value = "";
    if (directActive() && !discardComposition && !pendingSelection && event.inputType !== "insertCompositionText") enqueue(value);
  });
  keyboard.addEventListener("beforeinput", event => {
    if (!directActive() || composing || event.isComposing || discardComposition || pendingSelection) return;
    if (event.inputType === "deleteContentBackward" || event.inputType === "insertLineBreak") { event.preventDefault(); enqueue(event.inputType === "deleteContentBackward" ? "\x7f" : "\r"); }
  });
  keyboard.addEventListener("keydown", event => {
    if (!directActive() || event.isComposing || composing || event.metaKey || pendingSelection) return;
    if (event.isTrusted) discardComposition = false;
    let value = keyboardKeys[event.key];
    if (event.ctrlKey && /^[a-zA-Z@[\\\]^_]$/.test(event.key)) value = String.fromCharCode(event.key.toUpperCase().charCodeAt(0) & 31);
    if (value) { event.preventDefault(); enqueue(value); }
  });
  keyboard.addEventListener("blur", () => { if (composing && !pendingSelection) cancelComposition(); });
  viewport.addEventListener("scroll", () => {
    if (screenHeight !== viewport.scrollHeight || screenClientHeight !== viewport.clientHeight) { screenHeight = viewport.scrollHeight; screenClientHeight = viewport.clientHeight; return; }
    scrollRevision++; following = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 8; viewState();
  });
  $("console-latest").addEventListener("click", () => {
    if (selectedText()) window.getSelection().removeAllRanges();
    following = true; held = false; if (latestFrame) renderFrame(latestFrame, true);
    viewport.scrollTop = viewport.scrollHeight; viewState();
  });
  $("console-info").addEventListener("click", () => { if (!isComposing()) $("console-dialog").showModal(); });
  $("console-info-close").addEventListener("click", () => $("console-dialog").close());
  divider.addEventListener("pointerdown", event => { if (event.button !== 0) return; event.preventDefault(); drag = {id: event.pointerId, y: event.clientY, height: dock.getBoundingClientRect().height}; divider.setPointerCapture(event.pointerId); });
  divider.addEventListener("pointermove", event => { if (drag?.id === event.pointerId) applyHeight(drag.height + drag.y - event.clientY, true); });
  const endDrag = event => { if (drag?.id !== event.pointerId) return; drag = null; if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId); };
  divider.addEventListener("pointerup", endDrag); divider.addEventListener("pointercancel", endDrag); divider.addEventListener("lostpointercapture", () => { drag = null; });
  divider.addEventListener("keydown", event => {
    let value = dock.getBoundingClientRect().height, step = event.shiftKey ? 32 : 16;
    if (event.key === "ArrowUp") value += step; else if (event.key === "ArrowDown") value -= step;
    else if (event.key === "Home") value = limits().min; else if (event.key === "End") value = limits().max; else return;
    event.preventDefault(); applyHeight(value, true);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.keyCode === 229 || isComposing() || document.querySelector("dialog[open]")) return;
    if (ownsEscape(event.target)) { if (composerExpanded && !$("editor-host").contains(event.target)) { event.preventDefault(); enlarge(false); } return; }
    if (expanded) { event.preventDefault(); expandDetail(false); }
    else if (embedded) { event.preventDefault(); post({type: "round08-close"}); }
  });
  window.addEventListener("message", event => {
    if (!embedded || event.origin !== location.origin || event.source !== parent || event.data?.type !== "round08-select-pane") return;
    if (typeof event.data.id !== "string" || !["string", "number"].includes(typeof event.data.requestId)) return;
    void choose(event.data.id, event.data.requestId);
  });
  new ResizeObserver(fit).observe(document.querySelector(".console-layout"));
  function fitViewport() { if (window.visualViewport) document.querySelector(".console-app").style.height = visualViewport.height + "px"; fit(); }
  window.addEventListener("resize", fitViewport); window.visualViewport?.addEventListener("resize", fitViewport); fitViewport();
  window.addEventListener("pagehide", () => { reset(); editor.destroy(); });
  window.addEventListener("offline", () => reset("Offline. Delivery may be uncertain. Reconnect manually."));
  setInterval(() => {
    if (!lease) return;
    const seconds = Math.ceil((deadline - Date.now()) / 1000);
    if (seconds <= 0) { reset("Lease expired. Drafts cleared; reconnect manually."); return; }
    text("console-expiry", `Lease expires in ${seconds}s. Refresh does not extend it.`);
    if (!pending && !sampling && !queued && Date.now() - lastPoll >= 500) { lastPoll = Date.now(); void operate("screen"); }
  }, 100);
  (async () => {
    if (location.search) { status("Query selection is unsupported. Use a dashboard link."); return; }
    try {
      const auth = await fetch("/api/console/bootstrap", {cache: "no-store", signal: AbortSignal.timeout(5000)});
      if (!auth.ok) throw new Error("Authentication failed");
      const info = await auth.json(); csrf = info.csrf; allowed = info.allow_input === true;
      text("operator", `Authenticated operator: ${info.identity} · ${allowed ? "Input permitted by policy; each pane starts read-only" : "Server policy: read-only"}`);
      const result = await request("targets", {}, AbortSignal.timeout(5000)); targets = result.targets;
      for (const project of new Set(targets.map(t => t.project))) $("console-project").add(new Option(project, project));
      $("console-project").disabled = !targets.length;
      const requested = selection.get("id") || selection.get("run");
      let initial = null;
      if (selection.has("id")) initial = targets.find(t => t.id === requested && t.project === selection.get("project") && (!selection.has("session") || t.session_id === selection.get("session")));
      else if (selection.has("run")) initial = targets.find(t => t.run === requested && t.project === selection.get("project"));
      else initial = targets[0];
      if (initial) { setPicker(initial); current = initial; session = initial.session_id; editor.selectPane(initial.id); editor.setLabel(`Compose draft for ${label(initial)}. Enter edits; Send submits.`); text("console-target", `Project: ${initial.project} · Machine: ${initial.machine} · Session: ${initial.session_id} · Pane: ${initial.pane} · Identity: ${initial.id}`); status("Pane selected. Connect explicitly to read recent output."); }
      else { if (targets.length) { $("console-project").value = targets[0].project; populate(); $("console-run").value = ""; } status(requested ? "Requested pane or session is stale. Select a pane explicitly; no replacement connected." : "No allowed pane available."); }
      buttons(); post({type: "round08-ready"});
    } catch { reset("Pane console unavailable. Authentication or policy requires attention."); post({type: "round08-ready"}); }
  })();
})();
