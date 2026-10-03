"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let projects = [], csrf = "", lease = null, deadline = 0, generation = 0, pending = false, controller = null;
  const selection = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname);
  function status(message) { $("console-status").textContent = message; }
  function target() {
    const project = projects.find(p => p.id === $("console-project").value);
    const run = project?.runs.find(r => r.id === $("console-run").value);
    return run ? {project: project.id, run: run.id, machine: run.machine} : null;
  }
  function buttons() {
    $("console-connect").disabled = pending || !!lease || !target() || !csrf;
    $("console-refresh").disabled = pending || !lease;
    $("console-close").disabled = !lease && !pending;
  }
  async function request(action, body, signal) {
    const response = await fetch(`/api/console/${action}`, {
      method: "POST", cache: "no-store", credentials: "same-origin", signal,
      headers: {"Content-Type": "application/json", "X-Console-CSRF": csrf}, body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error("The target is unavailable, changed, or expired. Reconnect manually.");
    return response.json();
  }
  function clear(message) {
    const old = lease;
    generation++;
    controller?.abort(); controller = null; lease = null; deadline = 0; pending = false;
    $("console-screen").textContent = "";
    $("console-input").value = "";
    $("console-expiry").textContent = "Disconnected. Reconnection is always manual.";
    if (message) status(message);
    buttons();
    // Best effort release only. Never retry a request or replay input.
    if (old) void request("close", {lease: old}, AbortSignal.timeout(4000)).catch(() => {});
  }
  function selected() {
    clear("Target selected. Connect explicitly to view its screen.");
    const value = target();
    $("console-target").textContent = value ? `Project: ${value.project} · Machine: ${value.machine} · Run: ${value.run}` : "No current local run is available.";
    buttons();
  }
  function runs() {
    $("console-run").replaceChildren();
    const project = projects.find(p => p.id === $("console-project").value);
    for (const run of project?.runs || []) $("console-run").add(new Option(`${run.source} · ${run.machine} · ${run.id}`, run.id));
    $("console-run").disabled = !project?.runs.length;
    selected();
  }
  async function screen(epoch) {
    const result = await request("screen", {lease}, controller.signal);
    if (epoch !== generation || !lease) return;
    $("console-screen").textContent = result.screen;
    status(`Read-only snapshot · ${new Date(result.sampled_at * 1000).toLocaleTimeString()}`);
  }
  async function operate(connect) {
    if (pending || (!connect && !lease)) return;
    const epoch = generation;
    pending = true; buttons();
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 10000);
    try {
      if (connect) {
        const result = await request("open", target(), controller.signal);
        if (epoch !== generation) return;
        lease = result.lease; deadline = Date.now() + result.expires_in * 1000;
        $("console-expiry").textContent = `Read-only lease expires in ${result.expires_in}s. Refresh does not extend it.`;
      }
      await screen(epoch);
    } catch {
      if (epoch === generation) clear("Disconnected or target changed. Screen cleared; reconnect manually. No input was sent.");
    } finally {
      clearTimeout(timeout);
      if (epoch === generation) { pending = false; buttons(); }
    }
  }
  $("console-project").addEventListener("change", runs);
  $("console-run").addEventListener("change", selected);
  $("console-connect").addEventListener("click", () => operate(true));
  $("console-refresh").addEventListener("click", () => operate(false));
  $("console-close").addEventListener("click", () => clear("Closed. Screen cleared."));
  window.addEventListener("pagehide", () => clear());
  window.addEventListener("offline", () => clear("Offline. Screen cleared; reconnect manually."));
  setInterval(() => {
    if (!lease) return;
    const seconds = Math.ceil((deadline - Date.now()) / 1000);
    if (seconds <= 0) clear("Lease expired. Screen cleared; reconnect manually.");
    else $("console-expiry").textContent = `Read-only lease expires in ${seconds}s. Refresh does not extend it.`;
  }, 500);
  (async () => {
    try {
      const auth = await fetch("/api/console/bootstrap", {cache: "no-store", signal: AbortSignal.timeout(5000)});
      if (!auth.ok) throw new Error();
      const info = await auth.json(); csrf = info.csrf;
      $("operator").textContent = `Authenticated operator: ${info.identity} · Server-enforced read-only mode`;
      const response = await fetch("/api/snapshot", {cache: "no-store", signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error();
      const snapshot = await response.json();
      if (snapshot.mode !== "live") throw new Error();
      projects = snapshot.projects.map(p => ({...p, runs: p.runs.filter(r => r.reachability === "present" && r.freshness !== "stale" && !["ended", "completed"].includes(r.state) && r.source !== "cloud-import")}));
      for (const project of projects) $("console-project").add(new Option(project.id, project.id));
      $("console-project").disabled = !projects.length;
      if (projects.some(p => p.id === selection.get("project"))) $("console-project").value = selection.get("project");
      runs();
      if (Array.from($("console-run").options).some(o => o.value === selection.get("run"))) $("console-run").value = selection.get("run");
      selected();
    } catch { clear("Pane viewer unavailable. Enablement requires a separately approved local policy."); }
  })();
})();
