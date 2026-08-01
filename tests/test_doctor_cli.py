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
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")
REIN_PLUGIN_DIR = os.path.join(REPO_ROOT, "plugins", "rein")


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


class DoctorStalenessFixture(DoctorCliFixture):
    """Puts a REAL copy of the rein plugin at a fake
    .../cache/<marketplace>/<name>/<version> path, because
    version_staleness's loader derives the marketplace/plugin identity from
    PLUGIN_ROOT's own path shape -- REIN_BIN's real location (a repo
    checkout, not a marketplace cache) would otherwise always report
    'unknown' regardless of the fixtures below.
    """

    MARKETPLACE = "fixture-marketplace"
    PLUGIN_NAME = "rein"

    def setUp(self):
        super().setUp()
        self.plugin_version_dir = os.path.join(
            self.home, ".claude", "plugins", "cache", self.MARKETPLACE, self.PLUGIN_NAME, "9.9.9"
        )
        shutil.copytree(os.path.join(REIN_PLUGIN_DIR, "bin"), os.path.join(self.plugin_version_dir, "bin"))
        shutil.copytree(os.path.join(REIN_PLUGIN_DIR, "lib"), os.path.join(self.plugin_version_dir, "lib"))
        self.fixture_bin = os.path.join(self.plugin_version_dir, "bin", "rein")

    def _write_installed(self, version: str) -> None:
        path = os.path.join(self.home, ".claude", "plugins", "installed_plugins.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = {
            "version": 2,
            "plugins": {f"{self.PLUGIN_NAME}@{self.MARKETPLACE}": [{"scope": "user", "version": version}]},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def _write_marketplace(self, version: str) -> None:
        path = os.path.join(
            self.home, ".claude", "plugins", "marketplaces", self.MARKETPLACE, ".claude-plugin", "marketplace.json"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = {"name": self.MARKETPLACE, "plugins": [{"name": self.PLUGIN_NAME, "version": version}]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def _snapshot_home(self) -> dict:
        snap = {}
        for dirpath, _dirnames, filenames in os.walk(self.home):
            for name in filenames:
                p = os.path.join(dirpath, name)
                with open(p, "rb") as fh:
                    snap[p] = fh.read()
        return snap

    def _run_fixture_doctor(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run(
            [sys.executable, self.fixture_bin, "doctor", self.root, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )


class TestStaleVerdictPrintsBothCommandsInOrder(DoctorStalenessFixture):
    def test_stale_prints_marketplace_refresh_before_plugin_update(self):
        self._write_installed("0.4.0")
        self._write_marketplace("0.6.3")

        doctor = self._run_fixture_doctor()
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("version : stale", doctor.stdout)

        marketplace_idx = doctor.stdout.index(f"claude plugin marketplace update {self.MARKETPLACE}")
        update_idx = doctor.stdout.index(f"claude plugin update {self.PLUGIN_NAME}")
        self.assertLess(marketplace_idx, update_idx, "must refresh the marketplace clone BEFORE updating the plugin")


class TestUpToDateStillPrintsRefreshLine(DoctorStalenessFixture):
    def test_same_version_is_up_to_date_but_refresh_line_still_shown(self):
        self._write_installed("0.4.0")
        self._write_marketplace("0.4.0")

        doctor = self._run_fixture_doctor()
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("version : up-to-date", doctor.stdout)
        # D3: same version proves nothing about the clone itself -- the fix
        # line for refreshing it is printed anyway, with the reason.
        self.assertIn(f"$ claude plugin marketplace update {self.MARKETPLACE}", doctor.stdout)
        self.assertIn("clone", doctor.stdout)
        self.assertNotIn(f"$ claude plugin update {self.PLUGIN_NAME}", doctor.stdout)


class TestStalenessNeverChangesExitCodeOrWritesAnything(DoctorStalenessFixture):
    def test_stale_fixture_keeps_exit_zero_and_touches_nothing(self):
        self._write_installed("0.4.0")
        self._write_marketplace("0.6.3")

        before = self._snapshot_home()
        doctor = self._run_fixture_doctor()
        after = self._snapshot_home()

        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)  # D4: never a failure
        self.assertEqual(before, after)  # D2: report, never act -- nothing written


class TestDoctorJsonGainsStalenessAlongsideExistingKeys(DoctorStalenessFixture):
    def test_json_output_has_pinned_keys_plus_staleness(self):
        self._write_installed("0.4.0")
        self._write_marketplace("0.6.3")

        doctor = self._run_fixture_doctor("--json")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        report = json.loads(doctor.stdout)

        self.assertEqual(
            set(report.keys()),
            {"version", "pluginRoot", "project", "plan", "verifyState", "ledger", "staleness"},
        )
        self.assertEqual(
            set(report["staleness"].keys()),
            {"verdict", "reason", "installedVersion", "availableVersion"},
        )
        self.assertEqual(report["staleness"]["verdict"], "stale")
        self.assertEqual(report["staleness"]["installedVersion"], "0.4.0")
        self.assertEqual(report["staleness"]["availableVersion"], "0.6.3")


if __name__ == "__main__":
    unittest.main()
