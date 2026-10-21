"""Resume a ship state written by the previous Rein (no repo_gates fields)."""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_state  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_ship import FakeOrca, ShipCase, scripted_gate  # noqa: E402

OLD_ONLY = ("repo_gates", "repo_gate_fails")


def write_old(st, path):
    """Save, then strip the fields the new version added: the previous version's file shape."""
    ship_state.save(st, path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for k in OLD_ONLY:
        data.pop(k, None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class TestOldStateResume(ShipCase):
    def base_state(self):
        plan = ship._load_plan(self.root, "demo")
        st = ship_state.from_plan(plan, "plan-repo", "demo")
        for repo in ("proxima-api", "proxima-admin"):
            st.worktrees[repo] = self.wtid(repo)
            st.worktree_paths[repo] = self.wtpath(repo)
        return st

    def test_old_file_loads(self):
        st = self.base_state()
        write_old(st, self.state_file)
        loaded = ship_state.load(self.state_file)
        self.assertEqual(loaded.repo_gates, {})
        self.assertEqual(loaded.repo_gate_fails, {})

    def test_old_gating_task_resumes_with_a_gate_not_a_timeout(self):
        """Old driver stopped mid-gate; the task was dispatched 50 minutes ago (timeout 45)."""
        self.clock_value = 100000.0
        st = self.base_state()
        ids = list(st.tasks)
        for t in st.tasks.values():
            t.status = "done"
        g = st.tasks[ids[0]]
        g.status, g.dispatch_id, g.started_at = "gating", "d-old", self.clock_value - 50 * 60
        write_old(st, self.state_file)
        inner = scripted_gate()
        import time as _t
        def gates(path, unit_only, timeout=None, tier="", cancel=None, **kw):
            if tier == "task":
                _t.sleep(1.5)  # a real gate takes minutes: a Tick comes before its verdict
            return ship.call_with(inner, path, unit_only, timeout, tier=tier, cancel=cancel, **kw)
        ship.GATE_POLL_SECONDS = 0.05
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit(root=self.wtpath("proxima-admin"))
        fake.auto_resolve = "retry"
        code = self.run_ship(fake, gates)
        print("\n--- exit", code, "\n", self.out[-3000:])
        self.assertNotIn("has run (and been gated)", self.out)

    def test_old_auditing_state_continues_the_audit(self):
        st = self.base_state()
        for t in st.tasks.values():
            t.status = "done"
        st.phase = "auditing"
        write_old(st, self.state_file)
        gates = scripted_gate()
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit(root=self.wtpath("proxima-admin"))
        code = self.run_ship(fake, gates)
        print("\n--- auditing exit", code, "full", gates.full_calls, "task", gates.calls)
        self.assertEqual(code, 0, self.out)
        self.assertEqual(gates.calls, [])


if __name__ == "__main__":
    unittest.main()
