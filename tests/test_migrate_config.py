"""`ship` config block exposure and `rein migrate-config`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugins", "rein", "lib"))

import detect  # noqa: E402
import migrate_config  # noqa: E402
import ship_decide as sd  # noqa: E402

REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")

DEFAULT_SHIP = {
    "claudeWeeklyThreshold": 60,
    "maxConcurrentWorkers": 3,
    "maxRetries": 2,
    "maxAuditRounds": 2,
    "taskTimeoutMinutes": 45,
    "largeChangeFiles": 25,
    "riskyPaths": ["**/auth/**", "**/payments/**", "**/migrations/**"],
}


class TestMigrate(unittest.TestCase):
    def test_migrate_drops_and_adds(self):
        cfg = {"stack": "python", "models": {"impl": "sonnet"}, "limits": {"maxTaskSteps": 8},
               "worktree": {"enabled": True}, "plan": {"source": "tasks-md"}}
        out = migrate_config.migrate(cfg)
        self.assertEqual(out, {"stack": "python", "plan": {"source": "tasks-md"}, "ship": DEFAULT_SHIP})
        self.assertIn("models", cfg)  # pure: the input is untouched

    def test_migrate_preserves_existing_ship(self):
        cfg = {"models": {}, "ship": {"maxRetries": 5}}
        self.assertEqual(migrate_config.migrate(cfg), {"ship": {"maxRetries": 5}})

    def test_migrate_is_idempotent(self):
        once = migrate_config.migrate({"stack": "go", "limits": {}})
        self.assertEqual(migrate_config.migrate(once), once)


class TestResolveExposesShip(unittest.TestCase):
    def _root(self, cfg):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        if cfg is not None:
            with open(os.path.join(d, "flow.config.json"), "w") as fh:
                json.dump(cfg, fh)
        return d

    def test_ship_block_is_exposed_raw(self):
        root = self._root({"ship": {"maxRetries": 7}})
        resolved = detect.resolve(root)
        self.assertEqual(resolved["ship"], {"maxRetries": 7})
        self.assertEqual(sd.ShipConfig.from_flow_config(resolved).max_retries, 7)

    def test_ship_is_empty_when_absent(self):
        self.assertEqual(detect.resolve(self._root({"stack": "python"}))["ship"], {})
        self.assertEqual(detect.resolve(self._root(None))["ship"], {})


class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.path = os.path.join(self.root, "flow.config.json")

    def _run(self, *args):
        return subprocess.run([sys.executable, REIN_BIN, "migrate-config", *args],
                              capture_output=True, text=True)

    def _write(self, cfg):
        with open(self.path, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")

    def _read(self):
        with open(self.path) as fh:
            return fh.read()

    def test_cli_without_write_changes_nothing(self):
        self._write({"stack": "python", "models": {"impl": "sonnet"}})
        before = self._read()
        proc = self._run(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._read(), before)
        self.assertIn("-  \"models\"", proc.stdout)
        self.assertIn("+  \"ship\"", proc.stdout)

    def test_cli_write_applies(self):
        self._write({"stack": "python", "limits": {"maxTaskSteps": 8}})
        proc = self._run(self.root, "--write")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self._read()), {"stack": "python", "ship": DEFAULT_SHIP})

    def test_cli_nothing_to_change(self):
        self._write({"stack": "python", "ship": {"maxRetries": 1}})
        proc = self._run(self.root, "--write")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("nothing to change", proc.stdout)

    def test_cli_missing_or_bad_json_is_1(self):
        self.assertEqual(self._run(self.root).returncode, 1)
        with open(self.path, "w") as fh:
            fh.write("{nope")
        self.assertEqual(self._run(self.root).returncode, 1)

    def test_cli_usage_error_is_2(self):
        self.assertEqual(self._run(self.root, "--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
