# Skill Governance

Minimal, evidence-based keep/update/remove decisions for Codex and Claude
Skills. It uses the open-source [Promptfoo](https://github.com/promptfoo/promptfoo)
runner and native Agent Skill discovery. It does not create a capability
registry, production telemetry pipeline, or observability stack.

Skill source: [`skills/skill-governance`](skills/skill-governance/).

## Install

Install the two official Promptfoo Skills and this Skill from GitHub:

```bash
npx skills@latest add promptfoo/promptfoo --skill promptfoo-evals \
  --global --agent codex claude-code --yes
npx skills@latest add promptfoo/promptfoo --skill promptfoo-provider-setup \
  --global --agent codex claude-code --yes
npx skills@latest add hanzw/skill-governance --skill skill-governance \
  --global --agent codex claude-code --yes
```

Each dependency has one job:

| Source | Role | Adjust when |
| --- | --- | --- |
| [`skills`](https://www.npmjs.com/package/skills) | Native Skill installer and updater | Codex or Claude changes its Skill discovery format |
| [`promptfoo-evals`](https://github.com/promptfoo/promptfoo) | Creates and runs repeatable eval suites | The regression cases or assertions need to change |
| [`promptfoo-provider-setup`](https://github.com/promptfoo/promptfoo) | Connects Promptfoo to Codex, Claude, APIs, or local providers | Authentication or the evaluated runtime changes |
| `skill-governance` | Converts one controlled ablation into a keep/update/remove decision | The lifecycle policy itself needs to change |

Nothing is vendored: install and update commands pull the current Skills from
their canonical GitHub sources. Pin a Git revision in your own deployment only
when reproducibility matters more than automatic updates.

Requirements: Node.js supported by Promptfoo and an authenticated Codex CLI or
an appropriate model API key. No API key is required when Promptfoo's Codex SDK
provider can reuse an existing Codex login.

## Use

Ask Codex or Claude:

```text
Use $skill-governance to determine whether <skill-name> should be kept,
updated, or removed.
```

The installed Skill contains a tested two-arm Promptfoo template under
`assets/eval/`. The agent copies and adapts it for one target Skill and real
regression cases. Runs are local, read-only, uncached, and unshared by default.

## Architecture

```text
Native Skill discovery  -> canonical capability source
ReMe                    -> durable history
Buildomator/HANDOFF      -> current long-task state
Promptfoo               -> controlled Skill ablation
```

These responsibilities do not overlap. Do not add a second registry or use
memory/task-state tools as evaluators.

Buildomator is the new name for GSD 4.x. Both `/bm:` and `/gsd:` work during
the 4.x transition; use `/bm:` for new instructions because `/gsd:` retires in
v5.0.

## Maintain

```bash
npx skills@latest update promptfoo-evals promptfoo-provider-setup \
  skill-governance --global --yes
```

Remove cleanly if it stops earning its place:

```bash
npx skills@latest remove skill-governance --global \
  --agent codex claude-code --yes
```

Add evaluation cases only for observed regressions that could change a Skill
decision. Do not commit Promptfoo result files or authentication state.
