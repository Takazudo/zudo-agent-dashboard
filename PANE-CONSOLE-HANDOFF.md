# Pane console: unfinished implementation handoff

This branch is a work in progress, **not ready to enable or merge**. It is based
on `3192ebecb9f38a934c3095ba1e47e676549469c6` from `feat/multi-device-hub` (PR #2).
The development session was moved to a cloud environment before UI or tests
were written. Keep the existing live deployment unchanged.

## Current files

- `zudo_agent/console.py`: independently authored draft of an explicit project
  allowlist, owner-only policy, Basic-auth principal, short-lived target lease,
  live metadata identity validation, bounded plain-text screen capture and
  deliberate single-line send. No output/input is written to observation storage.
- `zudo_agent/server.py`: draft optional console routes with authentication,
  exact loopback Host/Origin and CSRF checks. Ordinary observation mode remains
  the default. WebSocket upgrades are rejected; this is not a WebSocket/PTy server.
- `zudo_agent/cli.py`: draft `serve --console-policy PATH` opt-in.

The console HTML/JS assets and referenced operator documentation **do not exist**.
No real credential or policy was created. No pane capture or input was performed.

## Scope and source inspection

The requested eventual feature is a human-operated browser terminal integrated
with project/run selection. This draft takes the permitted first-step path of
a screen snapshot and deliberate text sender: label it **pane console**, not a
full terminal. It has no interactive PTY, terminal resizing, control keys,
continuous stream, multiline paste or session switching. Full tmux attachment
needs an isolation design because prefix commands can navigate other sessions.

Inspection of zudo-text at `a09ac8bf34a74aed1124f61eadd4402d22ad6a1b` found a real
Rust portable-pty implementation exposed through macOS Tauri commands, and a
separate browser echo mock. Its useful architectural ideas are generation-bound
sessions, bounded acknowledged output and explicit lifecycle cleanup. No source
was copied: the inspected repository had no detected license.

## Required work before delivery

1. Review the draft thoroughly. In particular address metadata/send TOCTOU:
   tmux's queued condition checks server PID and pane PID, but does not atomically
   validate the agent process generation. Do not claim this race is solved.
   Check all malformed-input/error paths, timeout/connection cleanup, auth header
   bounds, Unicode handling, and screen control-character filtering.
2. Build a responsive UI linked to a selected current local run. Show exact
   project/machine/run, server-enforced read-only status, expiration and uncertain
   delivery. Require deliberate send; never retry or replay input after a lost
   response or reconnect. Clear input/output when switching targets or closing.
3. Add fixture tests for authorization, origin/CSRF and upgrade rejection,
   default-off/read-only, stale/reused targets, dedup/out-of-order input,
   disconnect/no-replay, malformed bodies and output limits. Any actual tmux
   input/capture must use a private disposable fixture server, never user jobs.
4. Run existing regression tests, package checks and desktop/mobile browser QA.
   Follow local heavy-test guard instructions when running on the user's host.
5. Add operator documentation and an exact future activation/audience proposal.
   This draft accepts literal-loopback origins only. It does not integrate the
   existing identity proxy, tailnet route or multi-device hub. Do not enable any
   live terminal output or control without separate approval. No credentials,
   firewall/ACL changes, persistent services or global hook modifications.
6. Push verified changes and create a draft PR with the PR #2 dependency explicit.
   Do not merge. The initial handoff commit is only a preservation checkpoint.

Terminal input/output must remain ephemeral, excluded from logs, fixtures,
SQLite, forwarding and published artifacts. Synthetic examples must be labeled.
The agent must never use this feature to operate or recover real user agents.
