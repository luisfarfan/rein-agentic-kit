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


RENDERED = {"mode": "rendered", "tools": ["browser"], "requires": [], "forbids": []}
UNIT = {"mode": "unit", "tools": [], "requires": [], "forbids": []}
SERVE = {"command": "npm run dev", "url": "http://localhost:4321"}
GOOD_RENDER = {"rendered": True, "httpStatus": 200, "evidence": ["the heading is there"]}


class RenderPolicyTestCase(unittest.TestCase):
    """"The tests pass but the UI is broken" -- the failure a unit gate cannot see.

    `detect` gives every frontend subtype mode "rendered". The old loop honoured
    that and then let it slide: a rendered-unverified outcome was documented as
    NOT blocking approval, which is the switch this kit exists to remove.
    """

    def test_a_unit_policy_ignores_render_entirely(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=UNIT)
        self.assertEqual(d["exit"], 0)

    def test_a_green_suite_is_not_enough_when_a_render_is_required(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED, serve=SERVE)
        self.assertEqual(d["decision"], gate.GATE_SETUP)
        self.assertEqual(d["exit"], 126)
        self.assertIn("none was recorded", d["reason"])

    def test_no_browser_tool_says_so_and_says_what_to_configure(self):
        d = gate.decide_gate(_report(test=_slot("ok")),
                             verify_policy={**RENDERED, "tools": []}, serve=SERVE)
        self.assertEqual(d["exit"], 126)
        self.assertIn("no browser tool reachable", d["reason"])
        self.assertIn("commands.serve", d["reason"])

    def test_no_serve_command_is_also_unattemptable(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED,
                             serve={"command": "", "url": ""})
        self.assertEqual(d["exit"], 126)
        self.assertIn("serve command/url", d["reason"])

    def test_a_real_render_completes_the_gate(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED,
                             serve=SERVE, render=GOOD_RENDER)
        self.assertEqual(d["exit"], 0)
        self.assertIn("render", d["passed"])

    def test_evidence_present_settles_whether_a_render_was_possible(self):
        """Somebody clearly managed to look, so the dispatch question is moot."""
        d = gate.decide_gate(_report(test=_slot("ok")),
                             verify_policy={**RENDERED, "tools": []},
                             serve={"command": "", "url": ""}, render=GOOD_RENDER)
        self.assertEqual(d["exit"], 0)

    def test_a_claim_without_facts_is_a_failed_render(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED, serve=SERVE,
                             render={"rendered": True, "httpStatus": 200, "evidence": []})
        self.assertEqual(d["decision"], gate.GATE_RED)
        self.assertIn("evidence is empty", d["reason"])

    def test_a_non_2xx_status_fails(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED, serve=SERVE,
                             render={"rendered": True, "httpStatus": 500, "evidence": ["x"]})
        self.assertEqual(d["exit"], 1)
        self.assertIn("500 is not 2xx", d["reason"])

    def test_an_absent_status_fails_and_says_absent(self):
        d = gate.decide_gate(_report(test=_slot("ok")), verify_policy=RENDERED, serve=SERVE,
                             render={"rendered": True, "evidence": ["x"]})
        self.assertIn("is absent", d["reason"])

    def test_a_failing_suite_still_outranks_the_render(self):
        """The more actionable answer wins: fix the tests first."""
        d = gate.decide_gate(_report(test=_slot("failed", 1)), verify_policy=RENDERED, serve=SERVE)
        self.assertEqual(d["decision"], gate.GATE_RED)
        self.assertIn("test", d["reason"])

    def test_broken_setup_still_outranks_the_render(self):
        d = gate.decide_gate(_report(test=_slot("not_invocable", 127, invocable=False)),
                             verify_policy=RENDERED, serve=SERVE)
        self.assertIn("setup problem", d["reason"])


class RenderPrimitivesTestCase(unittest.TestCase):
    """The two functions as they were inside loop.js, now reachable."""

    def test_dispatch_is_silent_when_the_policy_does_not_ask(self):
        d = gate.decide_render_dispatch(UNIT, SERVE)
        self.assertFalse(d["dispatch"])
        self.assertFalse(d["unverified"], "not asked for is not the same as could not look")

    def test_dispatch_when_tools_and_serve_are_both_there(self):
        self.assertTrue(gate.decide_render_dispatch(RENDERED, SERVE)["dispatch"])

    def test_rendered_false_fails(self):
        self.assertTrue(gate.decide_render_outcome({"rendered": False})["failed"])

    def test_a_boolean_is_not_an_http_status(self):
        """`True` is an int in Python; it must not pass for 200."""
        d = gate.decide_render_outcome({"rendered": True, "httpStatus": True, "evidence": ["x"]})
        self.assertTrue(d["failed"])

    def test_evidence_must_be_a_list_not_a_string(self):
        d = gate.decide_render_outcome({"rendered": True, "httpStatus": 200, "evidence": "looks fine"})
        self.assertTrue(d["failed"], "a sentence is not a list of facts")

    def test_an_empty_render_report_fails_rather_than_crashing(self):
        for bad in (None, {}):
            with self.subTest(render=bad):
                self.assertTrue(gate.decide_render_outcome(bad)["failed"])


if __name__ == "__main__":
    unittest.main()
