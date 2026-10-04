# Shared observations and accurate activity

The collector, provider hooks and local dashboard must use the same absolute
`--config` and `--db` paths. A console policy controls authorization; it does not
select a separate database. Run one metadata collector per machine/tmux server.
A second dashboard can use the same database with `serve --no-collect`; this
suppresses its collection/forwarding thread, while allowing snapshot reads,
manual workflow metadata and separately authorized pane discovery. It does not
disable the existing collector or provider hooks. Defaults remain unchanged.

For example, after local paths have been verified, retain the existing collector
and add `--no-collect` to the second dashboard's existing `serve` command. Point
its global `--db` argument to the hook-written database. Keep its existing port,
console policy and access boundaries. No tmux or agent restart is required.

A fresh discovery heartbeat proves an agent process was seen, not that it is
working. Discovery-only runs display **Agent detected · activity unavailable**
and **No lifecycle events for this run**. Lifecycle events must match project,
machine and run identity. Check project hook trust/enablement, provider support,
absolute config/database paths and whether the hook can resolve the same agent
process ancestry. Do not infer lifecycle state from terminal text or generate
fake hook events in a live agent. `Stop` means idle, not task completed.

The card summarizes all authorized panes, with waiting, error, working, unknown,
idle, completed, then no-agent precedence. It also lists the component states
and flags stale observations. The inspector describes its selected pane. Last
seen freshness and lifecycle state freshness remain separate. Missing confidence
is not a confidence measurement and is no longer displayed as one.

Preview authentication is explicit. Until it succeeds, pane mapping is unknown,
not zero panes. Successful discovery with no match means no **authorized local**
pane matched; check policy project scope and refresh previews. Failed discovery
is reported separately and never presented as a successful empty inventory.
After authentication, home workflow refreshes update authorized pane mappings,
so new panes become discoverable. While an inspector is open, it retains its
selected mapping and the console revalidates that target; home discovery resumes
after closing the inspector. Auth failure clears cached pane mappings and previews.
These operations never authorize or replay terminal input.

## Existing split databases: compatibility contract for a local updater

This repair changes no SQLite tables, keys or event formats. A live migration is
a separate local maintenance action. Inspect both databases with read-only SQLite
connections first; constructing `Store` can create/migrate tables. API-visible
Inbox items do not prove the absence of stored workflow rows or orphan aliases.

Before switching paths, quiesce the old console's workflow writers and collector,
then make uniquely named SQLite online backups of **both** databases. Use the
[SQLite backup API](https://www.sqlite.org/backup.html), not a plain copy of only
the main file: committed WAL contents belong to the database snapshot. Keep the
original split database and backups. Avoid printing rows, credentials or local
paths into shared reports.

The updater must inspect actual `sqlite_master`/`PRAGMA table_info` schemas and
abort on unknown/incompatible layouts. Current local tables are:

| Table | Key | Fields to preserve |
| --- | --- | --- |
| events | id | project, run, machine, source, kind, at |
| workflow | id | lane, revision, edited |
| workflow_alias | alias | canonical |
| presence | run | machine, seen, present |
| collectors | machine | checked, connected, panes, unmatched |

`outbox` may also exist when forwarding is configured; leave the live collector's
transport state unchanged. Do not copy or merge transport configuration/state
from another collector. Do not migrate a hub database using this local contract.

Merge the old console snapshot's event history, **all** workflow rows (including
ones absent from the API), and **all** aliases into the hook-written destination
in one [write transaction](https://www.sqlite.org/lang_transaction.html), using
`BEGIN IMMEDIATE` before checking conflicts. Preserve original revisions and edit
timestamps exactly. An identical duplicate key is harmless; any duplicate key
with different values is an ambiguous conflict and must abort the whole merge.
Never choose a winner by row order, overwrite with `INSERT OR REPLACE`, reconcile
aliases, or increment revisions as part of the migration. Detect conflicting
alias chains/cycles or incompatible canonical identities and abort for review.

Keep the destination's live `presence` and `collectors` rows authoritative; these
are transient observations, not lifecycle history. Their old values remain in
the retained source/backup. Verify the full event/workflow/alias union and SQLite
integrity before committing. On contention, roll back and retry the complete
transaction against current destination rows; do not overwrite new hook writes.
Once verified, restart only the second dashboard with the shared path and
`--no-collect`. Verify fresh discovery and genuine lifecycle evidence separately.
A project with only discovery in both sources must remain activity-unavailable.

Rollback means reverting the console process configuration while retaining every
new live hook write. **Never restore an old backup over the hook-written live
database.** Resolving conflicts or reversing imported rows requires a separately
reviewed transaction; a whole-file restore is not a safe rollback.

## Background preview refresh

Home refresh serializes the complete snapshot, authorized workflow discovery and
bounded capture batch. Timer ticks and repeated refresh clicks join that batch;
they cannot start a competing discovery while capture is pending. There is no
page navigation or automatic terminal input in this cycle.

A temporary busy/unavailable discovery or HTTP failure retains the last known
session grouping. Captures are explicitly marked **STALE · last capture · retrying**;
inspection and workflow moves are disabled until discovery succeeds. Cached output
expires after 60 seconds without successful capture and exists only in page
memory. A denied authentication response or a successful discovery that removes
or replaces a target immediately removes its capture, even if text is selected.

Cards are updated in place. Unchanged nodes, focused controls, lane scroll and
selected capture text survive background updates. Selected capture text is held
for copying and marked **Selected capture · updates paused**; revocation and
expiry still take precedence. No stale capture is relabeled as a new target.
