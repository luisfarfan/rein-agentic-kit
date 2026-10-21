"""`gate.cleanup`: a per-worktree cleanup command run after `rein done` / `rein quick close` remove it.

Gates create per-worktree resources outside the tree (a test database per `{worktree}`), and
nothing dropped them when the change closed. The command is the project's: Rein only fills
`{worktree}` (the same slug `gate.env` uses), `{repo}` and `{path}`, and runs it once the
worktree is gone. Nothing configured, nothing runs."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import gate_env  # noqa: E402
from test_ship_done import FakeOrca, QuickCloseBase, commit_file, git  # noqa: E402


class Cleanup(QuickCloseBase):
    def configure(self, gate):
        commit_file(self.seed, "flow.config.json", json.dumps({"gate": gate}), "config")
        git(self.seed, "push", "-q", "origin", "develop")

    def log(self):
        path = os.path.join(self.tmp, "cleanup.log")
        return open(path).read().split() if os.path.exists(path) else []

    def command(self):
        return "echo {worktree} {repo} >> %s" % os.path.join(self.tmp, "cleanup.log")

    def test_quick_close_runs_it_after_removing(self):
        self.configure({"cleanup": self.command()})
        path, _ = self.linked("fix-db")
        self.record(path)
        code, orca = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [path])
        self.assertEqual(self.log(), [gate_env.worktree_slug(path), "api"])
        self.assertIn("cleanup", self.text())

    def test_a_kept_worktree_is_not_cleaned(self):
        self.configure({"cleanup": self.command()})
        path, _ = self.linked("fix")
        self.record(path)
        commit_file(path, "new.txt")  # not integrated: kept
        code, _ = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertEqual(self.log(), [])

    def test_a_failed_remove_is_not_cleaned(self):
        self.configure({"cleanup": self.command()})
        path, _ = self.linked("fix")
        self.record(path)
        code, _ = self.close(arg=path, orca=FakeOrca(fail_rm=(path,)))
        self.assertEqual(code, 126)
        self.assertEqual(self.log(), [])

    def test_nothing_configured_runs_nothing(self):
        path, _ = self.linked("fix")
        self.record(path)
        code, _ = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("cleanup", self.text())

    def test_a_failing_cleanup_is_reported_not_hidden(self):
        self.configure({"cleanup": "echo boom >&2; exit 3"})
        path, _ = self.linked("fix")
        self.record(path)
        code, _ = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertIn("WARNING: cleanup", self.text())
        self.assertIn("exit 3", self.text())

    def test_rein_done_runs_the_plan_roots_cleanup_for_each_worktree(self):
        path, _ = self.linked("api-wt")
        root = self.plan_repo()
        with open(os.path.join(root, "flow.config.json"), "w") as fh:
            json.dump({"plan": {"source": "openspec"}, "gate": {"cleanup": self.command()}}, fh)
        git(root, "add", "flow.config.json")
        git(root, "commit", "-q", "-m", "cfg")
        self.state(root, {"api": path})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(self.log(), [gate_env.worktree_slug(path), "api"])

    def test_dry_run_only_says_what_it_would_run(self):
        self.configure({"cleanup": self.command()})
        path, _ = self.linked("fix")
        self.record(path)
        code, _ = self.close(arg=path, dry=True)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(self.log(), [])
        self.assertIn("would run cleanup", self.text())


class Expansion(unittest.TestCase):
    def test_placeholders(self):
        cmd = gate_env.expand_command("drop {worktree} of {repo} at {path}", "/x/My WT", "api")
        self.assertEqual(cmd, "drop my_wt of api at '/x/My WT'")


if __name__ == "__main__":
    unittest.main()
