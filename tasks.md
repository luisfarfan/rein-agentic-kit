# Change: works-in-any-repo

## Why
Trying to run an experiment in two of the author's real repos failed twice, and
neither failure was the repo's fault. `firecrawl` (7 sub-projects, no root
manifest) resolved to `stack: unknown` with zero commands. A Python repo resolved
`test: poetry run pytest -q` — a command that does not run on that machine, which
would have turned the loop's mechanical gate red and sent the reviewer looking for
a defect in the code. And `rein setup --install` cannot activate serena
unattended because serena's project setup blocks on interactive prompts.

The kit's promise is "installable in ANY project". These three are what break it
for the first person who tries.

## Scope
- In: monorepo-aware detection; verifying that resolved commands actually run;
  unattended serena activation
- Out: guessing which sub-project of a monorepo is the target — that is the
  operator's call, and inventing it is worse than reporting the choice
- Out: CodeGraph and any further retrieval-tool evaluation, which is only
  meaningful after these land
- Out: installing project dependencies on the operator's behalf

## Decisions
- D1 A monorepo reports its sub-projects and resolves NO commands at the root — "unknown" sends the operator to the wrong problem, and a guess sends the loop to the wrong sub-project
- D2 A resolved command is an inference until something runs it; `verify` executes and reports, and never repairs
- D3 Verification happens where it is cheap — a red gate found at Prepare costs nothing, the same one found at Review costs a whole run
- D4 Unattended means unattended: stdin closed, and no language or feature enabled that the operator did not ask for

---

- [ ] T001 See into monorepos
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_monorepo`
  - Acceptance:
    - when the root has no language manifest but directories one or two levels down do, `resolve()` reports a `subprojects` list, each entry carrying its relative path, detected stack, and its own resolved commands
    - the root `stack` becomes `monorepo` in that case, never `unknown` — reporting "unknown" for a repo the kit can plainly see into sends the operator to the wrong problem (D1)
    - root-level `commands` stay EMPTY and `missingCommands` explains that a sub-project must be chosen — the kit must not pick one, even when there is only a single candidate
    - `flow.config.json` may name one with a `subproject` key; the resolved commands then carry the path so they run from the repo root, and `commandSources` attributes them to that key
    - the scan is bounded at depth 2 and skips the same directories `_infra_files` skips, so `rein detect` never pays for a recursive walk
    - a single-project repo produces byte-identical output to today: no `subprojects` key, and `tests/test_detect.py` passes unchanged
    - a new `tests/test_monorepo.py` covers a `firecrawl`-shaped fixture (`apps/api`, `apps/web`, no root manifest), a single-candidate monorepo, the `subproject` override, and a plain repo

- [ ] T002 A resolved command is an inference until something runs it
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_commands`
  - Acceptance:
    - `rein verify [root]` executes each resolved command and reports, per command, whether it is invocable, its exit code, and the first lines of its output — and exits non-zero if any configured command is not invocable
    - "not invocable" is distinguished from "ran and failed": a missing binary or a shell resolution failure is a SETUP problem, a test suite that runs and reports failures is a CODE problem, and conflating them is precisely the misdirection this task exists to remove
    - `testOne` is verified with its `{target}` substituted by something guaranteed to exist and be cheap, never by running the whole suite
    - each command runs with a timeout, and a timeout is reported as its own outcome rather than as a failure or a pass
    - `verify` never repairs, never installs, and never writes to the repo (D2); a test asserts the working tree is unchanged after a run
    - `rein doctor` reports the verification state of each command when it is known, without running anything itself
    - a new `tests/test_verify_commands.py` covers: invocable, missing binary, runs-and-fails, timeout, and the no-write guarantee, using commands built in a temp directory — no dependency on what is installed on the machine

- [ ] T003 The loop learns a gate is broken before it pays implementers
  - Type: implementation
  - Depends on: T002
  - Human review: false
  - Verification: `python3 -m unittest tests.test_verify_commands`
  - Acceptance:
    - the Prepare agent runs `rein verify` and reports each command's invocability into `CONTEXT_SCHEMA`, in the same "report it literally, do not re-derive it" style as the existing fields
    - a `test` command that is not invocable STOPS the run before Isolate with that fact in the return value — no implementer is paid to work toward a gate that cannot pass (D3)
    - a command that is invocable but currently failing does NOT stop the run: that is the normal state of a repo mid-change, and stopping for it would make the loop unusable
    - `lint` or `typecheck` not being invocable is a warning carried to the reviewer, not a stop — neither is required for a verdict
    - the stop decision is a pure function extracted and executed by tests, like `decideRound` and `decidePlanCheck`
    - `node --check plugins/rein/workflows/loop.js` passes, and a repo whose commands all verify produces a Prepare phase indistinguishable from today

- [ ] T004 Unattended setup means unattended
  - Type: implementation
  - Depends on: none
  - Human review: false
  - Verification: `python3 -m unittest tests.test_setup`
  - Acceptance:
    - `rein setup --install` activates serena for the current repo without blocking on stdin — verified by a test that runs it with stdin closed and asserts it terminates
    - no language, feature, or tool is enabled that the operator did not ask for: the activation takes the same defaults the interactive prompts offer, and a test asserts the generated config does not enable a language the repo's own files do not require (D4)
    - an already-activated repo is left untouched, and the run reports it as such rather than re-creating anything
    - a serena binary that is absent is reported as a missing prerequisite, not attempted
    - failure to activate is reported and the rest of `--install` continues — one tool never takes the others down
