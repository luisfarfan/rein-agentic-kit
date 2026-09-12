"""`decide_claimable`: is the task claimable, and can its gate even run?

`next_task` answers the first half from the plan alone. The second half lived
only inside loop.js's Prepare phase, so it existed for exactly one caller and
vanished for everyone else. This is that rule in the library.

Freshness is this kit's existing convention, not a timestamp: a recorded
outcome is about the command it was recorded against, so it stops counting the
moment the resolved command changes.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import gate  # noqa: E402

READY = {"ready": True, "reason": "", "taskId": "T001", "remaining": 3}
CMDS = {"test": "pytest -q", "lint": "ruff check .", "typecheck": "mypy ."}


def _state(**slots):
    """A persisted verify report. `command` defaults to the resolved one, so a
    test only says otherwise when it means to simulate staleness."""
    results = {}
    for name, spec in slots.items():
        results[name] = {
            "outcome": spec.get("outcome", "ok"),
            "invocable": spec.get("invocable", True),
            "command": spec.get("command", CMDS.get(name)),
        }
    return {"results": results}


class DecideClaimableTestCase(unittest.TestCase):

    # -- the rule that only existed inside the loop -------------------------

    def test_an_uninvocable_test_command_blocks_the_claim(self):
        d = gate.decide_claimable(
            READY, _state(test={"outcome": "not_invocable", "invocable": False}), CMDS)
        self.assertFalse(d["ready"])
        self.assertIn("not invocable", d["reason"])
        self.assertIn("gate that cannot pass", d["reason"])

    def test_a_working_test_command_leaves_the_claim_alone(self):
        d = gate.decide_claimable(READY, _state(test={"outcome": "ok"}), CMDS)
        self.assertTrue(d["ready"])
        self.assertTrue(d["gateProven"])
        self.assertEqual(d["taskId"], "T001", "the plan's own answer is preserved")

    def test_a_failing_test_command_does_not_block_a_claim(self):
        """Red tests are the ordinary state of a repo mid-change; that is what
        the task is FOR."""
        d = gate.decide_claimable(READY, _state(test={"outcome": "failed"}), CMDS)
        self.assertTrue(d["ready"])

    # -- lint and typecheck warn, they do not block ------------------------

    def test_an_uninvocable_linter_only_warns(self):
        """You can still write code with a broken linter."""
        d = gate.decide_claimable(
            READY, _state(test={"outcome": "ok"},
                          lint={"outcome": "not_invocable", "invocable": False}), CMDS)
        self.assertTrue(d["ready"])
        self.assertEqual(len(d["warnings"]), 1)
        self.assertIn("lint", d["warnings"][0])
        self.assertIn("carried", d["warnings"][0])

    def test_both_uninvocable_checkers_produce_two_warnings(self):
        d = gate.decide_claimable(
            READY, _state(test={"outcome": "ok"},
                          lint={"invocable": False, "outcome": "not_invocable"},
                          typecheck={"invocable": False, "outcome": "not_invocable"}), CMDS)
        self.assertTrue(d["ready"])
        self.assertEqual(len(d["warnings"]), 2)

    def test_existing_warnings_are_preserved_not_replaced(self):
        incoming = {**READY, "warnings": ["something the plan already said"]}
        d = gate.decide_claimable(
            incoming, _state(test={"outcome": "ok"},
                             lint={"invocable": False, "outcome": "not_invocable"}), CMDS)
        self.assertEqual(len(d["warnings"]), 2)
        self.assertIn("something the plan already said", d["warnings"])

    # -- freshness is about the command, not the clock ---------------------

    def test_an_outcome_recorded_against_another_command_does_not_count(self):
        """A lockfile change can rewrite the command out from under a report
        that ran seconds ago, so the timestamp proves nothing."""
        stale = _state(test={"outcome": "not_invocable", "invocable": False,
                             "command": "pytest -q --old-flag"})
        d = gate.decide_claimable(READY, stale, CMDS)
        self.assertTrue(d["ready"], "a stale uninvocable must not block")
        self.assertFalse(d["gateProven"])
        self.assertIn("no fresh", d["gateReason"])

    def test_a_fresh_ok_marks_the_gate_proven(self):
        d = gate.decide_claimable(READY, _state(test={"outcome": "ok"}), CMDS)
        self.assertTrue(d["gateProven"])
        self.assertNotIn("gateReason", d)

    # -- nothing known: report, never block -------------------------------

    def test_no_verify_state_reports_unproven_without_blocking(self):
        """Refusing to claim on a fresh checkout is friction that gets the
        command bypassed, which is worse than an unproven gate."""
        for empty in (None, {}, {"results": {}}):
            with self.subTest(state=empty):
                d = gate.decide_claimable(READY, empty, CMDS)
                self.assertTrue(d["ready"])
                self.assertFalse(d["gateProven"])
                self.assertIn("rein gate", d["gateReason"])

    # -- the monorepo stop -------------------------------------------------

    def test_an_unconfigured_monorepo_root_blocks(self):
        d = gate.decide_claimable(READY, _state(test={"outcome": "ok"}), CMDS,
                                  monorepo_unconfigured=True)
        self.assertFalse(d["ready"])
        self.assertIn("subproject", d["reason"])

    def test_the_monorepo_stop_outranks_everything_else(self):
        d = gate.decide_claimable(
            READY, _state(test={"outcome": "not_invocable", "invocable": False}), CMDS,
            monorepo_unconfigured=True)
        self.assertFalse(d["ready"])
        self.assertIn("subproject", d["reason"], "no command resolves at all yet")

    # -- it never invents readiness ---------------------------------------

    def test_a_plan_that_was_not_ready_stays_not_ready(self):
        not_ready = {"ready": False, "reason": "no pending tasks", "taskId": ""}
        d = gate.decide_claimable(not_ready, _state(test={"outcome": "ok"}), CMDS)
        self.assertFalse(d["ready"])
        self.assertEqual(d["reason"], "no pending tasks", "the plan's reason survives")

    def test_a_missing_result_is_not_a_crash(self):
        for bad in (None, {}):
            with self.subTest(result=bad):
                d = gate.decide_claimable(bad, None, None)
                self.assertFalse(d.get("gateProven"))


class MonorepoUnconfiguredTestCase(unittest.TestCase):
    """The boolean loop.js asked an LLM to derive from prose."""

    def test_a_monorepo_root_with_unresolved_commands_is_unconfigured(self):
        self.assertTrue(gate.monorepo_unconfigured(
            {"stack": "monorepo", "missingCommands": ["test", "lint"]}))

    def test_a_monorepo_with_every_command_resolved_is_configured(self):
        self.assertFalse(gate.monorepo_unconfigured(
            {"stack": "monorepo", "missingCommands": []}))

    def test_an_ordinary_repo_missing_commands_is_not_a_monorepo_problem(self):
        """A plain python repo with no linter configured is not the same thing,
        and blocking it would stop work for no reason."""
        self.assertFalse(gate.monorepo_unconfigured(
            {"stack": "python", "missingCommands": ["lint"]}))

    def test_it_does_not_crash_on_a_missing_resolution(self):
        for bad in (None, {}, {"stack": None}):
            with self.subTest(resolved=bad):
                self.assertFalse(gate.monorepo_unconfigured(bad))


if __name__ == "__main__":
    unittest.main()
