# Change: the-ledger-knows-how-long

## Why
The ledger cannot answer *how long did this run take*. Not approximately —
at all. A row carries tokens, turns, `ctx_max`, Opus share and now a change
label, and no duration of any kind. You can see that a run spent 50M tokens
and not whether it took five minutes or three hours.

The same gap swallowed the parallelism facts. The loop computes
`parallelGroups` and `parallelPathTaken`, returns them in the workflow's
result object, and nothing writes them down — the object reaches the
notification and evaporates. That is precisely the failure `measure-itself`
existed to end, one level in: a value that exists in a return nobody records.

The data is already on disk. Every agent transcript carries ISO timestamps,
and 15 lines over the last four runs produced:

| run | wall clock | agent minutes | overlap |
|---|---:|---:|---:|
| wf_cfb5c388 | 40.0m | 40.0m | 1.00× |
| wf_d143bcf2 | 1.6m | 1.6m | 1.00× |
| wf_eab342ad | 55.5m | 55.4m | 1.00× |
| wf_f9c9e72d | 37.4m | 37.4m | 1.00× |

**1.00× every time — perfectly serial.** That ratio is the number that will
prove or disprove the parallel path: 1.8× means it worked, and a run that
stays at 1.00× means either it never fired or the runtime serialised the
agents anyway — a question that cannot be answered today.

## Scope
- In: run duration, agent time and their ratio, derived from the transcripts
  `token-report` already walks, and persisted per run
- In: recording how the run grouped its tasks, so the ratio has context
- Out: any claim about what the numbers will show. This change makes the
  question answerable; the answer is a later measurement
- Out: changing how the loop parallelises. This measures it, nothing more
- Out: a new storage engine — the same JSONL, the same append-only file

## Decisions
- D1 Overlap is measured from clocks, never claimed from status flags. `Date.now()` is unavailable inside a workflow script (it breaks resume), so the measurement belongs in `token-report`, where the transcripts' own ISO timestamps already are
- D2 Absent is absent. A run whose timestamps cannot be read records NO duration fields rather than zeros — a fabricated 0m would read as an instant run and poison every average
- D3 The 14 rows already written stay valid and readable. New fields are added, never required, exactly as the `change` label was
- D4 A number the page shows is a number the page explains: anything surfaced in the dashboard needs its GLOSSARY entry, which the existing completeness ratchet already enforces

---

- [ ] T001 A run records how long it took, and whether anything overlapped
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_run_duration`
  - Acceptance:
    - `token_report` derives each agent's first and last ISO timestamp from its transcript and computes three run-level values: `wall_clock_min` (last end minus first start across all agents), `agent_min` (the sum of per-agent spans) and `overlap` (`agent_min / wall_clock_min`); a test builds synthetic transcripts with KNOWN timestamps — including one pair that genuinely overlaps and one that does not — and asserts the exact values, so a serial run reads 1.00× and an overlapping one reads above it
    - the three values are persisted in the ledger row, and a row written without them still reads back and renders — a test loads a fixture row in the pre-existing shape and asserts no key error and no invented zero (D3)
    - a transcript with missing, unparseable or out-of-order timestamps yields NO duration fields for that run rather than a zero or a negative span, and a test covers each of those three shapes (D2)
    - the measure step passes the run's task grouping so the row records it beside the ratio; a fully serial plan records groups of one, and a test asserts the recorded grouping matches what the loop decided rather than being re-derived
    - `rein ledger` prints the duration and the overlap next to each run, and prints neither for a row that has none — a test asserts the older rows render unchanged
    - the dashboard shows run duration, with its GLOSSARY entry, and the existing completeness ratchet passes — a metric on the page that nothing explains must keep failing the build (D4)
