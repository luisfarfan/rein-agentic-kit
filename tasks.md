# Change: review-economics

## Why
4 of 6 runs exhausted their 3 review rounds. A round costs one Opus reviewer
(~2M tokens) plus a fix agent plus wall-clock. Two measured causes: findings
carry no severity, so cosmetic notes force rounds exactly like real defects; and
plan-level defects only surface in code review — the most expensive place to
find them. The counter-evidence exists too: the one task with impeccable
criteria (T006, port-edges) approved in a single round.

## Scope
- In: severity on findings · only BLOCKING triggers another round · APPROVED
  with recorded observations · a PlanCheck phase in the loop before implementers run
- Out: rendered verification composition — that is the next change
- Out: dashboard changes and reviewer model changes
- Out: the /rein:review skill's manual procedure beyond the severity vocabulary

## Decisions
- D1 Three severities, one consequence — BLOCKING repeats the round; IMPORTANT travels to the fix agent when a round happens anyway; SUGGESTION is only ever recorded and reported
- D2 CHANGES_REQUESTED requires at least one BLOCKING and APPROVED tolerates none — both incoherences are refused at the gate, not left to convention
- D3 The plan is checked by a different agent inside the loop, never by the planner — no agent approves its own plan
- D4 A BLOCKING plan finding stops the run before any implementer is paid

---

- [x] T001 Severity-tagged findings in the review gate
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_gate`
  - Acceptance:
    - `gate.record_review` parses a `BLOCKING:` / `IMPORTANT:` / `SUGGESTION:` prefix (case-insensitive) on each finding string and stores findings in the episode as `{"severity", "text"}` objects; an untagged finding defaults to `IMPORTANT`, never silently to the mildest level
    - recording `CHANGES_REQUESTED` with zero BLOCKING findings raises `ValueError` naming the rule, and recording `APPROVED` with one or more BLOCKING findings does the same — both directions of D2, refused at write time
    - `gate.check_review` returns the structured findings, and episodes written before this change (plain-string findings) still load and check without error — the old shape is read as `IMPORTANT`
    - `rein review record --findings "BLOCKING: x|SUGGESTION: y"` round-trips through the CLI, and `rein review check` prints each finding with its severity
    - new tests in `tests/test_gate.py` cover the prefix parsing, both D2 refusals, the untagged default, and the old-episode compatibility case — with at least one fixture whose finding text itself contains a colon, so the prefix split cannot be naive

- [ ] T002 Only a blocker buys another round
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_loop_policy`
  - Acceptance:
    - `REVIEW_SCHEMA` findings become objects `{severity, text}` with both required, severity restricted to the three levels, and the reviewer prompt states D1 and D2 in one short paragraph — not an essay
    - the round decision is a pure function in `loop.js` (review result in, one of `approve` / `fix` / `escalate` / `reject` out) so it can be extracted and executed by tests the way `tests/test_verify_policy.py` already executes the policy blocks
    - a review with gate green and no BLOCKING findings resolves to approve even when the reviewer said CHANGES_REQUESTED, with the override logged — symmetric to the existing red-gate override in the other direction
    - when a round does happen, the fix agent receives BLOCKING and IMPORTANT findings only; SUGGESTIONs never reach a fix prompt and never cost a round
    - an approved run still carries its non-blocking observations into the final return value, so nothing the reviewer noticed is silently dropped
    - `node --check plugins/rein/workflows/loop.js` passes, and a new `tests/test_loop_policy.py` executes the extracted decision function across: blockers present, only suggestions, only importants, gate red with no blockers, and needsHumanDecision

- [ ] T003 Check the plan before paying implementers
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_loop_policy`
  - Acceptance:
    - a PlanCheck phase runs in `loop.js` after Prepare and before Isolate: one agent, low effort, implementation model, that reads ONLY the plan (never the codebase) and returns severity-tagged findings per task id
    - its prompt carries exactly four lenses: criteria that cannot be checked as written; verifications that are unbounded (whole suite where one test would do); criteria satisfiable in letter by a test whose fixture avoids the case — the failure this repo produced five times; and tasks that contradict the plan's own Scope or dependency order
    - a BLOCKING plan finding stops the run before Isolate with the findings in the return value (D4), and non-blocking findings are logged and carried in the return without stopping anything
    - the phase is skippable with `args.planCheck: false`, and a dead PlanCheck agent degrades to a logged warning — a run must never be lost to the checker itself
    - the check costs one agent: no retries beyond the standard `agentRetry`, no second opinion, no per-task fan-out
    - `tests/test_loop_policy.py` executes the extracted prompt-builder and stop-decision: blocking finding stops, suggestion-only continues, `planCheck: false` skips, and the prompt names all four lenses and forbids reading the codebase
