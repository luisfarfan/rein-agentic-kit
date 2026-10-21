"""N1: a sub-project with nested folders resolves; and a guard that breaks never breaks the gate.

`resolve_at` listed the chosen sub-project with a non-recursive `ls-tree` and wrote every entry
as a file, trees included: `app/tools/` (holding a package.json already extracted) became an
IsADirectoryError that escaped `guard.check` and `rein quick done`. Only blobs are extracted
now, and any error inside the guard is reported as the environment's (126), not raised."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import detect  # noqa: E402
import guard  # noqa: E402
import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_guard import run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402
from test_stalled_worker import Driver  # noqa: E402

TREE = {"flow.config.json": json.dumps({"subproject": "app"}), "app/package.json": "{}",
        "app/mise.toml": '[tasks.test]\nrun = "true"\n', "app/tools/package.json": "{}",
        "app/.config/mise/config.toml": "[tools]\n", "app/src/deep/x.py": "x = 1\n"}


class NestedSubproject(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        for name, text in TREE.items():
            os.makedirs(os.path.dirname(os.path.join(self.repo, name)), exist_ok=True)
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "chore: base")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()

    def test_resolve_at_finishes_and_sees_the_runner(self):
        resolved = guard.resolve_at(self.repo, self.base)
        self.assertEqual(resolved["commands"].get("test"), detect.resolve(self.repo)["commands"].get("test"))

    def test_the_guard_finishes_and_still_catches_the_runners_removal(self):
        self.assertEqual(guard.check(self.repo, self.base, detect.resolve(self.repo)), [])
        run_git(self.repo, "rm", "-q", "app/mise.toml")
        problems = guard.check(self.repo, self.base, detect.resolve(self.repo))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("app/mise.toml", problems[0])


if __name__ == "__main__":
    unittest.main()
