"""Tests for `verify.py` -- a resolved command is an inference until run (T002).

stdlib unittest, throwaway temp directories only. Commands are tiny shell
scripts built INSIDE the temp fixture -- nothing here depends on what is
installed on the machine running the suite (no real pytest/jest/etc).
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import verify  # noqa: E402


class Project:
    """Throwaway project tree, described as {relative path: contents}.

    Any file whose relative path ends in `.sh` is made executable after being
    written -- a convenience for building fake commands.
    """

    def __init__(self, files: dict[str, str]):
        self.files = files

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        for rel, body in self.files.items():
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            if rel.endswith(".sh"):
                os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return self.tmp.name

    def __exit__(self, *exc):
        self.tmp.cleanup()


def _snapshot(root: str) -> dict:
    """path -> (mtime_ns, size) for every file under root, recursively.

    Cheap, reliable "did anything change" fingerprint -- used to prove verify
    never writes to the repo (D2 / acceptance 5), without depending on git.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_mtime_ns, st.st_size)
    return out


def _resolved(root: str, commands: dict[str, str]) -> dict:
    return {"root": root, "commands": commands}


class TestInvocable(unittest.TestCase):
    def test_ok_command_reports_invocable_and_exit_zero(self):
        with Project({"ok.sh": "#!/bin/sh\necho hello\nexit 0\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./ok.sh"}))
        res = report["results"]["test"]
        self.assertTrue(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_OK)
        self.assertEqual(res["exitCode"], 0)
        self.assertIn("hello", res["outputHead"])
        self.assertTrue(report["allInvocable"])


class TestMissingBinary(unittest.TestCase):
    def test_missing_binary_is_not_invocable_not_failed(self):
        with Project({}) as root:
            report = verify.verify_commands(
                _resolved(root, {"lint": "definitely-not-a-real-binary-rein-xyz --version"})
            )
        res = report["results"]["lint"]
        self.assertFalse(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_NOT_INVOCABLE)
        self.assertEqual(res["exitCode"], 127)
        self.assertNotEqual(res["outcome"], verify.OUTCOME_FAILED)
        self.assertFalse(report["allInvocable"])

    def test_not_invocable_is_distinguished_from_ran_and_failed(self):
        """A missing binary and a test suite that runs and fails must never
        collapse into the same outcome -- that conflation is what T002 exists
        to remove.
        """
        with Project({"fail.sh": "#!/bin/sh\necho boom\nexit 1\n"}) as root:
            report = verify.verify_commands(
                _resolved(
                    root,
                    {
                        "test": "./fail.sh",
                        "lint": "definitely-not-a-real-binary-rein-xyz",
                    },
                )
            )
        self.assertEqual(report["results"]["test"]["outcome"], verify.OUTCOME_FAILED)
        self.assertTrue(report["results"]["test"]["invocable"])
        self.assertEqual(report["results"]["lint"]["outcome"], verify.OUTCOME_NOT_INVOCABLE)
        self.assertFalse(report["results"]["lint"]["invocable"])


class TestRunsAndFails(unittest.TestCase):
    def test_nonzero_exit_is_failed_and_still_invocable(self):
        with Project({"fail.sh": "#!/bin/sh\necho oops\nexit 3\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./fail.sh"}))
        res = report["results"]["test"]
        self.assertTrue(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_FAILED)
        self.assertEqual(res["exitCode"], 3)
        self.assertIn("oops", res["outputHead"])
        # A code problem does not flip the CLI-level signal -- only "not
        # invocable" (a setup problem) does.
        self.assertTrue(report["allInvocable"])


class TestTimeout(unittest.TestCase):
    def test_hanging_command_reports_timeout_not_failure_or_pass(self):
        with Project({"hang.sh": "#!/bin/sh\nsleep 30\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./hang.sh"}), timeout=0.3)
        res = report["results"]["test"]
        self.assertEqual(res["outcome"], verify.OUTCOME_TIMEOUT)
        self.assertNotEqual(res["outcome"], verify.OUTCOME_FAILED)
        self.assertNotEqual(res["outcome"], verify.OUTCOME_OK)
        self.assertIsNone(res["exitCode"])
        # It WAS invoked -- it just did not finish -- so this is not the same
        # "not invocable" bucket a missing binary falls into.
        self.assertTrue(res["invocable"])
        self.assertTrue(report["allInvocable"])


class TestOneCheapTarget(unittest.TestCase):
    def test_target_is_substituted_with_a_real_cheap_path_outside_the_repo(self):
        # capture.sh writes the path it was called with to a file OUTSIDE the
        # project tree, so the test can inspect what {target} resolved to
        # without that inspection itself perturbing the "no write" guarantee.
        capture_dir = tempfile.mkdtemp()
        capture_file = os.path.join(capture_dir, "captured.txt")
        script = (
            "#!/bin/sh\n"
            f'printf "%s" "$1" > "{capture_file}"\n'
            'test -f "$1"\n'  # cheap sanity check the substituted path is real
            "exit 0\n"
        )
        try:
            with Project({"one.sh": script}) as root:
                report = verify.verify_commands(_resolved(root, {"testOne": "./one.sh {target}"}))
            res = report["results"]["testOne"]
            self.assertEqual(res["outcome"], verify.OUTCOME_OK)
            with open(capture_file, encoding="utf-8") as fh:
                target_path = fh.read()
            self.assertTrue(target_path, "the {target} placeholder was never substituted")
            # capture.sh's own `test -f "$1"` ran successfully (outcome OK
            # above) -- proof the substituted path existed and was readable
            # AT THE TIME THE COMMAND RAN. verify_commands cleans the scratch
            # file up afterwards, so it is gone by now; that cleanup is the
            # point, not something to assert away.
            self.assertFalse(
                os.path.abspath(target_path).startswith(os.path.abspath(root) + os.sep),
                "testOne's cheap target must never be written inside the repo",
            )
            self.assertFalse(os.path.exists(target_path), "the scratch target should be cleaned up afterwards")
        finally:
            if os.path.exists(capture_file):
                os.remove(capture_file)
            os.rmdir(capture_dir)

    def test_testOne_never_runs_the_whole_suite(self):
        """A command containing {target} must always receive a substituted
        single path -- never fall back to the bare command (which would, for
        a real test runner, run everything).
        """
        with Project({"one.sh": "#!/bin/sh\necho \"args: $#\"\nexit 0\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./one.sh {target}"}))
        res = report["results"]["testOne"]
        self.assertEqual(res["outputHead"], ["args: 1"])


class TestNoWriteGuarantee(unittest.TestCase):
    def test_working_tree_is_unchanged_after_a_run(self):
        with Project(
            {
                "ok.sh": "#!/bin/sh\necho fine\nexit 0\n",
                "fail.sh": "#!/bin/sh\nexit 1\n",
                "one.sh": "#!/bin/sh\nexit 0\n",
            }
        ) as root:
            before = _snapshot(root)
            report = verify.verify_commands(
                _resolved(
                    root,
                    {
                        "test": "./ok.sh",
                        "lint": "./fail.sh",
                        "testOne": "./one.sh {target}",
                    },
                )
            )
            after = _snapshot(root)
            self.assertEqual(before, after)
            self.assertEqual(set(report["results"]), {"test", "lint", "testOne"})

    def test_write_state_persists_outside_the_repo_only(self):
        with Project({"ok.sh": "#!/bin/sh\nexit 0\n"}) as root:
            before = _snapshot(root)
            report = verify.verify_commands(_resolved(root, {"test": "./ok.sh"}))
            state_path = verify.write_state(root, report)
            try:
                self.assertFalse(
                    os.path.abspath(state_path).startswith(os.path.abspath(root) + os.sep),
                    "verify state must never be written inside the repo",
                )
                after = _snapshot(root)
                self.assertEqual(before, after)
                restored = verify.read_state(root)
                self.assertIsNotNone(restored)
                self.assertEqual(restored["results"]["test"]["outcome"], verify.OUTCOME_OK)
            finally:
                if os.path.exists(state_path):
                    os.remove(state_path)


class TestSkippedServe(unittest.TestCase):
    def test_serve_is_skipped_not_run_to_completion(self):
        with Project({}) as root:
            report = verify.verify_commands(_resolved(root, {"serve": "npm run dev"}))
        res = report["results"]["serve"]
        self.assertEqual(res["outcome"], verify.OUTCOME_SKIPPED)
        self.assertTrue(res["invocable"])
        self.assertTrue(report["allInvocable"])


class TestEmptyCommandsSkipped(unittest.TestCase):
    def test_blank_command_slots_are_not_reported(self):
        with Project({}) as root:
            report = verify.verify_commands(_resolved(root, {"build": "", "test": "   "}))
        self.assertEqual(report["results"], {})
        self.assertTrue(report["allInvocable"])


if __name__ == "__main__":
    unittest.main()
