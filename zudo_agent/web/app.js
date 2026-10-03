"use strict";
let snapshot = null;
let failed = false;
let pending = false;
let consoleEnabled = false;
const $ = (id) => document.getElementById(id);
const labels = {working: "Working", "needs-attention": "Needs attention", idle: "Turn stopped", unknown: "Unknown", "error-observed": "Error observed", ended: "Session ended", completed: "Task completed"};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function age(at) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - at));
  return seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`;
}
function isStale(run) { return failed || run.freshness === "stale" || run.state_freshness === "stale"; }
function isPrevious(run) { return run.reachability === "absent" || ["ended", "completed"].includes(run.state); }
function render() {
  if (!snapshot) return;
  const sample = snapshot.mode === "sample";
  const hub = snapshot.mode === "hub";
  $("mode").textContent = sample ? "SAMPLE DATA" : hub ? "SHARED OBSERVATIONS" : "LOCAL OBSERVATIONS";
  $("notice").className = `notice ${failed ? "warning" : sample ? "sample" : ""}`;
  $("notice").textContent = failed ? "Dashboard connection lost. Showing the last snapshot; every signal below may be stale." : sample ? "SAMPLE WORKSPACE — Synthetic projects and runs. No live telemetry is being collected." : hub ? "Registered devices only. Transport health and agent activity are separate; project completion remains unknown." : "Local observation only. Project completion remains unknown; agent questions without a lifecycle signal remain unknown.";
  if (!failed && snapshot.transport) $("notice").textContent += ` Hub forwarding: ${snapshot.transport.status}${snapshot.transport.pending ? " · retry pending" : ""}.`;
  document.querySelector('.cloud p').textContent = hub ? 'Local imports only' : 'Validated imports only';
  document.querySelector('.cloud small').textContent = hub ? 'Cloud imports stay on source devices. No live cloud connection.' : 'No live cloud connection. Imported states are dated observations.';
  const runs = snapshot.projects.flatMap((p) => p.runs).filter((run) => !isPrevious(run));
  $("project-count").textContent = snapshot.projects.length;
  $("run-count").textContent = runs.length;
  $("attention-count").textContent = runs.filter((r) => r.state === "needs-attention" && !isStale(r) && !["absent", "disconnected", "offline"].includes(r.reachability)).length;
  $("stale-count").textContent = runs.filter(isStale).length;
  const query = $("search").value.toLowerCase();
  const filter = $("filter").value;
  const list = $("projects");
  const expanded = new Set(Array.from(list.querySelectorAll('details[open]'), node => node.dataset.run));
  const expandedHistory = new Set(Array.from(list.querySelectorAll('.history[open]'), node => node.dataset.project));
  list.replaceChildren();
  const projects = snapshot.projects.filter((p) => `${p.id} ${p.repository}`.toLowerCase().includes(query) && (filter === "all" || p.runs.some((r) => filter === "stale" ? isStale(r) : r.state === filter)));
  for (const project of projects) {
    const card = el("article", undefined, "project");
    const head = el("div", undefined, "project-head");
    const title = el("div"); title.append(el("h3", project.id), el("p", project.repository, "repo"));
    const current = project.runs.filter((run) => !isPrevious(run));
    const previous = project.runs.filter(isPrevious);
    head.append(title, el("span", `${current.length} current run${current.length === 1 ? "" : "s"}`, "run-count"));
    card.append(head);
    if (!project.runs.length) card.append(el("p", "No runs observed. Configure project roots, then start the collector or opt-in hooks.", "empty"));
    if (!current.length && previous.length) card.append(el("p", "No current runs. Previous observations are kept below.", "empty"));
    const history = el("details", undefined, "history");
    history.dataset.project = project.id;
    history.open = expandedHistory.has(project.id);
    history.append(el("summary", `Previous runs (${previous.length})`));
    for (const run of [...current, ...previous]) {
      const row = el("details", undefined, "run");
      row.dataset.run = run.id;
      row.open = expanded.has(run.id);
      const summary = el("summary");
      const who = el("div", undefined, "who");
      who.append(el("span", run.source === "claude" ? "C" : run.source === "codex" ? "◈" : "↗", "avatar"));
      const name = el("div"); name.append(el("strong", run.source === "cloud-import" ? "Cloud import" : run.source === "tmux" ? "Agent process" : run.source === "claude" ? "Claude Code" : "Codex"), el("small", `${run.machine} · ${run.id.slice(0, 8)}`)); who.append(name);
      const status = el("div", undefined, "run-status");
      const stateLabel = run.reachability === "absent" && !["ended", "completed"].includes(run.state) ? "No longer present" : labels[run.state] || "Unknown";
      status.append(el("span", stateLabel, `badge ${isPrevious(run) ? "previous" : run.state}`), el("small", `${isStale(run) ? "Stale · " : ""}${run.reachability} · ${age(run.state_at)}`));
      summary.append(who, status); row.append(summary);
      const detail = el("div", undefined, "run-detail");
      detail.append(el("p", `Evidence: ${run.evidence}. Last presence/observation ${age(run.last_seen)}; lifecycle state ${age(run.state_at)}. Project completion: unknown.`));
      const events = el("ol");
      if (hub) detail.append(el("p", "Compact forwarded snapshot; event history stays on the source device."));
      for (const event of run.recent_events.slice().reverse()) events.append(el("li", `${event.kind} · ${event.source} · ${age(event.at)}`));
      if (consoleEnabled && !sample && !hub && !isPrevious(run) && !isStale(run) && run.reachability === "present" && run.source !== "cloud-import") {
        const link = el("a", "View read-only pane snapshot", "pane-link");
        link.href = `/console.html#${new URLSearchParams({project: project.id, run: run.id})}`;
        detail.append(link);
      }
      detail.append(events); row.append(detail); (isPrevious(run) ? history : card).append(row);
    }
    if (previous.length) card.append(history);
    list.append(card);
  }
  if (!projects.length) list.append(el("p", snapshot.projects.length ? "No projects match these filters." : "No projects configured. Add an explicit repository identity and local roots to config.local.json.", "empty"));
  const connections = $("connections"); connections.replaceChildren();
  for (const c of snapshot.collectors) {
    const node = el("div", undefined, "connection");
    node.append(el("span", undefined, `dot ${["connected", "online"].includes(c.status) && !failed ? "" : "muted"}`), el("strong", c.machine), el("p", failed ? "Snapshot unavailable" : `${c.status}${c.collector_status ? " · collector " + c.collector_status : ""}`), el("small", `${c.panes} panes · ${c.unmatched_panes} unassigned / no agent`));
    if (hub) node.append(el("small", c.checked_at === null ? "No snapshot received" : `Last received ${age(c.checked_at)}`));
    if (c.omitted_runs) node.append(el("small", `${c.omitted_runs} older runs omitted by snapshot limit`));
    connections.append(node);
  }
  if (!snapshot.collectors.length) connections.append(el("p", "No collector observation yet.", "empty"));
  $("updated").textContent = `${sample ? "Sample generated" : "Snapshot"} ${age(snapshot.generated_at)}`;
}
async function refresh() {
  if (pending) return;
  pending = true;
  $("refresh").disabled = true;
  $("refresh").textContent = "Refreshing…";
  $("refresh-status").textContent = "Checking observations…";
  try {
    const response = await fetch("/api/snapshot", {cache: "no-store", signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error("Unavailable");
    const next = await response.json();
    if (next.schema_version !== 1 || !Array.isArray(next.projects)) throw new Error("Invalid snapshot");
    snapshot = next; failed = false; render();
    $("refresh-status").textContent = `Updated ${new Date().toLocaleTimeString()}. Auto-refresh every 5s.`;
  } catch {
    failed = true;
    $("refresh-status").textContent = "Refresh failed. Last observations may be stale.";
    if (snapshot) render();
    else { $("notice").textContent = "Cannot reach the local dashboard. Start the server, then refresh."; $("notice").className = "notice warning"; }
  } finally { pending = false; $("refresh").disabled = false; $("refresh").textContent = "↻ Refresh"; }
}
$("search").addEventListener("input", render);
$("filter").addEventListener("change", render);
$("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 5000);

fetch("/api/console/status", {cache: "no-store", signal: AbortSignal.timeout(4000)})
  .then(response => response.ok ? response.json() : null)
  .then(info => { consoleEnabled = info?.enabled === true; if (consoleEnabled) { document.querySelector("footer span").textContent = "Optional read-only pane viewer · no agent controls"; render(); } })
  .catch(() => {});
