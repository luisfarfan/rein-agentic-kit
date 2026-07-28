---
name: run
description: "Execute exactly ONE ready task of the plan, verify it, and only then tick its checkbox. One task per invocation, with hard attempt limits. Use when the user asks to implement or run the next task, or invokes /rein:run."
license: MIT
---

# /rein:run

Thin orchestration that executes **one** task and records evidence. Selection, ordering and
closure live in the `rein` CLI, not in your judgement. **One task per invocation. No `--all`.**

Usage: `/rein:run [change-name]`

Resolve the CLI once and reuse it:

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1)
```

## Steps

1. **Ask the gate, do not choose yourself**: `"$R" next .` — it returns
   `{ready, taskId, humanReview, verification, reason}` and exits non-zero when nothing is
   claimable. If `ready` is false, report `reason` and **stop**.
2. If `humanReview` is true, **stop before claiming it** and tell the user this task needs
   their judgement.
3. Show the selected task and its acceptance criteria (`"$R" tasks .`). Load **only** what
   that task needs — the criteria, the relevant design notes, and the files it touches. Read
   symbols and regions, never whole files speculatively: every file you pull in is re-read on
   every later turn.
4. State a brief implementation plan, then implement **strictly within scope**.
5. Run the task's own `Verification` command, then the project's configured checks
   (`"$R" detect .` → `commands.lint`, `commands.typecheck`). Keep output small.
6. Compare the result against the acceptance criteria, one by one. A criterion you cannot
   demonstrate is not met.
7. Tick the checkbox **only if** all of: implementation complete, verification passes,
   configured checks pass, and — when `humanReview` is true — a human approved:
   `"$R" close <taskId>`. Never hand-edit the plan.
8. Report what landed and **stop**.

## Limits (per invocation)

- **Max 3 implementation attempts**, **max 5 failed commands**, **max 1 task**.
- No self-approval: never close a task because the implementation "seems" done.
- If the project's `verifyPolicy.mode` is `rendered`, a passing test suite alone does not
  satisfy step 6 — the observed render is the evidence. If it is `plan-only`, never run the
  forbidden operations as verification.

## On failure

Do not tick the checkbox. Leave the task open, report the failing commands verbatim and what
needs fixing, and **stop — do not loop**. Retrying past the limits is how a bounded step turns
into the 200-turn agent this whole flow exists to prevent.
