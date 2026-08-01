# Change: parallel-when-it-merits

## Why
The loop implements tasks with a plain `for (const task of tasks) { await ... }`
— **strictly serial, always**, even for tasks that declare no dependency on
each other. Measured on the last run: agent durations summed to 55 minutes
against a 55.5 minute wall clock, so nothing overlapped at all. Implementation
was 23.3 of those minutes across three tasks.

The honest size of the prize, from the five changes run in this repo:

| change | dependencies | parallelisable |
|---|---|---|
| works-in-any-repo | T001, T004 independent | **yes, 2 tasks** |
| graph-reaches-the-agents | T002←T001 | no, a chain |
| one-owner-for-retrieval | T002←T001 | no, a chain |
| measure-itself | T002←T001 | no, a chain |
| the-dashboard-answers-the-question | T003←T002←T001 | no, a chain |

**One run in five.** This is a real but bounded win, and it must not be paid
for with a new failure mode in the part of the system that produces the work.

Sharing one worktree is not an option, and the reason is not file edits —
it is the two things every task does besides editing. Reproduced directly:
two concurrent commits in one worktree gave

```
fatal: Unable to create '.../worktrees/p1/index.lock': File exists.
commits: B          <- only one landed
?? file_a.txt       <- the other agent's work, absent from history
```

A worktree has exactly one git index. And the loop verifies per task by
running the suite, so two agents in one tree run it over each other's
half-finished edits: a failure from one reads as a defect in the other, and
a reviewer gets sent after a bug that does not exist.

A per-task worktree costs **3.5s** (create + `codegraph init` + serena
activation) and **5.4 MB**, against 7–8 minutes per task — 0.8% overhead.

## Scope
- In: running tasks concurrently when it is provably safe, each in its own
  worktree, merged deterministically into the run's worktree
- In: recording whether parallelism fired and what it actually saved
- Out: resolving merge conflicts automatically. If the safety check passed
  and a conflict happens anyway, the check was wrong and that must surface
- Out: parallelising review rounds. Measured across five runs, the last round
  produced a real BLOCKING three times; review depth is not a speed knob
- Out: any `--fast` preset. The knob here is "run what is independent
  concurrently", not "verify less"
- Out: changing the bounded-step mechanism inside a task

## Decisions
- D1 Two conditions, both required: no declared dependency AND no overlap in the scout's reported touchpoints. Dependencies alone are not enough — two independent tasks editing one file is the conflict case
- D2 Unknown means serial. The scout's map is a HINT and may be empty or missing; a task whose touchpoints are unknown runs serially, because the safe default for missing information is the behaviour we have today
- D3 Serial is the floor, never a regression. Every failure in the parallel path — worktree creation, merge, a dead agent — falls back to the serial behaviour that ships today; the loop must never be worse than it is now
- D4 Merge order is deterministic (task id), so two runs of the same plan integrate in the same order and a bisect means something
- D5 The saving is measured, not asserted. A run records whether it parallelised and the wall-clock it took; without that, "it merits" is a claim, and this project does not ship those

---

- [x] T001 Tasks that cannot collide run at the same time
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_parallel_groups`
  - Acceptance:
    - a pure `planParallelGroups(tasks, codeMap)` in the SHIPPED `loop.js` returns an ordered list of groups, where every task in a group has no declared dependency on any other task in that group AND no touchpoint file in common with it; the function is extracted and EXECUTED by the test across a chain, two independent tasks, two independent tasks sharing a file, and an empty map — a source-substring assertion does not count (D1)
    - a task with no entry in the code map, or an entry with empty touchpoints, is placed in a group of its own — unknown means serial (D2), asserted directly
    - groups preserve the dependency order the plan declares: a test builds the five real dependency shapes from this repo's own history (four chains and the one two-independent case) and asserts the chains produce single-task groups and only that one case groups anything together
    - each task in a multi-task group implements in its OWN worktree and its result is merged into the run's worktree in task-id order (D4); a test asserts the merge order is by id and not by completion time, so a fast task cannot reorder history
    - any failure in the parallel path — worktree creation, a merge that conflicts, an agent that dies — is reported and the affected task is retried SERIALLY in the run's worktree, so the run's outcome is never worse than today's (D3); a test covers the conflict path and asserts the fallback rather than an aborted run
    - the run result reports `parallelGroups` (how tasks were grouped) and whether any group actually ran concurrently, so the ledger can answer whether this ever merits anything (D5) — with a test asserting a fully serial plan reports groups of one and no concurrency claim
