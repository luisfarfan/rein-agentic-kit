"""Every task gate and repo gate leaves a readable record: tier, slots, time, cache, env.

After a real run, nothing showed which tier a gate ran, how long each slot took or which
was reused from the cache: the per-task reports lived only in the driver's tab. The ship
driver now appends one line per gate next to the change's state, `rein gate` and
`rein quick done` one per checkout under ~/.claude/rein/verify/log, and `rein gate-log`
prints them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import gate_log  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship_done import Env as DoneEnv  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


def report(tier, cached=False):
    return json.dumps({
        "decision": {"decision": "green", "reason": "", "exit": 0},
        "report": {"tier": tier, "env": ["APP_TEST_DB"], "results": {
            "lint": {"slot": "lint", "outcome": "ok", "exitCode": 0, "elapsedMs": 812, "cached": cached,
                     "budgetSeconds": 120.0, "command": "ruff check ."},
            "test": {"slot": "test", "outcome": "ok", "exitCode": 0, "elapsedMs": 3010,
                     "budgetSeconds": 900.0, "command": "pytest tests/test_a.py"}},
            "affected": {"mode": "run", "count": 1}}})


class ShipLog(ShipCase):
    def test_each_task_and_repo_gate_is_kept_next_to_the_state(self):
        def gates(path, unit_only, timeout=None, **kw):
            return 0, report(kw.get("tier") or "default", cached=kw.get("tier") == "full")
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        entries = gate_log.read(gate_log.ship_log_path(self.state_file))
        tasks = [e for e in entries if e["kind"] == "task"]
        repos = [e for e in entries if e["kind"] == "repo"]
        self.assertEqual(sorted(e["task"] for e in tasks), ["T001", "T002", "T003"])
        self.assertEqual(sorted(e["repo"] for e in repos), ["proxima-admin", "proxima-api"])
        first = tasks[0]
        self.assertEqual(first["tier"], "task")
        self.assertEqual(first["env"], ["APP_TEST_DB"])
        self.assertEqual(first["exit"], 0)
        self.assertTrue(first["dispatch"])
        self.assertEqual({s["slot"] for s in first["slots"]}, {"lint", "test"})
        self.assertEqual(next(s for s in first["slots"] if s["slot"] == "test")["elapsedMs"], 3010)
        self.assertTrue(all(s["cached"] for e in repos for s in e["slots"] if s["slot"] == "lint"))

    def test_a_gate_that_is_not_json_is_still_recorded(self):
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, lambda path, unit_only, timeout=None, **kw: (0, "plain"))
        entries = gate_log.read(gate_log.ship_log_path(self.state_file))
        self.assertTrue(entries)
        self.assertEqual(entries[0]["slots"], [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ, REIN_GATE_LOG_DIR=os.path.join(self.tmp, "log"),
                        REIN_CACHE_DIR=os.path.join(self.tmp, "cache"))

    def rein(self, *args):
        return subprocess.run([sys.executable, REIN, *args], capture_output=True, text=True, env=self.env)

    def test_rein_gate_appends_its_report_per_checkout_and_gate_log_prints_it(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "flow.config.json"), "w") as fh:
            json.dump({"commands": {"lint": "true", "test": "true"}, "gate": {"env": {"X_DB": "x_{worktree}"}}}, fh)
        res = self.rein("gate", repo, "--tier", "full")
        self.assertEqual(res.returncode, 0, res.stdout)
        entries = gate_log.read(os.path.join(self.tmp, "log", os.path.basename(gate_log.root_log_path(repo))))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tier"], "full")
        self.assertEqual(entries[0]["env"], ["X_DB"])
        self.assertEqual(entries[0]["kind"], "gate")
        out = self.rein("gate-log", "--path", repo)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("tier=full", out.stdout)
        self.assertIn("lint", out.stdout)

    def test_gate_log_of_a_change(self):
        root = os.path.join(self.tmp, "plan")
        state = os.path.join(root, ".rein", "ship", "demo.json")
        os.makedirs(os.path.dirname(state))
        gate_log.append(gate_log.ship_log_path(state), dict(gate_log.summarize(report("task")), kind="task",
                                                              task="T001", repo="api", exit=0))
        out = self.rein("gate-log", "demo", "--root", root)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("task T001 api", out.stdout)
        self.assertIn("3010ms", out.stdout)
        self.assertIn("tier=task", out.stdout)
        res = self.rein("gate-log", "demo", "--root", root, "--json")
        self.assertEqual(json.loads(res.stdout)["gates"][0]["task"], "T001")

    def test_gate_log_without_a_log_says_so(self):
        out = self.rein("gate-log", "nothing", "--root", self.tmp)
        self.assertEqual(out.returncode, 1)
        self.assertIn("no gate log", out.stdout)


class QuickLog(DoneCase):
    def test_quick_done_appends_its_gate(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())
        entries = gate_log.read(gate_log.root_log_path(self.ws))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "quick")
        self.assertEqual(entries[0]["tier"], "task")
        self.assertTrue(entries[0]["slots"])


class Archived(DoneEnv):
    def test_rein_done_archives_the_gate_log_with_the_state(self):
        path = self.worktree()
        root = self.plan_repo(openspec=False)
        state = self.state(root, {"api": path})
        gate_log.append(gate_log.ship_log_path(state), {"kind": "task", "task": "T001", "slots": []})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        archived = os.path.join(root, ".rein", "ship", "archive", "demo.gates.jsonl")
        self.assertEqual(gate_log.read(archived)[0]["task"], "T001")


if __name__ == "__main__":
    unittest.main()
