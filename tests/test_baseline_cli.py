"""Subprocess-level tests for `rein baseline` -- the exit codes and file paths
T001's acceptance criteria are actually written in terms of.

Library-level tests (tests/test_baseline.py) prove `mark_baseline` raises
ValueError; they do not prove `bin/rein` turns that into exit code 1, or that
`rein baseline` with no subcommand / an unknown subcommand exits 2, or that the
marker actually lands in `~/.claude/rein/baseline.json`. These run the real CLI
as a subprocess with HOME pointed at a tmpdir, so the real ~/.claude/rein is
never touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")


def row(wf_id: str, ts: str) -> dict:
    return {
        "wf_id": wf_id,
        "ts": ts,
        "project": "proj-cli",
        "turns": 10,
        "turns_per_agent": 5.0,
        "ctx_max": 1000,
        "opus_share": 10.0,
        "total": 100,
        "opus_tokens": 10,
    }


class BaselineCliFixture(unittest.TestCase):
    """A tmp HOME with its own ~/.claude/rein -- never the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.rein_dir = os.path.join(self.home, ".claude", "rein")
        os.makedirs(self.rein_dir, exist_ok=True)
        self.ledger_path = os.path.join(self.rein_dir, "runs.jsonl")
        self.baseline_path = os.path.join(self.rein_dir, "baseline.json")

    def _write_ledger(self, rows: list[dict]) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run(
            [sys.executable, REIN_BIN, "baseline", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )


class TestExitCodes(BaselineCliFixture):
    def test_no_subcommand_exits_2(self):
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stdout.lower())

    def test_unknown_subcommand_exits_2(self):
        result = self._run("bogus")
        self.assertEqual(result.returncode, 2)

    def test_mark_unknown_id_exits_1_and_names_the_id(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        result = self._run("mark", "wf_does_not_exist")
        self.assertEqual(result.returncode, 1)
        self.assertIn("wf_does_not_exist", result.stdout)

    def test_mark_exits_0_and_writes_baseline_json_next_to_the_ledger(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        self.assertFalse(os.path.exists(self.baseline_path))

        result = self._run("mark", "wf_1")

        self.assertEqual(result.returncode, 0)
        self.assertIn("wf_1", result.stdout)
        self.assertTrue(os.path.exists(self.baseline_path), "baseline.json must be created next to the ledger")
        with open(self.baseline_path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(stored["wf_id"], "wf_1")

    def test_show_exits_0(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        self._run("mark", "wf_1")
        result = self._run("show")
        self.assertEqual(result.returncode, 0)
        self.assertIn("wf_1", result.stdout)

    def test_clear_exits_0(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        self._run("mark", "wf_1")
        result = self._run("clear")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.baseline_path))


if __name__ == "__main__":
    unittest.main()
