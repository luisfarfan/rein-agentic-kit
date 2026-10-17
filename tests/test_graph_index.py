"""The codegraph index is machine-local and must never be tracked.

This repo is its own consuming project (the kit is developed with the kit),
so its regenerated codegraph index must never be tracked here either.
"""

from __future__ import annotations

import os
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GraphOutputNeverTrackedTestCase(unittest.TestCase):
    def test_gitignore_excludes_dot_codegraph(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".codegraph/", f.read())

    def test_git_actually_ignores_the_codegraph_index_path(self):
        # A real functional check, not just a substring in .gitignore.
        proc = subprocess.run(
            ["git", "-C", REPO_ROOT, "check-ignore", "-q", ".codegraph/codegraph.db"],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, ".codegraph/ must stay git-ignored -- the index is machine-local")


if __name__ == "__main__":
    unittest.main()
