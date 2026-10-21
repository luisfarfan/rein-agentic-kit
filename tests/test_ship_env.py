"""`gate.env` reaches every check `rein ship` runs in a worktree, and the agents it starts.

Orca cannot pass an environment to an agent, so the worker's and the auditor's
brief carry it as an explicit `export` line; the driver's own gates and
verifications get it directly.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_state  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


class TestBriefs(unittest.TestCase):
    def task(self, **kw):
        base = dict(id="T001", repo="proxima-api", deps=[], human_review=False,
                    verification="`pytest -q tests/test_a.py`", type="implementation")
        base.update(kw)
        return ship_state.TaskState(**base)

    def test_worker_brief_opens_with_the_export_line(self):
        spec = ship.build_task_spec(self.task(), {}, env_lines=["export APP_DB=app_wt"])
        first = spec.split("\n", 1)[0]
        self.assertIn("export APP_DB=app_wt", first)
        self.assertNotIn("export", ship.build_task_spec(self.task(), {}))

    def test_audit_brief_names_the_export_line_of_each_worktree(self):
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"], "/wt/b": ["y.py"]}, "R", "/wt/a", "demo",
                                     env_lines={"/wt/a": ["export APP_DB=app_a"], "/wt/b": []})
        self.assertIn("export APP_DB=app_a", spec)
        self.assertLess(spec.index("export APP_DB=app_a"), spec.index("Run the rein-audit skill on /wt/b"))


class TestDriverEnv(ShipCase):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"gate": {"env": {"APP_DB": "app_{worktree}", "APP_REPO": "{repo}"}}}, fh)
        self.verify_env = []

        def verified(command, path, timeout, env=None):
            self.verify_env.append(env)
            return 0, ""
        ship.run_task_verification = verified

    def test_gates_verifications_and_briefs_get_the_worktree_env(self):
        seen = []

        def gates(path, unit_only, timeout=None, **kw):
            seen.append(kw)
            return 0, "ok"
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        self.assertTrue(seen)
        self.assertTrue(all(kw.get("env_root") == os.path.abspath(self.root) for kw in seen))
        self.assertIn("proxima-api", {kw.get("repo") for kw in seen})
        self.assertIn({"APP_DB": "app_proxima_api", "APP_REPO": "proxima-api"}, self.verify_env)
        specs = [t["spec"] for t in fake.tasks.values()]
        t001 = next(s for s in specs if "Task T001" in s)
        t003 = next(s for s in specs if "Task T003" in s)
        # T001 starts a new worktree, whose path is not known yet: the brief evaluates `rein env` there.
        self.assertIn("rein env", t001.split("\n", 1)[0])
        self.assertIn("--repo proxima-api", t001.split("\n", 1)[0])
        # Every task is created up front, before any worktree exists: the same `rein env` there.
        self.assertIn("rein env", t003.split("\n", 1)[0])
        audit = next(s for s in specs if "rein-audit" in s)
        self.assertIn("export APP_DB=app_proxima_api", audit)

    def test_without_config_nothing_changes(self):
        os.remove(os.path.join(self.root, "flow.config.json"))
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, lambda path, unit_only, timeout=None, **kw: (0, "ok"))
        self.assertTrue(all(not env for env in self.verify_env))
        self.assertFalse(any("export " in t["spec"] or "rein env" in t["spec"] for t in fake.tasks.values()))


if __name__ == "__main__":
    unittest.main()


class TestAuditGateCommand(ShipCase):
    def test_the_audit_brief_carries_the_gate_command_of_each_worktree(self):
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, lambda path, unit_only, timeout=None, **kw: (0, "ok"))
        audit = next(t["spec"] for t in fake.tasks.values() if "rein-audit" in t["spec"])
        api = self.wtpath("proxima-api")
        self.assertIn("gate %s --repo proxima-api --env-root %s --tier full" % (api, os.path.abspath(self.root)),
                      audit)
        self.assertIn("gate %s --repo proxima-admin" % self.wtpath("proxima-admin"), audit)
