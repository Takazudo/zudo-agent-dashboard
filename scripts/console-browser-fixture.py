"""Disposable QA server. Public fixture auth, synthetic screen, no tmux access."""
import hashlib
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zudo_agent.console import Console
from zudo_agent.model import digest
from zudo_agent.server import make_server
from zudo_agent.store import Store

class Fixture:
    def targets(self):
        return [dict(project="example", run=digest(name), machine="fixture") for name in ("first", "second")]
    def screen(self, target):
        return "SYNTHETIC SCREEN ONLY\n日本語 <img src=x onerror=alert(1)>\n" + target["run"]

with tempfile.TemporaryDirectory(prefix="zudo-browser-fixture-") as directory:
    config = dict(machine="fixture", tmux_socket="unused-fixture", stale_after=120,
                  projects={"example": dict(id="example", repository="github.com/example/fixture", roots=[directory])})
    policy = dict(identity="fixture", password_sha256=hashlib.sha256(b"public-fixture-password").hexdigest(), projects=["example"], allow_input=False)
    db = Path(directory) / "fixture.sqlite"
    store = Store(db, config)
    now = time.time()
    store.discovery([dict(project_id="example", run_id=digest(name), machine="fixture", source="tmux", kind="discovered", observed_at=now) for name in ("first", "second")], True, 2, 0, now)
    store.close()
    console = Console(config, policy, Fixture())
    with patch("zudo_agent.console.Console", return_value=console):
        server = make_server(config, db, 0, console_policy=policy)
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try: server.serve_forever()
    finally: server.server_close()
