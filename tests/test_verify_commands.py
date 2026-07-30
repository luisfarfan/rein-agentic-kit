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


class TestConfiguredVsExecutedCommand(unittest.TestCase):
    """`command` must stay the CONFIGURED (pre-substitution) command -- what
    `doctor` compares against `detect.resolve()`'s current output -- and the
    ACTUAL, substituted invocation must still be recoverable separately
    (finding 1). Comparing against the executed command would never match
    for `testOne` again: its target is a fresh temp path every run.
    """

    def test_command_field_is_configured_not_substituted(self):
        with Project({"one.sh": "#!/bin/sh\nexit 0\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./one.sh {target}"}))
        res = report["results"]["testOne"]
        self.assertEqual(res["command"], "./one.sh {target}")
        self.assertIn("{target}", res["command"])
        self.assertNotIn("{target}", res["executedCommand"])
        self.assertIn("./one.sh ", res["executedCommand"])

    def test_ordinary_command_has_matching_configured_and_executed(self):
        with Project({"ok.sh": "#!/bin/sh\nexit 0\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./ok.sh"}))
        res = report["results"]["test"]
        self.assertEqual(res["command"], "./ok.sh")
        self.assertEqual(res["executedCommand"], "./ok.sh")


class TestOnlyFilter(unittest.TestCase):
    """`only` restricts execution to the given slots -- what the loop's
    Prepare precheck needs (finding 3): it consumes test/lint/typecheck only
    and must not pay for `build` running in the operator's main checkout.
    """

    def test_only_runs_the_named_slots(self):
        with Project(
            {
                "test.sh": "#!/bin/sh\nexit 0\n",
                "lint.sh": "#!/bin/sh\nexit 0\n",
                "build.sh": "#!/bin/sh\nexit 0\n",
            }
        ) as root:
            report = verify.verify_commands(
                _resolved(root, {"test": "./test.sh", "lint": "./lint.sh", "build": "./build.sh"}),
                only={"test", "lint"},
            )
        self.assertEqual(set(report["results"]), {"test", "lint"})

    def test_only_with_no_matching_slots_runs_nothing(self):
        with Project({"build.sh": "#!/bin/sh\nexit 0\n"}) as root:
            report = verify.verify_commands(_resolved(root, {"build": "./build.sh"}), only={"test"})
        self.assertEqual(report["results"], {})


class TestWrapperRunnerSetupFailure(unittest.TestCase):
    """A wrapper runner (poetry/uv/npm/yarn) that invokes CLEANLY but exits
    non-zero because ITS target is missing must be read as not_invocable, not
    failed (finding 4) -- the 126/127 exit-code check alone cannot see this,
    since the wrapper itself was found and ran fine.
    """

    def test_poetry_style_command_not_found_is_not_invocable(self):
        script = "#!/bin/sh\necho 'Command not found: pytest'\nexit 1\n"
        with Project({"poetry.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./poetry.sh"}))
        res = report["results"]["test"]
        self.assertFalse(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_NOT_INVOCABLE)
        self.assertFalse(report["allInvocable"])

    def test_uv_style_failed_to_spawn_is_not_invocable(self):
        script = "#!/bin/sh\necho 'error: Failed to spawn: \\`pytest\\`'\nexit 2\n"
        with Project({"uv.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./uv.sh"}))
        res = report["results"]["test"]
        self.assertFalse(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_NOT_INVOCABLE)

    def test_npm_missing_script_is_not_invocable(self):
        script = "#!/bin/sh\necho 'npm ERR! Missing script: \"test\"'\nexit 1\n"
        with Project({"npm.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./npm.sh"}))
        res = report["results"]["test"]
        self.assertFalse(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_NOT_INVOCABLE)

    def test_ordinary_failure_output_is_not_misclassified(self):
        """A real code failure (assertion output, no wrapper phrasing) must
        stay `failed` -- the heuristic must not over-fire on ordinary text.
        """
        script = "#!/bin/sh\necho 'AssertionError: 1 != 2'\nexit 1\n"
        with Project({"fail.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./fail.sh"}))
        res = report["results"]["test"]
        self.assertTrue(res["invocable"])
        self.assertEqual(res["outcome"], verify.OUTCOME_FAILED)

    def test_wrapper_phrasing_past_the_reported_head_stays_a_code_failure(self):
        """The signal is matched against the head that is actually REPORTED.

        A suite that runs, prints pages of output, shells out to something
        missing along the way, and reports failures is a CODE problem. Reading
        the whole capture made it a SETUP problem -- and stopped the run at
        `decideGatePrecheck` -- while showing the operator an `outputHead`
        containing none of the phrasing the error message cited.
        """
        noise = "\n".join("echo 'ok test_%d'" % i for i in range(verify.OUTPUT_HEAD_LINES * 2))
        script = (
            "#!/bin/sh\n" + noise + "\n"
            "echo 'sh: 1: helper-tool: command not found'\n"
            "echo 'FAILED (failures=1)'\nexit 1\n"
        )
        with Project({"suite.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./suite.sh"}))
        res = report["results"]["test"]
        self.assertEqual(res["outcome"], verify.OUTCOME_FAILED)
        self.assertTrue(res["invocable"])
        self.assertTrue(report["allInvocable"])
        # The cited phrase is absent from the head, which is why it must not
        # be cited: whatever the error says has to be visible in outputHead.
        self.assertNotIn("command not found", "\n".join(res["outputHead"]))

    def test_wrapper_phrasing_inside_the_reported_head_still_fires(self):
        """The narrowing must not disarm the signal for the real case: a
        wrapper that cannot find its target says so and stops immediately."""
        script = (
            "#!/bin/sh\necho 'first line of noise'\n"
            "echo 'Command not found: pytest'\nexit 1\n"
        )
        with Project({"poetry.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"test": "./poetry.sh"}))
        res = report["results"]["test"]
        self.assertEqual(res["outcome"], verify.OUTCOME_NOT_INVOCABLE)
        self.assertIn("command not found", "\n".join(res["outputHead"]).lower())


class TestInconclusiveTestOne(unittest.TestCase):
    """`testOne`'s cheap synthetic `{target}` is real but owned by no actual
    test suite -- a runner reporting "nothing to run" for it must not be
    read as the configured command having failed (finding 5).
    """

    def test_pytest_style_not_found_target_is_inconclusive_not_failed(self):
        script = '#!/bin/sh\necho "ERROR: not found: $1"\nexit 4\n'
        with Project({"pytest.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./pytest.sh {target}"}))
        res = report["results"]["testOne"]
        self.assertEqual(res["outcome"], verify.OUTCOME_INCONCLUSIVE)
        self.assertNotEqual(res["outcome"], verify.OUTCOME_FAILED)
        self.assertTrue(res["invocable"])

    def test_vitest_style_no_test_files_found_is_inconclusive(self):
        script = "#!/bin/sh\necho 'No test files found, exiting with code 1'\nexit 1\n"
        with Project({"vitest.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./vitest.sh {target}"}))
        res = report["results"]["testOne"]
        self.assertEqual(res["outcome"], verify.OUTCOME_INCONCLUSIVE)

    def test_a_real_testOne_failure_still_reports_failed(self):
        """A genuine failure of the CONFIGURED testOne command (not "found
        nothing to run") must still be reported as `failed`.
        """
        script = "#!/bin/sh\necho 'AssertionError: 1 != 2'\nexit 1\n"
        with Project({"one.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./one.sh {target}"}))
        res = report["results"]["testOne"]
        self.assertEqual(res["outcome"], verify.OUTCOME_FAILED)

    def test_inconclusive_does_not_flip_allInvocable(self):
        script = '#!/bin/sh\necho "no tests found"\nexit 1\n'
        with Project({"one.sh": script}) as root:
            report = verify.verify_commands(_resolved(root, {"testOne": "./one.sh {target}"}))
        self.assertTrue(report["allInvocable"])


if __name__ == "__main__":
    unittest.main()
