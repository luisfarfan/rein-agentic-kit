---
name: plan
description: "Plan a change into verifiable tasks with acceptance criteria, dependencies and a bounded verification command each — dry-run and explicit confirmation before anything is written. Never implements. Use when the user asks to plan or break down work, or invokes /rein:plan."
license: MIT
---

# /rein:plan

Thin orchestration for the **planner** role. Turns a request into a task list `/rein:run` can
execute without re-deriving intent. **This skill never implements, never ticks a checkbox and
never invokes `/rein:run`.**

Usage: `/rein:plan <change-name> [description]`

```bash
R=$(command -v rein || ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1)
```

## Steps

1. Resolve the project so the plan lands where the loop will look for it and the verification
   commands are real: `"$R" context .` — note `plan.source`, `plan.path`,
   `config.commands` and `verifyPolicy.mode`.
2. Understand the request before decomposing it. Use bounded search and precise reads, not
   whole files. If scope, an interface or a trade-off is genuinely ambiguous, **ask now** — a
   wrong assumption here is paid for by every implementation step after it.
3. Draft the plan. It opens with a **short header** and then the tasks:

   ```markdown
   # Change: add-widget

   ## Why
   One or two sentences: what problem this solves, and what happens if it is not
   done. This is what the reviewer judges the work against.

   ## Scope
   - In: the parser and its tests
   - Out: the CLI surface — that is a separate change

   ## Decisions
   - D1 Constraints live in tasks.md — a separate file is an artifact nobody reads

   ---
   ```

   Then each task:

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

   **Why / Scope / Decisions are short on purpose.** `Out` and the decision titles
   travel in *every* agent's prompt and are re-read on every turn — with ten agents
   at fifty turns, a thousand extra tokens there costs half a million per run. Say
   the constraint, not the essay.

   **Why the header exists at all:** a plan whose criteria have nothing above them
   gives the reviewer nothing to check intent against. That is measured, not
   theoretical — it is how a criterion satisfied in letter, by a test whose fixture
   avoided the real case, survived six review rounds here.

   **Decisions are conditional.** Include the section only when the plan contains a
   choice an implementer could plausibly reverse without noticing. A two-task change
   usually has none, and inventing them is ceremony.

4. **Dry run.** Show the user the full draft: tasks in dependency order, each verification
   command, which tasks are gated on human review, and anything the plan assumes.
5. **Ask for explicit confirmation.** Never write the plan into the project without it.
6. On confirmation, write it to `plan.path`, then verify it parses the way the loop will read
   it: `"$R" tasks .` and `"$R" next .`. Report the resolved order.

## What makes a task good here

| Rule | Why |
|---|---|
| One **bounded** verification per task — one test file or id, never "the suite" | An implementation step that runs the whole suite dumps a huge output into a context re-read on every later turn |
| Acceptance criteria are **checkable**, not aspirational | The reviewer checks them literally; "works well" cannot be checked |
| A task fits in a few bounded steps | The cap is `maxTaskSteps`. A task that cannot finish inside it is really several tasks |
| `Depends on` is real | It drives execution order, and a failed dependency parks its dependents instead of cascading breakage |
| `Human review: true` when acceptance is a judgement only the user can give | The loop parks it rather than faking a verdict |
| Criteria name the **guarantee**, not a proxy for it | A criterion satisfiable by a test whose fixture dodges the real case will be satisfied that way. Say what must hold, not what must pass |

## Guardrails

- Do not implement. Do not tick a checkbox. Do not call `/rein:run` or `/rein:review`.
- Never write the plan without a dry run and explicit confirmation first.
- Do not invent verification commands the project cannot run — check them against
  `config.commands` first, and say which are missing rather than substituting one.
- If `"$R" tasks .` reports `unresolvableDeps`, there is a dependency cycle: fix it now rather
  than letting the loop run a best-effort order.
- If the request is too vague to produce checkable criteria, say so and ask — a plan that
  reads well and cannot be verified is worse than no plan.
