"""Synthetic demonstration, never merged with live observations."""

import time

from .model import digest


def sample_snapshot():
    now = time.time()
    projects = []
    for name, specs in [
        ("orbit-web", [("claude", "working", "present", 8), ("codex", "needs-attention", "present", 22)]),
        ("atlas-api", [("codex", "idle", "present", 46)]),
        ("design-system", [("claude", "unknown", "disconnected", 420)]),
        ("docs-site", [("cloud-import", "completed", "not-observed", 1800)]),
    ]:
        runs = []
        for i, (source, state, reachability, age) in enumerate(specs):
            runs.append(dict(id=digest("sample", name, i), machine="sample-laptop" if source != "cloud-import" else "sample-cloud",
                source=source, state=state, state_at=now-age, last_seen=now-age, freshness="stale" if age > 120 else "fresh",
                state_freshness="stale" if age > 120 else "fresh", reachability=reachability,
                evidence="imported" if source == "cloud-import" else "lifecycle",
                recent_events=[dict(kind="turn-stop" if state == "idle" else state, source=source, at=now-age)]))
        projects.append(dict(id=name, repository=f"github.com/example/{name}", completion="unknown", runs=runs))
    return dict(schema_version=1, mode="sample", generated_at=now,
                cloud=dict(status="import-only", live_connected=False),
                collectors=[dict(machine="sample-laptop", status="connected", checked_at=now, panes=12, unmatched_panes=7)], projects=projects)
