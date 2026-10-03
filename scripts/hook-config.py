"""Print an opt-in config for review. Does not install or change any hooks."""
import argparse
import json
import shlex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("provider", choices=["claude", "codex"])
parser.add_argument("--executable", required=True, help="Absolute venv/bin/zudo-agent path")
parser.add_argument("--config", required=True, help="Absolute local configuration path")
parser.add_argument("--db", required=True, help="Absolute shared local SQLite path")
args = parser.parse_args()
command = shlex.join([args.executable, "--config", args.config, "--db", args.db, "hook", args.provider])
events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop", "SessionEnd"]
events += ["PostToolUseFailure", "StopFailure"] if args.provider == "claude" else ["Interrupt"]
print(json.dumps({"hooks": {name: [{"hooks": [{"type": "command", "command": command, "timeout": 3}]}] for name in events}}, indent=2))
