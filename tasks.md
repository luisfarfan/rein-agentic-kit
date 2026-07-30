# Change: serena-experiment

## Why
Two loose ends the round-5 reviewer left as SUGGESTIONs, bundled because they are
small and touch one file each. Their real purpose is to be the workload for a
measurement: serena was just wired into the retrieval discipline, and its effect
on the 41 median turns-to-first-edit is unmeasured. A run on tasks of this size
is the cheapest way to find out — and if it does not move, that is the answer.

## Scope
- In: the two reviewer suggestions below, and nothing else
- Out: any further retrieval-tool work — this change is the measurement, not the tool
- Out: CodeGraph, which gets evaluated on the same metric only after this run reports

## Decisions
- D1 Deliberately small tasks — the point is a clean measurement, not the work

---

- [x] T001 `serve.stop` must report the failure it actually hit
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_serve_probe`
  - Acceptance:
    - `serve.stop` on a pidfile containing malformed JSON reports the JSON parse failure, not the integer one — today `{not json` yields `invalid literal for int() with base 10`, which sends the operator looking for the wrong problem
    - a pidfile holding a bare integer still tears the group down, and the existing test for that keeps passing unchanged
    - a pidfile holding valid JSON with a non-integer `pgid` reports that specific shape rather than a generic parse error
    - each of the three messages is asserted in `tests/test_serve_probe.py` by its distinguishing substring, so a future refactor cannot collapse them back into one

- [x] T002 Serve tests must not leak subprocess warnings over real ones
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_serve_probe`
  - Acceptance:
    - `tests/test_serve_probe.py` no longer emits `ResourceWarning: subprocess N is still running`; the suppression is scoped to that warning class in that module only, never a global filter
    - the suppression is justified in a comment naming why it is correct here: `start()` deliberately returns without retaining the `Popen`, because the process must outlive the launching invocation for a cross-process `--start`/`--stop` split to work at all
    - a test asserts that the module still surfaces warnings of other classes, so the filter cannot quietly hide a real one
    - `python3 -m unittest discover -s tests -q` produces no `ResourceWarning` lines
