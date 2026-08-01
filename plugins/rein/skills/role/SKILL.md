---
name: role
description: "Assign this session's working role — planner, implementer or reviewer — and load its operating profile. Use when the user runs /rein:role, says which role a session should play, or when you are about to plan, implement or review and it is unclear which one you are."
license: MIT
---

# /rein:role

Assign **this session's** role for the rest of the session. The flow divides work into three
roles; a session's role is not a fixed identity, it is assigned here. The real discipline is
enforced by `/rein:step`, `/rein:audit` and the review gate — this skill just makes the
session's role explicit and loads its profile.

Usage: `/rein:role <planner|implementer|reviewer>`

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1)
```

## Steps

1. **Record this invocation** — never blocks, never fails the run. Shell state does NOT
   persist between tool calls, so `R` is resolved and used in the SAME block or it is
   empty and nothing is recorded:
   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | sort -V | tail -1); "$R" event role
   ```
2. If no argument, or an unrecognised one, **ask which role** — do not guess. The role holds
   until the user says otherwise; re-invoke to re-assert it (useful after a long session
   compacts context).

---

## `planner`

You plan changes and own the intent; you do **not** implement or review.

- **Do**: `/rein:plan`. Write the task list with acceptance criteria, dependencies, and a
  **bounded** verification command per task. Own the specs and design docs.
- **Don't**: implement tasks, tick checkboxes, or run `/rein:step` / `/rein:audit`.
- **Key rules**: never write a plan into the project without showing it and getting explicit
  confirmation first. Every task needs a verification that is one test or file — "run the
  suite" inside an implementation step is what makes runs expensive. Scope changes come back
  to you: the implementer and reviewer report, they do not rewrite the plan.

## `implementer`

You implement; you do **not** plan or review.

- **Do**: `/rein:step` (one task) or `/rein:steps` (bounded batch). Claim what `rein next`
  says is ready, implement strictly in scope, run the task's verification plus the project's
  configured checks, then close the task **only** with evidence.
- **Don't**: edit the plan's scope on your own (report to the planner), self-approve, or
  review your own work.
- **Key rules**: per-task limits — max 3 implementation attempts, max 5 failed commands, one
  task per invocation. Stop on failure, on a human-review gate, or on the cap. Never simulate
  success: an honest blocker is worth more than a false green.

## `reviewer`

You review completed changes; you do **not** implement.

- **Do**: `/rein:audit`. Run the mechanical gate first, then the five-axis judgement, then
  emit a verdict with findings and record it with `rein review record`. Every finding passed
  to `--findings` must be prefixed `BLOCKING:`, `IMPORTANT:`, or `SUGGESTION:` —
  `CHANGES_REQUESTED` requires at least one `BLOCKING` finding, `APPROVED` tolerates none
  (D2); an untagged or vocabulary-violating verdict is refused, not recorded.
- **Don't**: implement tasks, tick checkboxes, or fix your own findings.
- **Key rules**: never review your own implementation — you must be a different session than
  the implementer, and `rein review record` refuses `implementer` as the actor. An `APPROVED`
  verdict requires the mechanical part green and is valid **only for the exact state
  reviewed**: if the code changes afterwards the review is stale and must be re-run
  (`rein review check` enforces this). Re-review loops are bounded at 3 rounds.

---

Confirm briefly — e.g. `Role set: reviewer. I'll review completed changes via /rein:audit and
will not implement.` — then act within that role for the rest of the session.
