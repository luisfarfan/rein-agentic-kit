# Change: the-dashboard-answers-the-question

## Why
The dashboard renders correct numbers and answers nothing. Generated from this
machine's real ledger, the page is a table dump with **0 tooltips and 0 `aria-`
attributes**, whose columns are jargon this project invented:

```
run · turns/agent · ctx_max/turn · opus share · Δ turns/agent · Δ ctx_max · Δ opus share
```

The single question a user has — *is this helping me?* — is answered nowhere,
and the one place it could be inferred is actively misleading: the deltas are
signed so **negative means better**, the opposite of the reflex a minus sign
triggers, and nothing on the page says so. Someone reading
`Δ turns/agent -42.4%` as "42% worse" concludes the kit is hurting them.

The page also does not show what the ledger now knows. `rein event` records
every `/rein:*` invocation and `rein ledger` counts them; `dashboard.py` has
**zero mentions of events**. Runs carry a `change` label since the last
change; the page shows `wf_ca4b1e78` and no name. So there is no usage
history: no sense of which skills a person actually uses, or whether things
are getting better over time.

## Scope
- In: a plain-language answer to "is this helping?", stated only as far as the
  data supports it
- In: every metric explained in place, keyboard-reachable
- In: usage history — which skills get invoked, which changes ran, and the
  trend across runs
- Out: SQLite. Measured: 1.9 KB per run, so a thousand runs is 1.9 MB. The
  stdlib ships `sqlite3` so it would add no dependency, but it buys nothing at
  this volume and costs a failure mode the text log does not have — a torn
  JSONL line is skipped (already handled), a torn database page is not
- Out: external CSS, fonts, JS or charting libraries. The page is served from
  127.0.0.1 and stays a single self-contained offline document
- Out: any NEW metric, and any claim the data does not support — no "you
  saved X%" anywhere
- Out: visual art direction. Legible, scannable and honest is the target;
  taste is not a testable criterion and is not claimed here

## Decisions
- D1 The page leads with the answer, not the data. The summary comes first and the tables support it, because a user who has to derive the conclusion from a table will not
- D2 The summary never claims more than the ledger proves. No baseline, one run, or runs of different shapes each produce a stated LIMIT — "not enough history to compare" is a valid and required answer, and is the difference between a dashboard and a sales page
- D3 One glossary, one source. Every rendered metric is defined in a single structure, and a test fails on the first metric with no entry — a new metric cannot ship unexplained
- D4 A tooltip that needs a mouse is decoration. Keyboard focus and an accessible description, or the explanation does not exist for the people most likely to need it
- D5 Self-contained and offline. Trends are drawn as inline SVG; a dashboard that phones out to render is not one this kit would recommend
- D6 Files, not a database. Bounded reads are the fix for growth

---

- [ ] T001 The page opens with the answer
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard_summary`
  - Acceptance:
    - each project section opens with a summary stating, in plain words and before any table, how the latest run compares to the baseline on the three numbers that predict cost — "fewer turns per agent", "more context per turn", "less of the Opus quota" — with the percentage beside each, never a bare signed number
    - the summary states its own LIMIT whenever one applies and shows NO comparison in that case (D2): no baseline marked, only one run, or a baseline older than every run shown; a test covers each of those three shapes and asserts no comparative wording is emitted
    - a `direction` helper decides better/worse/unchanged from a metric key and a delta, is a pure function, and is unit-tested per metric including the sign inversion — `opus share` down is better, and a helper that hardcodes "negative is better" for every metric would be wrong the day a metric is added where it is not
    - the summary says what the numbers are FOR in one sentence — cost is turns × context re-read every turn — so a first-time reader knows why these three and not others
    - `python3 -m unittest tests.test_dashboard` passes unchanged, and `rein dashboard --json` keeps every key it has today with the summary added alongside; a test pins the pre-existing keys

- [ ] T002 Every number says what it means
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard_glossary`
  - Acceptance:
    - a single `GLOSSARY` defines every metric the page renders — `turns`, `turns/agent`, `ctx_max/turn`, `opus share`, `total`, and each `Δ` column — each with a one-line meaning and a one-line "why it predicts cost"; a test asserts every rendered metric key has an entry and fails naming the first that does not (D3)
    - each metric header carries a `?` that reveals its glossary text in place, reachable by keyboard focus and exposed as an accessible description rather than a hover-only `title`; a test asserts both for every one (D4)
    - the delta columns state `negative = better` inside the same section as the delta values, and a test asserts the proximity rather than the mere presence of the text
    - the baseline is identified where it is used: which run, when it was marked, and — for a project with none — what to run to mark one
    - a test asserts the rendered HTML contains no `src`/`href` to an external host and no `@import`, so the page stays offline and self-contained (D5)

- [ ] T003 Usage history: what was run, with what, and whether it is improving
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard_history`
  - Acceptance:
    - each project shows its skill-invocation counts per skill name from `events.jsonl`, visually separated from run metrics and never summed into a run total or delta; a test asserts run rows are byte-identical with and without an events file
    - each run row shows its `change` label beside the `wf_id`, and a run recorded before labels existed renders as unlabelled rather than as `None` or a bare empty cell
    - the trend across a project's runs is drawn for `turns/agent` as inline SVG with no external asset, showing at least the last 10 runs, with the baseline marked on it; a project with fewer than 3 runs shows the reason instead of a misleading two-point line
    - reading events is bounded to the most recent N, N is a named constant with its reason in a comment, and a test writes more than N and asserts the render neither reads all of them nor crashes (D6)
    - an absent, empty or corrupt `events.jsonl` renders the page with runs and summary intact and the history section stating why it is empty — never a traceback, never a silently missing section
