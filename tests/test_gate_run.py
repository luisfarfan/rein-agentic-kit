"""`rein gate` runs only the gate slots, cheap ones first, and stops at the first failure.

The gate used to run every configured slot in alphabetical order: `build` before
`lint`, a `testOne` probe against a fake file whose result it then ignored (15-30 s
per gate), and the whole suite even after lint had already failed.
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

import gate_run  # noqa: E402


def _log(slot, code=0):
    return "echo %s >> order.log; exit %d" % (slot, code)


class GateRunCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        os.makedirs(self.root)
        self.cache = os.path.join(self.tmp, "cache")
        self.env = dict(os.environ, REIN_CACHE_DIR=self.cache)

    def config(self, commands, **extra):
        cfg = {"stack": "python", "commands": commands}
        cfg.update(extra)
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def gate(self, *extra):
        res = subprocess.run([sys.executable, REIN, "gate", self.root, "--json", *extra],
                             capture_output=True, text=True, env=self.env)
        try:
            payload = json.loads(res.stdout)
        except ValueError:
            self.fail("not JSON: %s %s" % (res.stdout, res.stderr))
        return res.returncode, payload

    def order(self):
        try:
            with open(os.path.join(self.root, "order.log"), encoding="utf-8") as fh:
                return fh.read().split()
        except OSError:
            return []


class TestSlotsAndOrder(GateRunCase):
    def test_cheap_slots_run_first(self):
        self.config({"test": _log("test"), "build": _log("build"), "typecheck": _log("typecheck"),
                     "lint": _log("lint")})
        code, payload = self.gate()
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.order(), ["lint", "typecheck", "build", "test"])

    def test_testone_and_custom_slots_never_run_in_a_gate(self):
        self.config({"test": _log("test"), "testOne": _log("testOne") + " {target}",
                     "harness": _log("harness"), "serve": "sleep 100"})
        code, payload = self.gate()
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.order(), ["test"])
        self.assertEqual(sorted(payload["report"]["results"]), ["test"])

    def test_a_failing_slot_skips_the_rest_and_reports_them_skipped(self):
        self.config({"lint": _log("lint", 1), "typecheck": _log("typecheck"), "test": _log("test")})
        code, payload = self.gate()
        self.assertEqual(code, 1, payload)
        self.assertEqual(self.order(), ["lint"])
        results = payload["report"]["results"]
        self.assertEqual(results["lint"]["outcome"], "failed")
        for slot in ("typecheck", "test"):
            self.assertEqual(results[slot]["outcome"], "skipped")
            self.assertIn("lint", results[slot]["error"])
        self.assertIn("skipped", payload["decision"]["reason"] + json.dumps(payload["decision"]["ignored"]))

    def test_a_setup_problem_also_stops_the_run(self):
        self.config({"lint": "definitely-not-a-binary-xyz", "test": _log("test")})
        code, payload = self.gate()
        self.assertEqual(code, 126, payload)
        self.assertEqual(self.order(), [])

    def test_only_still_restricts(self):
        self.config({"lint": _log("lint"), "test": _log("test")})
        code, payload = self.gate("--only", "test")
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.order(), ["test"])


class TestPlanSlots(unittest.TestCase):
    def test_order_and_filter(self):
        commands = {"test": "t", "lint": "l", "testOne": "o {target}", "build": "b", "x": "y", "typecheck": ""}
        self.assertEqual(gate_run.gate_slots(commands), ["lint", "build", "test"])
        self.assertEqual(gate_run.gate_slots(commands, only={"test", "testOne"}), ["test"])


if __name__ == "__main__":
    unittest.main()
