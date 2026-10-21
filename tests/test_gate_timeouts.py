"""Each gate slot's timeout comes from its own history: 2x its slowest recent pass, within
[120 s, 30 min]; without history, the project's ship.gateTimeoutSeconds (or --timeout).

The old bound was one budget per command and 6x that around the whole gate: a hung
gate could hold a task for 91 minutes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "rein", "lib"))
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")

import gate_cache  # noqa: E402
import gate_run  # noqa: E402
import ship  # noqa: E402


class CacheDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = os.environ.get("REIN_CACHE_DIR")
        os.environ["REIN_CACHE_DIR"] = os.path.join(self.tmp, "cache")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("REIN_CACHE_DIR", None)
        else:
            os.environ["REIN_CACHE_DIR"] = self._saved


class TestBudget(CacheDirCase):
    def test_no_history_is_the_fallback(self):
        self.assertEqual(gate_run.slot_budget("api", "test", "pytest", 900), 900)

    def test_twice_the_slowest_recent_pass_within_bounds(self):
        gate_cache.record_pass("api", "test", "pytest", 100)
        self.assertEqual(gate_run.slot_budget("api", "test", "pytest", 900), 200)
        gate_cache.record_pass("api", "test", "pytest", 300)
        gate_cache.record_pass("api", "test", "pytest", 50)
        self.assertEqual(gate_run.slot_budget("api", "test", "pytest", 900), 600)
        gate_cache.record_pass("api", "lint", "ruff", 3)
        self.assertEqual(gate_run.slot_budget("api", "lint", "ruff", 900), 120)
        gate_cache.record_pass("api", "e2e", "pw", 2000)
        self.assertEqual(gate_run.slot_budget("api", "e2e", "pw", 900), 1800)

    def test_only_the_last_five_passes_count(self):
        gate_cache.record_pass("api", "test", "pytest", 800)
        for _ in range(5):
            gate_cache.record_pass("api", "test", "pytest", 100)
        self.assertEqual(gate_run.slot_budget("api", "test", "pytest", 900), 200)

    def test_history_is_per_repo_and_command(self):
        gate_cache.record_pass("api", "test", "pytest", 100)
        self.assertEqual(gate_run.slot_budget("admin", "test", "pytest", 900), 900)
        self.assertEqual(gate_run.slot_budget("api", "test", "pytest -x", 900), 900)


def _git_project(root, commands):
    os.makedirs(root)
    with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
        json.dump({"stack": "python", "commands": commands}, fh)
    subprocess.run(["git", "init", "-q", root], check=True)


class TestTheGateUsesHistory(CacheDirCase):
    def test_a_pass_sets_the_next_budget(self):
        root = os.path.join(self.tmp, "p")
        _git_project(root, {"test": "true"})
        first = json.loads(subprocess.run([sys.executable, REIN, "gate", root, "--json", "--timeout", "50"],
                                          capture_output=True, text=True).stdout)
        self.assertEqual(first["report"]["results"]["test"]["budgetSeconds"], 50)
        second = json.loads(subprocess.run([sys.executable, REIN, "gate", root, "--json", "--no-cache"],
                                           capture_output=True, text=True).stdout)
        self.assertEqual(second["report"]["results"]["test"]["budgetSeconds"], 120)


class TestTheDriversOuterBound(CacheDirCase):
    def test_outer_bound_is_the_sum_of_the_slot_budgets(self):
        root = os.path.join(self.tmp, "wt")
        _git_project(root, {"lint": "true", "test": "true", "testOne": "x {target}"})
        seen = {}

        def fake_run(argv, **kw):
            seen["kw"] = kw
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        saved = ship.subprocess.run
        ship.subprocess.run = fake_run
        self.addCleanup(setattr, ship.subprocess, "run", saved)
        ship.default_gate_runner(root, True, 900, repo="wt")
        # lint + test at 900 s each, plus a minute: never 6 x 900.
        self.assertEqual(seen["kw"]["timeout"], 2 * 900 + 60)


if __name__ == "__main__":
    unittest.main()
