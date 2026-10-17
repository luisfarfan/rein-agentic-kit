"""`ship_state`: the resumable record of one `rein ship` run."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import ship_state  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402


def _task(id, checked=False, repo="", deps=None, **kw):
    t = {
        "id": id, "title": id, "checked": checked, "type": "feature",
        "dependsOn": deps or [], "humanReview": False, "verification": "make test",
        "area": "", "acceptance": [], "repo": repo,
    }
    t.update(kw)
    return t


def _plan(*tasks, path="/r/openspec/changes/c1/tasks.md", change="c1"):
    return {"change": change, "path": path, "tasks": list(tasks)}


class FromPlanTests(unittest.TestCase):
    def test_from_plan_marks_checked_done(self):
        st = ship_state.from_plan(_plan(_task("T001", True), _task("T002", deps=["T001"])), "api")
        self.assertEqual(st.change, "c1")
        self.assertEqual(st.plan_path, "/r/openspec/changes/c1/tasks.md")
        self.assertEqual(st.tasks["T001"].status, "done")
        self.assertEqual(st.tasks["T002"].status, "pending")
        self.assertEqual(st.tasks["T002"].deps, ["T001"])
        self.assertEqual(st.phase, "running")

    def test_default_repo_applies_when_blank(self):
        st = ship_state.from_plan(_plan(_task("T001"), _task("T002", repo="web")), "api")
        self.assertEqual(st.tasks["T001"].repo, "api")
        self.assertEqual(st.tasks["T002"].repo, "web")

    def test_mutable_defaults_are_not_shared(self):
        a = ShipState(change="a", plan_path="p")
        b = ShipState(change="b", plan_path="p")
        a.tasks["x"] = TaskState("x", "r", [], False, "", "feature")
        a.implementer_agents.append("codex")
        self.assertEqual(b.tasks, {})
        self.assertEqual(b.implementer_agents, [])


class ReconcileTests(unittest.TestCase):
    def test_reconcile_adds_new_and_skips_checked(self):
        st = ship_state.from_plan(_plan(_task("T001")), "api")
        st.tasks["T001"].status = "running"
        st.tasks["T001"].dispatch_id = "d1"
        plan = _plan(_task("T001"), _task("T002", True), _task("T003", deps=["T001"]))
        out = ship_state.reconcile(st, plan, "api")
        self.assertEqual(out.tasks["T001"].status, "running")
        self.assertEqual(out.tasks["T001"].dispatch_id, "d1")
        self.assertEqual(out.tasks["T002"].status, "done")
        self.assertEqual(out.tasks["T003"].status, "pending")
        self.assertEqual(out.tasks["T003"].deps, ["T001"])

    def test_reconcile_keeps_removed_tasks_and_refreshes_pending_fields(self):
        st = ship_state.from_plan(_plan(_task("T001"), _task("T002")), "api")
        st.tasks["T001"].status = "done"
        plan = _plan(_task("T002", verification="new cmd", deps=["T009"]))
        out = ship_state.reconcile(st, plan, "api")
        self.assertIn("T001", out.tasks)
        self.assertEqual(out.tasks["T002"].verification, "new cmd")
        self.assertEqual(out.tasks["T002"].deps, ["T009"])

    def test_reconcile_ticked_task_becomes_done(self):
        st = ship_state.from_plan(_plan(_task("T001")), "api")
        st.tasks["T001"].status = "gated"
        out = ship_state.reconcile(st, _plan(_task("T001", True)), "api")
        self.assertEqual(out.tasks["T001"].status, "done")


class PersistenceTests(unittest.TestCase):
    def test_save_load_round_trip(self):
        st = ship_state.from_plan(_plan(_task("T001", True), _task("T002", deps=["T001"])), "api")
        st.run_id = "run_1"
        st.worktrees["api"] = "wt-id"
        st.worktree_paths["api"] = "/wt/api"
        st.gates["T002"] = "gate_1"
        st.phase = "auditing"
        st.audit_rounds = 1
        st.audit_dispatch = "ctx_9"
        st.implementer_agents = ["codex", "claude"]
        st.tasks["T002"].started_at = 12.5
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".rein", "ship-state.json")
            ship_state.save(st, path)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(os.listdir(os.path.dirname(path)), ["ship-state.json"])
            self.assertEqual(ship_state.load(path), st)

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ship_state.load(os.path.join(d, "nope.json")))

    def test_load_corrupt_quarantines_and_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as fh:
                fh.write("{broken")
            with self.assertRaises(ship_state.ShipStateCorrupt) as cm:
                ship_state.load(p)
            self.assertFalse(os.path.exists(p))
            moved = [n for n in os.listdir(d) if n.startswith("s.json.corrupt-")]
            self.assertEqual(len(moved), 1)
            self.assertIn(moved[0], str(cm.exception))
            with open(os.path.join(d, moved[0])) as fh:
                self.assertEqual(fh.read(), "{broken")
            self.assertIsNone(ship_state.load(p))

    def test_load_wrong_shape_is_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as fh:
                fh.write("[1, 2]")
            with self.assertRaises(ship_state.ShipStateCorrupt):
                ship_state.load(p)

    def test_state_path(self):
        self.assertEqual(
            ship_state.state_path("/r"), os.path.join("/r", ".rein", "ship-state.json"))


if __name__ == "__main__":
    unittest.main()
