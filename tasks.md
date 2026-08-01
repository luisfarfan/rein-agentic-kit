# Change: the-plan-checks-itself

## Why
A plan defect is the cheapest thing in this system to catch and the most
expensive to miss. Measured here, once, on a defect the author actually
wrote: task T003's `Verification` named `tests.test_verify_commands` — T002's
test module — so the command could not mechanically confirm a single one of
T003's six acceptance criteria. The loop's PlanCheck stopped the run for
**81k tokens**. The same defect reaching Review would have cost a whole run,
around 30M.

But PlanCheck fires **inside the loop**, after the plan is written and the
human has confirmed it. The defect above was already committed and launched.

The gap is what a person ends up doing by hand: asking for the proposal to be
re-read, which reliably turns up something. That habit is not automated
anywhere — not in this kit, and not in openspec, whose `validate` is
structural by its own documentation ("check structure", "missing required
sections or malformed delta headers"). It cannot judge whether a verification
proves the criteria it is attached to; that is semantics, not format.

So the check moves one step earlier, to the moment the plan is written, where
changing scope still costs nothing.

## Scope
- In: `/rein:plan` critiquing its own output before writing it
- In: running openspec's own validator when openspec is the plan source, and
  reporting its errors verbatim
- Out: reimplementing any structural rule openspec already checks — compose,
  never rebuild
- Out: changing the loop's PlanCheck, which stays exactly as it is; this adds
  an earlier gate, it does not move that one
- Out: style, wording and taste findings. A check that always finds something
  stops being read, and the plan goes back to being unreviewed in practice

## Decisions
- D1 Automatic, never a flag. Two mechanisms shipped in this project failed exactly this way — `measure: "<command>"` was a string nobody ran, and five skills recorded with an empty `$R`. Instrumentation that depends on remembering is not instrumentation
- D2 BLOCKING has a closed definition, and only BLOCKING stops the write. Everything else is shown beside the plan for the author to accept or ignore
- D3 One definition, two consumers. The loop's PlanCheck and this check must name the SAME defect classes, or the two gates drift and the earlier one starts contradicting the later one
- D4 Structural checking belongs to openspec where openspec is in use. Its errors are reported as its own, not paraphrased and not re-derived
- D5 Unavailable is not a stop. If the check cannot run, the plan is written with that fact stated — never silently skipped, and never a hard failure over a gate that is meant to save money

---

- [x] T001 The plan is criticised before it is written
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_plan_self_check`
  - Acceptance:
    - `plugins/rein/skills/plan/SKILL.md` runs a critique pass over the drafted plan BEFORE writing it, as an unconditional step and not behind any flag or user request; a test reads the SHIPPED skill and fails if the step is absent or made conditional (D1)
    - the four BLOCKING classes are defined in ONE place — a verification that cannot mechanically confirm the criteria it is attached to, a criterion no command can check, a dependency that is circular or names a task that does not exist, and a criterion that contradicts a stated decision — and a test asserts the loop's existing PlanCheck prompt and the plan skill name the same four, so the two gates cannot drift (D2/D3)
    - the REAL defect is the fixture: a plan whose T003 verification names T002's test module is rejected with a BLOCKING finding that says which criteria it fails to prove — the exact plan text that cost 81k tokens is checked in as the test input
    - a healthy plan produces NO blocking findings: one of the plans already approved in this repo's history is checked in as a fixture and asserted to pass, because a check that always fires is a check nobody reads (D2)
    - when the plan source is openspec AND the binary is present, `openspec validate --strict` runs first and its output is reported verbatim as openspec's own; a test asserts the kit implements no structural rule of its own, and that a missing openspec binary skips that half without failing anything (D4)
    - a critique that cannot run — no agent, a timeout, a malformed response — results in the plan being WRITTEN with the failure stated beside it, never a silent skip and never a hard stop; a test covers each of those three shapes (D5)
