"""Gates run off the driver loop.

`run_gate` used to run inside the loop: while one 6-12 minute suite ran, no other
worker's completion, gate or owner answer was handled. Gates now run on background
threads that never touch the ship state; their verdicts are fed by the driver's
own thread. A task's timeout covers its gating time too, and a gate nobody needs
any more is cancelled (its process group killed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402
from test_ship_decide import CFG, NOW, ship as make_state, task, tick  # noqa: E402

TWO_REPOS = """# Change: demo

## Why
Two independent repos.

- [ ] T001 Api part
  - Repo: proxima-api
  - Verification: `pnpm test`
- [ ] T002 Admin part
  - Repo: proxima-admin
  - Verification: `pnpm test`
"""


class TestGatePool(unittest.TestCase):
    def test_a_verdict_lands_for_the_driver_to_collect(self):
        pool = ship.GatePool()
        self.assertTrue(pool.submit(("task", "T1", "d1"), lambda cancel: "verdict"))
        self.assertTrue(pool.wait(5))
        self.assertEqual(pool.results(), [(("task", "T1", "d1"), "verdict")])
        self.assertFalse(pool.busy())

    def test_one_job_per_key(self):
        pool = ship.GatePool()
        gate = threading.Event()
        self.assertTrue(pool.submit(("repo", "api"), lambda cancel: gate.wait(5)))
        self.assertFalse(pool.submit(("repo", "api"), lambda cancel: "second"))
        self.assertTrue(pool.submit(("repo", "admin"), lambda cancel: "other"))
        gate.set()
        deadline = time.time() + 5
        landed = []
        while len(landed) < 2 and time.time() < deadline:
            pool.wait(1)
            landed += pool.results()
        self.assertEqual(sorted(k for k, _ in landed), [("repo", "admin"), ("repo", "api")])

    def test_a_cancelled_job_is_told_and_its_verdict_dropped(self):
        pool = ship.GatePool()
        seen = {}

        def job(cancel):
            seen["cancelled"] = cancel.wait(5)
            return "late"
        pool.submit(("task", "T1", "d1"), job)
        pool.cancel(("task", "T1", "d1"))
        pool.shutdown(5)
        self.assertTrue(seen["cancelled"])
        self.assertEqual(pool.results(), [])

    def test_a_crashing_job_is_a_setup_verdict_not_a_dead_driver(self):
        pool = ship.GatePool()

        def boom(cancel):
            raise RuntimeError("disk full")
        pool.submit(("task", "T1", "d1"), boom)
        pool.wait(5)
        [(_key, verdict)] = pool.results()
        self.assertEqual((verdict.task_id, verdict.exit_code, verdict.dispatch_id), ("T1", 126, "d1"))
        self.assertIn("disk full", verdict.reason)


class TestTheLoopKeepsGoing(ShipCase):
    def test_another_task_finishes_while_a_slow_gate_runs(self):
        self.write_plan(TWO_REPOS)
        api_gate_running, admin_gated = threading.Event(), threading.Event()
        order = []

        def gates(path, unit_only, timeout=None, tier="", **kw):
            if tier != "task":
                return 0, "ok"
            if path.endswith("proxima-api"):
                api_gate_running.set()
                # Blocks until the admin task has been gated: impossible if gates block the loop.
                admin_gated.wait(10)
                order.append("api gate returns")
                return 0, "ok"
            api_gate_running.wait(10)
            order.append("admin gated")
            admin_gated.set()
            return 0, "ok"
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        self.assertEqual(order, ["admin gated", "api gate returns"])
        self.assertIn("2/2 tasks done", self.out)

    def test_a_question_is_relayed_while_a_gate_runs(self):
        self.write_plan(TWO_REPOS)
        release = threading.Event()
        seen = {}

        def gates(path, unit_only, timeout=None, tier="", **kw):
            if tier == "task" and path.endswith("proxima-api"):
                # Returns only once the owner's answer was relayed: the loop kept going meanwhile.
                seen["relayed while gating"] = release.wait(10)
            return 0, "ok"

        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        original = fake._check

        def check(a):
            if "--ack" not in a and "did" not in seen and len(fake.dispatches) >= 2:
                did = next(d for d, v in fake.dispatches.items() if "T002" in fake.tasks[v["task"]]["title"])
                fake.dispatches[did]["reported"] = True  # it asks instead of finishing
                seen["did"] = did
                return {"deliveryId": fake._id("delivery"), "messages": [
                    {"id": "q1", "type": "question", "from_handle": "term_" + did, "body": "which db?",
                     "payload": json.dumps({"dispatchId": did})}]}
            pending = [g for g in fake.gates if g["status"] == "pending"]
            if "did" in seen and pending and not fake.replies:
                pending[-1].update(status="resolved", resolution="postgres")  # the owner answers
            if ("q1", "postgres") in fake.replies and not release.is_set():
                fake.dispatches[seen["did"]]["reported"] = False  # now it finishes
                release.set()
            return original(a)
        fake._check = check
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        self.assertTrue(seen.get("relayed while gating"), self.out)


class TestTimeoutCoversGating(unittest.TestCase):
    def test_a_task_stuck_in_its_gate_opens_the_timeout_gate(self):
        st = make_state(task("T1", "api", status="gating", dispatch_id="d1", started_at=NOW - 46 * 60))
        new, actions = tick(st)
        self.assertEqual(new.tasks["T1"].status, "gated")
        self.assertEqual(new.pending_gates[-1]["kind"], "timeout")
        self.assertIn("gated", new.pending_gates[-1]["question"])

    def test_a_gate_within_the_task_time_is_left_alone(self):
        st = make_state(task("T1", "api", status="gating", dispatch_id="d1", started_at=NOW - 10 * 60))
        new, actions = tick(st)
        self.assertEqual(new.tasks["T1"].status, "gating")

    def test_the_verdict_of_an_earlier_attempt_is_ignored(self):
        st = make_state(task("T1", "api", status="gating", dispatch_id="d2", started_at=NOW))
        new, actions = sd.decide(st, sd.GateVerdict("T1", 0, "", "d1"), CFG)
        self.assertEqual(new.tasks["T1"].status, "gating")
        self.assertEqual(actions, [])
        new, _ = sd.decide(st, sd.GateVerdict("T1", 0, "", "d2"), CFG)
        self.assertEqual(new.tasks["T1"].status, "done")


class TestTheDriverCancelsAGateNobodyNeeds(ShipCase):
    def setUp(self):
        super().setUp()
        saved = ship.GATE_POLL_SECONDS
        ship.GATE_POLL_SECONDS = 0.05
        self.addCleanup(setattr, ship, "GATE_POLL_SECONDS", saved)

    def test_the_timeout_gate_cancels_the_running_gate(self):
        self.write_plan(TWO_REPOS.split("- [ ] T002")[0])
        cancelled = threading.Event()

        def gates(path, unit_only, timeout=None, tier="", cancel=None, **kw):
            if tier == "task":
                if cancel.wait(20):
                    cancelled.set()
                return 126, "gate cancelled"
            return 0, "ok"
        fake = FakeOrca(self.root)
        fake.auto_resolve = "skip"  # the owner skips the task once its timeout gate opens
        fake.show_status = "completed"

        def clock():
            self.clock_value += 600  # ten minutes per look: the 45-minute task timeout comes fast
            return self.clock_value
        self.clock = clock
        self.run_ship(fake, gates)
        self.assertTrue(cancelled.is_set(), self.out)
        self.assertIn("timeout", self.out)


class TestCancelKillsTheProcess(unittest.TestCase):
    def test_default_gate_runner_kills_a_cancelled_gate(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"stack": "python", "commands": {"test": "sleep 31.7"}}, fh)
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        started = time.monotonic()
        code, out = ship.default_gate_runner(tmp, True, 60, cancel=cancel)
        self.assertEqual(code, 126)
        self.assertIn("cancelled", out)
        self.assertLess(time.monotonic() - started, 15)
        deadline = time.monotonic() + 5
        while True:  # a killed process takes a moment to be reaped
            leftover = subprocess.run(["pgrep", "-f", "sleep 31.7"], capture_output=True, text=True).stdout
            if not leftover.strip() or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        self.assertEqual(leftover.strip(), "", "the check the gate was running was killed too")


if __name__ == "__main__":
    unittest.main()
