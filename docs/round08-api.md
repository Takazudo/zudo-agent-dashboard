# Round08 dashboard API

All routes use exact paths and `Cache-Control: no-store`. Local requests require a single loopback `Host` matching the server port. The optional Serve adapter requires its configured HTTPS origin and rewrites the verified origin to the local backend. JSON mutations require a single exact `Origin`, `Content-Type: application/json`, one bounded `Content-Length`, and their own CSRF header. Duplicate headers are rejected.

## Terminal discovery and capture

With console policy enabled, authenticate through existing HTTP Basic. `GET /api/console/bootstrap` returns `{ "csrf": "<opaque>", "identity": "operator", "allow_input": false }` after authentication. The token is memory-only. `POST /api/console/targets` with `X-Console-CSRF` and `{}` returns:

```json
{"targets":[{"project":"example","machine":"device","id":"<opaque-pane-id>","session_id":"<opaque-session-id>","workflow_id":"<opaque-workflow-id>","run":"<observed-run-id-or-null>","pane":"%1","server":[1234,1.0],"foreground":"sh","cols":80,"rows":24}]}
```

The authenticated collection groups by `workflow_id`. A tmux session spanning projects has one group per allowed project. `session_id` includes machine, boot, server process identity, tmux session identity and creation time, and project. Shell continuation keeps the key; server or session recreation changes it. Pane `id` additionally binds its root process identity. No remote pane discovery is implied by hub observations.

`POST /api/console/preview` uses the same Basic and console CSRF boundary, body exactly `{"project":"example","machine":"device","id":"<opaque-pane-id>"}`. It reacquires and validates the current allowed target and project before and after capture, discards output if the pane moved out of scope, and closes its temporary control connection. It creates no lease and cannot enable input. `200`:

```json
{"screen":"recent output","lines":1,"limit":500,"sampled_at":1760000000.0,"truncated":false,"byte_truncated":false,"foreground":"sh","cols":80,"rows":24}
```

`POST /api/console/screen` has the same capture fields and additionally uses the existing lease body `{"lease":"<opaque>"}`. Capture contains at most 500 recent lines and at most 131072 UTF-8 bytes; a busy or stale target returns `429` or `409`, unavailable capture `503`, invalid body `400`, missing Basic `401`, denied target/policy `403`. The browser should preserve a reading anchor only when overlap between bounded frames is unambiguous. On uncertain overlap it should freeze the old bounded frame, show that newer output exists and position is uncertain, and let **Latest** replace the frame. Neither endpoint records captured text in snapshots, SQLite, hub frames or logs.

Existing open, control, send, resize and close lease, sequence and revalidation contracts are unchanged. Input is never retried automatically.

## Manual workflow metadata

`GET /api/workflow` returns metadata independently of terminal capture. Local access does not require console Basic for observed run keys; authenticated local console discovery adds actual sessions. Hub access requires viewer Basic, never collector Bearer. Example:

```json
{"csrf":"<opaque-workflow-token>","lanes":["inbox","progress","review","done"],"items":{"<workflow-id>":{"lane":"inbox","revision":0}},"run_keys":[{"id":"<run-workflow-id>","canonical":"<workflow-id>","project":"example","machine":"device","run":"<observed-run-id>"}],"sessions":[{"id":"<workflow-id>","session_id":"<session-id>","project":"example","machine":"device","panes":["<pane-id>"],"runs":["<observed-run-id>"]}]}
```

The `run_keys` mapping lets clients join snapshot runs without recreating server hashes. Unauthenticated local calls and hub calls have `sessions:[]`; they disclose no console pane/session identifiers. `canonical` points to an authenticated discovered session after alias reconciliation. A run can be observation-only when unmapped. The `items` object contains the lane and revision for known IDs, initially Inbox revision 0. The UI label for `progress` is **In progress**.

`POST /api/workflow` has body exactly `{"id":"<workflow-id>","lane":"review","revision":0}` and `X-Workflow-CSRF: <token>` from GET. Local access requires matching loopback HTTP Host and Origin; hub requires viewer Basic and its configured HTTP or HTTPS authority. `200` returns `{"id":"<workflow-id>","lane":"review","revision":1}`. A stale revision returns `409` with the current `id`, `lane` and `revision`; refresh and ask the user to repeat the move rather than silently overwriting it. Unknown or no-longer-current run/session IDs return `409` with an error. Invalid shape, lane or revision returns `400`; missing viewer identity `401`; origin/CSRF mismatch `403`. A retained stale or disconnected run can still receive manual board metadata while visibly uncertain; an ended or absent run cannot receive a new move. This never grants terminal access. Session writes also require current authenticated console discovery. Lanes live in a separate SQLite table (sample mode only in ephemeral memory), never altering observation state. Alias reconciliation selects the newest explicit run edit when several observed runs map to one actual session; it stores only IDs and manual metadata.

## Assets and content security

The servers and Serve adapter allow only exact asset paths: `/`, `/app.js`, `/style.css`, `/console.html` (local authenticated), `/console.js`, `/preferences.js`, `/tokens.css`, `/dashboard.css`, `/detail.css`, `/editor.js`, `/THIRD_PARTY_NOTICES.txt`, `/favicon.svg`. HTML receives a fresh cryptographic `meta[name=csp-nonce]` and matching `style-src` nonce for CodeMirror's generated style elements. Scripts and assets remain same-origin; local detail uses `frame-ancestors 'self'`, while home uses `frame-ancestors 'none'`. Hub remains observation-only and does not serve the console detail.
