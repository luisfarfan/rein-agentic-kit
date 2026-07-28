---
name: review
description: "Independently review a completed change as a whole — mechanical gate first, then correctness, readability, architecture, security and performance. Use when the user asks to review a change, audit a branch before merge, or invokes /rein:review."
license: MIT
---

# /rein:review

The independent gate. Reviews a **complete change**, never a single task.
`/rein:loop` runs this automatically; invoke it directly to review work done by
hand or by another tool.

## Two rules that come from real failures

**No agent approves its own implementation.** A flow where the planner implemented
and verified its own work shipped defects to the user. If you wrote the code under
review, say so and stop.

**Review the change as a whole.** Per-task review was tried and was wrong: it
multiplies rounds (3 rounds × 8 tasks = 24 reviewer invocations) and no reviewer
ever sees the change as a unit — which is exactly where cross-task coherence
defects live.

## Steps

1. **Resolve the project's real commands** — do not guess them:

   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1); "$R" detect .
   ```

2. **Mechanical gate first.** Run the resolved `test`, `lint` and `typecheck`.
   Report their literal output. **Anything red means the verdict cannot be
   APPROVED**, however good the code reads. If a slot is not configured, say it is
   absent rather than substituting one.

3. **Judgement over the full diff** (`git diff <base>...<branch>`), on five axes:
   correctness, readability, architecture, security, performance. Look at the
   change as a unit — coherence defects *between* tasks are the ones nobody else
   will see.

4. **Coverage.** Check each task in the plan genuinely meets its acceptance
   criteria, and that no checkbox was ticked without them being met.

5. **Verdict.** `APPROVED`, or `CHANGES_REQUESTED` with findings precise enough to
   act on without asking you anything: file, what is wrong, what is missing.

6. **Escalate instead of looping** when the *only* thing blocking approval is a
   judgement solely the user can give — a supervised task whose acceptance is "the
   owner confirms". An implementer cannot resolve that, so another round is wasted
   time. Name which task and what must be judged.

## Guardrails

- Do not modify product code. Doing so makes your own review stale.
- Be demanding. Approving something broken is worse than asking for another round.
- Never report a green gate you did not actually run.
