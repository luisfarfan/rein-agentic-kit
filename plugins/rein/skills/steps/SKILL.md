---
name: steps
description: "Repeat /rein:step's per-task procedure in a bounded loop until a deterministic stop condition — nothing ready, a verification failure, a human-review gate, or the per-pass cap. Use when the user asks to work through several tasks in one go, or invokes /rein:steps."
license: MIT
---

# /rein:steps

Repeats the **per-task procedure of `/rein:step`** in a **bounded** loop, in one session, until
an explicit stop condition. It does not reimplement selection, verification or closure — every
iteration is a full `/rein:step` pass.

Usage: `/rein:steps [change-name] [max-tasks]`

`max-tasks` is the per-pass cap (default **3**). It bounds this invocation only. It is never
unlimited.

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1)
```

## Loop

1. **Record this invocation** — never blocks, never fails the run. Shell state does NOT
   persist between tool calls, so `R` is resolved and used in the SAME block or it is
   empty and nothing is recorded:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" event steps
   ```
2. Set `done = 0`, `cap = max-tasks or 3`.
3. **Check the deterministic gate before each iteration**: `"$R" next .`
4. **Stop now, without claiming anything**, if any stop condition below holds.
5. Otherwise run **one full iteration of `/rein:step`**, unabridged: select, load only what the
   task needs, plan, implement in scope, run its verification plus the configured checks,
   compare against acceptance, close only with evidence.
6. Increment `done`. Re-check the conditions. Otherwise go to 3.

## Stop conditions — deterministic, not model judgement

Each is read from a verifiable signal, **never** from your impression that "this is probably
enough". That distinction is the whole point of this skill: a loop that stops when the model
feels finished has no gate at all.

| Condition | Signal |
|---|---|
| Nothing ready | `rein next` exits non-zero / `ready: false` — report its `reason` |
| Verification failure | The task's verification or a configured check exits ≠ 0 |
| Human-review gate | `rein next` reports `humanReview: true` — stop **before** claiming it |
| Blocked dependencies | `rein next` reports `blockedBy` — nothing is claimable |
| Per-pass cap | `done >= cap` |

## On stop

- **Never** simulate success, and never continue as if the pass had finished when it has not.
- Report **which** condition stopped the loop and how many tasks completed this pass.
- State how to continue: re-invoke `/rein:steps`, or `/rein:step` for a single task. The
  next invocation re-reads the gate — **no session state is needed to resume**.
- Human-review stop: the gated task stays unclaimed and ready; the user decides.
- Verification failure: follow `/rein:step`'s own failure behaviour for that task — do not
  close it, report the failing commands, do not retry past the limits.

## Per-task contract preserved intact

This skill changes **nothing** about what `/rein:step` guarantees per task: implementation
strictly in scope, the task's verification plus configured checks, closing only with
sufficient evidence, the per-task limits (max 3 attempts, max 5 failed commands), and no
self-approval. The only thing automated here is relaunching between tasks.

Completing a pass is **not** approval. When the tasks are done, the change still has to go
through `/rein:audit`.
