"""`gate.env` in the quick lane: `rein quick done` runs its checks with it, and the brief exports it."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import quick  # noqa: E402
from test_quick_driver import CONFIG, DoneCase, _result  # noqa: E402


class _EnvGate:
    def __init__(self):
        self.envs = []

    def __call__(self, slot, command, cwd, timeout, configured=None, env=None):
        self.envs.append(env)
        return _result(slot)


class TestDoneUsesTheEnv(DoneCase):
    def test_every_check_gets_the_worktree_env(self):
        self.make_repo(config={**CONFIG, "gate": {"env": {"APP_DB": "app_{worktree}", "R": "{repo}"}}})
        self.write("src/foo.ts", "v2\n")
        run = _EnvGate()
        code, _, _ = self.done(run=run)
        self.assertEqual(code, 0, self.text())
        self.assertTrue(run.envs)
        self.assertTrue(all(e == {"APP_DB": "app_quick_fix", "R": "proxima-api"} for e in run.envs), run.envs)

    def test_without_config_no_env(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        run = _EnvGate()
        self.done(run=run)
        self.assertTrue(all(not e for e in run.envs))


class TestBrief(unittest.TestCase):
    def test_brief_opens_with_the_export_line(self):
        brief = quick.build_brief("arregla x", env_lines=["export APP_DB=app_x"])
        self.assertIn("export APP_DB=app_x", brief.split("\n", 1)[0])
        self.assertNotIn("export", quick.build_brief("arregla x"))


if __name__ == "__main__":
    unittest.main()
