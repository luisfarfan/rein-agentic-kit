"""I8: Rein notices a worker that wrote outside its worktree.

A Codex worker ran a sibling repo's update script, which added a rule import to
proxima-qa/CLAUDE.md -- a repo only its owner may change. The briefs forbid it; now Rein
also checks it: when a worker (or quick agent) is launched it notes the git state of
every other checkout Orca knows, and when its task ends any new change in one of them
fails the task with the files named. (Prevention -- a sandbox the agent cannot write out
of -- is the agent's or Orca's to provide; this is detection.)"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import guard  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


def write(path, rel, text):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a") as fh:
        fh.write(text)


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "proxima-qa")
        make_repo(self.qa, hook=False)

    def test_an_untouched_sibling_is_clean(self):
        before = guard.outside_snapshot([self.qa])
        self.assertEqual(guard.outside_changes(before), [])

    def test_an_edit_in_a_sibling_is_named(self):
        before = guard.outside_snapshot([self.qa])
        write(self.qa, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("proxima-qa", problems[0])
        self.assertIn("CLAUDE.md", problems[0])

    def test_a_commit_in_a_sibling_is_caught(self):
        before = guard.outside_snapshot([self.qa])
        commit(self.qa, "app.py", "x = 5\n", "chore: sneaky")
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_a_file_already_dirty_and_edited_further_is_caught(self):
        write(self.qa, "app.py", "y = 1\n")
        before = guard.outside_snapshot([self.qa])
        write(self.qa, "app.py", "y = 2\n")
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_rein_state_and_ignored_paths_do_not_count(self):
        before = guard.outside_snapshot([self.qa])
        write(self.qa, ".rein/ship/demo.json", "{}")
        write(self.qa, "tasks.md", "- [x] T001\n")
        self.assertEqual(guard.outside_changes(before, ignore=[os.path.join(self.qa, "tasks.md")]), [])

    def test_a_vanished_checkout_is_not_a_crash(self):
        before = guard.outside_snapshot([self.qa])
        shutil.rmtree(self.qa)
        self.assertEqual(guard.outside_changes(before), [])


class Quick(DoneCase):
    def test_quick_done_fails_when_the_agent_wrote_in_another_repo(self):
        qa = os.path.join(self.root, "proxima-qa")
        make_repo(qa, hook=False)
        self.make_repo(outside_before=guard.outside_snapshot([qa]))
        self.write("src/foo.ts", "v2\n")
        write(qa, "CLAUDE.md", "@rules/new.md\n")
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("CLAUDE.md", self.text())


class Ship(ShipCase):
    def test_a_worker_that_writes_in_a_sibling_repo_is_retried_with_the_reason(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")
        qa = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(qa, hook=False)
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-qa"))
        original = fake._answer

        def answer(a):
            out = original(a)
            if a[:2] == ["repo", "list"]:
                for r in out["repos"]:
                    if r["displayName"] == "proxima-qa":
                        r["path"] = qa
            return out
        fake._answer = answer
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:  # the first worker ran the sibling's update script
                    write(qa, "CLAUDE.md", "@rules/new.md\n")
                # the retry leaves the sibling as it found it at its own launch
            return 0, "ok"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("CLAUDE.md", specs[1])
        self.assertIn("outside", specs[1])


if __name__ == "__main__":
    unittest.main()


class UntrackedAndIgnored(unittest.TestCase):
    """R3: a pre-existing untracked file edited or deleted is caught. R4: a path Rein ignores
    (the plan it ticks) never makes another dirty file look changed."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "proxima-qa")
        make_repo(self.qa, hook=False)

    def test_an_untracked_file_edited_further_is_caught(self):
        write(self.qa, "CLAUDE.md", "@rules/a.md\n")
        before = guard.outside_snapshot([self.qa])
        write(self.qa, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])

    def test_an_untracked_file_deleted_is_caught(self):
        write(self.qa, "CLAUDE.md", "@rules/a.md\n")
        before = guard.outside_snapshot([self.qa])
        os.remove(os.path.join(self.qa, "CLAUDE.md"))
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])

    def test_a_tracked_dirty_file_deleted_is_caught(self):
        write(self.qa, "app.py", "y = 1\n")
        before = guard.outside_snapshot([self.qa])
        os.remove(os.path.join(self.qa, "app.py"))
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_the_ignored_plan_never_blames_another_dirty_file(self):
        write(self.qa, "tasks.md", "- [ ] T001\n")
        run_git(self.qa, "add", "tasks.md")
        run_git(self.qa, "commit", "-q", "-m", "chore: plan")
        write(self.qa, "app.py", "y = 1\n")  # dirty before the worker started
        before = guard.outside_snapshot([self.qa])
        with open(os.path.join(self.qa, "tasks.md"), "w") as fh:
            fh.write("- [x] T001\n")
        self.assertEqual(guard.outside_changes(before, ignore=[os.path.join(self.qa, "tasks.md")]), [])



from test_quick_driver import DriverCase, _Git, _limits, _Orca  # noqa: E402


class QuickBaselineBeforeLaunch(DriverCase):
    """R5: the baseline of the other checkouts is taken before the agent is launched."""

    def test_a_write_during_the_launch_is_caught(self):
        qa = os.path.join(self.root, "proxima-qa")
        make_repo(qa, hook=False)
        self.repos.append({"id": "qa", "displayName": "proxima-qa", "path": qa})
        orca = _Orca(self.repos, _limits(10, 10), self.worktree)
        original = orca.__call__

        def call(argv):
            if argv[1:3] in (["worktree", "create"], ["agent", "launch"]):
                write(qa, "CLAUDE.md", "@rules/new.md\n")  # the agent is already at work
            return original(argv)
        code, _, _ = self.run_quick(here=True, git=_Git(branch="feature/x"), orca=call)
        self.assertEqual(code, 0, self.text())
        problems = guard.outside_changes(self.record(self.repo_path)["outside_before"])
        self.assertEqual(len(problems), 1, problems)
