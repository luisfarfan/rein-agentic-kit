"""`decide_plan_check`: do these findings stop the run?

The same findings have TWO consumers with two correct answers, which is why
this decision lives apart from the command that produces them.

Inside `/rein:rein-plan` the command reports and exits 0 — D5, "never a silent
skip, never a hard stop". It runs before the plan is written, where a hard stop
would replace the planner's judgement with a regex's. That default is load-
bearing and these tests pin it.

A RUNNER is the other consumer: loop.js called decidePlanCheck and stopped
before Isolate, because a BLOCKING finding caught there costs nothing and the
same one caught at Review costs a whole run. `--gate` is that path.

The scoping rule is the subtle half and it is NOT "only findings about my
tasks": a finding with no taskId is plan-level and concerns the whole run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")
sys.path.insert(0, os.path.join(REPO, "plugins", "rein", "lib"))

import plan_check  # noqa: E402

STOP, GO = plan_check.DECISION_STOP, plan_check.DECISION_CONTINUE


def _f(severity, task_id="", text="x"):
    return {"severity": severity, "taskId": task_id, "text": text}


class DecidePlanCheckTestCase(unittest.TestCase):

    def test_no_findings_continues(self):
        self.assertEqual(plan_check.decide_plan_check([], [])["decision"], GO)

    def test_important_alone_never_stops(self):
        d = plan_check.decide_plan_check([_f("IMPORTANT", "T001")], [])
        self.assertEqual(d["decision"], GO)

    def test_one_blocking_stops(self):
        d = plan_check.decide_plan_check([_f("BLOCKING", "T001")], [])
        self.assertEqual(d["decision"], STOP)
        self.assertIn("1 BLOCKING", d["reason"])
        self.assertEqual(len(d["blocking"]), 1)

    # -- scoping ----------------------------------------------------------

    def test_a_blocking_finding_on_a_task_this_run_skips_does_not_stop_it(self):
        """It cannot waste an implementer nobody is paying for that task."""
        d = plan_check.decide_plan_check([_f("BLOCKING", "T009")], ["T001", "T002"])
        self.assertEqual(d["decision"], GO)

    def test_a_blocking_finding_on_a_task_in_the_run_does_stop_it(self):
        d = plan_check.decide_plan_check([_f("BLOCKING", "T002")], ["T001", "T002"])
        self.assertEqual(d["decision"], STOP)

    def test_a_plan_level_finding_stops_whatever_the_scope(self):
        """No taskId means a Scope contradiction or a broken dependency order:
        it concerns the whole run, so scoping must not hide it."""
        d = plan_check.decide_plan_check([_f("BLOCKING", "")], ["T001"])
        self.assertEqual(d["decision"], STOP)

    def test_an_empty_scope_means_everything_is_in_play(self):
        d = plan_check.decide_plan_check([_f("BLOCKING", "T009")], [])
        self.assertEqual(d["decision"], STOP)

    def test_task_ids_match_case_insensitively(self):
        d = plan_check.decide_plan_check([_f("BLOCKING", "t002")], ["T002"])
        self.assertEqual(d["decision"], STOP)

    # -- shapes it must tolerate -------------------------------------------

    def test_a_bare_string_finding_is_treated_as_important(self):
        d = plan_check.decide_plan_check(["something the checker said"], [])
        self.assertEqual(d["decision"], GO)
        self.assertEqual(d["findings"][0]["severity"], "IMPORTANT")

    def test_severity_matching_is_case_insensitive(self):
        self.assertEqual(plan_check.decide_plan_check([_f("blocking", "T1")], [])["decision"], STOP)

    def test_nothing_at_all_is_not_a_crash(self):
        for bad in (None, []):
            with self.subTest(findings=bad):
                self.assertEqual(plan_check.decide_plan_check(bad, None)["decision"], GO)


class PlanCheckCliTestCase(unittest.TestCase):
    """The exit code is the part a shell can act on."""

    PLAN = (
        "## Tasks\n"
        "- [ ] T001 Add the thing\n"
        "  - Verification: `pytest tests/test_does_not_exist.py`\n"
        "- [ ] T002 Something else\n"
        "  - Depends on: T999\n"
    )

    CLEAN = (
        "## Tasks\n"
        "- [ ] T001 Add the thing\n"
        "  - Acceptance: it works\n"
        "  - Verification: `python3 -m unittest tests.test_plan_check_decision`\n"
    )

    def _run(self, text, *extra):
        """Always with --gate: without it D5 applies and the exit is always 0."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tasks.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return subprocess.run([sys.executable, REIN, "plan-check", path, "--gate", *extra],
                                  capture_output=True, text=True, cwd=tmp)

    def test_a_plan_with_blocking_findings_exits_1(self):
        res = self._run(self.PLAN)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertEqual(json.loads(res.stdout)["decision"], STOP)

    def test_scoping_to_a_clean_task_exits_0(self):
        """T002's broken dependency cannot stop a run that only does T001."""
        res = self._run("## Tasks\n- [ ] T001 Fine\n  - Acceptance: it works\n"
                        "- [ ] T002 Broken\n  - Depends on: T999\n", "--tasks=T001")
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_the_same_plan_unscoped_exits_1(self):
        res = self._run("## Tasks\n- [ ] T001 Fine\n  - Acceptance: it works\n"
                        "- [ ] T002 Broken\n  - Depends on: T999\n")
        self.assertEqual(res.returncode, 1, res.stdout)

    def test_a_missing_file_is_setup_for_a_runner(self):
        res = subprocess.run([sys.executable, REIN, "plan-check", "/nope/nothing.md", "--gate"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 126, res.stdout)

    def test_no_argument_is_setup_too(self):
        res = subprocess.run([sys.executable, REIN, "plan-check", "--gate"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 126, res.stdout)

    # -- D5 is the default and must stay that way --------------------------

    def test_without_the_flag_a_blocking_plan_still_exits_0(self):
        """D5: never a silent skip, never a hard stop. This command runs inside
        the planning skill, where the judgement belongs to the planner."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tasks.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.PLAN)
            res = subprocess.run([sys.executable, REIN, "plan-check", path],
                                 capture_output=True, text=True, cwd=tmp)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(json.loads(res.stdout)["decision"], STOP,
                         "the verdict is still reported -- it just does not fail the caller")

    def test_without_the_flag_a_missing_file_still_exits_0(self):
        res = subprocess.run([sys.executable, REIN, "plan-check", "/nope/nothing.md"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_the_json_still_carries_every_finding(self):
        res = self._run(self.PLAN)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["findings"], "findings must not disappear behind the verdict")
        self.assertIn("reason", payload)


if __name__ == "__main__":
    unittest.main()
