"""A gate slot that passed on exactly this tree state is not run again.

The worker's last run, the task gate, the pre-audit gate and the auditor's
`rein gate` often test the same commit. The key is the worktree's content
(HEAD, `git status`, the tracked diff, untracked files), the slot, the exact
command and the gate env; only a pass is ever stored.
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


def _git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True)


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        os.makedirs(self.root)
        self.log = os.path.join(self.tmp, "runs.log")  # outside the tree: it must not change its state
        self.cache = os.path.join(self.tmp, "cache")
        self.env = dict(os.environ, REIN_CACHE_DIR=self.cache)

    def init(self, test_cmd=None):
        test_cmd = test_cmd or "echo test >> %s" % self.log
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"stack": "python", "commands": {"test": test_cmd, "lint": "echo lint >> %s" % self.log},
                       "gate": {"env": {"APP_DB": "db_{worktree}"}}}, fh)
        self.write("src/a.py", "a = 1\n")
        subprocess.run(["git", "init", "-q", self.root], check=True)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")

    def write(self, rel, body):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def gate(self, *extra, as_json=True):
        argv = [sys.executable, REIN, "gate", self.root] + (["--json"] if as_json else []) + list(extra)
        res = subprocess.run(argv, capture_output=True, text=True, env=self.env)
        return res.returncode, (json.loads(res.stdout) if as_json else res.stdout)

    def runs(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().split()
        except OSError:
            return []


class TestTheCache(CacheCase):
    def test_a_pass_on_the_same_state_is_reused(self):
        self.init()
        code, first = self.gate()
        self.assertEqual(code, 0, first)
        code, second = self.gate()
        self.assertEqual(code, 0, second)
        self.assertEqual(self.runs(), ["lint", "test"])
        self.assertTrue(all(r.get("cached") for r in second["report"]["results"].values()))
        self.assertFalse(any(r.get("cached") for r in first["report"]["results"].values()))

    def test_the_human_output_says_cached(self):
        self.init()
        self.gate()
        code, text = self.gate(as_json=False)
        self.assertEqual(code, 0, text)
        self.assertIn("cached", text)

    def test_an_edit_to_a_tracked_file_runs_again(self):
        self.init()
        self.gate()
        self.write("src/a.py", "a = 2\n")
        self.gate()
        self.assertEqual(self.runs(), ["lint", "test", "lint", "test"])

    def test_an_edit_to_an_untracked_file_runs_again(self):
        self.init()
        self.write("src/new.py", "x = 1\n")
        self.gate()
        self.write("src/new.py", "x = 2\n")
        self.gate()
        self.assertEqual(self.runs(), ["lint", "test", "lint", "test"])

    def test_a_new_commit_runs_again(self):
        self.init()
        self.gate()
        _git(self.root, "commit", "-q", "--allow-empty", "-m", "next")
        self.gate()
        self.assertEqual(len(self.runs()), 4)

    def test_reins_own_state_does_not_invalidate(self):
        self.init()
        self.gate()
        self.write(".rein/ship/demo.json", "{}")
        self.gate()
        self.assertEqual(self.runs(), ["lint", "test"])

    def test_a_failure_is_never_cached(self):
        self.init(test_cmd="echo test >> %s; exit 1" % os.path.join(self.tmp, "runs.log"))
        self.assertEqual(self.gate()[0], 1)
        self.assertEqual(self.gate()[0], 1)
        # lint passed and is reused; the red test slot runs again.
        self.assertEqual(self.runs(), ["lint", "test", "test"])

    def test_a_timeout_is_never_cached(self):
        self.init(test_cmd="echo test >> %s; sleep 5" % self.log)
        self.assertEqual(self.gate("--timeout", "1")[0], 126)
        self.assertEqual(self.gate("--timeout", "1")[0], 126)
        self.assertEqual(self.runs().count("test"), 2)

    def test_no_cache_flag_runs_everything(self):
        self.init()
        self.gate()
        self.gate("--no-cache")
        self.assertEqual(len(self.runs()), 4)

    def test_not_a_git_checkout_is_never_cached(self):
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"stack": "python", "commands": {"test": "echo test >> %s" % self.log}}, fh)
        self.gate()
        self.gate()
        self.assertEqual(self.runs(), ["test", "test"])


class TestKey(unittest.TestCase):
    def test_every_part_of_the_key_matters(self):
        base = gate_cache.key("/wt/a", "state", "test", "pytest -q", {"DB": "x"})
        self.assertEqual(base, gate_cache.key("/wt/a", "state", "test", "pytest -q", {"DB": "x"}))
        for other in (gate_cache.key("/wt/b", "state", "test", "pytest -q", {"DB": "x"}),
                      gate_cache.key("/wt/a", "state2", "test", "pytest -q", {"DB": "x"}),
                      gate_cache.key("/wt/a", "state", "lint", "pytest -q", {"DB": "x"}),
                      gate_cache.key("/wt/a", "state", "test", "pytest -q -x", {"DB": "x"}),
                      gate_cache.key("/wt/a", "state", "test", "pytest -q", {"DB": "y"})):
            self.assertNotEqual(base, other)


if __name__ == "__main__":
    unittest.main()


class TestTheAuditorUsesTheGate(unittest.TestCase):
    """Auditors ran the raw suite again, or `rein gate` with its 120 s default: a 6-10 minute
    suite always 'timed out' and became a SETUP finding."""

    def test_default_timeout_is_the_projects_not_120s(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "p")
            os.makedirs(root)
            with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
                json.dump({"stack": "python", "commands": {"test": "true"}}, fh)
            res = subprocess.run([sys.executable, REIN, "gate", root, "--json"], capture_output=True, text=True)
            self.assertEqual(json.loads(res.stdout)["report"]["timeoutSeconds"], 900)
            with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
                json.dump({"stack": "python", "commands": {"test": "true"},
                           "ship": {"gateTimeoutSeconds": 1500}}, fh)
            res = subprocess.run([sys.executable, REIN, "gate", root, "--json"], capture_output=True, text=True)
            self.assertEqual(json.loads(res.stdout)["report"]["timeoutSeconds"], 1500)

    def test_audit_brief_names_the_gate_command_per_worktree(self):
        import ship
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"], "/wt/b": ["y.py"]}, "R", "/wt/a", "demo",
                                     gate_commands={"/wt/a": "rein gate /wt/a --repo a",
                                                    "/wt/b": "rein gate /wt/b --repo b"})
        self.assertIn("rein gate /wt/a --repo a", spec)
        self.assertIn("rein gate /wt/b --repo b", spec)
        self.assertIn("--timeout", spec)  # the warning not to shorten it

    def test_the_skill_runs_rein_gate_not_the_raw_commands(self):
        with open(os.path.join(REPO, "plugins", "rein", "skills", "rein-audit", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        step2 = skill.split("2. **Mechanical part.**", 1)[1].split("\n3. ", 1)[0]
        self.assertIn("rein gate", step2)
        self.assertIn("--timeout", step2)
        self.assertIn("cached", step2)
