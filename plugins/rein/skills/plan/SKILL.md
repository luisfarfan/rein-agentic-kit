---
name: plan
description: "Plan a change into verifiable tasks with acceptance criteria, dependencies and a bounded verification command each, written to this project's plan format (tasks.md or OpenSpec). Use when the user asks to plan, break down or spec work before implementing, or invokes /rein:plan."
license: MIT
---

# /rein:plan

Turns a change request into a task list `/rein:loop` can execute **without
re-deriving intent**. Plans only — never implements, never ticks a checkbox, never
invokes the loop.

## Steps

1. **Resolve the project** so the plan is written where the loop will look for it,
   and so verification commands are real:

   ```bash
   R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1); "$R" context .
   ```

   Note `plan.source` (`tasks-md` or `openspec`), `plan.path`, and
   `config.commands`. If `testOne` is missing, say so — every task needs a bounded
   verification, and without `testOne` they will fall back to the whole suite,
   which is what makes implementation steps expensive.

2. **Understand the request before decomposing it.** Read what the change actually
   touches, using the retrieval discipline the loop itself follows: bounded search
   and precise reads, not whole files. If anything material is ambiguous — scope,
   an interface, a trade-off — ask now. A wrong assumption here is paid for by
   every implementation step that follows.

3. **Write the tasks** to `plan.path` in this format:

   ```markdown
   - [ ] T001 Parse the config file
     - Type: implementation
     - Depends on: none
     - Human review: false
     - Verification: `python3 -m unittest tests.test_config`
     - Acceptance:
       - reads flow.config.json when present
       - falls back to autodetect otherwise
   ```

   Every field is optional except the checkbox line, but omitting `Verification`
   costs real tokens later. Ids must be `Txxx` and stable — the loop's ledger and
   `rein close` key off them.

4. **Verify the plan parses** the way the loop will read it, and show the user the
   resulting order:

   ```bash
   "$R" tasks .
   ```

   Check `pending` and `unresolvableDeps`. A non-empty `unresolvableDeps` means a
   dependency cycle: fix it now rather than letting the loop run a best-effort order.

## What makes a task good here

| Rule | Why |
|---|---|
| **One bounded verification per task** — one test file or id, never "run the suite" | An implementation step that runs the whole suite dumps a huge output into a context that is re-read every subsequent turn |
| **Acceptance criteria are checkable**, not aspirational | The reviewer checks them literally; "works well" cannot be checked |
| **A task fits in a few bounded steps** | The cap is `maxTaskSteps` (default 8). A task that cannot finish inside it is really several tasks |
| **`Depends on` is real** | It drives execution order, and a failed dependency parks its dependents instead of cascading breakage |
| **`Human review: true`** for anything whose acceptance is a judgement only the user can give | The loop parks it rather than faking a verdict |

## Guardrails

- Do not implement any task. Do not tick any checkbox. Do not call `/rein:loop`.
- Do not invent verification commands the project cannot run — check them against
  `config.commands` first.
- If the request is too vague to produce checkable acceptance criteria, say so and
  ask, rather than writing a plan that reads well and cannot be verified.
