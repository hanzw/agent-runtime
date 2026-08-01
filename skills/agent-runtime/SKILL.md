---
name: agent-runtime
description: >
  Install, audit, update, diagnose, or roll back the shared Codex and Claude
  global runtime in this repository. Use when managing lifecycle Hooks,
  deterministic side-effect policy, ReMe memory, runtime receipts, launchd
  services, or the boundary between native Skills, task state, and memory.
---

# Agent Runtime

Manage one reversible global runtime without creating a second Skill registry
or copying project rules into global configuration.

## Invariants

- Treat repository code and tests as the runtime source of truth.
- Treat native Codex/Claude discovery as the Skill source of truth.
- Keep ReMe for durable history and Buildomator/HANDOFF for current task state.
- Preserve project-local Skills, user logs, unrelated MCP servers, and unrelated
  instruction sections.
- Never store prompts, tool payloads, command text, secrets, or raw transcripts
  in runtime evidence.
- Require an exact backup path for rollback.

## Audit

1. Inspect `~/.agent-runtime/install-receipt.json`.
2. Resolve the receipt's release, backup, source revision, and dirty flag.
3. Inspect native Hook files and ReMe MCP entries without changing them.
4. Read `~/.agent-runtime/state/heartbeat.json`; do not infer health from process
   presence alone.
5. Run the repository tests before recommending an update:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

6. Report verified state separately from intended changes.

## Install or Update

1. Confirm the repository and branch are the intended source.
2. Preserve a dirty worktree; do not install from it unless the user explicitly
   accepts `source_dirty=true` provenance.
3. Run tests and inspect the pending diff.
4. Install from the checked-out source:

   ```bash
   python3.11 -m agent_runtime.installer install --source .
   ```

5. Read back the receipt, Hook files, ReMe health, and release path.
6. State that already-running agents discover new Skills on their next turn or
   session; do not claim an in-flight tool call was reconfigured.

Use `--no-launchd` only for synthetic validation or unsupported environments.

## Roll Back

1. Resolve the exact backup from the receipt or backup directory.
2. Verify its `manifest.json` exists.
3. Run:

   ```bash
   python3.11 -m agent_runtime.installer rollback \
     --backup <exact-backup-path>
   ```

4. Read back every restored managed file and service state.

## Scope

Change policy behavior in `agent_runtime/policy.py` together with focused tests.
Change memory bounds in `agent_runtime/memory.py` or `reme-minimal.yaml`. Change
installation targets only in `agent_runtime/installer.py`. Do not add a generic
configuration framework for a single policy change.

## Report

Return runtime revision, receipt, ReMe status, tests run, files changed, policy
effects, installation or rollback result, and the exact recovery command.
