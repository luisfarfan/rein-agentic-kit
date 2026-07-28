# Plan

Live plan for `rein-agentic-kit`. `/rein:loop` reads this file; `rein close <id>`
ticks the boxes. Format is documented in `/rein:plan`.

---

- [ ] T001 Add `rein baseline` to mark a reference run
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_baseline`
  - Acceptance:
    - `rein baseline mark <wf_id>` records that run id as the baseline, stored in `~/.claude/rein/baseline.json` next to the ledger, with the ledger itself left untouched
    - `rein baseline mark` with no argument marks the most recent run present in the ledger
    - `rein baseline show` prints the marked run's turns, turns_per_agent, ctx_max and opus_share, or a clear "no baseline marked" message when none is set
    - marking an id that is not in the ledger fails with a non-zero exit and a message naming the id, rather than storing a dangling reference
    - `rein baseline clear` removes the marker and is a no-op when none is set
    - a new file `tests/test_baseline.py` covers each of the above against a temporary ledger, with no writes to the real `~/.claude/rein/`
    - the logic lives in `plugins/rein/lib/token_report.py` and uses only the Python 3 standard library

- [ ] T002 Show the delta against the baseline in `rein ledger`
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_baseline`
  - Acceptance:
    - when a baseline is marked, `rein ledger` marks that run and shows, for every later run, the signed percentage change in turns_per_agent, ctx_max and opus_share against it
    - when no baseline is marked, output is exactly what it is today plus the existing line explaining that a saving requires a marked baseline — no invented savings figure
    - a run recorded before the baseline is not given a delta, since comparing backwards would misrepresent the direction of the change
    - the three deltas are labelled so a negative number reads unambiguously as an improvement
    - `tests/test_baseline.py` covers the with-baseline and without-baseline renderings
