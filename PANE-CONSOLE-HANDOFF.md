# Pane console handoff

This feature branch targets PR #2 (`feat/multi-device-hub`) and remains draft.
The clarified product contract is an external controller for an existing tmux
pane. Foreground agent exit back to its shell is expected terminal behavior.
The initial run aids selection; it does not bind input to an agent lifetime.

Implemented: separately authenticated optional local pane console; explicit
pane/server identity and foreground display; policy plus per-connection human
control enablement; text send, direct keyboard and special keys, explicit resize;
bounded plain-text screen refresh; expiry/disconnect cleanup and no input replay.
Pane replacement/respawn and server restart fail closed. The default deployment
still exposes no terminal capture or input; every new connection is read-only.

Limits: snapshot rendering at up to 2 Hz, not a full streaming emulator. No color,
scrollback, mouse forwarding, remote hub control, or new network exposure.
See [operator documentation](docs/pane-console.md) for details and the exact
later activation proposal. Source is independently authored. Real terminal QA
uses only private disposable tmux/PTY fixtures and synthetic data/credentials.

No merge, live activation, real-agent capture/input, production route, network
configuration, real credentials, persistent service, or global hook change has
been made or authorized here. The assistant must not use this feature to bypass
earlier denials of input to real agents.
