"""QA-only private tmux/PTY fixture. No default server or installed agents."""
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zudo_agent.collector import discover
from zudo_agent.console import PaneBackend
from zudo_agent.server import make_server
from zudo_agent.store import Store


def stop(*_args):
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, stop)
with tempfile.TemporaryDirectory(prefix="zudo-browser-fixture-") as directory:
    socket = "zudo-browser-" + os.urandom(8).hex()
    command = ["tmux", "-L", socket, "-f", "/dev/null"]
    config = dict(machine="fixture", tmux_socket=socket, stale_after=120,
                  projects={"example": dict(id="example", repository="github.com/example/fixture", roots=[directory])})
    if "--slow-previews" in sys.argv:
        original_preview = PaneBackend.preview
        def slow_preview(self, target):
            time.sleep(1.2)  # Hold only this disposable fixture's console read lock.
            return original_preview(self, target)
        PaneBackend.preview = slow_preview
    policy = dict(identity="fixture", password_sha256=hashlib.sha256(b"public-fixture-password").hexdigest(), projects=["example"], allow_input=True)
    db = Path(directory) / "fixture.sqlite"
    scripts = {}
    for name in ("shared-a", "shared-b", "separate"):
        script = Path(directory) / f"{name}.py"
        script.write_text(
            "import ctypes\n"
            "ctypes.CDLL(None).prctl(15,b'codex',0,0,0)\n"
            f"print('\\n'.join(f'HISTORY-{name}-{{i:04d}}' for i in range(620)), flush=True)\n"
            "print('SYNTHETIC SCREEN ONLY 日本語 <img src=x onerror=alert(1)>',flush=True)\n"
            + ("import threading,time\n"
             "def tick():\n"
             "    for i in range(600):\n"
             "        time.sleep(.7)\n"
             "        print('FIXTURE TICK',i,flush=True)\n"
             "threading.Thread(target=tick,daemon=True).start()\n" if "--stream-output" in sys.argv else "") +
            "input()\n"
        )
        scripts[name] = script
    server = None
    auxiliary = []
    try:
        first = subprocess.check_output(command + ["new-session", "-d", "-s", "shared", "-c", directory, "-P", "-F", "#{pane_id}", "sh"], text=True).strip()
        second = subprocess.check_output(command + ["split-window", "-d", "-t", "shared", "-c", directory, "-P", "-F", "#{pane_id}", "sh"], text=True).strip()
        third = subprocess.check_output(command + ["new-session", "-d", "-s", "separate", "-c", directory, "-P", "-F", "#{pane_id}", "sh"], text=True).strip()
        for pane, name in ((first, "shared-a"), (second, "shared-b"), (third, "separate")):
            subprocess.run(command + ["send-keys", "-t", pane, "-l", sys.executable + " " + str(scripts[name])], check=True)
            subprocess.run(command + ["send-keys", "-t", pane, "Enter"], check=True)
        backend = PaneBackend(config)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            targets = backend.targets()
            if len(targets) == 3 and all(t["run"] for t in targets): break
            time.sleep(.05)
        else: raise RuntimeError("Synthetic agents did not start")
        store = Store(db, config)
        discover(config, store)
        store.close()
        server = make_server(config, db, 0, console_policy=policy)
        origin = f"http://127.0.0.1:{server.server_port}"
        if "--proxy" in sys.argv:
            import runpy
            start = runpy.run_path(str(Path(__file__).with_name("console-proxy-browser-fixture.py")))["start"]
            front, adapter, origin = start(server, directory)
            auxiliary = [adapter, server]
            server = front
        print(origin, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server: server.server_close()
        for item in auxiliary:
            item.shutdown(); item.server_close()
        subprocess.run(command + ["kill-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
