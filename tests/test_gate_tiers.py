"""`rein gate --tier task|full`: what a task's gate and a repo's pre-audit gate run."""

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
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")


def _git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class TierCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        self.log = os.path.join(self.tmp, "runs.log")
        self.env = dict(os.environ, REIN_CACHE_DIR=os.path.join(self.tmp, "cache"))

    def log_cmd(self, label, code=0):
        return "echo %s >> %s; exit %d" % (label, self.log, code)

    def init(self, commands):
        os.makedirs(self.root)
        cfg = {"stack": "python", "commands": commands, "gate": {"affected": {"python": "pytest"}}}
        self.write("flow.config.json", json.dumps(cfg))
        self.write("src/app/catalog/service.py", "x = 1\n")
        self.write("tests/unit/catalog/test_service.py", "")
        self.write("tests/unit/test_other.py", "")
        subprocess.run(["git", "init", "-q", self.root], check=True)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")

    def write(self, rel, body):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def gate(self, *extra):
        res = subprocess.run([sys.executable, REIN, "gate", self.root, "--json", *extra],
                             capture_output=True, text=True, env=self.env)
        return res.returncode, json.loads(res.stdout)

    def runs(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().split("\n")[:-1]
        except OSError:
            return []

    def full_commands(self):
        return {"lint": self.log_cmd("lint"), "typecheck": self.log_cmd("typecheck"),
                "build": self.log_cmd("build"), "test": self.log_cmd("FULL"),
                "testOne": "echo one {target} >> %s" % self.log,
                "testIntegration": self.log_cmd("integration")}


class TestTaskTier(TierCase):
    def test_runs_lint_typecheck_and_only_the_affected_tests(self):
        self.init(self.full_commands())
        self.write("src/app/catalog/service.py", "x = 2\n")
        code, payload = self.gate("--tier", "task", "--base", self.base)
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.runs(), ["lint", "typecheck", "one tests/unit/catalog/test_service.py"])
        self.assertEqual(payload["report"]["affected"]["mode"], "run")
        self.assertEqual(payload["report"]["tier"], "task")

    def test_nothing_affected_runs_no_test_and_says_why(self):
        self.init(self.full_commands())
        self.write("docs/notes.md", "x\n")
        code, payload = self.gate("--tier", "task", "--base", self.base)
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.runs(), ["lint", "typecheck"])
        test = payload["report"]["results"]["test"]
        self.assertEqual(test["outcome"], "skipped")
        self.assertIn("before the audit", test["error"])

    def test_nothing_affected_and_nothing_else_configured_is_still_green(self):
        self.init({"test": self.log_cmd("FULL"), "testOne": "echo one {target} >> %s" % self.log})
        self.write("docs/notes.md", "x\n")
        code, payload = self.gate("--tier", "task", "--base", self.base)
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.runs(), [])

    def test_a_red_affected_test_is_red(self):
        cmds = self.full_commands()
        cmds["testOne"] = "echo one {target} >> %s; exit 1" % self.log
        self.init(cmds)
        self.write("src/app/catalog/service.py", "x = 2\n")
        code, payload = self.gate("--tier", "task", "--base", self.base)
        self.assertEqual(code, 1, payload)

    def test_without_a_base_the_full_test_slot_runs(self):
        self.init(self.full_commands())
        code, payload = self.gate("--tier", "task")
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.runs(), ["lint", "typecheck", "FULL"])
        self.assertIn("--base", payload["report"]["affected"]["reason"])

    def test_an_unknown_base_runs_the_full_test_slot(self):
        self.init(self.full_commands())
        code, payload = self.gate("--tier", "task", "--base", "no-such-ref")
        self.assertEqual(code, 0, payload)
        self.assertIn("FULL", self.runs())

    def test_bad_tier_is_usage(self):
        self.init(self.full_commands())
        res = subprocess.run([sys.executable, REIN, "gate", self.root, "--tier", "nope"],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(res.returncode, 2)


class TestFullTier(TierCase):
    def test_every_gate_slot_with_the_integration_suite_and_no_render_demand(self):
        self.init(self.full_commands())
        with open(os.path.join(self.root, "flow.config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["verify"] = {"mode": "rendered"}
        self.write("flow.config.json", json.dumps(cfg))
        code, payload = self.gate("--tier", "full")
        self.assertEqual(code, 0, payload)
        self.assertEqual(self.runs(), ["lint", "typecheck", "build", "FULL", "integration"])


if __name__ == "__main__":
    unittest.main()
