"use strict";
let snapshot = null;
let failed = false;
let pending = false;
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
function render() {
  if (!snapshot) return;
  const sample = snapshot.mode === "sample";
  $("mode").textContent = sample ? "SAMPLE DATA" : "LOCAL OBSERVATIONS";
  $("notice").className = `notice ${failed ? "warning" : sample ? "sample" : ""}`;
  $("notice").textContent = failed ? "Dashboard connection lost. Showing the last snapshot; every signal below may be stale." : sample ? "SAMPLE WORKSPACE — Synthetic projects and runs. No live telemetry is being collected." : "Local observation only. Project completion remains unknown; agent questions without a lifecycle signal remain unknown.";
  const runs = snapshot.projects.flatMap((p) => p.runs);
  $("project-count").textContent = snapshot.projects.length;
  $("run-count").textContent = runs.length;
  $("attention-count").textContent = runs.filter((r) => r.state === "needs-attention" && !isStale(r) && !["absent", "disconnected"].includes(r.reachability)).length;
  $("stale-count").textContent = runs.filter(isStale).length;
  const query = $("search").value.toLowerCase();
  const filter = $("filter").value;
  const list = $("projects");
  const expanded = new Set(Array.from(list.querySelectorAll('details[open]'), node => node.dataset.run));
  list.replaceChildren();
  const projects = snapshot.projects.filter((p) => `${p.id} ${p.repository}`.toLowerCase().includes(query) && (filter === "all" || p.runs.some((r) => filter === "stale" ? isStale(r) : r.state === filter)));
  for (const project of projects) {
    const card = el("article", undefined, "project");
    const head = el("div", undefined, "project-head");
    const title = el("div"); title.append(el("h3", project.id), el("p", project.repository, "repo"));
    head.append(title, el("span", `${project.runs.length} run${project.runs.length === 1 ? "" : "s"}`, "run-count"));
    card.append(head);
    if (!project.runs.length) card.append(el("p", "No runs observed. Configure project roots, then start the collector or opt-in hooks.", "empty"));
    for (const run of project.runs) {
      const row = el("details", undefined, "run");
      row.dataset.run = run.id;
      row.open = expanded.has(run.id);
      const summary = el("summary");
      const who = el("div", undefined, "who");
      who.append(el("span", run.source === "claude" ? "C" : run.source === "codex" ? "◈" : "↗", "avatar"));
      const name = el("div"); name.append(el("strong", run.source === "cloud-import" ? "Cloud import" : run.source === "tmux" ? "Agent process" : run.source === "claude" ? "Claude Code" : "Codex"), el("small", `${run.machine} · ${run.id.slice(0, 8)}`)); who.append(name);
      const status = el("div", undefined, "run-status");
      status.append(el("span", labels[run.state] || "Unknown", `badge ${run.state}`), el("small", `${isStale(run) ? "Stale · " : ""}${run.reachability} · ${age(run.state_at)}`));
      summary.append(who, status); row.append(summary);
      const detail = el("div", undefined, "run-detail");
      detail.append(el("p", `Evidence: ${run.evidence}. Last presence/observation ${age(run.last_seen)}; lifecycle state ${age(run.state_at)}. Project completion: unknown.`));
      const events = el("ol");
      for (const event of run.recent_events.slice().reverse()) events.append(el("li", `${event.kind} · ${event.source} · ${age(event.at)}`));
      detail.append(events); row.append(detail); card.append(row);
    }
    list.append(card);
  }
  if (!projects.length) list.append(el("p", snapshot.projects.length ? "No projects match these filters." : "No projects configured. Add an explicit repository identity and local roots to config.local.json.", "empty"));
  const connections = $("connections"); connections.replaceChildren();
  for (const c of snapshot.collectors) {
    const node = el("div", undefined, "connection");
    node.append(el("span", undefined, `dot ${c.status === "connected" && !failed ? "" : "muted"}`), el("strong", c.machine), el("p", failed ? "Snapshot unavailable" : c.status), el("small", `${c.panes} panes · ${c.unmatched_panes} unassigned / no agent`));
    connections.append(node);
  }
  if (!snapshot.collectors.length) connections.append(el("p", "No collector observation yet.", "empty"));
  $("updated").textContent = `${sample ? "Sample generated" : "Snapshot"} ${age(snapshot.generated_at)}`;
}
async function refresh() {
  if (pending) return;
  pending = true;
  try {
    const response = await fetch("/api/snapshot", {cache: "no-store", signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error("Unavailable");
    const next = await response.json();
    if (next.schema_version !== 1 || !Array.isArray(next.projects)) throw new Error("Invalid snapshot");
    snapshot = next; failed = false; render();
  } catch {
    failed = true;
    if (snapshot) render();
    else { $("notice").textContent = "Cannot reach the local dashboard. Start the server, then refresh."; $("notice").className = "notice warning"; }
  } finally { pending = false; }
}
$("search").addEventListener("input", render);
$("filter").addEventListener("change", render);
$("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 5000);
