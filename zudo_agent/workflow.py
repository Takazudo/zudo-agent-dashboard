"""Manual dashboard lanes, kept separate from observed lifecycle and capture."""
import secrets
import threading

from .model import digest

LANES = ("inbox", "progress", "review", "done")


def run_id(project, machine, run):
    return digest("workflow-run", project, machine, run)


def known_runs(snapshot, current=False):
    return {run_id(project["id"], run["machine"], run["id"]):
            {"project": project["id"], "machine": run["machine"], "run": run["id"]}
            for project in snapshot["projects"] for run in project["runs"]
            if not current or (run["state"] != "ended" and run["reachability"] != "absent")}


class Workflow:
    def __init__(self):
        self.csrf = secrets.token_urlsafe(32)
        self.sample = {}
        self.lock = threading.Lock()

    def state(self, store, ids):
        if store:
            return {key: store.workflow_get(store.workflow_canonical(key)) for key in ids}
        with self.lock:
            return {key: self.sample.get(key, {"lane": "inbox", "revision": 0}) for key in ids}

    def move(self, store, key, lane, revision):
        if type(key) is not str or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid workflow id")
        if lane not in LANES or type(revision) is not int or revision < 0 or revision > 2**53 - 1:
            raise ValueError("Invalid workflow move")
        if store:
            return store.workflow_move(key, lane, revision)
        with self.lock:
            current = self.sample.get(key, {"lane": "inbox", "revision": 0})
            if current["revision"] != revision:
                return None, current
            updated = {"lane": lane, "revision": revision + 1}
            self.sample[key] = updated
            return updated, None
