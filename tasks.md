# Change: rendered-verification

## Why
`verifyPolicy.mode: "rendered"` already resolves, `serve.command`/`serve.url` already
resolve, and `_browser_tools` already reports which browser this project can reach — but
`loop.js` contains zero browser references. The mode *demands* a render and nothing
renders. A frontend implementer is handed an instruction it cannot satisfy, and the
reviewer accepts an unobserved "renders correctly" as the very evidence it was told to
demand. This is the failure the whole kit is named for: the tests pass, the UI is broken.

## Scope
- In: a deterministic serve probe; a render step inside the existing Verify phase; the
  reviewer refusing to approve a rendered-mode change with no render evidence
- Out: visual/design judgement — whether it *looks* good stays a human call
- Out: installing browsers or adding any runtime dependency; if none is reachable the run
  says so
- Out: screenshot diffing, baselines, or any visual-regression store
- Out: dashboard changes

## Decisions
- D1 The render happens in Verify, by an agent that implemented nothing — the implementer implements, an independent step observes, same rule as the review gate
- D2 The server is started, polled until the port accepts, and torn down by one deterministic CLI — never an agent improvising background bash it may orphan
- D3 Evidence is facts the loop can check (HTTP status, page title, uncaught console errors), never a sentence — a claim with no facts is a failed render, not a pass
- D4 No reachable browser is an explicit `rendered-unverified` outcome carried to the reviewer, never a silent pass and never a hard stop

---

- [x] T001 A deterministic serve probe
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_serve_probe`
  - Acceptance:
    - a new `plugins/rein/lib/serve.py` exposes a context manager that starts `serve.command` as a background process in a given cwd, polls its `serve.url` host/port until it accepts a TCP connection, and terminates the whole process group on exit — including when the body raises
    - readiness is a real TCP connect to the resolved port, never a fixed sleep, with a configurable timeout whose expiry returns a failure carrying the last N lines of the server's own stderr rather than a bare "timed out"
    - a command that exits immediately (a typo, a missing binary) is reported as a failure within the timeout, not waited on for the full duration
    - the terminate path kills the process *group*, because `npm run dev` spawns a child that outlives its parent; a test asserts no listener remains on the port after exit
    - `rein serve-probe --command <c> --url <u> [--timeout N]` prints one JSON object `{ready, url, elapsedMs, error, stderrTail}` and exits 0 only when `ready` is true, so an agent gets the signal from the exit code without parsing
    - Python 3 standard library only
    - a new `tests/test_serve_probe.py` covers ready, immediate-exit, timeout, and the no-listener-after-exit case using `python3 -m http.server` over a temp directory as the fixture — no npm, no network

- [ ] T002 Render it in the Verify phase
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_render_policy`
  - Acceptance:
    - when `verifyPolicy.mode` is `rendered` and at least one browser tool is reachable, the Verify phase dispatches one additional agent whose prompt names the exact `serve.command`, `serve.url`, and the reachable tools from `verifyPolicy.tools` — it must never name a tool the project cannot reach
    - that prompt instructs the agent to use `rein serve-probe` for startup and teardown rather than backgrounding the server itself (D2), and to report `{rendered, httpStatus, title, consoleErrors, evidence, notes}`
    - the loop treats the render as failed when `rendered` is false, when `httpStatus` is absent or not 2xx, or when `evidence` is empty — a `rendered: true` with no facts alongside it is a failed render (D3), and a pure function decides this so tests can execute it
    - a failed render is reported to the reviewer as a finding, and the run's return carries `renderEvidence`; the loop does not merge on a failed render
    - `mode` other than `rendered` adds no agent and leaves the Verify phase byte-identical to today — nothing changes for library, CLI or backend projects
    - `verifyPolicy.tools` empty yields `rendered-unverified` in the return with the reason, no render agent dispatched, and no stop (D4)
    - a new `tests/test_render_policy.py` extracts and executes the decision function and the prompt builder from the shipped `loop.js`, covering: evidence-less true, non-2xx, empty tools, non-rendered mode, and a prompt that names only reachable tools

- [ ] T003 The reviewer cannot approve an unobserved render
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_render_policy`
  - Acceptance:
    - the reviewer prompt receives the render outcome and is told that in `rendered` mode a green test suite with a failed or absent render is not grounds for `APPROVED`
    - the loop overrides an `APPROVED` verdict to a fix round when the mode is `rendered` and the render failed — symmetric to the existing red-gate override, and decided by the same pure function so it is executed by tests, not asserted by comment
    - `rendered-unverified` (D4: no tool reachable) does **not** override approval, but is carried into the return and stated in the reviewer's prompt as an incomplete gate — the distinction between "we looked and it broke" and "we could not look" must survive to the operator
    - `plugins/rein/skills/review/SKILL.md` and `loop/SKILL.md` state the render requirement and the two outcomes, replacing the current text that demands a render nothing performed
    - `tests/test_render_policy.py` executes the override across: render failed + APPROVED, render passed + APPROVED, unverified + APPROVED, and non-rendered mode + APPROVED
