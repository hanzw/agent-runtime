---
name: skill-governance
description: >
  Evaluate whether an Agent Skill should be kept, updated, or removed using a
  minimal Promptfoo ablation against real regression cases. Use when auditing
  Skill quality, testing a Skill change, investigating duplicate capabilities,
  or deciding whether an installed Skill still earns its maintenance cost.
---

# Skill Governance

Produce evidence for one keep, update, or remove decision without building a
registry or observability platform.

## Rules

- Treat native Codex/Claude Skill discovery as the only capability source.
- Use the GitHub-hosted `promptfoo-evals` and `promptfoo-provider-setup` Skills.
- Compare the same model and task with and without exactly one target Skill.
- Use Promptfoo built-in assertions and JSON Schema before custom graders.
- Keep ReMe as durable memory and Buildomator/HANDOFF as task state; neither is
  an eval registry.
- Never collect prompts, secrets, or production telemetry merely to improve an
  eval.
- Do not delete a Skill from one stochastic result. Require a reproduced
  regression, a true duplicate, or repeated absence of value on relevant cases.

## Workflow

1. State one decision: keep, update, or remove one named Skill.
2. Locate its canonical installed source with `npx skills@latest list --global`.
3. Update upstream-managed Skills before testing:

   ```bash
   npx skills@latest update <skill-name> --global --yes
   ```

4. Copy `assets/eval/` from this Skill into the target repository. Preserve two
   isolated fixtures:
   - `baseline`: no target Skill;
   - `current`: one snapshot of the target Skill under `.agents/skills/`.
5. Replace the example cases only with real tasks whose expected decision can
   be expressed using Promptfoo built-in assertions. Add a case only when it can
   change the keep/update/remove decision.
6. Validate, then run without cache or sharing:

   ```bash
   npx promptfoo@0.121.20 validate config -c <eval-dir>/promptfooconfig.yaml
   npx promptfoo@0.121.20 eval -c <eval-dir>/promptfooconfig.yaml \
     -o /tmp/skill-governance.json --no-cache --no-share
   ```

7. Inspect the JSON result, including errors, token usage, latency, and
   `response.metadata.skillCalls`. Confirm the target Skill was actually loaded
   in the treatment arm and absent in the baseline.
8. Decide:
   - **Keep** when it prevents a relevant regression without critical harm.
   - **Update** when it triggers but repeatedly fails a well-specified case.
   - **Remove** when it is a true duplicate, causes a reproduced regression, or
     adds no value across the tasks it claims to cover.
9. Apply lifecycle changes only through the upstream manager:

   ```bash
   npx skills@latest remove <skill-name> --global --agent codex claude-code --yes
   ```

## Stop Conditions

Stop when the decision is supported. Do not add dashboards, trace backends,
shadow registries, generalized scoring frameworks, or synthetic case volume.

## Report

Return the target Skill and version, cases run, Skill-call evidence, pass/fail
comparison, observed cost/latency, decision, files changed, and rollback command.
Clearly separate a single-run observation from a reproduced result.
