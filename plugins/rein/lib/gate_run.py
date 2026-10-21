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

import gate_cache as _cache
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


BUDGET_MIN = 120.0
BUDGET_MAX = 1800.0
BUDGET_FACTOR = 2.0


def slot_budget(repo: str, label: str, command: str, fallback: float) -> float:
    """A slot's timeout: twice its slowest recent pass, within [2 min, 30 min]; `fallback`
    (the project's ship.gateTimeoutSeconds, or --timeout) when it has never passed.

    The slowest of the last few passes, not the last one: a suite slowed by a busy machine
    must not time out because it once ran alone."""
    passes = _cache.recent_passes(repo, label, command) if repo else []
    if not passes:
        return float(fallback)
    return min(max(BUDGET_FACTOR * max(passes), BUDGET_MIN), BUDGET_MAX)


def _skipped(slot: str, command: str, why: str) -> dict:
    return _verify.CommandResult(slot, command, True, _verify.OUTCOME_SKIPPED, None, [], 0,
                                 error=why).to_dict()


def run_steps(root: str, steps: list, timeout_for: Callable[[str], float], env: dict = None,
              run: Callable = _verify.run_one, fail_fast: bool = True, cache: bool = False,
              history_repo: str = "") -> dict:
    """Run `[(slot, command, configured)]` in order; after the first slot that is not ok, skip the rest.

    `timeout_for(slot)` is each slot's budget. A slot whose outcome is anything but
    `ok` (failed, timeout, not invocable) ends the run: what follows is reported
    `skipped`, naming the slot that stopped it. With `cache`, a slot that passed on
    this exact tree state, command and env is reused (`"cached": true`) instead of run,
    and a new pass is stored (gate_cache); nothing else is ever stored. With
    `history_repo`, each pass's duration is recorded for the slot's next budget."""
    results: dict = {}
    stopped_by = ""
    for slot, command, configured in steps:
        if stopped_by:
            results[slot] = _skipped(slot, configured, "skipped: %s did not pass first" % stopped_by)
            continue
        k = ""
        if cache:
            state = _cache.tree_state(root)  # per slot: an earlier slot may have written files
            k = _cache.key(root, state, slot, command, env or {}) if state else ""
            hit = _cache.get(root, k) if k else None
            if hit is not None:
                hit.pop("storedAt", None)
                results[slot] = {**hit, "cached": True}
                continue
        budget = timeout_for(slot)
        res = run(slot, command, root, budget, configured=configured, env=env or None)
        entry = res.to_dict()
        entry["budgetSeconds"] = budget
        if res.outcome != _verify.OUTCOME_OK:
            entry["errorLines"] = _verify.failure_lines(res.output)
            if fail_fast:
                stopped_by = slot
        else:
            if k:
                _cache.put(root, k, entry)
            if history_repo:
                _cache.record_pass(history_repo, slot, configured, res.elapsed_ms / 1000.0)
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
