---
name: review
description: "Independently review a completed change as a whole across correctness, readability, architecture, security and performance, with a mechanical gate first. PHASE 0 STUB. Use when the user asks for a review gate with /rein:review."
license: MIT
---

# /rein:review

The independent gate. Reviews the **complete change**, never a single task.

## Two rules that come from real failures

**No agent approves its own implementation.** A flow where the planner implemented
and verified its own work shipped defects to the user. The reviewer must not have
written the code it is judging.

**Review the change as a whole, not task by task.** Per-task review was tried and
was wrong: it multiplies rounds (3 rounds × 8 tasks = 24 reviewer invocations),
and no reviewer ever sees the change as a unit — which is exactly where
cross-task coherence defects live.

## Status: PHASE 0 STUB

Not implemented yet. Lands in phase 1 as part of the loop's review rounds.

## Contract (what phase 1 will do)

1. **Mechanical part first.** Run the project's resolved `test` / `lint` /
   `typecheck` from `flow.config.json`. Anything red means the verdict **cannot**
   be `APPROVED`, no matter how good the code reads.
2. **Judgement, five axes** over the full diff: correctness, readability,
   architecture, security, performance — plus coherence between tasks.
3. **Coverage check**: every acceptance criterion actually met; no checkbox marked
   without its criteria satisfied.
4. **Verdict**: `APPROVED` or `CHANGES_REQUESTED` with findings that are precise
   and actionable (file, what is wrong, what is missing) — the implementer fixes
   them without talking to you.
5. **Escalation.** If the *only* thing blocking approval is a judgement that only
   the human can make, return `needsHumanDecision` instead of
   `CHANGES_REQUESTED`. Another implementer round cannot resolve it, so spending
   one is waste.

Bounded at **3 rounds per change**. A reviewer that dies is retried once without
consuming a round.

## Integrity (non-negotiable, inherited from the origin project)

Real logic and real tests. Never weaken or skip a verification, never fake
success, never stub a real product step to force green, never commit failing
tests, never let a failure pass silently. An honest blocker is worth more than a
false green.

The lesson behind this: tests with fakes passed while the real output was broken.
If a task has a verification against reality, perform it — do not substitute a mock.
