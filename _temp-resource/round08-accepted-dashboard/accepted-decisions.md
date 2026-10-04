# Accepted round08 requirements

## Home and navigation

One home combines essential device/project health and capture freshness with a
single collection of sessions. Gallery and Kanban are views of the same data.
Selecting a device/project in the tree or overview scopes that collection. Detail
opens from it; do not create duplicate dashboards or drop existing machine/project
health functions. Prototype device, project, session and pane data is synthetic.

Use the connected recursive tree structure: continuous ancestor guides span their
subtrees, last-child guides terminate at the row midpoint, trailing expand controls
are separate from selection, selected/current states are distinct, and keyboard
navigation supports arrows, Home/End and activation. Keep usable coarse-pointer
hit targets and responsive mobile navigation.

## Gallery and workflow

Captured terminal previews behave like image thumbnails. S/M/L are explicit;
L is visibly larger. Every preview has a consistent height for its selected size.
Kanban cards keep content-driven height and never shrink to fit a short window.
Each column owns Y scrolling below its header. Long labels wrap, and footer/control
content remains reachable. Short/narrow windows scroll instead of compressing cards.

Manual workflow (Inbox/In progress/Review/Done) is dashboard metadata. Drag/drop and
an accessible menu update only this metadata. Preserve lane scrolling and support
moves into scrolled lanes. A manual move never sends a terminal command or changes
observed activity. Persist production manual state in the appropriate repository
storage, separate from observed process/task state.

Observed waiting for input, task completed, idle, no agent and unknown remain
distinct. Show confidence, freshness and evidence where available. Stale or missing
evidence remains visibly uncertain. Never infer task completion from idle or stopped
output. Preserve existing production distinctions and authorization limitations.

## Terminal detail, recent output and input

Detail retains the accepted reading-first terminal layout, pane navigation and an
independent full-window Expand/Restore control. Geometry changes keep the same
editor/terminal state, selection, draft, scroll position and input visibility.
Escape must respect IME and editor ownership before restoring or closing detail.

Keep a bounded recent-output window, represented by a 500-line cap in the prototype.
Show the cap and current/latest position honestly. Reading older output pauses
following; Latest jumps down and resumes. Eviction preserves the retained reading
anchor. Do not imply infinite history; deep history belongs in the actual terminal.
Implement production capture only within the existing approved access boundary.

Input starts hidden and disabled. One prominent Terminal input control opens the
panel and deliberately enables input for the named current connection/pane. No
separate redundant checkbox. Closing revokes input and keeps the draft. Retarget,
disconnect and reconnect revoke input and require explicit reopening. Viewing has
no hidden keyboard capture. Target, mode and active state are obvious. Keep resize
via pointer/touch and keyboard; clamp or scroll controls so nothing is clipped.

Compose and Direct input have distinct behavior. Compose uses actual CodeMirror 6
and the real Vim extension, not a styled textarea. Enter and modified Enter write/
edit without unintended submission; Send is the explicit draft-submit action.
Special-key controls are explicit terminal actions. Direct mode preserves terminal
key and IME-commit semantics; Vim settings do not apply to Direct input.

Compose Enlarge editor / Restore changes the existing editor in place and is
separate from expanding terminal detail. Keep document, selection, undo, focus
policy and editor scroll; do not create a second editor or duplicate listeners.
In enlarged mode, peers are inert and controls remain reachable. Vim Escape,
including Normal mode, must not unexpectedly close the composer or inspector.
IME conversion owns Escape. Retarget during compose IME revokes input immediately
and resolves composition before changing pane editor state. Late input must never
be delivered to a new target. Test interrupted and repeated interactions.

Within a session, maintain separate draft/editor state for each pane. The prototype
keeps this only while its detail frame exists and discards drafts on session switch,
close or reload. Production persistence/lifetime must be deliberately documented;
do not mistake this throwaway lifecycle for a production storage requirement.

## Settings and themes

Provide accessible editor/appearance settings without requiring terminal-input
activation. Settings include Vim, line wrapping, line numbers, editor text size,
and System/Light/Dark. Apply commits; Cancel/X/Escape discard pending edits;
reopening starts from the saved values. The accepted prototype does not preview
unapplied values or reload the page. It adapts zudo-text rather than copying its
Save & Reload behavior. Keep current pane/draft/selection/undo across Apply.

Persist the selected theme preference separately from resolved appearance. System
follows OS preference changes; explicit Light/Dark stays pinned. Set the effective
mode before first paint. Reconfigure CodeMirror's dark facet in place alongside
CSS tokens; recolor captured output/editor without replacing their state.

Use palette → semantic roles → component-local aliases. Semantic roles include
canvas, surfaces, text, muted text, borders, hover, active, selection, cursor/focus
and status colors. Light/dark change mappings, not component raw colors. Follow the
restrained neutral/warm reference direction. State must be readable without color.
All functional controls retain useful icons, readable labels and accessible names.
The visible brand/title remains exactly `zudo-agent-dashboard`.

## Evidence and verification boundary

The original prototype's real CodeMirror/Vim bundle executed under JSDOM with
explicit geometry, dialog and OS-media shims. Tested view identity, writing-only
Enter, Vim mode/Escape ownership, undo/selection retention, settings Apply/Cancel/
reopen, System resolution, separate pane drafts, activation/revocation, repeated
cycles, simulated IME and recent-output cap/scroll semantics passed. Dashboard
model/DOM simulations cover tree, filters, manual moves, scrolled lanes, full-window
detail and mock connection changes. Seventy semantic contrast pairs passed numeric
relative-luminance thresholds. These are not rendered-page accessibility results.

Native browser pixels, short/narrow layout, real mouse/touch resize/drag, physical
IME, browser selection/scroll behavior and screen readers were not verified for
round08. Production implementation requires real browser coverage at desktop,
mobile and short-window sizes, dark/light modes, many cards per lane, long labels,
settings interruption/reopen, focus trapping, editor history/IME and repeated use.
No live terminal transport or production permissions were tested by the prototype.

Actual reference paths and immutable source revisions are in references.md. Keep
accepted behavior while using the production repository's components, storage,
tokens and security architecture. Acceptance does not activate real access or
authorize public exposure of captured terminal text.
