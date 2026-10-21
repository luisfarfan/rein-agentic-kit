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

import inspect
import time
from typing import Callable

import affected as _affected
import gate_cache as _cache
import verify as _verify

# Cheap first. testIntegration (an e2e/integration suite) is the slowest by nature.
ORDER = ("lint", "typecheck", "build", "test", "testIntegration")
INTEGRATION = "testIntegration"
TIERS = ("", "task", "full")
# A task's gate: the cheap whole-repo checks, then only the tests its change reaches.
TASK_SLOTS = ("lint", "typecheck")


def call_with(fn: Callable, *args, **kw):
    """`fn(*args, **kw)` passing only the keywords `fn` accepts: older runners take fewer."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params):
        return fn(*args, **kw)
    names = {p.name for p in params}
    return fn(*args, **{k: v for k, v in kw.items() if k in names})


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


def plan(root: str, resolved: dict, tier: str = "", only=None, unit_only: bool = False,
         base: str = "", changed: list = None) -> dict:
    """`{"steps", "affected", "skipped"}` for one gate run.

    tier "task": lint and typecheck, then the tests affected by the change since
    `base` (or `changed`, repo-relative) instead of the `test` slot; no build, no
    integration suite. Nothing affected: no test step (`skipped` says why).
    tier "full": every gate slot including `testIntegration`.
    no tier: every gate slot, `testIntegration` unless `unit_only`."""
    commands = (resolved or {}).get("commands") or {}
    if tier != "task":
        integration = tier == "full" or not unit_only
        return {"steps": [(s, commands[s], commands[s]) for s in gate_slots(commands, only, integration)],
                "affected": None, "skipped": {}}
    steps = [(s, commands[s], commands[s]) for s in gate_slots(commands, only, integration=False)
             if s in TASK_SLOTS]
    info, skipped = None, {}
    has_test = bool((commands.get("test") or "").strip())
    if (has_test or (commands.get("testOne") or "").strip()) and (only is None or "test" in only):
        if changed is None:
            if not base:
                info = {"strategy": "", "mode": "full", "targets": [],
                        "reason": "no --base was given, so the changed files are not known"}
            else:
                try:
                    changed = _affected.changed_files(root, base)
                except RuntimeError as exc:
                    info = {"strategy": "", "mode": "full", "targets": [],
                            "reason": "the changes since %s are not known (%s)" % (base, exc)}
        if info is None:
            info = _affected.compute(root, resolved, changed)
        if info["mode"] == "run":
            steps.append(("test", info["command"], info["configured"]))
        elif info["mode"] == "full" and has_test:
            steps.append(("test", commands["test"], commands["test"]))
        elif info["mode"] == "full":
            skipped["test"] = "the full suite is needed (%s) and the repo defines no `test`" % info["reason"]
        else:
            skipped["test"] = ("no affected tests (%s); the full suite runs once per repo before the audit"
                               % info["reason"])
    return {"steps": steps, "affected": info, "skipped": skipped}


def step_budget(repo: str, slot: str, configured: str, commands: dict, fallback: float) -> float:
    """`slot_budget` of one step. A `test` step that runs a subset (the affected tests) gets at
    least the full suite's budget: its own history may come from a much smaller subset."""
    budget = slot_budget(repo, slot, configured, fallback)
    full = (commands or {}).get(slot) or ""
    if slot == "test" and full and configured != full:
        budget = max(budget, slot_budget(repo, slot, full, fallback))
    return budget


def _skipped(slot: str, command: str, why: str) -> dict:
    return _verify.CommandResult(slot, command, True, _verify.OUTCOME_SKIPPED, None, [], 0,
                                 error=why).to_dict()


def run_steps(root: str, steps: list, timeout_for: Callable[[str, str], float], env: dict = None,
              run: Callable = _verify.run_one, fail_fast: bool = True, cache: bool = False,
              history_repo: str = "", skipped: dict = None, prepare: str = "") -> dict:
    """Run `[(slot, command, configured)]` in order; after the first slot that is not ok, skip the rest.

    `timeout_for(slot, configured)` is each slot's budget. A slot whose outcome is anything but
    `ok` (failed, timeout, not invocable) ends the run: what follows is reported
    `skipped`, naming the slot that stopped it. With `cache`, a slot that passed on
    this exact tree state, command and env is reused (`"cached": true`) instead of run,
    and a new pass is stored (gate_cache); nothing else is ever stored. With
    `history_repo`, each pass's duration is recorded for the slot's next budget.

    `prepare` (gate.prepare: generated code, a schema) runs once, before the first slot that is
    not answered from the cache; when it fails, every slot left is `not_invocable` -- the
    environment could not run the checks, which says nothing about the code."""
    results: dict = {}
    stopped_by = ""
    unprepared = ""  # why gate.prepare failed, once it did
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
        if prepare and "prepare" not in results:
            pres = call_with(run, "prepare", prepare, root, timeout_for("prepare", prepare), configured=prepare,
                             env=env or None)
            results["prepare"] = pres.to_dict()
            if pres.outcome != _verify.OUTCOME_OK:
                results["prepare"]["errorLines"] = _verify.failure_lines(pres.output)
                lines = results["prepare"]["errorLines"] or [pres.error or "exit %s" % pres.exit_code]
                unprepared = "gate.prepare failed ($ %s): %s" % (prepare, " | ".join(lines)[-500:])
        if unprepared:
            results[slot] = _verify.CommandResult(slot, configured, False, _verify.OUTCOME_NOT_INVOCABLE, None,
                                                  [], 0, error=unprepared).to_dict()
            continue
        budget = timeout_for(slot, configured)
        res = call_with(run, slot, command, root, budget, configured=configured, env=env or None)
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
    for slot, why in (skipped or {}).items():
        results[slot] = _skipped(slot, "", why)
    budgets = [timeout_for(s, k) for s, _c, k in steps]
    return {
        "root": root,
        "timeoutSeconds": max(budgets) if budgets else 0,
        "checkedAt": time.time(),
        "results": results,
        "allInvocable": all(r["invocable"] for r in results.values()),
        "order": [s for s, _c, _k in steps],
    }
