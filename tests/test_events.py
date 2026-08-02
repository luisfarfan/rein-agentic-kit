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

    def test_invalid_utf8_bytes_are_skipped_not_raised(self):
        # Reviewer finding #1 (round 1): only json.JSONDecodeError was caught,
        # and the decode itself happens in `for line in fh`, outside any
        # guard -- a line truncated mid multi-byte character (the
        # concurrent/killed-append case this module's docstring names) raised
        # UnicodeDecodeError straight out of the iterator, taking `rein
        # ledger` down with it. Reproduce the exact byte sequence: one good
        # line, then a line with a truncated UTF-8 continuation byte.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "events.jsonl")
        with open(path, "wb") as fh:
            fh.write(json.dumps({"name": "run", "ts": "2026-01-01T00:00:00+00:00", "project": "/p"}).encode("utf-8") + b"\n")
            fh.write(b'{"name": "pl\xc3\n')  # truncated mid multi-byte char -- invalid UTF-8
            fh.write(json.dumps({"name": "review", "ts": "2026-01-01T00:00:02+00:00", "project": "/p"}).encode("utf-8") + b"\n")

        rows = ev.read_events(path)  # must not raise
        self.assertEqual([r["name"] for r in rows], ["run", "review"])

    def test_unreadable_file_degrades_to_no_events(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "events.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": "run", "ts": "2026-01-01T00:00:00+00:00", "project": "/p"}) + "\n")
        os.chmod(path, 0)
        self.addCleanup(lambda: os.chmod(path, stat.S_IREAD | stat.S_IWRITE))

        if os.geteuid() == 0:
            self.skipTest("root ignores file permission bits")
        self.assertEqual(ev.read_events(path), [])


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

    def test_json_shape_is_pinned_to_runs_and_events_by_project(self):
        # Reviewer finding #4 (round 1): `rein ledger --json` moved from a
        # top-level array of run rows to an object -- a silent breaking
        # change to the only machine-readable output of the command. Pin the
        # new shape so it cannot drift again unnoticed: top-level object with
        # exactly "runs" (the unchanged array of run rows) and
        # "events_by_project" (D3 -- counted separately, never folded in).
        run_row = self._write_run_row()
        self._run("event", "run", "--root", "/some/project")

        result = self._run("ledger", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)

        self.assertEqual(set(payload.keys()), {"runs", "events_by_project"})
        self.assertIsInstance(payload["runs"], list)
        self.assertEqual(payload["runs"], [run_row])
        self.assertIsInstance(payload["events_by_project"], dict)
        self.assertEqual(payload["events_by_project"], {os.path.realpath("/some/project"): 1})

    def test_a_broken_events_file_does_not_take_down_the_runs_report(self):
        # Finding #4's second half: guard the events read so an events-side
        # failure can never take the runs report down with it.
        self._write_run_row()
        os.makedirs(self.rein_dir, exist_ok=True)
        with open(self.events_path, "wb") as fh:
            fh.write(b'{"name": "run", "ts": "2026-01-01T00:00:00+00:00", "project": "/p"}\n')
            fh.write(b'{"name": "pl\xc3\n')  # invalid UTF-8, truncated

        result = self._run("ledger", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["runs"]), 1)


# ------------------------------------------------------------- shipped skills --


SKILL_FILES = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md")))


def _skill_recording_failure(path: str) -> str | None:
    """Return a failure message if `path`'s first step does not record the
    skill's OWN name via `event {name}`, else None. Shared by the shipped-skill
    sweep and the negative-fixture test so both exercise the same check."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    name_match = re.search(r'^name:\s*(\S+)\s*$', text, re.MULTILINE)
    if not name_match:
        return f"{path}: no `name:` in front matter"
    name = name_match.group(1).strip('"\'')

    steps_match = re.search(r'^## (?:Steps|Loop)\s*$', text, re.MULTILINE)
    if not steps_match:
        return f"{path}: no '## Steps' / '## Loop' section"

    rest = text[steps_match.end():]
    # Step 1's full body: everything from '1.' up to the next numbered item
    # ('2.') -- the recording command may sit on a later line (e.g. inside a
    # fenced code block), not the same line as the '1.' marker.
    first_step_match = re.search(r'^1\.\s+(.*?)(?=^\d+\.\s|\Z)', rest, re.MULTILINE | re.DOTALL)
    if not first_step_match:
        return f"{path}: no numbered step 1 under Steps/Loop"
    first_step = first_step_match.group(1)

    # Anchored, not a bare substring: this repo ships both `run` and
    # `run-auto`, so `event run-auto` would satisfy an unanchored check for
    # the skill named `run` -- the skill would pass while recording under
    # another skill's name, which is exactly the miscount the guard exists
    # to prevent.
    if not re.search(rf'event {re.escape(name)}(?![\w.-])', first_step):
        return (
            f"{path}: first step does not record its own invocation "
            f"(expected 'event {name}'): {first_step!r}"
        )
    # Shell state does NOT persist between tool calls, so a step that uses
    # `$R` without resolving it in the same block records nothing: the
    # guarantee would hold in the SKILL.md text and fail in effect.
    if '"$R" event' in first_step and 'command -v rein' not in first_step:
        return (
            f"{path}: step 1 uses $R without resolving it in the same block — "
            f"$R is empty in a fresh tool call, so nothing is recorded"
        )
    return None


class TestShippedSkillsRecordOwnInvocation(unittest.TestCase):
    def test_at_least_the_six_known_skills_are_present(self):
        # A guard against a silently empty glob (e.g. a moved skills dir)
        # making every test below vacuously pass.
        names = {os.path.basename(os.path.dirname(f)) for f in SKILL_FILES}
        expected = {"rein-apply", "rein-step", "rein-steps", "rein-plan", "rein-audit", "rein-role", "rein-discover"}
        self.assertTrue(expected.issubset(names), f"missing skill dirs: {expected - names}")

    def test_every_shipped_skill_records_its_own_invocation_as_its_first_step(self):
        failures = [f for f in (_skill_recording_failure(p) for p in SKILL_FILES) if f]
        self.assertFalse(failures, "\n".join(failures))

    def test_a_skill_recording_under_another_skills_name_fails_the_check(self):
        # Reviewer finding #3 (round 1): the guard OR-ed in a "rein event"
        # alternative, so a skill whose first step records `event loop` passed
        # even when its own front-matter name is `run` -- the OR made either
        # alternative satisfy the check instead of requiring the skill's own
        # name. Only `event {name}` may satisfy the guard.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        skill_dir = os.path.join(tmp.name, "run")
        os.makedirs(skill_dir)
        bad_path = os.path.join(skill_dir, "SKILL.md")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write(
                "---\nname: run\n---\n\n## Steps\n\n"
                "1. Record this invocation:\n\n   ```bash\n   rein event loop\n   ```\n\n"
                "2. Do the rest.\n"
            )

        failure = _skill_recording_failure(bad_path)
        self.assertIsNotNone(failure, "a skill recording under another skill's name must fail the check")
        self.assertIn("expected 'event run'", failure)


if __name__ == "__main__":
    unittest.main()


class TestTheGuardCatchesTheTwoWaysItWasFooled(unittest.TestCase):
    """Both found by review after the guard shipped, both real in this repo."""

    def _skill(self, body: str) -> str:
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "SKILL.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_a_prefix_of_another_skills_name_does_not_satisfy_the_check(self):
        """`run` and `run-auto` both ship here: an unanchored substring check
        let the `run` skill pass while recording as `run-auto`."""
        path = self._skill(
            "---\nname: run\n---\n\n## Steps\n\n"
            '1. Record: `R=$(command -v rein); "$R" event run-auto`.\n\n'
            "2. Next.\n"
        )
        self.assertIsNotNone(_skill_recording_failure(path))

    def test_the_exact_name_still_passes(self):
        path = self._skill(
            "---\nname: run\n---\n\n## Steps\n\n"
            '1. Record: `R=$(command -v rein); "$R" event run`.\n\n'
            "2. Next.\n"
        )
        self.assertIsNone(_skill_recording_failure(path))

    def test_an_unresolved_R_is_rejected(self):
        """Shell state does not persist between tool calls -- verified: a var
        exported in one call reads empty in the next. A step that only says
        `"$R" event x` records nothing at all."""
        path = self._skill(
            "---\nname: plan\n---\n\n## Steps\n\n"
            '1. Record: `"$R" event plan`.\n\n'
            "2. Next.\n"
        )
        err = _skill_recording_failure(path)
        self.assertIsNotNone(err)
        self.assertIn("$R is empty", err)
