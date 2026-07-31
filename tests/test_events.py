"""Tests for T002 -- 'a skill invocation leaves a trace' (events, not runs).

Library-level tests exercise `plugins/rein/lib/events.py` directly. CLI-level
tests run the real `rein` binary as a subprocess with HOME pointed at a
tmpdir (mirrors tests/test_baseline_cli.py), so the real ~/.claude/rein is
never touched. A separate suite reads the six SHIPPED SKILL.md files so a new
skill cannot ship without recording its own invocation (AC2).
"""

from __future__ import annotations

import glob
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
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")
SKILLS_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "skills")

sys.path.insert(0, LIB_DIR)
import events as ev  # noqa: E402


# ------------------------------------------------------------- library level --


class TestRecordEvent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_appends_a_json_line_with_name_ts_and_project(self):
        path = os.path.join(self.tmp.name, "rein", "events.jsonl")
        ok, error = ev.record_event("run", root=self.tmp.name, events_path=path)
        self.assertTrue(ok, error)
        self.assertEqual(error, "")

        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["name"], "run")
        self.assertEqual(row["project"], os.path.realpath(self.tmp.name))
        # ISO timestamp -- fromisoformat must parse it without raising.
        import datetime

        datetime.datetime.fromisoformat(row["ts"])

    def test_creates_the_directory_when_it_does_not_exist_yet(self):
        path = os.path.join(self.tmp.name, "does", "not", "exist", "events.jsonl")
        self.assertFalse(os.path.exists(os.path.dirname(path)))
        ok, error = ev.record_event("plan", root=self.tmp.name, events_path=path)
        self.assertTrue(ok, error)
        self.assertTrue(os.path.exists(path))

    def test_appends_rather_than_overwrites_across_calls(self):
        path = os.path.join(self.tmp.name, "events.jsonl")
        ev.record_event("run", root=self.tmp.name, events_path=path)
        ev.record_event("review", root=self.tmp.name, events_path=path)
        rows = ev.read_events(path)
        self.assertEqual([r["name"] for r in rows], ["run", "review"])

    def test_unwritable_directory_is_reported_not_raised(self):
        ro_dir = os.path.join(self.tmp.name, "ro")
        os.makedirs(ro_dir)
        os.chmod(ro_dir, stat.S_IREAD | stat.S_IEXEC)
        self.addCleanup(lambda: os.chmod(ro_dir, stat.S_IRWXU))

        path = os.path.join(ro_dir, "subdir", "events.jsonl")
        ok, error = ev.record_event("run", root=self.tmp.name, events_path=path)
        self.assertFalse(ok)
        self.assertTrue(error)


class TestReadEventsSkipsCorruptLines(unittest.TestCase):
    def test_a_corrupt_or_truncated_line_is_skipped_not_raised(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "events.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": "run", "ts": "2026-01-01T00:00:00+00:00", "project": "/p"}) + "\n")
            fh.write("{not json at all\n")
            fh.write('{"name": "plan", "ts": "2026-01-01T00:00:01+00:00"' )  # truncated, no closing brace/newline
            fh.write("\n")
            fh.write(json.dumps({"name": "review", "ts": "2026-01-01T00:00:02+00:00", "project": "/p"}) + "\n")

        rows = ev.read_events(path)
        self.assertEqual([r["name"] for r in rows], ["run", "review"])

    def test_missing_file_returns_empty_list(self):
        self.assertEqual(ev.read_events("/nonexistent/path/events.jsonl"), [])


class TestCountByProject(unittest.TestCase):
    def test_counts_grouped_by_project(self):
        rows = [
            {"name": "run", "project": "/a"},
            {"name": "run", "project": "/a"},
            {"name": "plan", "project": "/b"},
        ]
        self.assertEqual(ev.count_by_project(rows), {"/a": 2, "/b": 1})


# ------------------------------------------------------------------- CLI level --


class EventCliFixture(unittest.TestCase):
    """A tmp HOME with its own ~/.claude/rein -- never the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.rein_dir = os.path.join(self.home, ".claude", "rein")
        self.events_path = os.path.join(self.rein_dir, "events.jsonl")
        self.ledger_path = os.path.join(self.rein_dir, "runs.jsonl")

    def _run(self, *args: str, home: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = home if home is not None else self.home
        return subprocess.run(
            [sys.executable, REIN_BIN, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )


class TestEventCliNeverFails(EventCliFixture):
    def test_exits_0_when_the_directory_does_not_exist_yet(self):
        self.assertFalse(os.path.exists(self.rein_dir))
        result = self._run("event", "demo-skill")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(self.events_path))
        with open(self.events_path, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["name"], "demo-skill")
        self.assertIn("ts", row)
        self.assertIn("project", row)

    def test_unwritable_home_still_exits_0_and_reports_on_stderr(self):
        os.makedirs(os.path.join(self.home, ".claude"), exist_ok=True)
        claude_dir = os.path.join(self.home, ".claude")
        os.chmod(claude_dir, stat.S_IREAD | stat.S_IEXEC)
        self.addCleanup(lambda: os.chmod(claude_dir, stat.S_IRWXU))

        result = self._run("event", "demo-skill")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stderr.strip(), "an unwritable directory must be reported on stderr")
        self.assertFalse(os.path.exists(self.events_path))


class TestLedgerCountsEventsSeparately(EventCliFixture):
    def _write_run_row(self) -> dict:
        row = {
            "wf_id": "wf_1",
            "ts": "2026-01-01T00:00:00Z",
            "project": "proj-cli",
            "turns": 10,
            "turns_per_agent": 5.0,
            "ctx_max": 1000,
            "opus_share": 10.0,
            "total": 100,
            "opus_tokens": 10,
            "agents": [],
        }
        os.makedirs(self.rein_dir, exist_ok=True)
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    def test_ledger_shows_an_invocation_count_per_project_without_a_run(self):
        self._write_run_row()
        self._run("event", "run", "--root", "/some/project")
        self._run("event", "run", "--root", "/some/project")
        self._run("event", "plan", "--root", "/other/project")

        result = self._run("ledger")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(os.path.realpath("/some/project"), result.stdout)
        self.assertIn("2 invocation(s)", result.stdout)
        self.assertIn(os.path.realpath("/other/project"), result.stdout)
        self.assertIn("1 invocation(s)", result.stdout)

        # D3: an event never enters a run total -- the run section still
        # reports exactly the one recorded run.
        self.assertIn("1 run(s)", result.stdout)

    def test_existing_per_run_fields_are_byte_identical_before_and_after_events(self):
        self._write_run_row()
        with open(self.ledger_path, "rb") as fh:
            before = fh.read()

        self._run("event", "run", "--root", "/some/project")
        self._run("event", "plan", "--root", "/other/project")
        self._run("ledger")  # also exercised through the read path

        with open(self.ledger_path, "rb") as fh:
            after = fh.read()

        self.assertEqual(before, after, "recording/reading events must never touch runs.jsonl")


# ------------------------------------------------------------- shipped skills --


SKILL_FILES = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md")))


class TestShippedSkillsRecordOwnInvocation(unittest.TestCase):
    def test_at_least_the_six_known_skills_are_present(self):
        # A guard against a silently empty glob (e.g. a moved skills dir)
        # making every test below vacuously pass.
        names = {os.path.basename(os.path.dirname(f)) for f in SKILL_FILES}
        expected = {"loop", "run", "run-auto", "plan", "review", "role"}
        self.assertTrue(expected.issubset(names), f"missing skill dirs: {expected - names}")

    def test_every_shipped_skill_records_its_own_invocation_as_its_first_step(self):
        failures = []
        for path in SKILL_FILES:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()

            name_match = re.search(r'^name:\s*(\S+)\s*$', text, re.MULTILINE)
            if not name_match:
                failures.append(f"{path}: no `name:` in front matter")
                continue
            name = name_match.group(1).strip('"\'')

            steps_match = re.search(r'^## (?:Steps|Loop)\s*$', text, re.MULTILINE)
            if not steps_match:
                failures.append(f"{path}: no '## Steps' / '## Loop' section")
                continue

            rest = text[steps_match.end():]
            # Step 1's full body: everything from '1.' up to the next
            # numbered item ('2.') -- the recording command may sit on a
            # later line (e.g. inside a fenced code block), not the same
            # line as the '1.' marker.
            first_step_match = re.search(r'^1\.\s+(.*?)(?=^\d+\.\s|\Z)', rest, re.MULTILINE | re.DOTALL)
            if not first_step_match:
                failures.append(f"{path}: no numbered step 1 under Steps/Loop")
                continue
            first_step = first_step_match.group(1)

            if "rein event" not in first_step and f'event {name}' not in first_step:
                failures.append(
                    f"{path}: first step does not record its own invocation "
                    f"(expected 'event {name}'): {first_step!r}"
                )

        self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
