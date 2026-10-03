import argparse
import json
import os
import sys
from pathlib import Path

from .collector import discover, hook_event
from .model import load_config
from .server import serve
from .store import Store, import_cloud


def main():
    parser = argparse.ArgumentParser(description="Read-only local agent observations")
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--db", default=str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "zudo-agent/observations.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect", help="Discover all panes on the selected tmux server once")
    commands.add_parser("snapshot", help="Print sanitized project/run JSON")
    cloud = commands.add_parser("import-cloud", help="Validate and atomically import explicit cloud observations")
    cloud.add_argument("file")
    hook = commands.add_parser("hook", help="Consume one hook payload; emit no decisions or stdout")
    hook.add_argument("provider", choices=["claude", "codex"])
    server = commands.add_parser("serve", help="Foreground loopback-only collector and UI")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--sample", action="store_true", help="Use only synthetic data; disable collector and database")
    args = parser.parse_args()
    store = None
    try:
        if args.command == "serve" and args.sample:
            serve(None, None, args.port, sample=True)
            return
        config = load_config(args.config)
        if args.command == "serve":
            serve(config, args.db, args.port)
            return
        if args.command == "hook":
            payload = sys.stdin.buffer.read(65537)
            if len(payload) > 65536:
                return
            event = hook_event(json.loads(payload), args.provider, config)
            if event:
                store = Store(args.db, config)
                store.ingest(event)
            return
        store = Store(args.db, config)
        if args.command == "collect":
            result = discover(config, store)
        elif args.command == "snapshot":
            result = store.snapshot()
        else:
            result = dict(imported=import_cloud(args.file, store), live_connected=False)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if args.command != "hook":
            # Never echo payloads, paths, subprocess stderr, or arbitrary external strings.
            print(f"Invalid input or unavailable local resource ({type(exc).__name__}). See README for the schema.", file=sys.stderr)
            raise SystemExit(1)
    except Exception:
        if args.command != "hook":
            raise
        # Observation must not block or alter an agent's decisions, even on DB contention.
    finally:
        if store:
            store.close()
