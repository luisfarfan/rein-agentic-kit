---
name: plan
description: "Plan a change into verifiable tasks with acceptance criteria, dependencies and a bounded verification command each, in this project's plan format (OpenSpec or plain tasks.md). PHASE 0 STUB. Use when the user asks to plan work with /rein:plan before implementing."
license: MIT
---

# /rein:plan

Turns a change request into a task list the loop can execute **without re-deriving
intent**. Plans only — never implements, never closes tasks, never invokes
`/rein:loop`.

## Status: PHASE 0 STUB

Not implemented yet. It lands in phase 1 together with the `tasks-md` adapter.
If invoked now, say so plainly and offer to plan by hand instead.

## Contract (what phase 1 will produce)

The plan is the prompt. Each task carries the fields the loop reads literally, so
no agent has to reconstruct them:

| Field | Why the loop needs it |
|---|---|
| `id` (`T001`…) | stable identity across steps and the ledger |
| `title` | what the step is for |
| `Depends on` | topological ordering; a failed dependency parks its dependents |
| `Type` | `docs` / `implementation` / `test` |
| `Verification` | the **exact bounded command** — one test or file, never the suite |
| `Human review` | `true` parks the task for the user unless explicitly delegated |

Two plan sources, chosen by `plan.source` in `flow.config.json`:

- **`openspec`** — `openspec/changes/<change>/{proposal,design,tasks,specs}`
- **`tasks-md`** — a plain `tasks.md` with checkboxes, for projects with no
  OpenSpec setup. This is the default: the kit must work in a repo with nothing
  installed.

## Guardrails

- Do not implement any task. Do not mark any checkbox. Do not call `/rein:loop`.
- Every task must have a **bounded** `Verification` — "run the test suite" is not
  acceptable, it is what makes implementation steps expensive.
