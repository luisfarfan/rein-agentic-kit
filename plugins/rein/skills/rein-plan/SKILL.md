---
name: rein-plan
description: "Plan a change into verifiable tasks with acceptance criteria, a Repo, dependencies and a runnable verification each — dry-run and explicit confirmation before anything is written. Never implements. Use when the user asks to plan or break down work, or invokes /rein:rein-plan."
license: MIT
---

# /rein:rein-plan

The **planner** role. Turns a request into a task list that `/ship` can execute without
re-deriving intent. **Never implements, never ticks a checkbox.**

Usage: `/rein:rein-plan <change-name> [description]`

## Steps

1. Resolve the project: `rein context .` — note `plan.source`, `plan.path`, `config.commands`.
2. Understand the request with bounded searches and offset reads. If scope, an interface or a
   trade-off is ambiguous, **ask now**: a wrong assumption is paid for by every worker after it.
3. **Decide `Repo:` per task.** One repo: write to `<repo>/openspec/changes/<c>/`. Several:
   write to `proxima-engineering/openspec/changes/<c>/`. A task edits exactly one repo.
4. Draft: a short header, then tasks.

   ```markdown
   # Change: add-widget

   ## Why
   One or two sentences: the problem, and what happens if it is not solved.

   ## Scope
   - In: the parser and its tests
   - Out: the CLI surface

   ## Decisions
   - D1 Constraints live in tasks.md, not a separate file

   ---
   - [ ] T001 Parse the config file
     - Type: implementation
     - Repo: proxima-api
     - Depends on: none
     - Human review: false
     - Verification: `python3 -m unittest tests.test_config`
     - Acceptance:
       - reads flow.config.json when present
   ```

   The header is short: `Out` and decision titles ride in every worker's prompt. Decisions
   only where a worker could plausibly reverse a choice unnoticed.
5. **Critique the draft before it is shown or written.** UNCONDITIONAL: every invocation, never
   behind a flag, never only when the user asks for it.
   a. If `plan.source` is `openspec` and `command -v openspec` finds it, run
      `openspec validate --strict` and report its output VERBATIM, labeled as openspec's own;
      this skill reimplements none of openspec's structural checks. A missing `openspec` binary
      skips this half silently: not a failure.
   b. Save the draft to a scratch file (`mktemp`) and run `rein plan-check --gate <file>`.
      Exit 1 means a BLOCKING finding; `IMPORTANT` ones are shown beside the plan.
   c. Critique every task yourself against the two classes no command can decide:
      - a criterion no command can check
      - a criterion that contradicts a stated decision

      With (b)'s two, these are the ONLY four BLOCKING classes:
      - a verification that cannot mechanically confirm the criteria it is attached to
      - a dependency that is circular or names a task that does not exist

      Style and wording are SUGGESTIONS, never a stop.
   d. If the critique cannot run (no critique agent is available, it times out, or its
      response is malformed) do NOT skip it silently and do NOT hard-stop: write the plan
      anyway, naming the failure beside it.
   e. Report BLOCKING findings and fix them first; never dry-run or write with one open.
6. **Dry run.** Show tasks in dependency order with each `Repo:`, verification and
   human-review gate, and the target path.
7. **Ask for explicit confirmation.** Never write without it.
8. On confirmation, write it to the step 3 location, then re-parse it from the repo that
   holds the plan (`proxima-engineering` for multi-repo): `rein tasks <root> --change <c>`.
   Report the order; a dependency cycle (`unresolvableDeps`) is fixed now.
9. **End the session.** Tell the user implementation runs with `/ship <change>`
   (`/rein:ship` when namespaced) in an Orca terminal.

## What makes a task good

- Every task has a **runnable** `Verification` (one test file or id, never "the suite") or
  `Human review: true`. Check commands against `config.commands`; say which are missing.
  `rein ship` runs only a field that **starts** with a backticked command; prose is not run.
- Criteria are checkable and name the **guarantee**, not a proxy a fixture can dodge.
- `Human review: true` only for a product decision or content the owner must sign (labels,
  copy, prices, a go/no-go) — never for technical work the Verification and the audit already
  check; at most 2 per change.
- A task fits one worker session; `Depends on` is real and drives the order.
- If the request is too vague for checkable criteria, say so and ask.
