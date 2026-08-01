"""`rein verify --plan` runs each task's own Verification before implementers.

The three hours this exists to prevent: a real plan named
`tests/unit/test_tech_video_rubric.py`, a module that did not exist, with `-k`
filters that selected nothing. Both "passed" -- pytest exits 5 on
`no tests collected` and the head of its output is plugin warnings -- so the
implementers were paid, the work was built against verifications that could
confirm nothing, and the defect surfaced two hours later at review.

The distinction the whole module turns on: a verification that FAILS before
the work exists is the normal state of a plan. A verification that proves
NOTHING is not, and it is the one that must stop a run.
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
import verify  # noqa: E402


class PlanTree:
    def __init__(self, tasks: str, files: dict | None = None):
        self.tasks, self.files = tasks, files or {}

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        with open(os.path.join(root, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write("# Change: probe\n## Why\nx\n---\n" + self.tasks)
        for rel, body in self.files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        return root

    def __exit__(self, *exc):
        self.tmp.cleanup()


def _task(tid: str, cmd: str) -> str:
    return (
        f"- [ ] {tid} probe\n"
        f"  - Type: implementation\n"
        f"  - Depends on: none\n"
        f"  - Verification: `{cmd}`\n"
        f"  - Acceptance:\n"
        f"    - something\n\n"
    )


class TestAVerificationThatProvesNothingIsCaught(unittest.TestCase):
    def _run(self, root: str) -> dict:
        sys.path.insert(0, os.path.join(REPO, "plugins", "rein", "lib"))
        import plan
        return verify.verify_plan(root, plan.read_plan(root, "")["tasks"])

    def test_a_missing_test_module_proves_nothing(self):
        """The exact shape that cost three hours."""
        with PlanTree(_task("T003", "python3 -m pytest tests/unit/test_nope.py -k rubric"),
                      {"tests/test_real.py": "def test_ok():\n    assert True\n"}) as root:
            report = self._run(root)
        self.assertEqual(report["results"]["T003"]["outcome"], verify.OUTCOME_PROVES_NOTHING)
        self.assertEqual(report["unusable"], ["T003"])
        self.assertFalse(report["allUsable"])

    def test_a_selector_that_matches_nothing_proves_nothing(self):
        """pytest exits 5 and buries the phrase under plugin warnings, so the
        exit code carries this one, not the text."""
        with PlanTree(_task("T005", "python3 -m pytest tests/ -k no_such_selector_anywhere"),
                      {"tests/test_real.py": "def test_ok():\n    assert True\n"}) as root:
            report = self._run(root)
        self.assertEqual(report["results"]["T005"]["outcome"], verify.OUTCOME_PROVES_NOTHING)

    def test_a_verification_that_simply_fails_is_FINE(self):
        """The distinction the module turns on: before the work exists, a
        failing verification is the normal state of a plan. Treating it as a
        problem would stop every run of every plan ever written."""
        with PlanTree(_task("T007", 'python3 -c "raise SystemExit(1)"')) as root:
            report = self._run(root)
        self.assertEqual(report["results"]["T007"]["outcome"], verify.OUTCOME_FAILED)
        self.assertTrue(report["allUsable"])
        self.assertEqual(report["unusable"], [])

    def test_a_verification_that_passes_is_fine_too(self):
        with PlanTree(_task("T001", 'python3 -c "pass"')) as root:
            report = self._run(root)
        self.assertEqual(report["results"]["T001"]["outcome"], verify.OUTCOME_OK)
        self.assertTrue(report["allUsable"])

    def test_a_missing_binary_is_unusable_not_merely_failed(self):
        with PlanTree(_task("T002", "definitely-not-a-binary --run")) as root:
            report = self._run(root)
        self.assertIn(report["results"]["T002"]["outcome"],
                      (verify.OUTCOME_NOT_INVOCABLE, verify.OUTCOME_PROVES_NOTHING))
        self.assertEqual(report["unusable"], ["T002"])

    def test_a_task_with_no_verification_is_skipped_not_failed(self):
        body = ("- [ ] T004 probe\n  - Type: implementation\n  - Depends on: none\n"
                "  - Acceptance:\n    - something\n\n")
        with PlanTree(body) as root:
            report = self._run(root)
        self.assertEqual(report["results"]["T004"]["outcome"], verify.OUTCOME_SKIPPED)
        self.assertTrue(report["allUsable"])


class TestTheCliContract(unittest.TestCase):
    def _cli(self, root: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, REIN, "verify", root, "--plan", *extra],
                              capture_output=True, text=True, timeout=120)

    def test_exit_is_non_zero_only_for_unusable_never_for_failing(self):
        with PlanTree(_task("T007", 'python3 -c "raise SystemExit(1)"')) as root:
            failing = self._cli(root)
        with PlanTree(_task("T003", "python3 -m pytest tests/unit/test_nope.py"),
                      {"tests/test_real.py": "def test_ok():\n    assert True\n"}) as root:
            unusable = self._cli(root)
        self.assertEqual(failing.returncode, 0, "a failing verification must not stop a run")
        self.assertEqual(unusable.returncode, 1, "one that proves nothing must")

    def test_json_names_what_cannot_prove_anything(self):
        with PlanTree(_task("T003", "python3 -m pytest tests/unit/test_nope.py"),
                      {"tests/test_real.py": "def test_ok():\n    assert True\n"}) as root:
            proc = self._cli(root, "--json")
        report = json.loads(proc.stdout)
        self.assertEqual(report["unusable"], ["T003"])
        self.assertTrue(report["results"]["T003"]["provesNothing"])
