# Pane viewer handoff

The cloud implementation supersedes the unfinished sender checkpoint `7db972d`.
This feature branch remains dependent on PR #2 at `3192ebe` and is intended for a
**draft PR only**. No merge or live activation is authorized by this handoff.

Implemented: opt-in authenticated local pane snapshots integrated with dashboard
run selection, bounded capture, generation revalidation, ephemeral UI,
desktop/mobile layout, security/fixture tests, and operator documentation.

Safely narrowed: the unsafe tmux text sender was removed. Send is unavailable
and rejected server-side; policies requesting input are rejected at startup.
This is not a full interactive terminal. The remaining implementation blocker is
atomic run-bound input delivery that cannot fall through to a shell after the
agent exits. No amount of pre/post metadata checking solves that send race.

See [operator documentation](docs/pane-console.md) for the exact proposed future
read-only activation steps, limits, and the separate control implementation
requirement. No real credentials, policy, pane capture/input, production route,
network configuration, or persistent service were changed. All runtime evidence
comes from disposable synthetic fixtures; source was independently authored.
