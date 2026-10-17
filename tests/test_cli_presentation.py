"""T003: the CLI is readable, and still machine-safe.

`doctor` prints plain text: no colour, no escape bytes, whether piped, under
NO_COLOR or with --json. Its resolved-commands table aligns on the longest
slot name, and its --json keys are pinned.
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
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")

_ESCAPE = "\x1b"
_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


class DoctorFixture(unittest.TestCase):
    """Same shape as tests/test_doctor_cli.py's fixture: a tmp HOME (own
    ~/.claude/rein state) and a throwaway project root with one resolved
    command, so `rein doctor` prints a deterministic table."""

    def setUp(self):
        self.home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_tmp.cleanup)
        self.home = self.home_tmp.name

        self.root_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.root_tmp.cleanup)
        self.root = self.root_tmp.name

        self._write_script("ok.sh", "#!/bin/sh\necho fine\nexit 0\n")

    def _write_script(self, name: str, body: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def _write_config(self, cfg: dict) -> None:
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def _base_env(self) -> dict:
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("NO_COLOR", None)
        return env

    def _run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, REIN_BIN, *args],
            capture_output=True,
            text=True,
            env=env if env is not None else self._base_env(),
            timeout=30,
        )

class TestOutputIsPlainText(DoctorFixture):
    """Escape bytes never appear in `doctor` output -- piped, under NO_COLOR
    or with --json."""

    def test_piped_doctor_output_has_no_escape_byte(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(_ESCAPE, result.stdout)

    def test_no_color_env_has_no_escape_byte(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        env = self._base_env()
        env["NO_COLOR"] = "1"
        result = self._run("doctor", self.root, env=env)
        self.assertNotIn(_ESCAPE, result.stdout)


class TestPipedOutputIsByteIdenticalToBefore(DoctorFixture):
    """AC3: nothing that greps this breaks. Piped `doctor` output for a
    fixture with only short slot names has an exact, stable format -- the
    resolved-commands column width is the historical 10 when no slot exceeds
    it.
    """

    def test_doctor_output_unchanged_for_a_short_slot_name_fixture(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        sys.path.insert(0, LIB_DIR)
        import detect as _detect  # noqa: E402 -- deferred so LIB_DIR is on sys.path first

        plugin_root = os.path.join(REPO_ROOT, "plugins", "rein")
        rein_on_path = shutil.which("rein") or "(not on PATH -- call it via $CLAUDE_PLUGIN_ROOT/bin/rein)"
        ledger_path = os.path.join(self.home, ".claude", "rein", "runs.jsonl")
        with open(REIN_BIN, encoding="utf-8") as fh:
            rein_version = re.search(r'^VERSION\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)
        # The capabilities section reports what is actually on THIS machine's
        # PATH (and in this throwaway root) -- hardcoding a literal tool list
        # would pin this host's tooling into a portable test. Deriving it the
        # same way `cmd_doctor` does keeps the assertion an exact-equality
        # check on FORMAT (spacing, ordering, colour absence) rather than on
        # which tools happen to be installed here.
        caps = _detect._capabilities(self.root)
        optional_lines = "".join(
            f"    {name:<10} {'ok' if name in caps else 'absent (optional -- flow degrades, never breaks)'}\n"
            for name in ("graphify", "openspec", "serena", "codegraph")
        )

        expected = (
            f"rein {rein_version}\n"
            f"  plugin root : {plugin_root}\n"
            "  CLAUDE_PLUGIN_ROOT env : (not set)\n"
            f"  `rein` on PATH : {rein_on_path}\n"
            "\n"
            f"  project root : {self.root}\n"
            "  flow.config.json : found\n"
            "  stack : unknown\n"
            "  package manager : -\n"
            "  task runner : -\n"
            "  plan source : tasks-md   tracker: none\n"
            f"  plan        : NOT FOUND at {os.path.join(self.root, 'tasks.md')}\n"
            "\n"
            "  resolved commands:\n"
            "    test       ./ok.sh                                  "
            "[flow.config.json]  verified: unknown -- run `rein verify`\n"
            "    MISSING: testOne, lint, typecheck -- set them in flow.config.json\n"
            "\n"
            "  verify : mode=unit  tools=(none reachable)\n"
            "\n"
            "  models : aux=haiku  impl=sonnet  review=opus\n"
            "  limits : maxTaskSteps=8  maxReviewRounds=3\n"
            "\n"
            f"  capabilities : {', '.join(caps) or '(none)'}\n"
            f"{optional_lines}"
            "\n"
            f"  ledger : {ledger_path} (0 run(s) recorded)\n"
            "  latest workflow run : (none found)\n"
        )
        self.assertEqual(
            result.stdout, expected,
            f"doctor output drifted from the pinned format.\n--- got ---\n{result.stdout}",
        )


class TestResolvedCommandsTableAligns(DoctorFixture):
    """AC4: the table aligns on the longest slot name actually present, so
    a long name like `harnessTest` cannot break the columns."""

    def _row_for(self, stdout: str, slot: str) -> str:
        for line in stdout.splitlines():
            if re.match(rf"^\s*{re.escape(slot)}\s", line):
                return line
        self.fail(f"no row for slot {slot!r} in:\n{stdout}")

    def test_a_long_slot_name_does_not_break_column_alignment(self):
        self._write_config({"commands": {"test": "./ok.sh", "harnessTest": "./ok.sh"}})
        result = self._run("doctor", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        short_row = self._row_for(result.stdout, "test")
        long_row = self._row_for(result.stdout, "harnessTest")
        # Both rows resolve to the same command text, so its start column
        # must be identical -- that IS "the columns did not break".
        self.assertEqual(short_row.index("./ok.sh"), long_row.index("./ok.sh"))
        # And the long name itself is never truncated.
        self.assertIn("harnessTest", long_row)

    def test_short_names_alone_still_use_the_historical_width_of_ten(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root)
        row = self._row_for(result.stdout, "test")
        self.assertEqual(row.index("./ok.sh"), row.index("test") + 11)  # 10-wide slot + 1 space


class TestDoctorJsonKeysArePinned(DoctorFixture):
    """AC6: `rein doctor --json` keeps every key unchanged."""

    TOP_LEVEL = {"version", "pluginRoot", "project", "plan", "verifyState", "ledger"}

    def test_top_level_keys_are_exactly_the_pinned_set(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(set(report.keys()), self.TOP_LEVEL)

    def test_json_output_has_no_escape_byte_regardless_of_tty(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root, "--json")
        self.assertNotIn(_ESCAPE, result.stdout)


if __name__ == "__main__":
    unittest.main()


class TestTheReviewsCoherenceGaps(unittest.TestCase):
    """Three findings the review raised after approving. Each is a place where
    a value this change made LONGER met a width that was chosen when it was
    shorter, or a shape that varies per branch."""

    def test_the_command_column_widens_for_the_commands_it_prints(self):
        """The single-subproject path emits `cd <dir> && <cmd>`, which is
        systematically longer than what the hardcoded 40 was chosen for -- so
        the [source] column broke on exactly the repos this change added."""
        with open(REIN_BIN, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("{cmd:<40}", src, "the command column is fixed again")
        self.assertIn("cmd_width = max([40]", src)

    def test_check_is_documented_as_a_non_slot(self):
        """T001's criterion named `check` and rein has no such slot. Recording
        WHY beats adding a guess: a runner target called `check` is an
        aggregate whose contents the runner does not declare."""
        with open(os.path.join(REPO_ROOT, "plugins", "rein", "lib", "detect.py"), encoding="utf-8") as fh:
            src = fh.read()
        head = src[:src.index("_SLOT_ALIASES = {")]
        self.assertIn("`check` is deliberately NOT here", head)
        aliases = src[src.index("_SLOT_ALIASES = {"):]
        aliases = aliases[:aliases.index("}")]
        self.assertNotIn('"check"', aliases, "check became an alias without the decision changing")


class TestReadPlanHasOneShape(unittest.TestCase):
    """`rein doctor --json` and `rein context` splat this dict verbatim, so a
    key that appears on one branch and not another is a contract a machine
    consumer cannot rely on."""

    def _plan(self):
        import sys
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugins", "rein", "lib"))
        import plan
        return plan

    def test_available_changes_is_present_on_every_branch(self):
        plan = self._plan()
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            shapes = [
                plan.read_plan(REPO_ROOT, ""),          # exists: this repo's tasks.md
                plan.read_plan(empty, ""),         # nothing at all
            ]
        for r in shapes:
            self.assertIn("availableChanges", r, f"missing on {r.get('source')}/{r.get('exists')}")
            self.assertIsInstance(r["availableChanges"], list)
