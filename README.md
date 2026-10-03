# Zudo Agent Dashboard

A read-only, local-first view of agent work across projects and individual runs.
Python 3.11+ on Linux/WSL or macOS, SQLite, and a dependency-free browser UI.
The default mode has no terminal capture or control. An optional, separately
authorized [local pane console](docs/pane-console.md) provides human terminal
control. No transcript scraping or public hosting is included.

**v0.2:** local tmux discovery and opt-in Claude Code / Codex hooks, plus an
authenticated private hub for multiple devices; cloud tasks through validated imports only. This is an observation
tool, not an assertion that a project is complete.

## Try the interface

```sh
git clone https://github.com/Takazudo/zudo-agent-dashboard.git
cd zudo-agent-dashboard
python3 -m zudo_agent serve --sample
# Open http://127.0.0.1:8765
```

Sample mode is prominently labeled, uses exclusively synthetic data, and does
not open a database or run a collector. It is separate from live mode.

## Local observations

For one shared dashboard across devices, see [multiple-device setup](docs/multiple-devices.md).
It uses explicit registration and existing secure private transport. There is
no automatic LAN scan, credential creation, network change or public deployment.

```sh
cp examples/config.json config.local.json
# Edit machine, repository identity, and absolute checkout roots first.
python3 -m zudo_agent --config config.local.json collect
python3 -m zudo_agent --config config.local.json snapshot
python3 -m zudo_agent --config config.local.json serve
```

The foreground server polls every five seconds. Stop it with Ctrl-C. It binds
only `127.0.0.1`; there is deliberately no public-bind option, write endpoint,
remote asset, or service installation. WSL users can open the loopback URL in a
browser if their existing WSL localhost forwarding supports it.

Configuration fields:

| Field | Meaning |
| --- | --- |
| `machine` | Explicit safe identifier, e.g. `work-laptop`; no automatic hostname export |
| `projects[].id` | Safe project slug |
| `projects[].repository` | Explicit canonical `host/owner/repo`, without scheme, credentials or query string |
| `projects[].roots` | Absolute local checkout paths, kept out of snapshot JSON |
| `tmux_socket` | Optional tmux `-L` socket name; omit for the default selected server |
| `stale_after` | Seconds until observations become stale (default 120) |

One project can have many roots and many runs. Register each repository once;
combine its roots. Linked Git worktrees are matched through their common Git
directory. Unregistered paths never become named projects automatically.

State defaults to `$XDG_STATE_HOME/zudo-agent/observations.sqlite3` (or
`~/.local/state/zudo-agent/observations.sqlite3`). All commands accept an explicit
`--db /absolute/path/state.sqlite3` **before** the subcommand. Use the same config
and DB for the collector, hooks, and imports. The database is local/private;
export only the sanitized `snapshot` command when desired. Do not commit local
configuration, databases, terminal output, or actual imported task observations.

## What a signal means

| Signal | Interpretation |
| --- | --- |
| Metadata only | A recognized agent process exists; its activity is **unknown** |
| Working | A prompt/tool lifecycle event was observed |
| Needs attention | A permission request was observed; no permission decision is made |
| Turn stopped | Agent stopped responding; the run is idle, project completion **unknown** |
| Error observed | A lifecycle failure was observed; later work can supersede it |
| Session ended | Session lifecycle ended; project completion still **unknown** |
| Task completed | Explicit cloud-import task state, not project completion |
| Stale | Presence or lifecycle evidence is old; both ages are preserved separately |
| Disconnected / absent | Collector cannot reach tmux / process absent from a successful scan |

Arbitrary conversational questions are not interpreted. A Stop containing a
question still means only "turn stopped." Tool errors are not terminal failures.
The dashboard retains last evidence and connection/freshness labels rather than
silently claiming success or changing disconnected runs to ended.

Every scan lists **all sessions, windows, and panes** on the selected tmux server.
The collector reads pane metadata plus Linux `/proc/*/stat` or macOS `libproc`, never process arguments
or pane contents. It discovers the nearest unambiguous `claude` or `codex`
process beneath a pane, including launches through a shell. Shell-only,
ambiguous, or unregistered panes are counted as unassigned/no-agent.

Run identity hashes the configured machine, PID and process start identity
(Linux boot ID/start ticks or macOS absolute start time). A restarted agent in the same pane becomes a distinct run; PID reuse
and machine reboot cannot silently revive an old run. tmux session/window names
are not project identities. Native process names are recognized; wrappers whose
only process name is `node` need hooks and otherwise remain unknown.

## Opt-in lifecycle hooks

For guided setup with preview, explicit approval, safe merging and owned rollback,
use the bundled [Claude Code / Codex setup skills](docs/setup.md). Their shared
`scripts/setup.py` can run directly from this clone, even outside the monitored
project. Nothing is installed simply by invoking the skill or generating a plan.

No hooks are installed by this repository. First install the CLI into a local
virtual environment so hooks can invoke it from any working directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
python3 scripts/hook-config.py claude \
  --executable /absolute/dashboard/.venv/bin/zudo-agent \
  --config /absolute/dashboard/config.local.json \
  --db /absolute/private/observations.sqlite3
# Repeat with `codex` for Codex's supported event set.
```

Review the printed object. For Claude, merge its `hooks` entries into the
selected project's `.claude/settings.local.json`; for Codex, use the selected
project's `.codex/hooks.json` and its hook review/trust flow. Preserve existing
hooks; do not overwrite an existing config. Do not install global hooks as part
of trying this app. Configure the matching project roots before enabling them.

The handler reads at most 64 KiB of stdin, selects only recognized lifecycle
fields, and discards all free text, tool inputs, transcripts and command
arguments. It never opens `transcript_path`. It writes no stdout/stderr and
always returns success on observation failures: no approval, denial, blocking
decision, injected context, or retry directive. A hook may be dropped on timeout,
oversized input, unknown schema, or database contention; missing events imply
uncertainty, not a successful transition. Synchronous short handlers reduce
reordering; timestamps are local receipt times, not guaranteed source order.

When a recognizable ancestor process exists, hooks and tmux share its run ID.
Otherwise the session ID is hashed with machine and provider, producing a
separate lifecycle run whose tmux presence is not proven. Subagent payloads
with `agent_id` or `agent_type` are ignored to avoid stopping a parent run from
a child event. Providers without those markers cannot safely disambiguate
subagent events; validate hook behavior for your installed version.

Reference checks: [Claude Code hooks](https://code.claude.com/docs/en/hooks),
[Codex hooks](https://learn.chatgpt.com/docs/hooks). Implementation inspection
used Claude Code **2.1.288**, Codex CLI **0.159.3**, tmux **3.4**, and Python
**3.12.3** on WSL. Hook samples were checked against those official references;
they were **not installed into running user agents**. Lifecycle handling is
fixture-tested, not a claim of end-to-end live hook delivery.

## Cloud task imports and adapter boundary

There is **no live cloud API integration in v0.2**. No supported authenticated
live task API was established in this executor. The UI and JSON always report
`cloud.live_connected: false`, independent of imports. No internal tooling,
private endpoints, or internal task IDs are used as a production integration.

An eventual adapter should authenticate through a supported API, independently
report connectivity/capabilities, map known task states to the following
minimal observation schema, and preserve stable run IDs and timestamps for
replays. It must not invent status from prose. A supported live cloud-task
integration remains future work; JSON import is the current cloud boundary.

```json
{
  "schema_version": 1,
  "events": [
    {
      "project_id": "registered-project-slug",
      "run_id": "64 lowercase hexadecimal characters: hash of your stable task identity",
      "machine": "cloud-account-alias",
      "source": "cloud-import",
      "kind": "working",
      "observed_at": 1700000000
    }
  ]
}
```

The run ID sentence above is explanatory, **not a valid import value**. Generate
a digest locally, for example `hashlib.sha256(stable_id.encode()).hexdigest()`.
Never include the original task ID, URL, title, transcript, secret, or command
arguments. Import observations you are authorized to access:

```sh
python3 -m zudo_agent --config config.local.json import-cloud /private/tasks.json
```

Accepted `kind` values: `discovered`, `session-start`, `working`,
`permission-request`, `turn-stop`, `error-observed`, `session-end`, `interrupted`,
`unknown`, `completed`. Timestamps are finite Unix seconds, nonnegative and at
most 60 seconds in the future. Imports accept at most 1 MB / 1000 events,
reject extra fields, require registered projects, and validate the entire file
before one transaction writes anything. Exact event replays are deduplicated.
Older events cannot regress state; contradictory equal-time states become
unknown. Preserve original timestamps on retry; events without stable upstream
timestamps cannot be semantically deduplicated reliably.

## Validation and development

```sh
bash scripts/check.sh
python3 -m pip wheel --no-deps --wheel-dir dist .
# Optional browser QA using an available playwright-core and its Chromium:
npm ci
npx playwright-core install chromium
npm run test:browser
```

The browser script requires `playwright-core` to be resolvable (a local dev
install or `NODE_PATH` to an existing installation), and a matching installed
Chromium. It starts and terminates its own sample server. On machines using
`~/.codex/scripts/heavy-guard.sh`, run browser QA through that guard. Screenshots
go to ignored `test-results/`. CI runs unit/syntax checks, builds the Python
wheel on 3.11–3.13, and runs sample-only Chromium QA. No TypeScript or separate
frontend build is required.

Tests cover discovery across sessions/windows/panes, restarts, missing and
disconnected processes, stale evidence, out-of-order and duplicate events,
transient errors, turn-stop semantics, privacy filtering, atomic cloud imports,
sample isolation, loopback binding, Host validation, and read-only HTTP.

## Limits

- One selected tmux server per collector/config. Multi-device aggregation requires
  the explicitly configured authenticated hub; no SSH automation, LAN scan or daemon.
- tmux polling can miss short-lived runs between scans; hooks complement it.
- Repository roots and labels are explicitly trusted configuration. Safe slugs
  prevent incidental leakage, but cannot detect a secret deliberately put into
  a label. Hashes pseudonymize IDs; they are not an encryption boundary.
- Metadata heartbeats are coalesced to the latest observation per run. Lifecycle
  SQLite history is local, retained until you remove the database while stopped;
  there is no long-term archival/retention UI. Intended for a modest personal
  workspace, not unbounded fleet telemetry.
- Local-mode GET endpoints remain unauthenticated loopback only. Hub GETs require
  a separate viewer credential; ingestion requires device credentials. Both use
  strict Host validation and no CORS. Local processes can read local observations.
- No commands are sent to existing tmux panes, and no approval decisions are made.

### Optional pane console

The separately authenticated, loopback-only [pane console](docs/pane-console.md)
controls an explicitly selected existing tmux pane after separate activation
approval and explicit human enablement. It supports text/keyboard input and
resize with live plain-text screen snapshots. Input can execute shell commands,
including after an agent exits to its shell. It is off by default, and each
connection starts read-only. Terminal text is never forwarded or stored in
observation history. The multi-device hub remains observation-only.
