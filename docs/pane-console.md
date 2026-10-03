# Human-operated local pane console (draft)

This feature depends on PR #2 (`feat/multi-device-hub`). It adds an **optional
external controller for an existing tmux pane**, including its shell. It does not
own or start the pane's processes. If an agent exits to a shell in the same pane,
input continues to that shell as expected. Input may execute shell commands.

The console is off by default. A private local policy separately permits viewing
or control. Every connection starts read-only and requires an explicit **Enable
control of this pane** action, even when the policy permits input. Only one lease
can control a given pane at a time. The authenticated multi-device hub stays
observation-only. No remote listener, proxy route, or network access is added.

## Operator workflow and delivered capabilities

Expand a current local run in the dashboard and select **Open pane console**.
After operator authentication, select an existing pane from an allowed project.
The initial run is a selection aid, not the control target. The console can also
select shell panes without an agent. It shows project, machine, server identity,
pane ID, full pane identity, and the latest foreground process name and dimensions.
Connect, inspect the pane, then explicitly enable control if authorized.

- **Compose text / Send** supports Unicode, multiline text, and optional Enter.
  Newlines and Enter may execute commands; there is no content inspection.
- **Interactive keyboard** sends typed text directly, including IME composition.
  It supports Enter, Tab, Backspace, Delete, Escape, arrows, Home/End, Page Up/Down,
  and ASCII Ctrl combinations. Mobile buttons provide Enter, Tab, Esc, Ctrl-C,
  and arrows. Standard ANSI arrow sequences are sent; not every terminal's
  application-specific key encoding is emulated.
- **Pane size** explicitly requests 20–300 columns and 5–200 rows. This changes
  the shared tmux layout and is visible to other clients; tmux can clamp it.
- Output is a **plain-text screen snapshot refreshed every 500 ms**, at most 200
  visible rows. This is real tmux/PTY input with snapshot rendering, not a full
  streaming terminal emulator. Colors, scrollback, mouse forwarding, function
  keys, and clipboard/OSC handling are not implemented. Slow operations reduce
  refresh frequency; the client never overlaps requests or accumulates screens.

Changing selection, closing, expiry, or a detected disconnect clears visible
text and unsent input. Reconnection is manual and starts read-only. A quiet
network loss is detected by the next screen request or expiry. The initial
selection list is refreshed by reloading the page. Closing or expiring a lease
closes only the dashboard's tmux control client; it does not terminate the pane
or detach another user's client.

## Identity, authorization, and delivery

The server binds each lease to machine/boot identity, tmux server PID/start time,
pane ID, and pane-root PID/start time. Foreground process changes and shell cwd
changes do not revoke that already authorized pane. Project roots constrain
initial selection, not what an authorized terminal user can do inside its shell.
They are **not a filesystem sandbox**. Pane respawn/replacement or a server
restart invalidates the target; the user must select and connect again.

Each lease owns a connection to the existing tmux server using control mode,
`no-output` and `ignore-size`. It never reconnects or creates a server. Metadata
validation, capture, send, and resize use that same connection. Before a mutation,
the complete stored identity is revalidated; inside tmux's command queue an
additional pane PID/alive check guards respawn. Input bytes are encoded as hex
arguments to `send-keys`, never interpolated as tmux/shell commands. The existing
pane interprets those bytes normally. No client-selected executable, shell,
socket, process ID, or raw tmux command is accepted by the HTTP API.

Opening a control-mode client is a real tmux attachment: existing tmux
client-attachment hooks may run. It does not switch the pane, alter sizing on
attach, or create a persistent service. Check local hook behavior when reviewing
activation. See [upstream tmux documentation](https://man.openbsd.org/tmux.1).

Mutations share a strictly increasing per-lease sequence. The sequence is
consumed before dispatch; duplicates/out-of-order operations revoke the lease.
A failed/uncertain mutation revokes it too. Acknowledgment means **sent to the
pane**, not that the command succeeded. Browser input is bounded to 4096 UTF-8
bytes waiting plus one request of at most 4096 bytes in flight. Nothing is
retried or replayed after failure, target changes, expiry, or reconnect. Input
already delivered cannot be undone by closing the page.

Leases expire after 120 seconds and are cleaned up even without more HTTP
requests. At most eight leases and sixteen HTTP requests are active; a single
console operation runs at a time. Transactions have a three-second I/O deadline
and 256 KiB response cap. HTTP bodies are capped at 32 KiB. One screen replaces
the previous screen, so there is no unbounded output buffer. The renderer treats
pane content as plain text and strips control/format characters.

Authentication uses one explicit operator principal and a high-entropy secret
verifier. Exact loopback Host, same-origin POST, and bootstrap CSRF proof are
required. Duplicate security headers and WebSocket upgrades are rejected. No
CORS access is enabled. Pane input/output is excluded from SQLite, event history,
forwarding, browser storage, and application logs. A pane can show text predating
the selected run. The operator/browser and local tmux environment still see it.
All committed test data and published test screenshots are synthetic.

## Exact proposed activation — requires later approval; not performed

Approve the machine, configured project IDs, operator identity, private policy
path, loopback port, and **view-only versus input/resize permission** explicitly.
Approval to implement this feature is not live activation approval.

1. The human operator supplies a fresh high-entropy secret (at least 32 random
   bytes) through their existing secret-management process. Do not reuse public
   fixture passwords or hub/device credentials. SHA-256 is used as a verifier
   for this secret, not as password stretching for a memorable password.
2. The human writes an owner-only regular file, mode `0600`, outside the repo,
   containing exactly `identity`, `password_sha256` (64 lowercase hex digits),
   `projects` (explicit configured IDs), and `allow_input`. Set `allow_input:
   false` for viewing only; set it to `true` **only after separate approval of
   human input and resizing**. No real policy or credential was generated here.
3. Following approval, start the foreground process with the approved paths:

   ```text
   python -m zudo_agent --config /approved/config.json --db /approved/observations.sqlite3 serve --port 8765 --console-policy /approved/private/pane-policy.json
   ```

4. Visit `http://127.0.0.1:8765` directly on that machine. Confirm the selected
   pane and foreground state before explicitly enabling control. A private
   browser profile is recommended because browsers cache Basic credentials;
   close the profile afterward. Policy edits take effect on server restart.
5. Revoke access by stopping the foreground process and restarting without
   `--console-policy`. Restart invalidates leases, their clients, and CSRF proof.

Do not expose these endpoints through the existing x0x route. No TLS/proxy,
tailnet audience, DNS, network/ACL/firewall, persistent service, global hook,
or multi-device terminal transport change is included. Mobile layout is tested,
but accessing it from a separate phone/network remains a separate proposal.
The assistant must never use this feature to operate real user agents or evade
an earlier denial of assistant input.

## Verification

`bash scripts/check.sh` includes authorization, control-off, explicit enablement,
single controller, sequence/no-replay, stale identity, output bounds, and existing
lifecycle/history/freshness/privacy regressions. Linux tests use a private
unpredictable tmux socket, `/dev/null` configuration, temporary shell and synthetic
agent-like child. They exercise real PTY input, shell continuation, resize,
literal punctuation, pane replacement, and server restart. They never access a
default/user server. Cleanup kills only the private fixture server.

`npm ci && npm run test:browser` uses the same isolation for browser control QA,
including desktop/mobile interaction and a response lost after input delivery.
It also runs the original dashboard regressions. Linux and tmux are required for
the console browser fixture; `CHROMIUM_PATH` can select an installed Chromium.
Native macOS metadata and common console tests run in macOS CI; the real PTY
fixture is Linux-only. No real-user terminal testing was performed.
