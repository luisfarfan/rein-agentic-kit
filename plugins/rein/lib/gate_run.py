"""How `rein gate` runs the project's checks: which slots, in what order, and when to stop.

`verify.verify_commands` answers "can every configured command be invoked" and
so runs all of them. A gate asks a different question -- "did the checks pass" --
and only the gate slots answer it. So a gate:

- runs only `lint`, `typecheck`, `build`, `test` (and `testIntegration` when the
  caller asks for it), never `testOne` against a fake target, `serve`, or a
  custom slot: their result was ignored anyway, after paying for the run;
- runs the cheap slots first and stops at the first one that does not pass,
  reporting the rest as `skipped`, so a lint error is not followed by a
  ten-minute suite whose verdict cannot change the outcome.

The report has the shape `verify_commands` returns, so `gate.decide_gate` and
the ship driver's failure detail read it unchanged.
"""

from __future__ import annotations

import time
from typing import Callable

import verify as _verify

# Cheap first. testIntegration (an e2e/integration suite) is the slowest by nature.
ORDER = ("lint", "typecheck", "build", "test", "testIntegration")
INTEGRATION = "testIntegration"


def gate_slots(commands: dict, only=None, integration: bool = True) -> list:
    """The configured gate slots to run, in run order."""
    out = []
    for slot in ORDER:
        if slot == INTEGRATION and not integration:
            continue
        if only is not None and slot not in only:
            continue
        if (commands.get(slot) or "").strip():
            out.append(slot)
    return out


def _skipped(slot: str, command: str, why: str) -> dict:
    return _verify.CommandResult(slot, command, True, _verify.OUTCOME_SKIPPED, None, [], 0,
                                 error=why).to_dict()


def run_steps(root: str, steps: list, timeout_for: Callable[[str], float], env: dict = None,
              run: Callable = _verify.run_one, fail_fast: bool = True) -> dict:
    """Run `[(slot, command, configured)]` in order; after the first slot that is not ok, skip the rest.

    `timeout_for(slot)` is each slot's budget. A slot whose outcome is anything but
    `ok` (failed, timeout, not invocable) ends the run: what follows is reported
    `skipped`, naming the slot that stopped it."""
    results: dict = {}
    stopped_by = ""
    for slot, command, configured in steps:
        if stopped_by:
            results[slot] = _skipped(slot, configured, "skipped: %s did not pass first" % stopped_by)
            continue
        res = run(slot, command, root, timeout_for(slot), configured=configured, env=env or None)
        entry = res.to_dict()
        if res.outcome != _verify.OUTCOME_OK:
            entry["errorLines"] = _verify.failure_lines(res.output)
            if fail_fast:
                stopped_by = slot
        results[slot] = entry
    budgets = [timeout_for(s) for s, _c, _k in steps]
    return {
        "root": root,
        "timeoutSeconds": max(budgets) if budgets else 0,
        "checkedAt": time.time(),
        "results": results,
        "allInvocable": all(r["invocable"] for r in results.values()),
        "order": [s for s, _c, _k in steps],
    }
