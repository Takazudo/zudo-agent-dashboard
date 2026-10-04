# Human-operated local pane console

This optional feature shows recent output from, and can provide human input to,
an existing local tmux pane, including its shell. It does not own or start the
pane's processes. Input can execute shell commands. The console policy is off
unless it is explicitly configured. The authenticated multi-device hub remains
observation-only and has no pane capture or terminal control.

## Dashboard previews and pane controls

The home page shows session cards alongside project and device health. Observed
remote runs are clearly labeled as observation-only; they have no invented pane
or capture. A local pane's recent output is hidden until a human selects
**Authenticate previews** and passes the existing operator Basic-auth check.
After that explicit choice, the dashboard makes bounded, one-off read requests
for authorized panes and pauses preview polling while the detail inspector is
open. Selecting a card, expanding a tree row, filtering, or moving a manual
workflow lane never sends terminal input. Workflow lanes are dashboard metadata,
stored separately from observed state and terminal text.

The detail inspector is a same-origin frame. A stale run or pane link does not
select a replacement target. Select an authorized pane explicitly. **Connect**
opens a short-lived read-only view of that pane; it does not enable input. The
selected project, machine, session, pane, foreground process and dimensions are
shown with the output. Embedded target selection is controlled by the parent
inspector's pane tabs.

The input panel starts hidden. **Terminal input** opens it and explicitly enables
the currently named pane, when the private policy allows input. There is no
separate control checkbox. Closing the panel with **Close input** disables
browser-side input immediately and requests server-side control revocation. The
pane's unsent draft stays in memory. The toggle remains disabled until the server
confirms revocation; if confirmation fails, the lease disconnects. Once the
panel is reopened in the same connection, input must be deliberately enabled
again.

Switching to another pane in the same tmux session revokes the old lease and
opens the selected pane read-only. Each pane keeps its own in-memory Compose
draft, selection and undo state while the detail frame stays in that session.
Changing sessions, disconnecting, closing the detail frame, reconnecting,
authentication failure or lease expiry clears per-pane editor state, including
Vim registers. Reconnect manually and enable input again. No draft is saved to disk,
browser storage, snapshots or the hub. Home previews and console output also stay
only in page memory, never in browser storage, snapshots, or the hub. Home cards
refresh their preview while the inspector is closed and pause preview polling
while it is open. The selected console frame and pending newer frame clear when
its target changes or the connection ends.

## Compose, direct input and settings

**Compose** uses the bundled CodeMirror 6 editor and the real Vim extension when
enabled. It keeps one editor view. Enter and modified Enter edit the draft; only
the explicit **Send** button submits it. Optional **Append Enter** adds a terminal
Enter after the draft, which may execute a command. Sent text is not inspected.

**Type directly** sends terminal keys immediately after text composition commits.
Uncommitted IME text remains local and is discarded if input closes or the pane
changes before commit. The mode supports Enter, Tab, Backspace, Delete, Escape,
arrows, Home/End, Page Up/Down and ASCII Ctrl combinations. Buttons provide
Enter, Tab, Esc, Ctrl-C and arrows; ANSI sequences may differ from a terminal's
application-specific key encoding. Vim applies only to Compose.

The input divider resizes by mouse, touch or keyboard. **Enlarge editor** expands
the existing editor in place; it is separate from expanding the whole detail
inspector. Geometry changes keep the editor, selection, undo history, output
reading position and input visibility. In the enlarged editor, other controls
are inert and keyboard focus stays within the composer. Escape respects active
IME conversion and Vim/editor ownership.

Editor and appearance settings include Vim, wrapping, line numbers, text size,
and System/Light/Dark theme. Apply commits changes in place; Cancel, X and Escape
discard uncommitted changes. System follows the operating-system appearance;
Light and Dark remain fixed. The browser stores only these whitelisted
preferences. Drafts and captured text are never stored there.

## Recent output and limits

The console displays plain-text tmux snapshots, sampled about every 500 ms while
connected. Each capture is bounded to at most 500 recent lines and 128 KiB; the
screen shows its sample time and cap. This is not a full terminal stream and has
no deep scrollback, ANSI colors, mouse forwarding, function keys or OSC/clipboard
handling.

Following **Latest** tracks new snapshots. Scrolling up pauses following, and
selecting screen text holds the displayed frame with at most one pending newer
frame. If bounded overlap cannot locate the old reading position reliably, the
console freezes the old frame and says the position is uncertain; **Latest**
replaces it. It never pretends that a bounded capture is complete history.

**Session info** shows operator identity and lease expiry. Pane resizing requests
20–300 columns and 5–200 rows; it changes the shared tmux layout and other clients
may see it. tmux may clamp the size. Closing or expiring a lease closes only the
dashboard's tmux control client. It does not terminate the pane or detach another
user's client.

## Identity, authorization, and delivery

The server binds each lease to machine/boot identity, tmux server PID/start time,
tmux session identity and creation time, pane ID, and pane-root PID/start time.
Foreground process changes and shell cwd
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
2. Use the [hidden-input policy command](../README.md#set-a-console-password-locally-does-not-activate-the-console)
   locally to create an owner-only regular file, mode `0600`, outside the repo,
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

Do not expose these endpoints through the existing x0x route. The optional
[Tailscale Serve adapter proposal](tailscale-console-proxy.md) uses a separate
HTTPS port and preserves the read-only route. Its activation remains a separate
approval; no live tailnet, DNS, network/ACL/firewall, persistent service, global
hook, or multi-device terminal transport change is included.
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
The UI checks cover unchanged/changed refreshes, selection and caret preservation,
mouse/touch/keyboard panel sizing, viewport bounds, deferred control/resize during
polling, control-off queue clearing and cancelled IME events across target changes.
Browser automation is not physical-phone keyboard or screen-reader validation.
It also runs the original dashboard regressions. Linux and tmux are required for
the console browser fixture; `CHROMIUM_PATH` can select an installed Chromium.
Native macOS metadata and common console tests run in macOS CI; the real PTY
fixture is Linux-only. No real-user terminal testing was performed.
