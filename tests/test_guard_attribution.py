"""The outside-worktree guard attributes to the worker only what the worker could have produced.

Another session's pushes, merges and commits in the same checkouts during a task are not the
worker's writes (a real run failed task after task on a `rein merge` and a release another
orchestrator made). What stays a failure: file content that differs from what it was at launch,
and a commit created in that checkout under the checkout's own identity. What cannot be told
(HEAD switched, commits that arrived by merge but did not exist at launch) is a warning for the
owner when the caller asks for one, never a failure and never silence."""

from __future__ import annotations

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
from test_guard_outside import write  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


def other_session_commit(path, rel, text, msg):
    """A commit made by someone else: another identity than the checkout's own."""
    with open(os.path.join(path, rel), "w") as fh:
        fh.write(text)
    run_git(path, "add", rel)
    env = dict(os.environ, GIT_AUTHOR_NAME="o", GIT_AUTHOR_EMAIL="other@o", GIT_COMMITTER_NAME="o",
               GIT_COMMITTER_EMAIL="other@o")
    subprocess.run(["git", "-C", path, "commit", "-q", "--no-verify", "-m", msg], check=True, env=env,
                   capture_output=True)


class Attribution(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "proxima-qa")
        make_repo(self.qa, hook=False)
        run_git(self.qa, "config", "user.email", "t@t")
        run_git(self.qa, "config", "user.name", "me")

    def review(self, before, **kw):
        warnings, held = [], []
        problems = guard.outside_changes(before, warnings=warnings, unresolved=held, **kw)
        return problems, warnings + held

    def test_a_fetch_that_advances_a_remote_ref_is_not_the_workers(self):
        remote = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        run_git(self.qa, "remote", "add", "origin", remote)
        run_git(self.qa, "push", "-q", "origin", "develop")
        before = guard.outside_snapshot([self.qa])
        other = os.path.join(self.tmp, "clone")
        subprocess.run(["git", "clone", "-q", "-b", "develop", remote, other], check=True)
        commit(other, "b.py", "b = 1\n", "feat: elsewhere")
        run_git(other, "push", "-q", "origin", "develop")
        run_git(self.qa, "fetch", "-q", "origin")
        self.assertEqual(self.review(before), ([], []))

    def test_a_fast_forward_to_work_that_existed_at_launch_is_not_the_workers(self):
        run_git(self.qa, "checkout", "-q", "-b", "feature")
        commit(self.qa, "f.py", "f = 1\n", "feat: a feature")
        run_git(self.qa, "checkout", "-q", "develop")
        before = guard.outside_snapshot([self.qa])
        run_git(self.qa, "merge", "-q", "--ff-only", "feature")  # `rein merge` from another session
        self.assertEqual(self.review(before), ([], []))

    def test_a_commit_by_another_identity_made_here_is_unresolved_not_a_failure(self):
        before = guard.outside_snapshot([self.qa])
        other_session_commit(self.qa, "rel.py", "r = 1\n", "chore: release")
        held = []
        problems = guard.outside_changes(before, warnings=[], unresolved=held)
        self.assertEqual(problems, [])
        self.assertEqual(len(held), 1, held)
        self.assertEqual(len(guard.outside_changes(before, warnings=[])), 1)  # no list for it: fails closed

    def test_a_file_dirty_at_launch_and_committed_unchanged_by_someone_else_is_clean(self):
        write(self.qa, "app.py", "y = 1\n")
        before = guard.outside_snapshot([self.qa])
        other_session_commit(self.qa, "app.py", "x = 1\ny = 1\n", "chore: commit the wip")
        self.assertEqual(self.review(before), ([], []))

    def test_a_commit_under_the_checkouts_own_identity_is_still_the_workers(self):
        before = guard.outside_snapshot([self.qa])
        commit(self.qa, "app.py", "x = 5\n", "chore: sneaky")
        problems, warnings = self.review(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(warnings, [])

    def test_a_real_edit_is_caught_whatever_else_happened(self):
        before = guard.outside_snapshot([self.qa])
        other_session_commit(self.qa, "rel.py", "r = 1\n", "chore: release")
        write(self.qa, "CLAUDE.md", "@rules/new.md\n")
        problems, _ = self.review(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])

    def test_a_switched_head_cannot_be_attributed_so_it_is_a_warning(self):
        diverged(self.qa)
        before = guard.outside_snapshot([self.qa])
        run_git(self.qa, "checkout", "-q", "lone")
        problems, warnings = self.review(before)
        self.assertEqual(problems, [], problems)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn(self.qa, warnings[0])

    def test_without_a_warnings_list_what_cannot_be_attributed_still_fails(self):
        diverged(self.qa)
        before = guard.outside_snapshot([self.qa])
        run_git(self.qa, "checkout", "-q", "lone")
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_a_merge_of_work_made_after_launch_is_a_warning_not_a_failure(self):
        run_git(self.qa, "checkout", "-q", "-b", "feature")
        before = guard.outside_snapshot([self.qa])
        run_git(self.qa, "checkout", "-q", "develop")
        # another session made this commit in another worktree after the worker started
        wt = os.path.join(self.tmp, "wt")
        run_git(self.qa, "worktree", "add", "-q", "-b", "later", wt, "develop")
        commit(wt, "l.py", "l = 1\n", "feat: later")
        run_git(self.qa, "merge", "-q", "--ff-only", "later")
        problems, warnings = self.review(before)
        self.assertEqual(problems, [], problems)
        self.assertEqual(len(warnings), 1, warnings)


def diverged(path):
    """A branch `lone` of history that does not contain develop's tip, existing before any launch."""
    run_git(path, "checkout", "-q", "--orphan", "lone")
    run_git(path, "commit", "-q", "--allow-empty", "--no-verify", "-m", "chore: lone")
    run_git(path, "checkout", "-q", "-f", "develop")


def switch_head(path):
    """Someone moves the checkout to `lone` (made by `diverged`): work that existed, not a descendant."""
    run_git(path, "checkout", "-q", "lone")


class Ship(ShipCase):
    def test_another_sessions_head_move_warns_the_owner_and_does_not_fail_the_task(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")
        qa = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(qa, hook=False)
        diverged(qa)
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

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                switch_head(qa)  # another orchestrator, meanwhile
            return 0, "ok"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 1, self.out)
        self.assertIn("guard warning", self.out)
        self.assertIn("proxima-qa", self.out)


class Quick(DoneCase):
    def test_quick_done_warns_and_stays_green_when_another_session_moved_a_checkout(self):
        qa = os.path.join(self.root, "proxima-qa")
        make_repo(qa, hook=False)
        diverged(qa)
        self.make_repo(outside_before=guard.outside_snapshot([qa]))
        self.write("src/foo.ts", "v2\n")
        switch_head(qa)
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())
        self.assertIn("guard warning", self.text())


if __name__ == "__main__":
    unittest.main()
