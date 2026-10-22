"""A launch that simply fails is a failed start, not a lost one.

In the retest every failed launch of T002 logged "adopted worker ... started before the driver
could record it" and then "recovered a lost worker_done (failed)". The start-intent record
(`TaskState.starting`) exists for a start whose ANSWER was lost (a kill, a dropped connection):
Orca may have started the worker, so the driver looks for it instead of starting a second one.
But a start that exits with an error and whose dispatch Orca already lists as failed lost no
answer: adopting it turned Orca's launch error into a "worker failed" retry, so retries were spent
and the owner never saw Orca's reason. Such a start now opens the setup gate with Orca's error,
and spends no retry."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship_state  # noqa: E402
from test_ship import Drained, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402

PLAN_ONE = "# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"


class AbortedLaunch(FakeOrca):
    """Orca makes the dispatch, the agent's launch fails, Orca aborts it: the start exits non-zero
    and worker-list shows that dispatch, failed."""

    def __init__(self, root, aborts=1, **kw):
        super().__init__(root, **kw)
        self.aborts = aborts

    def _answer(self, a):
        if a[:2] == ["orchestration", "worker-start"] and self.aborts:
            self.aborts -= 1
            did = self._id("d")
            wt = "wtid-proxima-api::%s/proxima-api" % self.wt_base
            self.dispatches[did] = {"task": _flag(a, "--task"), "worktree": wt, "reported": True, "live": False,
                                    "failed": True}
            raise FailEnvelope({"ok": False, "error": {"code": "worker_start_failed",
                                                       "message": "agent launch aborted", "failedStage": "agent"}})
        if a[:2] == ["orchestration", "worker-list"] and self.rows_override is None:
            rows = super()._answer(a)["workers"]
            for r in rows:
                if self.dispatches[r["dispatchId"]].get("failed"):
                    r.update(workerState="failed", dispatchStatus="failed", projection={"outcome": "failed"})
            return {"workers": rows}
        return super()._answer(a)


class FailedStart(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN_ONE)

    def run_until_idle(self, fake):
        fake.pending = [lambda f: []] * 4
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        return ship_state.load(self.state_file)

    def test_a_failed_launch_is_not_adopted_nor_recovered_as_a_lost_done(self):
        fake = AbortedLaunch(self.root, repos=("proxima-api",), autocomplete=False)
        state = self.run_until_idle(fake)
        self.assertNotIn("adopted worker", self.out)
        self.assertNotIn("recovered a lost worker_done", self.out)
        [question] = [_flag(c, "--question") for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]
        self.assertIn("Orca could not start a worker for T001", question)
        self.assertIn("agent launch aborted", question)
        self.assertEqual(len(fake.starts()), 1, "no retry is spent on it")
        t = state.tasks["T001"]
        self.assertEqual((t.status, t.attempts, t.starting), ("gated", 0, ""))
        self.assertEqual(state.worktree_paths.get("proxima-api"), self.wtpath("proxima-api"),
                         "a retry reuses the worktree Orca made")

    def test_a_retry_starts_one_fresh_worker(self):
        fake = AbortedLaunch(self.root, repos=("proxima-api",), autocomplete=True)
        fake.auto_resolve = "retry"
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        workers = [s for s in fake.starts() if "audit" not in fake.tasks[_flag(s, "--task")]["title"]]
        self.assertEqual(len(workers), 2)
        self.assertNotIn("adopted worker", self.out)


if __name__ == "__main__":
    unittest.main()
