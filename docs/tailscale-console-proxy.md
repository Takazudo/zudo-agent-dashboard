# Optional Tailscale Serve console adapter

This is an opt-in adapter for the human-operated [pane console](pane-console.md).
It does not configure Tailscale, create credentials, start persistent services,
or enable control. Installation and merge are not activation approval. The
multi-device hub remains observation-only; this adapter targets one machine's
local console, not a client-selected server or arbitrary shell endpoint.

## Proposed separate route

```text
Human browser HTTPS :8443
  → Tailscale Serve HTTP reverse proxy
  → 127.0.0.1:46208 console-proxy
  → 127.0.0.1:46207 local console

Existing read-only :443 → :46206 → :46205 stays unchanged.
```

The operator must approve the machine, exact HTTPS origin and Tailscale login,
projects, private policy path, ports, and view-only or input/resize permission.
All examples below are proposals with placeholders, not executed configuration.
Never point the existing read-only route at this adapter or console.

## Required trust boundary

Use **direct Tailscale Serve HTTP reverse proxy over TCP to 127.0.0.1**. Serve
replaces caller-supplied identity headers; Funnel and tagged-device requests do
not carry the same user identity. See the official
[identity-header contract](https://tailscale.com/docs/features/tailscale-serve#identity-headers).
The adapter requires exactly one configured login; other users, shared-node
visitors with other identities, missing identity and any Funnel marker fail
closed. Only printable ASCII login values are supported.

The adapter listens only on 127.0.0.1 and requires that exact peer address. It
checks single exact Host, X-Forwarded-Host, X-Forwarded-Proto=https and login
headers. The [Serve implementation](https://github.com/tailscale/tailscale/blob/main/ipn/ipnlocal/serve.go)
preserves the external Host for TCP HTTP backends. Unix-socket backends and
chained generic proxies are outside this adapter's contract.

**Local processes are trusted.** Loopback TCP cannot distinguish tailscaled
from another local process forging those headers. `--trust-local-tailscale-serve`
explicitly acknowledges this limitation; it is not automatic peer attestation.
Do not deploy on a host with untrusted local processes. Basic authentication is
still independently required for the console page, bootstrap, and operations,
even if a local process forges every Serve header. Read-only dashboard assets
and snapshots require the Serve identity gate but retain the backend's existing
local authentication behavior. The backend itself remains a local trust boundary.

Every POST requires the exact external HTTPS Origin before rewriting it to the
fixed backend's loopback Origin. A GET may omit Origin; if present it must match.
Basic Authorization and CSRF proof are forwarded unchanged, never synthesized.
The backend validates both. There is no CORS/preflight or WebSocket upgrade
support. The human console uses bounded HTTP screen refresh and input requests.

## Activation proposal for later approval

1. Review existing `tailscale serve status --json` and `tailscale funnel status
   --json` locally. Confirm 8443 is unused, the approved audience already has
   access, and no Funnel route targets this console. Do not change ACLs, enable
   Funnel, reset Serve, or replace the 443 route. If prerequisites are absent,
   stop and prepare a separate access proposal.
2. The human supplies a fresh high-entropy console secret using their existing
   secret process and prepares the owner-only policy described in
   [pane-console.md](pane-console.md#exact-proposed-activation--requires-later-approval-not-performed).
   Start with `allow_input: false`; setting it true requires explicit input and
   resize approval. No actual credentials or verifier belong in Git, command
   arguments, this document, screenshots, or chat.
3. After approval, start these foreground processes with approved values:

   ```text
   python -m zudo_agent --config /approved/config.json --db /approved/observations.sqlite3 serve --port 46207 --console-policy /approved/private/pane-policy.json
   python -m zudo_agent console-proxy --external-origin https://APPROVED-HOST.ts.net:8443 --allowed-login APPROVED-LOGIN --backend-port 46207 --port 46208 --trust-local-tailscale-serve
   tailscale serve --https=8443 http://127.0.0.1:46208
   ```

   Replace placeholders with the exact lowercase canonical hostname and login;
   no trailing slash in the origin. These commands intentionally omit `--bg`.
   The [official Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve)
   describes HTTPS reverse proxy and port-scoped removal.
4. The human opens the approved HTTPS origin on their phone/desktop and enters
   the separate console identity and secret into the browser's Basic prompt.
   Tailscale login and console identity are distinct. Use a private browser
   profile; close it to discard cached Basic credentials. Check read-only
   behavior first and confirm the exact pane before enabling approved control.
5. Revoke only the new route with
   `tailscale serve --https=8443 http://127.0.0.1:46208 off` if still configured,
   then stop adapter and console. Restart console without its policy to disable
   it. Never use `tailscale serve reset` because it affects unrelated routes.
   A console restart invalidates leases and CSRF proof. Policy changes require
   restart; the adapter does not reload or edit them.

Before any live use, verify the installed Serve version's identity and Host
behavior, denied-user/Funnel behavior, HTTPS browser authentication, and unchanged
read-only route with the human operator. Cloud fixture tests do not prove live
tailnet configuration. The assistant must not use the adapter to operate real
user agents or bypass a previous denial of assistant input.

## Bounds and verification

Only the shipped dashboard assets and fixed console API routes are forwarded;
queries, arbitrary URLs, redirects and client-selected destinations are rejected.
There are at most 16 active connections, 32 KiB request bodies, 2 MiB responses,
5-second I/O timeouts and 10-second total inbound/upstream deadlines. Python's
HTTP parser also bounds header count and line length. Chunked requests and
Expect are rejected. Headers are reconstructed from an allowlist. Responses
carry no-store; cookies, redirects and arbitrary upstream headers are stripped.
The adapter has no access/body/error logging or terminal persistence.

Each request has one upstream attempt. A lost reply or timeout may mean input
was delivered: reconnect explicitly, inspect the pane, and never replay input
automatically. Existing lease, sequence, target identity and control-off checks
remain authoritative at the console backend.

`bash scripts/check.sh` tests synthetic Serve identity/Host/Origin, Basic/CSRF,
preflight, control-off, sequences, header stripping, limits, timeout and uncertain
delivery. `npm run test:browser` runs existing desktop/mobile fixture tests both
directly and through a disposable local HTTPS Serve simulator plus the real
adapter. Its temporary self-signed TLS key and public Basic fixture password
are test-only, deleted with the fixture. The simulator does not prove Tailscale
identity injection; no real tailnet, credential or user terminal is used.
