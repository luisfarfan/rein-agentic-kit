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

    def test_a_vanished_checkout_is_reported_not_a_crash(self):
        before = guard.outside_snapshot([self.qa])
        shutil.rmtree(self.qa)
        self.assertEqual(len(guard.outside_changes(before)), 1)  # deleting a checkout is a write (N2)


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
        self.watch("proxima-qa")
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
                elif len(calls) == 2:  # the write is judged against the first launch: the retry puts it back
                    os.remove(os.path.join(qa, "CLAUDE.md"))
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
        with open(os.path.join(self.repo_path, "flow.config.json"), "w") as fh:
            json.dump({"gate": {"outsideWatch": ["proxima-qa"]}}, fh)  # a declared sibling
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


POST_COMMIT_BEADS = """#!/bin/sh
# What `bd hooks run post-commit` / lefthook's beads-export do: re-export the issues into the
# MAIN checkout's .beads/, whichever worktree the commit was made in.
main="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
mkdir -p "$main/.beads"
date +%s%N >> "$main/.beads/issues.jsonl"
"""


class RepoHooksOutsideIgnore(unittest.TestCase):
    """A repo's own git hooks (beads: `bd hooks run post-commit`, lefthook's beads-export) rewrite
    `.beads/` in the MAIN checkout on every commit made in a worktree. `gate.outsideIgnore` -- globs
    relative to the watched repo, from its config or the plan root's -- leaves those paths out;
    anything else still fails."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.main = os.path.join(self.tmp, "proxima-pos")
        make_repo(self.main, hook=False)
        hook = os.path.join(self.main, ".git", "hooks", "post-commit")
        with open(hook, "w") as fh:
            fh.write(POST_COMMIT_BEADS)
        os.chmod(hook, 0o755)
        write(self.main, ".beads/issues.jsonl", "{}\n")
        run_git(self.main, "add", "-A")
        run_git(self.main, "commit", "-q", "-m", "chore: beads")
        self.wt = os.path.join(self.tmp, "wt")
        run_git(self.main, "worktree", "add", "-q", "-b", "feature/x", self.wt)
        with open(os.path.join(self.main, ".beads", "issues.jsonl")) as fh:
            self.before_hook = fh.read()

    def configure(self, config):
        with open(os.path.join(self.main, "flow.config.json"), "w") as fh:
            json.dump(config, fh)
        run_git(self.main, "commit", "-q", "-am", "chore: config")

    def worker_commits(self):
        """The worker commits in its worktree; the repo's post-commit hook writes the main checkout."""
        with open(os.path.join(self.wt, "app.py"), "w") as fh:
            fh.write("x = 2\n")
        run_git(self.wt, "commit", "-q", "-am", "feat: x")
        with open(os.path.join(self.main, ".beads", "issues.jsonl")) as fh:
            self.assertNotEqual(fh.read(), self.before_hook, "the hook did not write the main checkout")

    def test_without_config_the_hook_write_fails_the_task(self):
        before = guard.outside_snapshot([self.main])
        self.worker_commits()
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(".beads/issues.jsonl", problems[0])

    def test_the_repo_config_ignores_the_hook_write(self):
        self.configure({"commands": {"test": "true"}, "gate": {"outsideIgnore": [".beads/"]}})
        before = guard.outside_snapshot([self.main])
        self.worker_commits()
        self.assertEqual(guard.outside_changes(before), [])

    def test_the_plan_root_config_ignores_the_hook_write(self):
        before = guard.outside_snapshot([self.main], ignore_globs=[".beads/"])
        self.worker_commits()
        self.assertEqual(guard.outside_changes(before), [])

    def test_a_write_outside_the_pattern_still_fails(self):
        self.configure({"commands": {"test": "true"}, "gate": {"outsideIgnore": [".beads/"]}})
        before = guard.outside_snapshot([self.main])
        self.worker_commits()
        write(self.main, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])
        self.assertNotIn(".beads", problems[0])

    def test_the_patterns_are_fixed_at_launch(self):
        """A worker that adds the ignore to the watched repo's config afterwards widens nothing."""
        before = guard.outside_snapshot([self.main])
        with open(os.path.join(self.main, "flow.config.json"), "w") as fh:
            json.dump({"gate": {"outsideIgnore": ["*"]}}, fh)
        write(self.main, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])

    def test_a_pattern_never_hides_a_new_commit(self):
        self.configure({"commands": {"test": "true"}, "gate": {"outsideIgnore": ["*"]}})
        before = guard.outside_snapshot([self.main])
        commit(self.main, "app.py", "x = 9\n", "chore: sneaky")
        self.assertEqual(len(guard.outside_changes(before)), 1)


class ShipPlanRootOutsideIgnore(ShipCase):
    def test_the_plan_roots_outside_ignore_applies_to_every_watched_repo(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            json.dump({"gate": {"outsideIgnore": [".beads/"]}}, fh)
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
                if len(calls) == 1:  # the repo's hooks, not the worker; a retry would not write again
                    write(qa, ".beads/issues.jsonl", "{}\n")
            return 0, "ok"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 1, self.out)


class OutsideIgnoreIsExact(unittest.TestCase):
    """Review round 2. The ignore excludes exactly what the declared pattern matches (`dir/` is that
    folder and its content, nothing more); ignored paths never count against the cap of watched
    paths; a `gate` that is not an object ignores nothing."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "proxima-pos")
        make_repo(self.qa, hook=False)

    def configure(self, config):
        with open(os.path.join(self.qa, "flow.config.json"), "w") as fh:
            json.dump(config, fh)
        run_git(self.qa, "commit", "-q", "-am", "chore: config")

    def cap(self, n):
        saved = guard.OUTSIDE_PATH_CAP
        guard.OUTSIDE_PATH_CAP = n
        self.addCleanup(setattr, guard, "OUTSIDE_PATH_CAP", saved)

    # I2: a file glob is never widened to the descendants of what it matches
    def test_a_file_glob_does_not_cover_a_folder_of_that_name(self):
        self.configure({"gate": {"outsideIgnore": [".beads/*.jsonl"]}})
        before = guard.outside_snapshot([self.qa])
        write(self.qa, ".beads/issues.jsonl", "{}\n")
        write(self.qa, ".beads/backup.jsonl/CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(".beads/backup.jsonl/CLAUDE.md", problems[0])
        self.assertNotIn("issues.jsonl", problems[0])

    def test_what_is_excluded_is_what_the_pattern_declares(self):
        cases = [
            (".beads/", ".beads/issues.jsonl", True),
            (".beads/", ".beads/a/b.jsonl", True),
            (".beads/", ".beads", False),  # a file named like the folder is not in it
            (".beads/", ".beadsX/issues.jsonl", False),
            (".beads/", "sub/.beads/issues.jsonl", False),
            (".beads", ".beads/issues.jsonl", False),  # no slash: the path itself, not a folder
            (".beads", ".beads", True),
            (".beads/*.jsonl", ".beads/issues.jsonl", True),
            (".beads/*.jsonl", ".beads/backup.jsonl/CLAUDE.md", False),
            ("*.lock", "yarn.lock", True),
            ("*.lock", "yarn.lock/evil.sh", False),
            ("cache*/", "cache1/x", True),
            ("cache*/", "cache1x/y/z", True),
            ("cache*/", "a/cache1/x", False),
            ("*/", "anything/x", True),  # one top-level folder, whatever its name ...
            ("*/", "top.md", False),     # ... but never a top-level file
        ]
        for glob, rel, want in cases:
            self.assertEqual(guard._outside_ignored(rel, [glob]), want, (glob, rel))

    # I1: ignored paths never use up the cap, and a cap that cannot cover the rest fails closed
    def test_ignored_files_do_not_use_up_the_cap(self):
        self.cap(3)
        self.configure({"gate": {"outsideIgnore": [".beads/"]}})
        before = guard.outside_snapshot([self.qa])
        for i in range(5):
            write(self.qa, ".beads/%05d.jsonl" % i, "{}\n")
        write(self.qa, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CLAUDE.md", problems[0])

    def test_ignored_files_already_there_do_not_use_up_the_snapshot_cap(self):
        self.cap(3)
        self.configure({"gate": {"outsideIgnore": [".beads/"]}})
        for i in range(5):
            write(self.qa, ".beads/%05d.jsonl" % i, "{}\n")
        write(self.qa, "notes.md", "a\n")  # dirty before the launch, edited by the worker
        before = guard.outside_snapshot([self.qa])
        self.assertIn("notes.md", before[self.qa]["paths"])
        write(self.qa, "notes.md", "b\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("notes.md", problems[0])

    def test_more_watched_paths_than_the_cap_fails_closed_at_the_end(self):
        self.cap(3)
        before = guard.outside_snapshot([self.qa])
        for i in range(4):
            write(self.qa, "f%d.md" % i, "x\n")
        problems, broken = guard.run_guard(guard.outside_changes, before)
        self.assertEqual(problems, [])
        self.assertIn("guard could not run", broken)

    def test_more_watched_paths_than_the_cap_fails_closed_at_launch(self):
        self.cap(3)
        for i in range(4):
            write(self.qa, "f%d.md" % i, "x\n")
        before = guard.outside_snapshot([self.qa])
        self.assertIn("error", before[self.qa])
        problems, broken = guard.run_guard(guard.outside_changes, before)
        self.assertIn("guard could not run", broken)

    # M1
    def test_a_gate_that_is_not_an_object_ignores_nothing(self):
        self.configure({"gate": "invalid"})
        before = guard.outside_snapshot([self.qa])
        self.assertNotIn("error", before[self.qa])
        write(self.qa, ".beads/issues.jsonl", "{}\n")
        self.assertEqual(len(guard.outside_changes(before)), 1)
        self.assertEqual(guard.outside_ignore_globs({"gate": ["x"]}), [])
        self.assertEqual(guard.outside_ignore_globs({"gate": {"outsideIgnore": "x"}}), [])
