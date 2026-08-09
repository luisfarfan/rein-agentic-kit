"""Tests for T001 ("A workspace is N repos, and a monorepo is not one").

Real `git init` fixtures throughout -- the walk and the member resolution are
proven against directories that exist, not stand-ins for them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import workspace  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")
_GIT = shutil.which("git")


def _sh(args: list, cwd: str | None = None) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: str, branch: str = "main") -> None:
    os.makedirs(path, exist_ok=True)
    _sh([_GIT, "init", "-q", "-b", branch, path])
    _sh([_GIT, "-C", path, "config", "user.email", "test@example.com"])
    _sh([_GIT, "-C", path, "config", "user.name", "Test"])
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("hello\n")
    _sh([_GIT, "-C", path, "add", "README.md"])
    _sh([_GIT, "-C", path, "commit", "-q", "-m", "init"])


def _head(path: str) -> str:
    proc = subprocess.run([_GIT, "-C", path, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _write_descriptor(root: str, members: list) -> None:
    os.makedirs(os.path.join(root, ".rein"), exist_ok=True)
    with open(os.path.join(root, ".rein", "workspace.json"), "w", encoding="utf-8") as fh:
        json.dump({"members": members}, fh)


class ThreeSiblingRepos(unittest.TestCase):
    """A workspace root holding three real, independent git repos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        for name, branch in (("api", "main"), ("web", "main"), ("docs", "trunk")):
            _init_repo(os.path.join(self.root, name), branch)
        _write_descriptor(self.root, [
            {"name": "api", "path": "api"},
            {"name": "web", "path": "web"},
            {"name": "docs", "path": "docs"},
        ])

    def test_discover_walks_up_to_the_descriptor(self):
        # Start from deep inside one member -- discover must still find the
        # root's .rein/workspace.json, not the member's own (nonexistent) one.
        start = os.path.join(self.root, "api")
        found = workspace.discover(start)
        self.assertTrue(found["found"])
        self.assertEqual(found["root"], self.root)
        self.assertEqual(found["error"], "")
        self.assertEqual(found["doc"]["members"][0]["name"], "api")

    def test_discover_reports_absence_without_raising(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        found = workspace.discover(outside)
        self.assertFalse(found["found"])
        self.assertEqual(found["doc"], None)

    def test_members_returns_the_exact_tuple_set(self):
        found = workspace.discover(self.root)
        ok, problems = workspace.members(found["doc"], found["root"])
        self.assertEqual(problems, [])
        expected = {
            ("api", os.path.join(self.root, "api"), "main", _head(os.path.join(self.root, "api"))),
            ("web", os.path.join(self.root, "web"), "main", _head(os.path.join(self.root, "web"))),
            ("docs", os.path.join(self.root, "docs"), "trunk", _head(os.path.join(self.root, "docs"))),
        }
        self.assertEqual(set(ok), expected)

    def test_git_invocations_are_pinned_to_rev_parse_only(self):
        """A later refactor cannot widen what this module executes: every
        call must be `git -C <member path> rev-parse ...` -- nothing else."""
        calls = []
        real_run = subprocess.run

        def _recording_run(args, **kwargs):
            calls.append(list(args))
            return real_run(args, **kwargs)

        found = workspace.discover(self.root)
        with unittest.mock.patch.object(workspace.subprocess, "run", side_effect=_recording_run):
            ok, problems = workspace.members(found["doc"], found["root"])
        self.assertEqual(problems, [])
        self.assertEqual(len(calls), 6)  # 2 git calls x 3 members
        for call in calls:
            self.assertEqual(call[0], "git")
            self.assertEqual(call[1], "-C")
            self.assertIn(call[3], ("rev-parse",))
            self.assertTrue(set(call).issubset({"git", "-C", call[2], "rev-parse", "--abbrev-ref", "--short", "HEAD"}))


class MonorepoIsNotAWorkspace(unittest.TestCase):
    """One `.git`, three subdirectories -- D5: a monorepo is not a workspace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        _init_repo(self.root, "main")
        for sub in ("backend", "frontend", "docs"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        _write_descriptor(self.root, [
            {"name": "backend", "path": "backend"},
            {"name": "frontend", "path": "frontend"},
            {"name": "docs", "path": "docs"},
        ])

    def test_all_three_subdirectories_are_rejected_and_named(self):
        found = workspace.discover(self.root)
        ok, problems = workspace.members(found["doc"], found["root"])
        self.assertEqual(ok, [])
        rejected_names = {name for name, _reason in problems}
        self.assertEqual(rejected_names, {"backend", "frontend", "docs"})
        for _name, reason in problems:
            self.assertIn(".git", reason)


class BrokenMemberDoesNotSinkTheWorkspace(unittest.TestCase):
    """A workspace with one broken entry must still answer for the others."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        _init_repo(os.path.join(self.root, "api"), "main")
        _write_descriptor(self.root, [
            {"name": "api", "path": "api"},
            {"name": "missing", "path": "does-not-exist"},
            {"name": "escapee", "path": "../../etc"},
        ])

    def test_survivors_and_reasons(self):
        found = workspace.discover(self.root)
        ok, problems = workspace.members(found["doc"], found["root"])
        self.assertEqual([m[0] for m in ok], ["api"])
        reasons = dict(problems)
        self.assertIn("missing", reasons)
        self.assertIn("does not exist", reasons["missing"])
        self.assertIn("escapee", reasons)
        self.assertIn("escapes", reasons["escapee"])


class WorkspaceCli(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, REIN_BIN, "workspace", *args],
                               capture_output=True, text=True, timeout=30)

    def test_no_descriptor_exits_0_with_one_line(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = os.path.realpath(tmp.name)
        proc = self._run(empty)
        self.assertEqual(proc.returncode, 0)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("no workspace descriptor", lines[0])

    def test_prints_one_line_per_member(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        _init_repo(os.path.join(root, "api"), "main")
        _init_repo(os.path.join(root, "web"), "main")
        _write_descriptor(root, [
            {"name": "api", "path": "api"},
            {"name": "web", "path": "web"},
        ])
        proc = self._run(root)
        self.assertEqual(proc.returncode, 0)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        names = {line.split("\t")[0] for line in lines}
        self.assertEqual(names, {"api", "web"})
        for line in lines:
            self.assertEqual(len(line.split("\t")), 4)  # name, branch, head, path


if __name__ == "__main__":
    unittest.main()
