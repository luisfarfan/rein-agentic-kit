# Change: dashboard

## Why
The ledger already holds real runs and nobody can see them without reading JSON
by hand. `rein ledger` prints them, but only to someone who already knows how to
read turns/agent, ctx_max and opus share. The measurement exists and is unusable.

## Scope
- In: `rein dashboard` — a local server, a self-contained page, metrics per
  project / session / run, deltas against a marked baseline, per-agent model config
- Out: hosting, auth, or sharing — this is local and stays local
- Out: live streaming during a run; the ledger is written when a run ends
- Out: charts over data the ledger does not already store

## Decisions
- D1 Zero toolchain — a plugin that drags npm along at install time is a plugin nobody installs
- D2 Data is embedded server-side, the page never fetches — that is what makes it verifiable without a browser
- D3 Writing config shows a diff and asks first — a server that edits N repos silently produces surprise diffs
- D4 No baseline, no savings figure — only a trend

---

- [x] T001 Assemble the dashboard view model and serve it
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard`
  - Acceptance:
    - a new `plugins/rein/lib/dashboard.py` builds a view model from the ledger: runs grouped by project, each with turns_per_agent, ctx_max, opus_share, totals and its per-agent rows
    - the baseline (from `~/.claude/rein/baseline.json`) is marked on its run, and every run recorded later in the same project carries signed deltas for those three metrics; runs in other projects and runs recorded before it carry none
    - `rein dashboard --json` prints that view model and exits without opening a socket, so the whole data path is testable without a network
    - `rein dashboard` serves it over `http.server` bound to 127.0.0.1 only, never `0.0.0.0`, with the port configurable via `--port`
    - a missing, empty or partly corrupt ledger yields an empty view carrying a plain message — never a traceback and never a partial read that silently drops valid rows
    - Python 3 standard library only; no new dependency appears anywhere
    - a new `tests/test_dashboard.py` builds temporary ledgers and asserts each of the above, including the corrupt-line case

- [x] T002 Render the page
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard`
  - Acceptance:
    - one self-contained HTML response: no external stylesheet, script, font or image, and no `fetch`/`XMLHttpRequest` — the numbers are already in the markup (D2)
    - for every run it shows turns/agent, ctx_max/turn and opus share, grouped by project, with the per-agent rows reachable without leaving the page
    - the baseline run is visibly marked and its deltas are labelled so that a negative number reads unambiguously as an improvement
    - with no baseline marked, the page shows the trend and no savings figure anywhere — D4 is the thing being protected, so a test must assert that absence, not merely the presence of the trend
    - it renders with zero runs, with one run, and with a run whose `agents` list is empty
    - a test starts the real server, fetches the page over HTTP, and asserts that values taken from the temp ledger appear in the returned markup — proving the data reached the page, not that a template rendered

- [x] T003 Edit per-agent models from the page
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_dashboard`
  - Acceptance:
    - each project in the view model shows its resolved `models.aux/impl/review` and the source of each (config or default)
    - a POST endpoint writes only `models.*` into that project's `flow.config.json`, preserving every other key byte-for-byte, including unknown keys and the `$comment` entries the example config ships
    - the response returns a unified diff of what would change, and nothing is written until a second confirming request arrives — a first request must never write
    - a write targeting a path that is not a project root already present in the ledger is refused with a non-2xx and no filesystem change; a test asserts this with a traversal attempt
    - writing to a project that has no `flow.config.json` creates one containing only the models block
    - tests cover: unknown keys preserved, diff-then-confirm ordering, refusal outside known roots, and creation from absent
