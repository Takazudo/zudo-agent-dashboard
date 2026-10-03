---
name: zudo-dashboard-setup
description: Preview, install, verify, or roll back project-local Zudo Agent Dashboard configuration and Claude Code observation hooks using the dashboard's tested setup scripts.
---

Use the shared [setup guide](../../../docs/setup.md) and
[setup script](../../../scripts/setup.py), resolved from this skill's actual
location in the dashboard clone, not from the monitored project's working directory.

For several devices, also read [private hub setup](../../../docs/multiple-devices.md).
Use explicit addresses/registration, never LAN scans. Each collector's optional
transport is part of its preview; `hub-plan` previews a hub registration candidate.
Do not provision credentials, change networks, pair real devices or start remote
listeners without separate action-time approval. Linux/WSL and macOS collectors
are supported; cloud stays import-only and the hub must remain online.

1. Run `doctor`. Confirm the target checkout, explicit repository identity,
   machine slug, and selected provider (`claude`, or `both` if requested).
2. Run `plan` with explicit absolute paths. Present its changed paths, added
   commands, environment limitations and approval SHA. A preview is not setup.
3. Ask the user to approve that concrete preview **before** `apply --approve`.
   A request to inspect or create a plan does not approve installation. If the
   exact preview was already approved in this session, proceed without re-asking.
4. Run `verify-synthetic` after applying. Report it as a temporary-DB pipeline
   check, never actual Claude hook delivery. Explain project trust/policy may
   still prevent hooks and show the explicit `--config PATH serve` invocation.

Use `rollback-plan` / `uninstall-plan` and the original receipt for removal;
review and approve that preview before applying. Retain reported conflicts.
Never hand-edit JSON, overwrite settings, or execute existing user hook commands
to verify setup. No global hooks, daemon installation or agent inputs.

For a clone outside the current project, read this skill by its absolute path
or start a new Claude session with `--add-dir /absolute/dashboard` and invoke
`/zudo-dashboard-setup`. Keep the clone in place because installed hook commands
reference it. Do not copy this skill folder alone: its helpers stay in the clone.
