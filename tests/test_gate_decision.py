"""`decide_gate`: did the configured commands PASS -- not "could they be invoked".

`rein verify` already answers invocability and exits 0 when everything could be
invoked, which means a suite that ran and failed exits 0 there. That is correct
for a precheck and unusable as a gate, so this is the other verdict over the
same report.

Every case below is a shape that actually occurs in `verify_commands` output.
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


def _slot(outcome, exit_code=0, invocable=True):
    return {"outcome": outcome, "exitCode": exit_code, "invocable": invocable}


def _report(**slots):
    return {"root": "/x", "results": dict(slots), "allInvocable": True}


class DecideGateTestCase(unittest.TestCase):

    # -- green ------------------------------------------------------------

    def test_everything_passed_is_green(self):
        d = gate.decide_gate(_report(test=_slot("ok"), lint=_slot("ok")))
        self.assertEqual(d["decision"], gate.GATE_GREEN)
        self.assertEqual(d["exit"], 0)
        self.assertEqual(d["passed"], ["lint", "test"])

    # -- red: it ran and the code is wrong --------------------------------

    def test_a_failing_test_is_red_not_green(self):
        """The exact case `rein verify` reports as exit 0."""
        d = gate.decide_gate(_report(test=_slot("failed", 1)))
        self.assertEqual(d["decision"], gate.GATE_RED)
        self.assertEqual(d["exit"], 1)
        self.assertIn("test", d["reason"])

    def test_lint_failing_also_blocks(self):
        d = gate.decide_gate(_report(test=_slot("ok"), lint=_slot("failed", 1)))
        self.assertEqual(d["exit"], 1)

    # -- setup: the environment failed, not the code ----------------------

    def test_uninvocable_test_is_setup_not_red(self):
        d = gate.decide_gate(_report(test=_slot("not_invocable", 127, invocable=False)))
        self.assertEqual(d["decision"], gate.GATE_SETUP)
        self.assertEqual(d["exit"], 126)
        self.assertIn("setup problem", d["reason"])

    def test_a_timeout_is_setup(self):
        d = gate.decide_gate(_report(test=_slot("timeout", None)))
        self.assertEqual(d["exit"], 126)

    def test_setup_beats_red(self):
        """An environment that cannot run half its checks has not earned the
        right to call the code wrong."""
        d = gate.decide_gate(_report(
            test=_slot("failed", 1),
            typecheck=_slot("not_invocable", 127, invocable=False),
        ))
        self.assertEqual(d["decision"], gate.GATE_SETUP)
        self.assertEqual(d["exit"], 126)

    def test_uninvocable_typecheck_blocks_here_even_though_precheck_only_warns(self):
        """decideGatePrecheck runs BEFORE the work and warns; this runs after
        and blocks. A check that never ran is not a check that passed."""
        d = gate.decide_gate(_report(test=_slot("ok"), typecheck=_slot("not_invocable", 127, invocable=False)))
        self.assertEqual(d["exit"], 126)

    def test_invocable_false_is_setup_whatever_the_outcome_says(self):
        d = gate.decide_gate(_report(test=_slot("failed", 1, invocable=False)))
        self.assertEqual(d["decision"], gate.GATE_SETUP)

    # -- the loudest possible lie -----------------------------------------

    def test_nothing_ran_is_never_green(self):
        """A gate that reports success because it checked nothing is the shape
        of every silent pass this kit exists to prevent."""
        d = gate.decide_gate(_report())
        self.assertEqual(d["decision"], gate.GATE_SETUP)
        self.assertEqual(d["exit"], 126)
        self.assertIn("nothing is proven", d["reason"])

    def test_only_non_gate_slots_is_not_green_either(self):
        d = gate.decide_gate(_report(testOne=_slot("ok"), serve=_slot("skipped")))
        self.assertEqual(d["exit"], 126)

    # -- slots that prove nothing either way ------------------------------

    def test_inconclusive_testone_neither_passes_nor_blocks(self):
        """testOne runs against a synthetic target no suite owns."""
        d = gate.decide_gate(_report(test=_slot("ok"), testOne=_slot("inconclusive", 4)))
        self.assertEqual(d["exit"], 0)
        self.assertIn("testOne [inconclusive]", d["ignored"])

    def test_serve_is_never_a_gate(self):
        """A dev server never runs to completion; verify reports it skipped."""
        d = gate.decide_gate(_report(test=_slot("ok"), serve=_slot("skipped")))
        self.assertEqual(d["exit"], 0)

    def test_a_skipped_gate_slot_does_not_pass_on_its_own(self):
        d = gate.decide_gate(_report(build=_slot("skipped")))
        self.assertEqual(d["exit"], 126, "skipped alone proves nothing")

    # -- the review half ---------------------------------------------------

    def test_review_is_not_required_unless_asked(self):
        d = gate.decide_gate(_report(test=_slot("ok")), review=None, require_review=False)
        self.assertEqual(d["exit"], 0)

    def test_required_review_missing_is_red(self):
        d = gate.decide_gate(_report(test=_slot("ok")),
                             review={"ok": False, "reason": "no review episode recorded"},
                             require_review=True)
        self.assertEqual(d["decision"], gate.GATE_RED)
        self.assertIn("no review episode recorded", d["reason"])

    def test_required_review_satisfied_is_green(self):
        d = gate.decide_gate(_report(test=_slot("ok")),
                             review={"ok": True}, require_review=True)
        self.assertEqual(d["exit"], 0)

    def test_a_broken_environment_outranks_a_missing_review(self):
        d = gate.decide_gate(_report(test=_slot("not_invocable", 127, invocable=False)),
                             review={"ok": False, "reason": "none"}, require_review=True)
        self.assertEqual(d["decision"], gate.GATE_SETUP)

    # -- defensive ---------------------------------------------------------

    def test_an_empty_or_missing_report_is_setup_not_a_crash(self):
        for bad in (None, {}, {"results": None}):
            with self.subTest(report=bad):
                self.assertEqual(gate.decide_gate(bad)["exit"], 126)

    def test_the_three_exit_codes_are_distinct(self):
        self.assertEqual(len({gate.EXIT_GREEN, gate.EXIT_RED, gate.EXIT_SETUP}), 3)


if __name__ == "__main__":
    unittest.main()
