"""Strict public observation boundary. No free-text telemetry fields."""

import hashlib
import json
import math
import re
import time
from pathlib import Path

KINDS = {"discovered", "session-start", "working", "permission-request", "turn-stop",
         "error-observed", "session-end", "interrupted", "unknown", "completed"}
SOURCES = {"tmux", "claude", "codex", "cloud-import"}
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
REPO = re.compile(r"[a-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError("Expected a safe lowercase identifier")
    return value


def timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid observation timestamp")
    if value < 0 or value > time.time() + 60:
        raise ValueError("Observation timestamp outside accepted range")
    return float(value)


def validate_event(raw, projects):
    required = {"project_id", "run_id", "machine", "source", "kind", "observed_at"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("Event must contain only the six documented fields")
    project = slug(raw["project_id"])
    if project not in projects:
        raise ValueError("Unregistered project")
    if not isinstance(raw["run_id"], str) or not DIGEST.fullmatch(raw["run_id"]):
        raise ValueError("Run identity must be a SHA-256 digest")
    if raw["source"] not in SOURCES or raw["kind"] not in KINDS:
        raise ValueError("Unsupported observation")
    if raw["kind"] == "completed" and raw["source"] != "cloud-import":
        raise ValueError("Only an explicit cloud import can assert task completion")
    return dict(project_id=project, run_id=raw["run_id"], machine=slug(raw["machine"]),
                source=raw["source"], kind=raw["kind"], observed_at=timestamp(raw["observed_at"]))


def load_config(path):
    raw = json.loads(Path(path).read_text())
    if set(raw) - {"machine", "projects", "tmux_socket", "stale_after", "transport"}:
        raise ValueError("Unknown configuration field")
    machine = slug(raw["machine"])
    projects = {}
    repos = set()
    for p in raw["projects"]:
        if set(p) != {"id", "repository", "roots"}:
            raise ValueError("Project requires id, repository, roots")
        pid = slug(p["id"])
        repo = p["repository"]
        if not isinstance(repo, str) or len(repo) > 256 or not REPO.fullmatch(repo) or ".." in repo:
            raise ValueError("Repository must be host/owner/repo without credentials or URL arguments")
        if pid in projects or repo.lower() in repos:
            raise ValueError("Duplicate project or repository identity; combine roots instead")
        if not isinstance(p["roots"], list) or any(not isinstance(r, str) or not Path(r).is_absolute() for r in p["roots"]):
            raise ValueError("Roots must be absolute local paths")
        projects[pid] = dict(id=pid, repository=repo, roots=[str(Path(r).resolve()) for r in p["roots"]])
        repos.add(repo.lower())
    socket = raw.get("tmux_socket")
    if socket is not None:
        slug(socket)
    stale = raw.get("stale_after", 120)
    if isinstance(stale, bool) or not isinstance(stale, int) or not 10 <= stale <= 86400:
        raise ValueError("stale_after must be 10..86400 seconds")
    config = dict(machine=machine, projects=projects, tmux_socket=socket, stale_after=stale)
    if "transport" in raw:
        from .transport import validate_transport
        config["transport"] = validate_transport(raw["transport"])
    return config
