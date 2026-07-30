"""Subprocess-level tests for `rein doctor`'s per-command verification line
(finding 1, round-1 review of T002/T004): a persisted `rein verify` report
must only be trusted when it was recorded against the SAME command `doctor`
is currently printing -- a `flow.config.json` edit, a lockfile-driven
autodetect change, or a newly-set `subproject` key can all rewrite the
resolved command out from under an already-persisted report.

Runs the real CLI as a subprocess with HOME pointed at a tmpdir, so the
real ~/.claude/rein/verify state is never touched (same convention as
tests/test_baseline_cli.py).
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")


class DoctorCliFixture(unittest.TestCase):
    """A tmp HOME (own ~/.claude/rein/verify) and a throwaway project root."""

    def setUp(self):
        self.home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_tmp.cleanup)
        self.home = self.home_tmp.name

        self.root_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.root_tmp.cleanup)
        self.root = self.root_tmp.name

        self._write_script("ok.sh", "#!/bin/sh\necho fine\nexit 0\n")
        self._write_config({"commands": {"test": "./ok.sh"}})

    def _write_script(self, name: str, body: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def _write_config(self, cfg: dict) -> None:
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run(
            [sys.executable, REIN_BIN, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def _doctor_line_for(self, stdout: str, slot: str) -> str:
        for line in stdout.splitlines():
            if re.match(rf"^\s*{re.escape(slot)}\s", line):
                return line
        self.fail(f"no doctor line for slot {slot!r} in:\n{stdout}")


class TestFreshVerificationIsShown(DoctorCliFixture):
    def test_outcome_shown_right_after_verify(self):
        verify = self._run("verify", self.root)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        doctor = self._run("doctor", self.root)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        line = self._doctor_line_for(doctor.stdout, "test")
        self.assertIn("verified: ok (exit=0)", line)
        self.assertNotIn("stale", line)


class TestChangedCommandIsStale(DoctorCliFixture):
    def test_command_changed_underneath_reports_stale_not_the_old_outcome(self):
        verify = self._run("verify", self.root)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        # The resolved command changes underneath the persisted report --
        # here via flow.config.json, the same mechanism a lockfile-driven
        # autodetect change or a new `subproject` key would trigger.
        self._write_script("other.sh", "#!/bin/sh\nexit 0\n")
        self._write_config({"commands": {"test": "./other.sh"}})

        doctor = self._run("doctor", self.root)
        line = self._doctor_line_for(doctor.stdout, "test")
        self.assertIn("verified: stale", line)
        self.assertIn("`rein verify` last ran", line)
        # Must NOT assert the old (now-stale) outcome as if it still applied.
        self.assertNotIn("verified: ok (exit=0)", line)


class TestNeverVerifiedIsUnknown(DoctorCliFixture):
    def test_no_prior_verify_reports_unknown(self):
        doctor = self._run("doctor", self.root)
        line = self._doctor_line_for(doctor.stdout, "test")
        self.assertIn("verified: unknown -- run `rein verify`", line)


class TestTestOneStaysCurrentDespiteSyntheticTargetPath(DoctorCliFixture):
    """The regression this fix specifically has to avoid: comparing against
    the EXECUTED command (which embeds a fresh temp path every run) would
    make every `testOne` verification report stale forever, even when
    nothing about the configured command changed.
    """

    def test_testOne_with_target_placeholder_is_not_perpetually_stale(self):
        self._write_config({"commands": {"test": "./ok.sh", "testOne": "./ok.sh {target}"}})
        verify = self._run("verify", self.root)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        doctor = self._run("doctor", self.root)
        line = self._doctor_line_for(doctor.stdout, "testOne")
        self.assertIn("verified: ok (exit=0)", line)
        self.assertNotIn("stale", line)


if __name__ == "__main__":
    unittest.main()
