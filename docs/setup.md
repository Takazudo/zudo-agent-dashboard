# Guided project-local setup

The repository includes Codex and Claude Code setup skills backed by the same
deterministic Python implementation. Setup is optional and never happens merely
by opening the repository or running the dashboard.

## Find the skill

Inside this dashboard checkout, Codex discovers
`.agents/skills/zudo-dashboard-setup/SKILL.md`; invoke `$zudo-dashboard-setup`.
Claude discovers `.claude/skills/zudo-dashboard-setup/SKILL.md`; invoke
`/zudo-dashboard-setup`.

If the dashboard is cloned **outside the project being monitored**, launching
Codex in that project does not automatically discover this clone's skills.
Tell the agent explicitly:

> Read and use the setup skill at `/absolute/dashboard/.agents/skills/zudo-dashboard-setup/SKILL.md` to preview setup for `/absolute/my-project`. Do not apply until I approve the preview.

Use the `.claude/skills/…` path for Claude, or start a **new** Claude session with
`claude --add-dir /absolute/dashboard` and invoke `/zudo-dashboard-setup`.
These paths remain anchored to the dashboard clone even when your working
directory is another repository. No global skill installation is necessary.
Do not copy only the skill folder: its relative helper links require the clone.

The skills are shipped in the source checkout/source distribution. The Python
wheel supplies `zudo-agent-setup` and the hook runner; it does not install skills
into your agents' discovery directories. Keeping the clone and using the commands
below avoids needing any package installation at all.

## Supported environment

Run setup in Linux, Ubuntu WSL or macOS with Python 3.11+ and tmux 3.4–3.x. Doctor
reports the OS, WSL flag, runtime versions and whether they pass compatibility
checks. Set `--claude-bin`, `--codex-bin`, or `--tmux-bin` to an explicit binary
when it is not on PATH. Only `--version` / `tmux -V` are invoked; setup does not
capture panes, start agents or modify running sessions.

The conservative setup compatibility ranges are Claude Code **2.1.288–2.1.x**
and Codex **0.159.3–0.159.x**, based on the installed versions inspected during
implementation. These are tested setup ranges, **not** claims that earlier
versions lack hooks. Unknown output, prereleases, missing tools and other
version ranges stop setup for review rather than silently assuming compatibility.

Native macOS uses libproc metadata instead of Linux `/proc`; its native smoke
test runs in macOS CI. Native Windows remains unsupported; use WSL. This does
not install WSL, tmux, Python, agents, services, credentials or firewall rules.

Each machine collects locally. For shared monitoring, follow the
[multi-device guide](multiple-devices.md): explicit hub registration and optional
`--hub-url`, `--stream`, `--token-file`, `--ca-file` references are previewed before
apply. Token provisioning, TLS/private transport and real-device pairing require
separate approval. Preview never reads a token or contacts another device.
`hub-plan` previews a user-authored registration candidate, with owned rollback.
Cloud tasks remain import-only. Installing a hook alone establishes no connection.

## Preview first

From anywhere, inspect the environment:

```sh
python3 /absolute/dashboard/scripts/setup.py doctor
```

Then create a local preview, choosing the target checkout and labels explicitly:

```sh
python3 /absolute/dashboard/scripts/setup.py plan \
  --project '/absolute/path/my project' \
  --project-id my-project \
  --repository github.com/example/my-project \
  --machine my-laptop \
  --provider both \
  --out /absolute/private/setup-plan.json
```

Use `--provider claude` or `--provider codex` for just one agent. The target must
be an existing Git checkout root, including a worktree. No repository origin
or hostname is guessed. Paths containing spaces are shell-quoted correctly.

Default dashboard config: `$XDG_STATE_HOME/zudo-agent/config.json` (falling back
to `~/.local/state/zudo-agent/config.json`). Default database: the adjacent
`observations.sqlite3`. Explicit `--config` and `--db` overrides are available.
The same existing machine config can accumulate several registered projects.
Repeat plan/apply per project; use a new preview filename and keep each receipt.

Preview creates **only** its private plan artifact (and uses disposable temp
files to validate config). It does not write dashboard settings, agent hooks
or the database. The printed summary lists changed paths, owned additions,
detected versions and an approval SHA. Existing commands/settings are preserved
and not printed. The full private plan includes before/after file contents;
inspect locally if needed, and never commit/share plans or receipts because
existing settings can contain sensitive values. New files have mode `0600`.

Review the preview with the user. The skill requires explicit approval of that
concrete result before the separate apply command. The token binds the exact
plan bytes semantically; it is not proof that a person actually consented.
Plans are trusted local artifacts, not an interchange format: do not apply
downloaded or untrusted plans.

## Apply the approved preview

```sh
python3 /absolute/dashboard/scripts/setup.py apply \
  --plan /absolute/private/setup-plan.json \
  --approve THE_SHA_FROM_THE_REVIEWED_PREVIEW
```

This merges the configured project and adds only observation handlers to:

- Claude: `<project>/.claude/settings.local.json`
- Codex: `<project>/.codex/hooks.json`

Other settings, hooks, matchers, event ordering and command values are retained.
Existing files are formatted as JSON when changed; rollback can restore their
exact original bytes if nothing was edited afterward. The handler command uses
an absolute Python executable, runner, config and database path. Keep that
Python and dashboard clone in place. Relocation requires reviewing/removing the
old installation before creating a new plan.

An ownership marker in each command distinguishes the installed handler.
Identical repeated plans add nothing; edited or duplicated owned handlers are
conflicts, not silently replaced. Malformed or duplicate-key JSON, symlinks,
hardlinks, changed files, disabled project hooks, and untested versions stop
setup without intentionally overwriting them. Project Codex TOML is read for
disable flags and duplicate runner references, never rewritten. Global/admin
configuration and trust stores are not changed; they can still disable hooks.
Recognizable older dashboard hooks without an ownership marker require manual
review before setup; they are neither adopted nor duplicated automatically.

Apply rechecks file contents and tool versions under a setup lock. Each file
replacement is atomic; the entire multi-file setup is **not** a filesystem
transaction. A private receipt is saved **before** writes so interrupted setup
can be recovered with rollback. Rechecks detect observed concurrent edits, but
cannot lock unrelated editors; do not edit target settings while applying.
The receipt path is printed; keep it. A no-change replay preserves the original
receipt's ownership instead of creating a claim over preexisting hooks.

Codex still requires its normal project/hook review and trust. Claude's project
trust and organization policies also apply. No running agent is restarted or
sent input. Only your agent's own reviewed lifecycle events establish real
delivery after setup; existing sessions may require normal user-led reload.

## Verify honestly, then start collection

```sh
python3 /absolute/dashboard/scripts/setup.py verify-synthetic --provider claude
python3 /absolute/dashboard/scripts/setup.py verify-synthetic --provider codex
```

These invoke **only the dashboard's own runner** with a fake Stop event, using
a temporary synthetic project/config/database that are deleted afterward.
Success says `mode: synthetic-only`, `actual_agent_delivery: not-verified`, and
`live_database_touched: false`. They never execute existing user hooks, load
live hook settings, insert sample events into the live dashboard, or claim an
agent delivered anything.

Start the real local collector separately (from the dashboard clone):

```sh
python3 -m zudo_agent \
  --config "$HOME/.local/state/zudo-agent/config.json" serve
# If you use XDG_STATE_HOME or --config/--db overrides, use those same paths here.
```

Open `http://127.0.0.1:8765`. `serve` automatically collects tmux metadata; no
separate watch process is required. The command above is a foreground process,
not an installed service. Setup itself does not launch a server. For a UI demo
instead, `python3 -m zudo_agent serve --sample` remains completely separate.

## Uninstall / interrupted-setup recovery

Use the **original setup receipt** printed by apply:

```sh
python3 /absolute/dashboard/scripts/setup.py uninstall-plan \
  --receipt /absolute/private/ORIGINAL.receipt.json \
  --out /absolute/private/uninstall-plan.json
# Review removals and retained conflicts, then approve this new SHA:
python3 /absolute/dashboard/scripts/setup.py apply \
  --plan /absolute/private/uninstall-plan.json \
  --approve THE_UNINSTALL_PREVIEW_SHA
```

`rollback-plan` is an alias for the same preview operation. Files that still
match the installation exactly are restored byte-for-byte or deleted if this
setup created them. If edited later, rollback removes only exact owned handlers
and roots. Additional hooks—even added to the same group—and unrelated settings
survive. Modified/duplicated owned handlers, changed project entries, or malformed
files are retained with conflicts reported for manual review. Rollback does not
delete observation history, skills in the source clone, receipts or directories.
An already absent owned addition is a no-op. Never restore a whole backup over
later user edits.
If setup changed transport and the shared configuration was edited later,
rollback retains that config for review rather than disconnecting subsequently
configured projects. A later-edited hub registry is likewise retained in full.
If an edited hook must be retained, rollback also keeps its dashboard config
until that conflict is resolved, so it does not knowingly leave a retained
handler referencing a removed configuration.

## Maintainer checks and sources

`bash scripts/check.sh` covers setup fixtures as well as the collector. All setup
tests use temporary repositories/configs and synthetic binaries; no user hooks
are installed. Skill validation runs against both entrypoints and their helper
links. Build a wheel with `python3 -m pip wheel --no-deps --wheel-dir dist .`.

The integration follows the current official references checked on 2026-10-03:
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude skills](https://code.claude.com/docs/en/skills),
[Codex hooks](https://learn.chatgpt.com/docs/hooks), and
[Codex skills](https://learn.chatgpt.com/docs/build-skills).
Those documents describe the agent-side facilities; successful setup or a
synthetic check alone does not prove those facilities delivered a live event.

For multiple local dashboards, use one shared hook/collector database and one
collector. The second dashboard supports `serve --no-collect`. See
[shared database and activity diagnostics](shared-database.md) before aligning
existing split databases; preserve workflow metadata and history first.
