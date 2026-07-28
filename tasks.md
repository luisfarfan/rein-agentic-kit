# Plan

Live plan for `rein-agentic-kit`. `/rein:loop` reads this file; `rein close <id>`
ticks the boxes. Format is documented in `/rein:plan`.

---

- [x] T001 Add `rein baseline` to mark a reference run
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

- [x] T002 Show the delta against the baseline in `rein ledger`
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

---

## Phase 2 — stack-aware verification

The failure this phase exists to catch: **"the tests pass but the UI is broken."**
Unit tests are the right gate for a library and the wrong one for a rendered page.
The loop already runs whatever `Verification` a task declares; what is missing is a
per-stack *policy* that says what verification must look like to count at all.

- [x] T003 Emit a per-stack verification policy from `rein detect`
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_policy`
  - Acceptance:
    - `plugins/rein/lib/detect.py` gains a `verifyPolicy` key in the `resolve()` output with the shape `{"mode": str, "requires": [str], "forbids": [str], "tools": [str]}`
    - mode is `rendered` when `subtypes` contains `frontend`, `plan-only` when `subtypes` contains `infra` and no test command is configured, and `unit` otherwise
    - `rendered` mode lists in `requires` that a real browser render must be observed, and names the available tools by probing which of Playwright, the Claude-in-Chrome MCP and the `browser-testing` skill this project can actually reach — never naming a tool that is absent
    - `plan-only` mode lists the destructive operations in `forbids` (at minimum `deploy`, `apply`, `destroy`), so an infra task can never be "verified" by mutating real infrastructure
    - a project whose `flow.config.json` sets `verify.mode` gets that value verbatim, overriding detection, consistent with the existing precedence rule
    - `unit` mode is unchanged in behaviour from today: `requires` is empty and nothing new is imposed on library or CLI projects
    - a new `tests/test_verify_policy.py` covers each mode, the config override, and the "never name an absent tool" rule, using temporary project trees only

- [x] T004 Detect how to serve a frontend so a render check is possible
  - Type: implementation
  - Depends on: T003
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_policy`
  - Acceptance:
    - for `frontend` projects `resolve()` reports a `serve` block `{"command": str, "url": str}` derived from `package.json` scripts, preferring `dev` and falling back to `start`, with the package manager prefix already applied
    - the URL is taken from `flow.config.json` `verify.url` when set; otherwise it defaults to `http://localhost:<port>` using a port parsed from the script's own flags when present, and 3000 only as a last resort
    - a frontend project with no runnable script reports an empty `serve.command` and says so in `missingCommands`, rather than inventing one that would fail at run time
    - non-frontend projects report no `serve` block at all
    - the existing `tests/test_detect.py` continues to pass unchanged, and the new cases live in `tests/test_verify_policy.py`

- [x] T005 Enforce the policy in the loop's implementer and reviewer prompts
  - Type: implementation
  - Depends on: T004
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_policy`
  - Acceptance:
    - `plugins/rein/workflows/loop.js` carries `verifyPolicy` and `serve` through `CONTEXT_SCHEMA` and the Prepare agent's instructions, in the same "report it literally, do not re-derive it" style as the existing fields
    - in `rendered` mode the implementer's bounded-step contract states that passing unit tests alone do NOT make a task done: the page must be served and actually rendered, and the observed evidence recorded in `verification`
    - in `rendered` mode the reviewer is told the mechanical gate is incomplete without that rendered evidence, and that a green test suite with no render is not grounds for approval
    - in `plan-only` mode both prompts carry the `forbids` list as a hard prohibition
    - in `unit` mode the prompts are byte-identical to today, so nothing changes for library and CLI projects
    - `node --check plugins/rein/workflows/loop.js` passes, and a test asserts the loop script contains no hard-coded stack name, framework name or port — everything comes from config

---

## Follow-ups (raised by the review gate, judged non-blocking)

- [ ] T006 Tighten two remaining port-heuristic edges
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_policy`
  - Acceptance:
    - a shell redirect (`node server.js --port 3000 2>&1`) is not classified `flag-compound`; the `&` in `2>&1` is not a background operator
    - the `env` port path range-checks its value the way the bare path already does, so `X_PORT=12` cannot become a URL
    - the unreachable "there is no serve command to read a port from" branch in `_serve`'s warning is removed
    - each of the three has a test asserting the full `_port_from` tuple, not a partial property

- [x] T007 Decide whether a nested Dockerfile alone should mean plan-only
  - Type: implementation
  - Depends on: none
  - Human review: true
  - Verification: `python3 -m unittest tests.test_verify_policy`
  - Acceptance:
    - a decision is recorded in `docs/` on whether `apps/*/Dockerfile` in a Node monorepo with no test script should resolve to `plan-only` (a prohibition, so it fails safe) or stay `unit`
    - detection matches whatever is decided, with a test naming the shape and the reasoning
