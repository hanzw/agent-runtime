---
name: evolve-skills
description: >
  Audit and evolve a native Codex or Claude Skill portfolio by locating
  canonical sources, updating upstream-managed Skills, finding true duplicates,
  evaluating uncertain value, and removing capabilities that no longer earn
  their context or maintenance cost. Use for cross-project Skill cleanup,
  consolidation, upgrade, deduplication, lifecycle review, or when a setup keeps
  installing Skills without retiring obsolete ones.
---

# Evolve Skills

Improve the native Skill layer without creating a registry, framework, or
second capability abstraction.

## Invariants

- Treat native Codex/Claude discovery as the only capability inventory.
- Keep one canonical source for each capability.
- Prefer upstream GitHub packages over copied Skill bodies.
- Preserve genuinely project-specific Skills in their repositories.
- Read a Skill before changing it; update or remove stale instructions instead
  of appending contradictions.
- Separate reusable capability from task state, memory, and authorization.
- Never collect raw prompts, secrets, or production telemetry for lifecycle
  decisions.

## Workflow

1. Inventory installed Skills and their sources:

   ```bash
   npx skills@latest list --global
   ```

2. Group only genuine overlaps by claimed outcome and trigger. Similar names do
   not prove duplication.
3. For each overlap, identify the canonical source in this order:
   project-specific customization, user-global reusable Skill, versioned
   upstream plugin.
4. Update upstream-managed candidates before comparing them:

   ```bash
   npx skills@latest update <skill-name> --global --yes
   ```

5. Remove byte-identical copies, stale wrappers, broken references, and obsolete
   aliases when one canonical source already covers the same trigger and
   workflow.
6. When value remains uncertain, invoke `skill-governance` for a controlled
   baseline-versus-Skill Promptfoo ablation. Do not recreate its evaluation
   procedure here.
7. Apply the smallest supported lifecycle change through the native manager:

   ```bash
   npx skills@latest remove <skill-name> \
     --global --agent codex claude-code --yes
   ```

8. Re-run native discovery. Confirm the intended Skill is present exactly once,
   removed aliases are absent, and project-local Skills were preserved.

## Layer Boundary

```text
ADK or application framework -> builds and runs agent applications
Native Agent Skills          -> reusable procedures and domain capability
Buildomator or HANDOFF        -> current long-task state
ReMe                          -> bounded durable history
Policy Hooks                  -> side-effect authorization
```

Do not represent the support runtime, memory service, or task orchestrator as a
Skill capability. They may support Skill use, but they do not replace the native
Skill layer.

## Stop Conditions

Stop after native discovery is unambiguous and every change has a source,
reason, verification, and rollback path. Do not add dashboards, shadow
registries, universal scoring, or another orchestration framework.

## Report

Return the before/after inventory, canonical sources, updated and removed
Skills, evidence for each decision, discovery validation, preserved
project-local capabilities, and exact reinstall commands.
