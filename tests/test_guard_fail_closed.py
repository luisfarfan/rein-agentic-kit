"""N2: the guard fails CLOSED. Any error rebuilding the base or comparing the config ends in
"guard could not run" (126, the environment), never in "no change".

A write error while extracting the base's mise.toml was swallowed; the base then had no runner,
deleting mise.toml was no longer a change to protected config, and `rein quick done` said 0.
Every silent `except`/`return` of the guard is covered here, one path per test."""

from __future__ import annotations

import builtins
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import detect  # noqa: E402
import gate_log  # noqa: E402
import guard  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402

MISE = '[tasks.test]\nrun = "true"\n'


def failing_git(verb, sub=None):
    """guard._git that fails (code 128) for `git <verb> [<sub>]`, and is the real one otherwise."""
    real = guard._git

    def fake(path, *args):
        if args and args[0] == verb and (sub is None or (len(args) > 1 and args[1] == sub)):
            return 128, ""
        return real(path, *args)
    return fake


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        with open(os.path.join(self.repo, "mise.toml"), "w") as fh:
            fh.write(MISE)
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "chore: base")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()
        run_git(self.repo, "rm", "-q", "mise.toml")  # the worker retires the runner

    def guarded(self):
        return guard.run_guard(guard.check, self.repo, self.base, detect.resolve(self.repo))

    def assert_fails_closed(self, result=None):
        problems, broken = result or self.guarded()
        self.assertEqual(problems, [], "an unverifiable result must not pass for a verdict")
        self.assertTrue(broken.startswith("guard could not run"), broken)

    def test_the_intact_path_still_names_the_violation(self):
        problems, broken = self.guarded()
        self.assertEqual(broken, "")
        self.assertEqual(len(problems), 1)


class RebuildingTheBase(Base):
    def test_a_write_error_while_extracting_fails_closed(self):
        real_open = builtins.open

        def no_space(file, mode="r", *a, **kw):
            if "rein-guard-base-" in str(file) and "w" in mode:
                raise OSError(28, "No space left on device")
            return real_open(file, mode, *a, **kw)
        with mock.patch("builtins.open", no_space):
            self.assert_fails_closed()

    def test_a_git_show_that_fails_fails_closed(self):
        real_run = subprocess.run

        def run(argv, *a, **kw):
            if argv[:1] == ["git"] and "show" in argv:
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: bad object")
            return real_run(argv, *a, **kw)
        with mock.patch.object(guard.subprocess, "run", run):
            self.assert_fails_closed()

    def test_no_merge_base_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("merge-base")):
            self.assert_fails_closed()

    def test_a_tree_listing_that_fails_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("ls-tree")):
            self.assert_fails_closed()

    def test_the_subprojects_listing_failing_fails_closed(self):
        with open(os.path.join(self.repo, "flow.config.json"), "w") as fh:
            json.dump({"subproject": "app"}, fh)
        os.makedirs(os.path.join(self.repo, "app"))
        with open(os.path.join(self.repo, "app", "package.json"), "w") as fh:
            fh.write("{}")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "chore: sub")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()
        real = guard._git

        def fake(path, *args):
            if args[:1] == ("ls-tree",) and "-r" not in args:
                return 128, ""
            return real(path, *args)
        with mock.patch.object(guard, "_git", fake):
            self.assert_fails_closed()


class ComparingTheChange(Base):
    def test_listing_the_changes_failing_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("diff")):
            self.assert_fails_closed()

    def test_not_a_git_checkout_fails_closed(self):
        os.makedirs(os.path.join(self.tmp, "plain"))
        self.assert_fails_closed(guard.run_guard(guard.check, os.path.join(self.tmp, "plain"), "origin/develop", {}))

    def test_no_base_fails_closed(self):
        self.assert_fails_closed(guard.run_guard(guard.check, self.repo, "", {}))


class CommitMessages(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo)
        commit(self.repo, "app.py", "x = 2\n", "fix: ok")

    def closed(self, result):
        problems, broken = result
        self.assertEqual(problems, [])
        self.assertTrue(broken.startswith("guard could not run"), broken)

    def test_listing_the_commits_failing_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("rev-list")):
            self.closed(guard.run_guard(guard.commit_msg_failures, self.repo, self.base))

    def test_reading_a_message_failing_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("log")):
            self.closed(guard.run_guard(guard.commit_msg_failures, self.repo, self.base))

    def test_a_hook_that_cannot_be_started_fails_closed(self):
        real_run = subprocess.run

        def run(argv, *a, **kw):
            if argv[:1] == ["git"] and argv[3:5] == ["hook", "run"]:
                raise OSError(7, "Argument list too long")
            return real_run(argv, *a, **kw)
        with mock.patch.object(guard.subprocess, "run", run):
            self.closed(guard.run_guard(guard.commit_msg_failures, self.repo, self.base))

    def test_not_finding_the_hooks_folder_fails_closed(self):
        with mock.patch.object(guard, "_git", failing_git("rev-parse", "--git-path")), \
                mock.patch.object(guard, "_git_runs_hooks", return_value=False):
            self.closed(guard.run_guard(guard.commit_msg_failures, self.repo, self.base))


class Outside(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "qa")
        make_repo(self.qa, hook=False)

    def closed(self, result):
        problems, broken = result
        self.assertEqual(problems, [])
        self.assertTrue(broken.startswith("guard could not run"), broken)

    def test_a_checkout_git_cannot_read_at_launch_fails_closed_at_the_end(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        before = guard.outside_snapshot([plain, self.qa])
        self.closed(guard.run_guard(guard.outside_changes, before))

    def test_a_checkout_that_vanished_is_a_change(self):
        before = guard.outside_snapshot([self.qa])
        shutil.rmtree(self.qa)
        problems, broken = guard.run_guard(guard.outside_changes, before)
        self.assertEqual(broken, "")
        self.assertEqual(len(problems), 1)
        self.assertIn("no longer", problems[0])

    def test_git_failing_at_the_end_fails_closed(self):
        before = guard.outside_snapshot([self.qa])
        with mock.patch.object(guard, "_git", failing_git("rev-parse")):
            self.closed(guard.run_guard(guard.outside_changes, before))

    def test_status_failing_fails_closed(self):
        before = guard.outside_snapshot([self.qa])
        with mock.patch.object(guard, "_git", failing_git("status")):
            self.closed(guard.run_guard(guard.outside_changes, before))

    @unittest.skipIf(os.geteuid() == 0, "root reads anything")
    def test_a_file_that_cannot_be_read_fails_closed(self):
        secret = os.path.join(self.qa, "notes.md")
        with open(secret, "w") as fh:
            fh.write("a")
        before = guard.outside_snapshot([self.qa])
        os.chmod(secret, 0)
        self.addCleanup(os.chmod, secret, stat.S_IRUSR | stat.S_IWUSR)
        self.closed(guard.run_guard(guard.outside_changes, before))

    def test_a_corrupt_snapshot_fails_closed(self):
        self.closed(guard.run_guard(guard.outside_changes, {self.qa: "garbage"}))


class QuickDoneFailsClosed(DoneCase):
    def test_a_write_error_rebuilding_the_base_is_126_and_logged(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        self.write("flow.config.json", json.dumps({"commands": {"test": "true"}}))
        real_open = builtins.open

        def no_space(file, mode="r", *a, **kw):
            if "rein-guard-base-" in str(file) and "w" in mode:
                raise OSError(28, "No space left on device")
            return real_open(file, mode, *a, **kw)
        with mock.patch("builtins.open", no_space):
            code, _, _ = self.done()
        self.assertEqual(code, 126, self.text())
        self.assertIn("guard could not run", self.text())
        [entry] = gate_log.read(gate_log.root_log_path(self.ws))
        self.assertEqual(entry["exit"], 126)
        self.assertIn("No space left", entry["guard"])


if __name__ == "__main__":
    unittest.main()
