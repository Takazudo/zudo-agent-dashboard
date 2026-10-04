"""SQLite gives hooks and the collector transactional, concurrent ingestion."""

import json
import os
import sqlite3
import time
from pathlib import Path

from .model import digest, validate_event

STATES = {"discovered": "unknown", "unknown": "unknown", "session-start": "unknown",
          "working": "working", "permission-request": "needs-attention", "turn-stop": "idle",
          "error-observed": "error-observed", "session-end": "ended", "interrupted": "unknown",
          "completed": "completed"}


class Store:
    def __init__(self, path, config):
        self.config = config
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass  # Another local hook/collector initialized the same DB.
        self.db = sqlite3.connect(path, timeout=2)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
          CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, project TEXT, run TEXT, machine TEXT,
            source TEXT, kind TEXT, at REAL);
          CREATE INDEX IF NOT EXISTS events_run ON events(run, at);
          CREATE TABLE IF NOT EXISTS presence (
            run TEXT PRIMARY KEY, machine TEXT, seen REAL, present INTEGER);
          CREATE TABLE IF NOT EXISTS collectors (
            machine TEXT PRIMARY KEY, checked REAL, connected INTEGER, panes INTEGER,
            unmatched INTEGER);
          CREATE TABLE IF NOT EXISTS workflow (
            id TEXT PRIMARY KEY, lane TEXT NOT NULL, revision INTEGER NOT NULL,
            edited REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS workflow_alias (
            alias TEXT PRIMARY KEY, canonical TEXT NOT NULL);
            """)
        except sqlite3.Error:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def workflow_get(self, key):
        row = self.db.execute("SELECT lane,revision FROM workflow WHERE id=?", (key,)).fetchone()
        return dict(lane=row[0], revision=row[1]) if row else dict(lane="inbox", revision=0)

    def workflow_canonical(self, key):
        row = self.db.execute("SELECT canonical FROM workflow_alias WHERE alias=?", (key,)).fetchone()
        return row[0] if row else key

    def workflow_move(self, key, lane, revision):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # A revision belongs to one identity. Never redirect an old run edit
            # into a session, including when discovery races the HTTP checks.
            if self.db.execute("SELECT 1 FROM workflow_alias WHERE alias=?", (key,)).fetchone():
                return None, {"error": "Workflow identity changed; refresh before moving"}
            current = self.workflow_get(key)
            if current["revision"] != revision:
                return None, current
            result = dict(lane=lane, revision=revision + 1)
            latest = self.db.execute("SELECT MAX(edited) FROM workflow").fetchone()[0]
            edited = max(time.time(), latest + 0.000001) if latest is not None else time.time()
            self.db.execute("INSERT INTO workflow VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET lane=excluded.lane,revision=excluded.revision,edited=excluded.edited",
                            (key, lane, result["revision"], edited))
            return result, None

    def workflow_reconcile(self, canonical, aliases):
        """Move the latest explicit run edit to its discovered session."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            ids = [canonical, *aliases]
            rows = self.db.execute("SELECT id,lane,revision,edited FROM workflow WHERE id IN (" +
                                   ",".join("?" for _ in ids) + ") ORDER BY edited DESC,id", ids).fetchall()
            current = next((row for row in rows if row[0] == canonical), None)
            # Discovery is not a manual edit. Preserve the explicit edit time,
            # prefer the canonical record on ties, and advance its revision.
            if rows and (current is None or rows[0][3] > current[3]):
                _old, lane, _rev, edited = rows[0]
                revision = max(row[2] for row in rows) + 1
                self.db.execute("INSERT INTO workflow VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET lane=excluded.lane,revision=excluded.revision,edited=excluded.edited",
                                (canonical, lane, revision, edited))
            for alias in aliases:
                self.db.execute("INSERT OR REPLACE INTO workflow_alias VALUES (?,?)", (alias, canonical))
            return self.workflow_get(canonical)

    def ingest(self, raw):
        return self.ingest_many([raw])

    def ingest_many(self, raw_events):
        events = [validate_event(e, self.config["projects"]) for e in raw_events]
        with self.db:
            return self._insert(events)

    def _insert(self, events):
        count = 0
        for e in events:
            count += self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                (digest(e), e["project_id"], e["run_id"], e["machine"], e["source"], e["kind"], e["observed_at"])).rowcount
        return count

    def discovery(self, events, connected, panes, unmatched, now):
        machine = self.config["machine"]
        events = [validate_event(e, self.config["projects"]) for e in events]
        with self.db:
            previous = self.db.execute("SELECT checked FROM collectors WHERE machine=?", (machine,)).fetchone()
            if previous and previous[0] > now:
                return
            self._insert(events)
            if connected:
                self.db.execute("UPDATE presence SET present=0 WHERE machine=?", (machine,))
                for event in events:
                    self.db.execute("INSERT INTO presence VALUES (?,?,?,1) ON CONFLICT(run) DO UPDATE SET seen=excluded.seen,present=1",
                                    (event["run_id"], machine, now))
                    # Presence heartbeats are not lifecycle history; retain only the newest.
                    self.db.execute("DELETE FROM events WHERE source='tmux' AND run=? AND at<?",
                                    (event["run_id"], event["observed_at"]))
            self.db.execute("INSERT OR REPLACE INTO collectors VALUES (?,?,?,?,?)", (machine, now, int(connected), panes, unmatched))

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        stale = self.config["stale_after"]
        collectors = []
        connectivity = {}
        for machine, checked, connected, panes, unmatched in self.db.execute("SELECT * FROM collectors ORDER BY machine"):
            state = "stale" if now - checked > stale else ("connected" if connected else "disconnected")
            connectivity[machine] = state
            collectors.append(dict(machine=machine, checked_at=checked, status=state, panes=panes, unmatched_panes=unmatched))
        projects = {pid: dict(id=pid, repository=p["repository"], completion="unknown", runs=[]) for pid, p in self.config["projects"].items()}
        rows = self.db.execute("SELECT project,run,machine,source,kind,at,id FROM events ORDER BY at,id").fetchall()
        grouped = {}
        for project, run, machine, source, kind, at, eid in rows:
            if project not in projects:
                continue
            key = (project, run, machine)
            grouped.setdefault(key, []).append((source, kind, at, eid))
        presence = {r[0]: r[1:] for r in self.db.execute("SELECT run,seen,present FROM presence")}
        for (project, run, machine), observations in grouped.items():
            lifecycle = [e for e in observations if e[0] != "tmux"]
            latest_at = max(e[2] for e in observations)
            selected = lifecycle[-1] if lifecycle else observations[-1]
            source, kind, state_at, _ = selected
            # Contradictory same-time events have no ordering proof.
            ties = {e[1] for e in (lifecycle or observations) if e[2] == state_at}
            state = STATES[kind] if len(ties) == 1 else "unknown"
            freshness = "stale" if now - latest_at > stale else "fresh"
            reachability = "not-observed"
            if run in presence:
                seen, present = presence[run]
                conn = connectivity.get(machine, "unknown")
                reachability = conn if conn != "connected" else ("present" if present else "absent")
            state_freshness = "stale" if now - state_at > stale else "fresh"
            projects[project]["runs"].append(dict(id=run, machine=machine, source=source,
                state=state, state_at=state_at, last_seen=latest_at, freshness=freshness,
                state_freshness=state_freshness, reachability=reachability,
                evidence="lifecycle" if lifecycle and source != "cloud-import" else ("imported" if source == "cloud-import" else "metadata-only"),
                recent_events=[dict(kind=e[1], source=e[0], at=e[2]) for e in observations[-8:]]))
        for p in projects.values():
            p["runs"].sort(key=lambda r: r["last_seen"], reverse=True)
        transport = None
        if "transport" in self.config and self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone():
            key = digest(self.config["machine"], self.config["transport"]["hub_url"], self.config["transport"]["stream"])
            row = self.db.execute("SELECT status,acknowledged,pending IS NOT NULL FROM outbox WHERE key=?", (key,)).fetchone()
            if row:
                transport = dict(status=row[0], acknowledged_at=row[1], pending=bool(row[2]))
        return dict(schema_version=1, mode="live", generated_at=now, transport=transport,
                    cloud=dict(status="import-only", live_connected=False), collectors=collectors,
                    projects=list(projects.values()))


def import_cloud(path, store):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise ValueError("Import exceeds 1 MB")
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "events"} or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("Expected version 1 cloud import envelope")
    if not isinstance(raw["events"], list) or len(raw["events"]) > 1000:
        raise ValueError("Expected at most 1000 events")
    for e in raw["events"]:
        if not isinstance(e, dict) or e.get("source") != "cloud-import":
            raise ValueError("Import accepts only cloud-import observations")
    return store.ingest_many(raw["events"])
