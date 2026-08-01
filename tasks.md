# Change: names-that-do-not-collide

## Why
The kit registers eight skills under generic verbs:

```
Skills (8)  loop, ping, plan, review, role, run, run-auto, setup
```

Three of them are names Claude Code already ships: **`loop`** (repeat a
prompt every N minutes), **`run`** (launch and drive this project's app) and
**`review`**. The namespace resolves the invocation correctly — `/rein:loop`
loads this kit's skill — but the identifier is the bare verb, so from there on
the session refers to it as `/loop`, and a later reference reaches the
built-in scheduler instead. Observed, in a real session:

> `/loop` is the scheduling skill (repeat a prompt every N minutes), and the
> argument you passed is an OpenSpec change. What did you want to run?

Nothing failed loudly. The command was **reinterpreted**, which is worse: a
crash would have been noticed immediately.

How the tool that solves this best does it — openspec, on this machine:

```
.claude/commands/opsx/
  apply.md  archive.md  continue.md  explore.md  ff.md
  new.md  onboard.md  propose.md  sync.md  verify.md
```

Short unique namespace, short generic verbs, and `name:` used as a readable
label rather than the identifier. That is the shape this kit already has —
`rein:` plus a verb. The only difference is that openspec's verbs are not
ones the host reserves, and three of ours are.

So the fix is not longer names. It is three different verbs, still short.

## Scope
- In: renaming the three colliding skills, and `run-auto` alongside them so
  the family reads as one
- In: a guard that fails the build when a skill takes a reserved name
- Out: an alias that keeps `/rein:loop` working. A directory named `loop`
  registers the colliding identifier — the alias would be the defect
- Out: renaming `plan`, `role`, `ping` or `setup`, none of which collide
- Out: the `name:` frontmatter becoming a prose label: this repo's own guard
  reads it to prove each skill records its own invocation, so it stays the
  identifier

## Decisions
- D1 The namespace carries uniqueness, the verb carries meaning — openspec's shape, and already this kit's. Long names like `change-loop` would fix the collision by making the interface worse
- D2 No compatibility alias. A skill directory named `loop` re-registers the exact identifier that collides, so an alias would preserve the bug it is meant to soften
- D3 The rename also fixes a real ambiguity: `loop` / `run` / `run-auto` do not distinguish themselves. `apply` (a whole plan), `step` (one task), `steps` (several, bounded) state the relation in the names
- D4 A reserved name is checked mechanically, not remembered. The next short, pretty name will collide too, and nobody will notice until a user's command is reinterpreted

---

- [ ] T001 The colliding verbs are renamed, everywhere
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_skill_names`
  - Acceptance:
    - the skill directories become `apply` (was `loop`), `step` (was `run`), `steps` (was `run-auto`) and `audit` (was `review`); `plan`, `role` and the `ping` / `setup` commands are untouched, and `claude plugin details rein` would list eight skills with no name Claude Code reserves
    - every in-repo reference to the old invocations is updated — `README.md`, `plugins/rein/README.md`, the workflow, the other skills' cross-references and `docs/` — and a test greps the whole repository for `/rein:loop`, `/rein:run`, `/rein:run-auto` and `/rein:review` and fails on any survivor outside a changelog or a plan's Why section
    - each renamed skill records its OWN new name via `rein event`, and the existing shipped-skill guard in `tests/test_events.py` passes unchanged — it reads the frontmatter `name:`, so that field stays the bare identifier and stays equal to the directory (D3 of the Scope)
    - no compatibility alias is left behind: a test asserts no skill directory named `loop`, `run`, `run-auto` or `review` exists, since such a directory would re-register the colliding identifier (D2)
    - the workflow keeps working end to end: `node --check` passes and `python3 -m unittest discover -s tests -q` is green

- [ ] T002 A reserved name cannot ship again
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_skill_names`
  - Acceptance:
    - a checked-in list of identifiers Claude Code ships — including `loop`, `run`, `review`, `init`, `simplify`, `schedule` — carries a comment saying it is a snapshot, when it was taken, and how to refresh it from `claude plugin details` plus the session's own skill listing, because a list presented as complete would be a claim this repo cannot verify
    - a test fails naming the offender when any skill directory or command file matches that list, and a second test asserts the list is non-empty and the sweep actually read the directories, so it cannot pass vacuously
    - the guard covers commands as well as skills: `claude plugin details` counts both as skills, so `commands/loop.md` would collide exactly as a skill directory would
    - a skill whose name merely CONTAINS a reserved word is allowed — `run-auto` was never the collision, `run` was — and a test pins that distinction so the guard does not start rejecting legitimate names
