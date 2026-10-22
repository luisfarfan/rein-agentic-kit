"""Audit and gate fix tasks (AUDIT<n>-<repo>, GATE<n>-<repo>) carry a verification command.

In the retest the api fix worker had none and had to ask what to run. A fix task gets the
verification of the original tasks of its repo that touched the files the findings name, else
the repo's affected tests (testOne) for those files, else its full suite."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
import ship_state  # noqa: E402
from test_ship import PLAN, FakeOrca, ShipCase, scripted_gate  # noqa: E402

TS = ship.ss.TaskState

PLAN_TASKS = {"tasks": [
    {"id": "T001", "repo": "api", "verification": "`pytest -q tests/test_receipts.py` the receipts"},
    {"id": "T002", "repo": "api", "verification": "`pytest -q tests/test_docs.py`"},
    {"id": "T003", "repo": "pos", "verification": "`pnpm exec ng test --include=src/a.spec.ts`"},
]}


class Helper(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.wt, True)
        for rel in ("app/receipts.py", "app/docs.py", "tests/test_receipts.py", "tests/test_docs.py"):
            os.makedirs(os.path.dirname(os.path.join(self.wt, rel)), exist_ok=True)
            open(os.path.join(self.wt, rel), "w").close()
        self.state = ship.ss.ShipState(change="demo", plan_path="tasks.md")
        self.state.worktree_paths["api"] = self.wt
        for tid, touched in (("T001", ["app/receipts.py", "tests/test_receipts.py"]), ("T002", ["app/docs.py"])):
            entry = next(e for e in PLAN_TASKS["tasks"] if e["id"] == tid)
            self.state.tasks[tid] = TS(id=tid, repo="api", deps=[], human_review=False,
                                       verification=entry["verification"], type="implementation",
                                       status="done", touched=touched)
        self.resolved = {"stack": "python", "commands": {"test": "pytest -q", "testOne": "pytest -q {target}"}}

    def fix(self, tid, text):
        kw = {"audit_findings": text} if tid.startswith("AUDIT") else {}
        return TS(id=tid, repo="api", deps=[], human_review=False, verification="", type="implementation",
                  last_failure=text, **kw)

    def verification(self, task):
        return ship.fix_verification(task, PLAN_TASKS, self.state, self.wt, self.resolved)

    def test_the_original_tasks_that_touched_the_named_files(self):
        out = self.verification(self.fix("AUDIT1-api", "BLOCKING: app/receipts.py:12 marks 4xx as not sent"))
        self.assertTrue(out.startswith("`pytest -q tests/test_receipts.py`"), out)
        self.assertIn("T001", out)
        self.assertNotIn("test_docs", out)
        self.assertEqual(ship.task_verification_command(out)[0], "pytest -q tests/test_receipts.py")

    def test_several_tasks_are_joined_into_one_command(self):
        out = self.verification(self.fix("AUDIT1-api", "BLOCKING: receipts.py and docs.py disagree"))
        self.assertEqual(ship.task_verification_command(out)[0],
                         "pytest -q tests/test_receipts.py && pytest -q tests/test_docs.py")

    def test_no_original_task_touched_them_the_affected_tests_through_test_one(self):
        os.makedirs(os.path.join(self.wt, "lib"))
        open(os.path.join(self.wt, "lib", "money.py"), "w").close()
        open(os.path.join(self.wt, "tests", "test_money.py"), "w").close()
        out = self.verification(self.fix("AUDIT2-api", "BLOCKING: lib/money.py rounds wrong"))
        self.assertEqual(ship.task_verification_command(out)[0], "pytest -q tests/test_money.py")

    def test_a_gate_fix_names_its_failing_test(self):
        out = self.verification(self.fix("GATE1-api", "FAILED tests/test_receipts.py::test_x - AssertionError"))
        self.assertEqual(ship.task_verification_command(out)[0], "pytest -q tests/test_receipts.py")

    def test_nothing_named_the_full_suite(self):
        out = self.verification(self.fix("GATE1-api", "the suite timed out"))
        self.assertEqual(ship.task_verification_command(out)[0], "pytest -q")

    def test_no_test_command_at_all_leaves_it_to_the_gate(self):
        self.resolved = {"stack": "unknown", "commands": {}}
        self.assertEqual(self.verification(self.fix("GATE1-api", "the suite timed out")), "")


class GateRecordsWhatATaskTouched(unittest.TestCase):
    def test_a_verdict_merges_the_touched_files(self):
        st = ship.ss.ShipState(change="demo", plan_path="tasks.md")
        st.tasks["T1"] = TS(id="T1", repo="a", deps=[], human_review=False, verification="", type="implementation",
                            status="gating", dispatch_id="d1", touched=["x.py"])
        new, _ = sd.decide(st, sd.GateVerdict("T1", 1, "red", "d1", touched=["y.py", "x.py"]), sd.ShipConfig())
        self.assertEqual(new.tasks["T1"].touched, ["x.py", "y.py"])

    def test_old_states_load_without_it(self):
        self.assertEqual(TS(id="T", repo="a", deps=[], human_review=False, verification="", type="").touched, [])


class Driver(ShipCase):
    def test_audit_fix_tasks_are_dispatched_with_a_verification(self):
        self.write_plan(PLAN.replace("  - Verification: `pnpm test`\n  - Acceptance:",
                                     "  - Verification: `pnpm test:endpoint`\n  - Acceptance:", 1))
        saved = ship.head_sha
        self.addCleanup(setattr, ship, "head_sha", saved)
        ship.head_sha = lambda path: "sha-" + os.path.basename(path)
        for repo in ("proxima-api", "proxima-admin"):
            os.makedirs(self.wtpath(repo), exist_ok=True)
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit("CHANGES_REQUESTED", ["BLOCKING: src/proxima-api.py: broken"])
        fake.auto_resolve = "reject"
        self.run_ship(fake, scripted_gate())
        state = ship_state.load(self.state_file)
        self.assertEqual(state.tasks["T001"].touched, ["src/proxima-api.py"])
        fix = state.tasks["AUDIT1-proxima-api"]
        self.assertEqual(ship.task_verification_command(fix.verification)[0], "pnpm test:endpoint && pnpm test")
        spec = next(t["spec"] for t in fake.tasks.values() if t["title"].startswith("AUDIT1-proxima-api"))
        self.assertIn("Verification command: `pnpm test:endpoint && pnpm test`", spec)
        self.assertIn(("pnpm test:endpoint && pnpm test", self.wtpath("proxima-api")), self.verifications)


if __name__ == "__main__":
    unittest.main()
