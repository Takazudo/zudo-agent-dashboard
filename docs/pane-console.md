# Local pane viewer (draft)

This branch depends on PR #2 (`feat/multi-device-hub`). It adds an **optional,
read-only pane snapshot viewer**, not the requested full interactive terminal.
Nothing is enabled by default. The authenticated multi-device hub remains
observation-only; this viewer is available only through a local collector's
literal loopback address. Mobile layout is tested, but phone/network access is
not provided by this change.

## What an operator can do

After separately approved activation, expand a current local run on the dashboard
and select **View read-only pane snapshot**. The viewer requires its own operator
login. Confirm the displayed project, machine, and complete run ID, then select
**Connect to selected run**. **Refresh screen** requests another snapshot.

The server resolves the run to a unique pane, retaining boot identity, tmux server
PID/start time, pane ID, root PID/start time, and agent PID/start time. It rechecks
that complete identity before and after each capture. Missing, ambiguous,
restarted, or changed targets fail closed and revoke the lease. Leases expire
120 seconds after opening; refreshing does not renew them. At most eight leases
and sixteen HTTP requests are active, with a single console operation at a time.
Each subprocess has a three-second deadline and 256 KiB output cap. HTTP bodies
are limited to 32 KiB. The viewer fetches at most 101 visible rows, no scrollback.

The screen belongs to the **pane**, so it may contain text left there before the
selected agent started. Content is rendered as plain text; control/format
characters are removed. No ANSI, links, clipboard commands, or HTML are executed.
Screen text is never stored in SQLite, forwarded, or logged by the dashboard.
Only synthetic screen content is used in QA screenshots. Browser memory holds
the latest snapshot; target changes, close, expiry, and detected disconnects
clear it. There is no polling or automatic reconnect. A quiet network loss is
noticed by the next request, the browser offline event, or lease expiry.

## Why Send is unavailable

The handoff implementation checked process metadata, then asked tmux to send
keys. An agent could exit between those operations and its old shell could
receive the text. A second check cannot undo input already delivered. The tmux
command queue does not make the external agent-generation check atomic.
See the upstream [tmux command execution documentation](https://man.openbsd.org/tmux.1#COMMAND_PARSING_AND_EXECUTION).

That sender has been removed. `/api/console/send` always rejects requests; even
`allow_input: true` is rejected at startup. The UI has a disabled, explicitly
labeled Send control. There is no interactive PTY, command execution endpoint,
resize, control-key support, remote terminal transport, or input replay. A future
control implementation needs a broker that owns the exact run's PTY lifetime
and cannot fall through to a shell or replacement run. It needs separate design,
implementation, review, and activation approval; changing a flag is insufficient.

## Proposed activation for later approval — not performed

First approve the exact local machine, registered project IDs, operator identity,
policy path, and loopback port. Activation reveals pane content and is a separate
action from enabling ordinary metadata observations.

1. The human operator supplies a high-entropy secret (at least 32 random bytes)
   via their existing secret-management process. Never reuse the public QA
   password, a hub/device credential, or a human-memorable password. The policy
   uses SHA-256 as a verifier for that high-entropy secret, not password stretching.
2. The operator writes an owner-only regular JSON file, mode `0600`, outside the
   repository, containing exactly `identity`, `password_sha256` (64 lowercase
   hexadecimal characters), `projects` (explicit configured project IDs), and
   `allow_input: false`. Symlinks and public files are refused. No real policy or
   credential was generated during development.
3. After approval, start the local foreground process with the existing explicit
   config and database paths:

   ```text
   python -m zudo_agent --config /approved/config.json --db /approved/observations.sqlite3 serve --port 8765 --console-policy /approved/private/pane-policy.json
   ```

4. Visit `http://127.0.0.1:8765` directly on that machine. Browser Basic login is
   limited to the local viewer; use a private browser profile and close it when
   finished because browsers cache Basic credentials. Host checks allow only
   literal loopback names with the bound port. POST requires exact matching
   Origin plus a bootstrap CSRF value; WebSocket upgrades are rejected. This
   implementation has one explicit local operator principal, not shared users.
5. To revoke access, stop the process and restart without `--console-policy`.
   Restart also invalidates every lease and CSRF value. Policy edits take effect
   on restart. No persistent service or global hook is installed.

Do not proxy these endpoints through the existing x0x route. No identity-proxy,
TLS termination, tailnet audience, ACL/firewall, DNS, persistent service, hub,
or remote listener change is included or proposed here. Remote/mobile-device
access and live input require a separate concrete approval and implementation.
The assistant must never use this viewer to operate real user agents.

## Verification

`bash scripts/check.sh` runs security, lifecycle, and existing regressions.
On Linux with tmux present, the test creates its own unpredictable named server
with `/dev/null` configuration, a temporary Python echo fixture labeled as
synthetic, and a real PTY. It checks output, fixture input, pane replacement,
closed panes, and input rejection in the product. Cleanup kills only that named
fixture server. It never queries or writes to a default/user tmux server.

`npm ci && npm run test:browser` tests both the existing dashboard and the new
viewer at desktop/mobile sizes using a synthetic authenticated local server.
`CHROMIUM_PATH` optionally selects an already installed Chromium binary.
The tests cover selection, text rendering, no controls, expiry, disconnect,
manual reconnect, stale responses, and cleared output. Generated artifacts are
ignored. Real macOS libproc verification remains in the existing macOS CI job;
the real PTY test is Linux-only and explicitly skipped elsewhere.
