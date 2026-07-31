# Change: measure-itself

## Why
The kit exists to measure agent cost, and its own history has holes.

Three loop runs happened in this repo today. When the ledger was checked,
**none of them was there** — the last entry was from the previous day. Running
`rein token-report` by hand recovered exactly one (the most recent); the other
two are gone for good, ~2.1M agent tokens that will never appear in any
history.

The cause is one line. `loop.js` ends with:

```js
measure: `${REIN} token-report`,   // a suggested STRING, not an execution
```

That is the only mention of `token-report` in the whole workflow. The
machinery underneath is fine — per-model parsing, ledger, baseline, signed
deltas, dashboard — it is simply never triggered. Instrumentation that depends
on a human remembering is not instrumentation.

Two more gaps found in the same check:

- **No skill invocation is observed at all.** `/rein:plan`, `/rein:run`,
  `/rein:run-auto`, `/rein:review` leave zero trace, so the review rounds and
  manual passes that cost real money make runs look cheaper than they were.
- **No ledger row says which change it was.** `wf_ca4b1e78 · 242 turns` is
  unreadable a week later.

The dashboard already renders signed deltas against a marked baseline, and it
is honest: the latest run reads `turns_per_agent +62.8%`, `ctx_max +83.9%`
against the baseline. The instrument works. It is starved of data.

## Scope
- In: the loop recording its own run, labelled with the change
- In: skill invocations recorded as events, and surfaced
- Out: hooks — rejected earlier in this project and not reopened; the skill
  records its own invocation, visibly, in the transcript
- Out: backfilling the two lost runs — their transcripts are gone
- Out: any new metric; this change makes the EXISTING ones arrive, and adds
  no claim about what they will show

## Decisions
- D1 The loop records itself. A string in a return value is a suggestion, and the measured result of relying on it is a 2-in-3 loss rate
- D2 A ledger row names its change, or the history is unreadable — `wf_ca4b1e78` is not an answer to "what did that cost"
- D3 A skill invocation is an EVENT, not a run: it is counted separately and never mixed into run totals, which would corrupt every existing delta
- D4 Recording never fails anything. A run whose measurement step dies is still a merged, approved run — the same non-blocking rule the graph index and the render server already follow
- D5 Recording reads only what Claude Code already wrote to disk locally, and adds no network call and no new file outside `~/.claude/rein/`

---

- [ ] T001 The loop records its own run
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_measure_step`
  - Acceptance:
    - a final cheap step runs `rein token-report --record --change <the change name>` after every other phase, so the run lands in the ledger without anyone remembering — and the returned `measure` field reports what was RECORDED, not a command to go run
    - `token-report` accepts `--change <label>`, writes it into the ledger row, and `rein ledger` prints it next to the `wf_id`; a row written without one still reads back cleanly, so the 13 existing rows are not invalidated (D2)
    - the step is non-blocking (D4): a missing CLI, a failed parse or a dead agent is reported into the result and never changes `ok`, `approved` or `merged` — covered by a test that executes the decision function with the failure shapes
    - the decision of what to record is a pure function extracted from the SHIPPED `loop.js` and EXECUTED, the same discipline as `decideRound` and `decideGraphAvailable`; a source-substring assertion does not count
    - a test asserts the step runs LAST — after Review and Integrate — since a measurement taken before the expensive phases would understate the run it claims to describe

- [ ] T002 A skill invocation leaves a trace
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_events`
  - Acceptance:
    - `rein event <name>` appends a JSON line to `~/.claude/rein/events.jsonl` with the name, an ISO timestamp and the project, creating nothing outside `~/.claude/rein/` (D5), and exits 0 even when the file or directory does not yet exist
    - each of the six `SKILL.md` files records its own invocation as its first step, with the skill's own name — and a test reads the shipped SKILL.md files and fails if one of them lacks it, so a new skill cannot ship unobserved
    - events are counted SEPARATELY from runs (D3): `rein ledger` shows an invocation count per project without any event entering a run total, and a test asserts the existing per-run fields are byte-identical before and after events exist
    - a corrupt or truncated line in `events.jsonl` is skipped rather than raising — the file is append-only from concurrent sessions, and a reader that dies on one bad line loses the whole history
    - `rein event` never fails a caller: an unwritable directory is reported on stderr and still exits 0, because a metrics write must never break the flow it is measuring (D4)
