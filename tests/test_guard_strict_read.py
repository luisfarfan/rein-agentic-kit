"""N3: the guard reads the base it rebuilt strictly.

`resolve_at` handed the rebuilt base to `detect.resolve`, whose readers turn an OSError (or bad
JSON) into "empty": a base mise.toml that was written but could not be READ lost its tasks, the
runner was no longer protected and `rein quick done` said 0. The guard now resolves through
`detect.resolve_strict`: a file that exists but cannot be read or parsed (or a folder that cannot
be listed) raises, and the guard fails closed (126). A missing file is still just missing, and
`detect.resolve` keeps its tolerant behaviour for the rest of Rein."""

from __future__ import annotations

import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import detect  # noqa: E402
import gate_log  # noqa: E402
import guard  # noqa: E402
from test_guard import run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402

MISE = '[tasks.test]\nrun = "true"\n'


def unreadable(suffix, inside="rein-guard-base-"):
    real_open = builtins.open

    def fake(file, mode="r", *a, **kw):
        if inside in str(file) and str(file).endswith(suffix) and "r" in mode and "w" not in mode:
            raise PermissionError(13, "Permission denied")
        return real_open(file, mode, *a, **kw)
    return fake


class Guard(unittest.TestCase):
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
        run_git(self.repo, "rm", "-q", "mise.toml")
        self.resolved = detect.resolve(self.repo)  # the current tree, read before any injection

    def test_a_base_file_that_cannot_be_read_fails_closed(self):
        with mock.patch("builtins.open", unreadable("/mise.toml")):
            problems, broken = guard.run_guard(guard.check, self.repo, self.base, self.resolved)
        self.assertEqual(problems, [])
        self.assertTrue(broken.startswith("guard could not run"), broken)
        self.assertIn("mise.toml", broken)

    def test_a_base_json_that_cannot_be_parsed_fails_closed(self):
        with open(os.path.join(self.repo, "package.json"), "w") as fh:
            fh.write("{not json")
        run_git(self.repo, "add", "package.json")
        run_git(self.repo, "commit", "-q", "-m", "chore: pkg")
        base = run_git(self.repo, "rev-parse", "HEAD").strip()
        problems, broken = guard.run_guard(guard.check, self.repo, base, self.resolved)
        self.assertTrue(broken.startswith("guard could not run"), broken)
        self.assertIn("package.json", broken)

    def test_the_intact_base_still_names_the_violation(self):
        problems, broken = guard.run_guard(guard.check, self.repo, self.base, self.resolved)
        self.assertEqual(broken, "")
        self.assertEqual(len(problems), 1)


class Detect(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        with open(os.path.join(self.root, "mise.toml"), "w") as fh:
            fh.write(MISE)

    def test_resolve_stays_tolerant_for_the_rest_of_rein(self):
        with mock.patch("builtins.open", unreadable("/mise.toml", inside=self.root)):
            self.assertIsInstance(detect.resolve(self.root), dict)

    def test_resolve_strict_raises_on_an_unreadable_file(self):
        with mock.patch("builtins.open", unreadable("/mise.toml", inside=self.root)):
            with self.assertRaises(detect.DetectReadError):
                detect.resolve_strict(self.root)

    def test_resolve_strict_raises_on_unparseable_json(self):
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            fh.write("{bad")
        self.assertIsInstance(detect.resolve(self.root), dict)
        with self.assertRaises(detect.DetectReadError):
            detect.resolve_strict(self.root)

    def test_resolve_strict_raises_on_a_folder_it_cannot_list(self):
        real = os.listdir

        def listdir(path="."):
            if os.path.realpath(path) == self.root:
                raise PermissionError(13, "Permission denied")
            return real(path)
        with mock.patch.object(detect.os, "listdir", listdir):
            self.assertIsInstance(detect.resolve(self.root), dict)
            with self.assertRaises(detect.DetectReadError):
                detect.resolve_strict(self.root)

    def test_missing_files_are_not_errors_in_strict_mode(self):
        self.assertIsInstance(detect.resolve_strict(self.root), dict)

    def test_strict_mode_is_per_thread_and_cleared_after_a_failure(self):
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            fh.write("{bad")
        with self.assertRaises(detect.DetectReadError):
            detect.resolve_strict(self.root)
        self.assertFalse(detect._strict_mode(), "cleared after the failure")
        seen, entered, release = [], threading.Event(), threading.Event()
        real = detect._read_json

        def slow(path):
            if detect._strict_mode():
                entered.set()
                release.wait(5)
            return real(path)

        def strict_in_a_worker():
            try:
                detect.resolve_strict(self.root)
            except detect.DetectReadError:
                pass
        with mock.patch.object(detect, "_read_json", slow):
            t = threading.Thread(target=strict_in_a_worker)
            t.start()
            self.assertTrue(entered.wait(5))
            seen.append(detect._strict_mode())  # this thread, while the worker is strict
            release.set()
            t.join(5)
        self.assertEqual(seen, [False])


class QuickDone(DoneCase):
    def test_an_unreadable_base_file_is_126_and_logged(self):
        self.make_repo(config={"commands": {"test": "mise run test", "lint": "true"}})
        with open(os.path.join(self.ws, "mise.toml"), "w") as fh:
            fh.write(MISE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: runner")
        with open(os.path.join(self.ws, ".rein", "quick.json")) as fh:
            record = json.load(fh)
        record["base_sha"] = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"], capture_output=True,
                                            text=True).stdout.strip()
        with open(os.path.join(self.ws, ".rein", "quick.json"), "w") as fh:
            json.dump(record, fh)
        os.remove(os.path.join(self.ws, "mise.toml"))
        self.write("src/foo.ts", "v2\n")
        with mock.patch("builtins.open", unreadable("/mise.toml")):
            code, _, _ = self.done()
        self.assertEqual(code, 126, self.text())
        self.assertIn("guard could not run", self.text())
        [entry] = gate_log.read(gate_log.root_log_path(self.ws))
        self.assertEqual(entry["exit"], 126)
        self.assertIn("mise.toml", entry["guard"])


if __name__ == "__main__":
    unittest.main()
