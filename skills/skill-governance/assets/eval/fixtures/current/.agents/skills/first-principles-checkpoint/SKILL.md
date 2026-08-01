---
name: first-principles-checkpoint
description: >
  Periodically pull substantial work back to the user's real outcome and the
  smallest evidence-producing action. Use proactively for multi-step builds,
  plans, agent orchestration, repeated blockers, new abstractions, or whenever
  scope/process is growing; always use when the user says first principles,
  overengineered, too complex, too heavy, drifted, simplify, 回到最初, 太重了,
  走样了, or 第一性原理.
---

# First-Principles Checkpoint

Prevent the workflow from becoming the product.

## The four questions

Answer these from current evidence, not from the existing plan:

1. What concrete user outcome are we trying to produce now?
2. What facts must be true for that outcome to be trustworthy?
3. What is the smallest next action that produces or falsifies one of those facts?
4. What process, artifact, abstraction, or scope can be deleted or deferred?

## Trigger points

Run the four questions:

- before choosing a workflow or spawning agents;
- after every blocker or failed test;
- before adding a gate, abstraction, dependency, document, or phase;
- at the end of each implementation wave;
- before claiming completion;
- immediately when the user signals drift or excess complexity.

Do not use elapsed time or tool-call counts as the cadence. Trigger on decisions
where complexity can compound.

## Decision rule

Prefer the path with the fewest new concepts and artifacts that can still
produce trustworthy evidence for the outcome.

- Preserve safety constraints that protect money, credentials, private data, or
  irreversible state.
- Challenge process constraints that exist only because an earlier plan said so.
- Use one direct experiment before creating a framework.
- Extend an existing seam before creating a new one.
- If an artifact has no immediate consumer, defer it.
- If a step cannot change the decision or result, remove it.
- If the workflow is taking longer to explain than the next useful action, take
  the useful action and report the evidence.

## Visible checkpoint

When the checkpoint changes the course of work, emit one concise line:

`[first-principles] Outcome: ... | Known: ... | Next smallest proof: ... | Deferred: ...`

Do not turn the checkpoint into a new report, scoring system, methodology, or
approval ceremony. Its purpose is subtraction.

## Reset behavior

When the user says the work has drifted or become too heavy:

1. Pause ongoing work that would add more scope.
2. Restate the outcome in the user's language.
3. Keep only safety constraints tied to real harm.
4. Replace the accumulated plan with the smallest executable next step.
5. Resume only after showing the simplified direction.

## Completion check

Before finalizing, confirm:

- the user received the requested outcome, not merely a plan or infrastructure;
- every material claim has direct evidence;
- remaining work is either necessary, explicitly deferred, or removed;
- no new abstraction was introduced without at least two real consumers.
