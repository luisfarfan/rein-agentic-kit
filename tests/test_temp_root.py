"""A plan root in a temporary directory loses `.rein/` on reboot: ship refuses, quick and plan warn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")
sys.path.insert(0, os.path.join(REPO_ROOT, "plugins", "rein", "lib"))

import tmproot  # noqa: E402

HOME = os.path.expanduser("~")


def rein(*args, env=None):
    proc = subprocess.run([sys.executable, REIN_BIN, *args], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, **(env or {})), stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout + proc.stderr


class Detect(unittest.TestCase):
    def test_the_usual_temp_roots(self):
        for path in ("/tmp/x", "/private/tmp/eng-wt", "/var/folders/ab/cd/T/plan", "/private/var/folders/ab/T/p"):
            self.assertTrue(tmproot.temp_root(path), path)

    def test_tmpdir_counts(self):
        scratch = os.path.join(HOME, "rein-test-scratch-tmpdir")
        with mock.patch.dict(os.environ, {"TMPDIR": scratch}):
            self.assertTrue(tmproot.temp_root(os.path.join(scratch, "plan")))

    def test_a_home_checkout_is_fine(self):
        self.assertEqual(tmproot.temp_root(os.path.join(HOME, "Developer", "plan")), "")
        self.assertEqual(tmproot.warning(os.path.join(HOME, "Developer", "plan")), "")

    def test_a_sibling_name_is_not_a_temp_dir(self):
        self.assertEqual(tmproot.temp_root("/tmpfoo/x"), "")

    def test_the_warning_says_what_is_lost(self):
        self.assertIn(".rein", tmproot.warning("/tmp/x"))


class Ship(unittest.TestCase):
    def test_ship_refuses_a_temp_root(self):
        with tempfile.TemporaryDirectory() as root:
            code, out = rein("ship", "demo", "--root", root)
        self.assertEqual(code, 2, out)
        self.assertIn("refuses", out)
        self.assertIn("--allow-temp-root", out)

    def test_the_flag_lets_it_through_to_the_real_checks(self):
        with tempfile.TemporaryDirectory() as root:
            code, out = rein("ship", "demo", "--root", root, "--allow-temp-root")
        self.assertNotIn("refuses", out)
        self.assertIn("warning", out)

    def test_a_dry_run_only_warns(self):
        with tempfile.TemporaryDirectory() as root:
            code, out = rein("ship", "demo", "--root", root, "--dry-run")
        self.assertNotIn("refuses", out)
        self.assertIn("warning", out)


class Plan(unittest.TestCase):
    def test_plan_check_warns_when_the_plan_root_is_temporary(self):
        with tempfile.TemporaryDirectory() as root:
            plan = os.path.join(root, "tasks.md")
            with open(plan, "w") as fh:
                fh.write("# Change: demo\n\n- [ ] T001 Do it\n  - Repo: x\n  - Verification: `true`\n")
            code, out = rein("plan-check", plan, "--root", root)
        data = json.loads(out[out.index("{"):])
        self.assertTrue(any("temporary" in w for w in data["warnings"]), data)

    def test_plan_check_has_no_warning_for_a_home_root(self):
        code, out = rein("plan-check", "/nonexistent/tasks.md", "--root", os.path.join(HOME, "Developer"))
        data = json.loads(out[out.index("{"):])
        self.assertEqual(data["warnings"], [])


class Quick(unittest.TestCase):
    def test_quick_warns_from_a_temp_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            proc = subprocess.run([sys.executable, REIN_BIN, "quick", "fix the typo"], capture_output=True,
                                  text=True, timeout=60, cwd=root, stdin=subprocess.DEVNULL)
        self.assertIn("temporary", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
