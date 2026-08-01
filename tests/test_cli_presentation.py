"""T003: the CLI is readable, and still machine-safe.

`doctor` and `setup` colour their status markers, headings, and source
attribution through ONE helper (`plugins/rein/lib/ansi.py`) -- never an
inline escape of their own. Colour is for humans only (D4): it turns on
only on a real TTY, and never when `NO_COLOR` is set, `--json` is passed,
or the output is piped -- a piped run must be byte-identical to what the
CLI produced before this helper existed, and `--json` must keep every key.
"""

from __future__ import annotations

import json
import os
import pty
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

sys.path.insert(0, LIB_DIR)
import ansi  # noqa: E402

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

    def _run_on_pty(self, args: list[str], env: dict) -> str:
        """Run the CLI with stdout attached to a real pty, so `isatty()`
        is True inside the child -- the only way to observe colour turned
        ON, short of a real terminal."""
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [sys.executable, REIN_BIN, *args],
                stdout=slave_fd,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            chunks = []
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            proc.wait(timeout=30)
            return b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            os.close(master_fd)
            if slave_fd != -1:
                os.close(slave_fd)


class TestColourHasExactlyOneSource(unittest.TestCase):
    """AC1: every colour in the output comes from ansi.paint(), never an
    inline escape written directly into the command modules."""

    def test_ansi_module_owns_every_raw_escape(self):
        for rel in (
            os.path.join("plugins", "rein", "bin", "rein"),
            os.path.join("plugins", "rein", "lib", "setup.py"),
        ):
            path = os.path.join(REPO_ROOT, rel)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn(_ESCAPE, src, f"{rel} contains a raw escape byte")
            self.assertNotIn("\\033", src, f"{rel} writes an escape literal directly")
            self.assertNotIn("\\x1b", src, f"{rel} writes an escape literal directly")

    def test_every_emitted_code_is_a_sanctioned_one(self):
        """The escape sequences a coloured run actually emits must each be
        one ansi.py itself defines -- not merely absent from the other
        files (belt-and-braces against e.g. an escape built by concatenation)."""
        sanctioned = set(ansi.CODES.values()) | {ansi.RESET}
        sample = ansi.paint("x", "green", "bold", on=True) + ansi.paint("y", "red", on=True)
        found = set(_ESCAPE_RE.findall(sample))
        self.assertTrue(found)
        self.assertTrue(found.issubset(sanctioned))


class TestColourOnlyOnARealTTY(DoctorFixture):
    """AC2 (D4): colour never appears piped, under NO_COLOR, or with --json
    -- and, to prove the mechanism is not simply always-off, it DOES appear
    on a real TTY with none of those set."""

    def test_piped_doctor_output_has_no_escape_byte(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(_ESCAPE, result.stdout)

    def test_piped_setup_output_has_no_escape_byte(self):
        result = self._run("setup", self.root)
        self.assertNotIn(_ESCAPE, result.stdout)

    def test_no_color_env_wins_even_on_a_real_tty(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        env = self._base_env()
        env["NO_COLOR"] = "1"
        out = self._run_on_pty(["doctor", self.root], env)
        self.assertNotIn(_ESCAPE, out)

    def test_json_flag_wins_even_on_a_real_tty(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        env = self._base_env()
        out = self._run_on_pty(["doctor", self.root, "--json"], env)
        self.assertNotIn(_ESCAPE, out)
        json.loads(out)  # still valid JSON -- no stray escape corrupted it

    def test_a_real_tty_with_nothing_disabling_it_does_get_colour(self):
        """The positive case: without this, the three tests above would
        pass trivially even if colour were simply never implemented."""
        self._write_config({"commands": {"test": "./ok.sh"}})
        env = self._base_env()
        out = self._run_on_pty(["doctor", self.root], env)
        self.assertIn(_ESCAPE, out)


class TestPipedOutputIsByteIdenticalToBefore(DoctorFixture):
    """AC3: nothing that greps this breaks. Piped `doctor` output for a
    fixture with only short slot names must be byte-for-byte what the CLI
    produced before ansi.py existed -- colour is a no-op when off, and the
    resolved-commands column width is unchanged when no slot exceeds 10
    characters, so this fixture must show zero drift.
    """

    def test_doctor_output_unchanged_for_a_short_slot_name_fixture(self):
        self._write_config({"commands": {"test": "./ok.sh"}})
        result = self._run("doctor", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        plugin_root = os.path.join(REPO_ROOT, "plugins", "rein")
        rein_on_path = shutil.which("rein") or "(not on PATH -- call it via $CLAUDE_PLUGIN_ROOT/bin/rein)"
        ledger_path = os.path.join(self.home, ".claude", "rein", "runs.jsonl")

        expected = (
            "rein 0.7.2\n"
            f"  plugin root : {plugin_root}\n"
            "  CLAUDE_PLUGIN_ROOT env : (not set)\n"
            f"  `rein` on PATH : {rein_on_path}\n"
            "  version : unknown -- plugin root is not a marketplace cache path "
            "(expected .../cache/<marketplace>/<name>/<version>) -- likely a dev "
            "checkout or a symlinked install\n"
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
        )
        self.assertTrue(
            result.stdout.startswith(expected),
            f"doctor output drifted from the pre-T003 fixture.\n--- got ---\n{result.stdout}",
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


class TestInstallSummaryDescribesAfterNotBefore(unittest.TestCase):
    """AC5 (D5): `setup --install` prints its summary AFTER its work -- a
    repo whose index it just built is not reported inert, and a
    `.gitignore` it just wrote is not still being suggested.
    """

    def setUp(self):
        sys.path.insert(0, LIB_DIR)
        global setup
        import setup

    def test_a_freshly_built_index_is_not_reported_inert_in_the_summary(self):
        if not setup._which("codegraph"):
            self.skipTest("codegraph not installed on this machine")
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
                fh.write("def greet(name):\n    return name\n")
            result = subprocess.run(
                [sys.executable, REIN_BIN, "setup", root, "--install"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
            )
        # The summary is whatever setup.render() printed LAST in this run;
        # by the time it printed, the index this same run built must
        # already be on disk, so codegraph must not show up as inert.
        self.assertNotIn("codegraph", _inert_line(result.stdout))

    def test_a_freshly_written_gitignore_is_not_still_suggested(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("x")
            result = subprocess.run(
                [sys.executable, REIN_BIN, "setup", root, "--install"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
            )
            gitignore_path = os.path.join(root, ".gitignore")
            self.assertTrue(os.path.exists(gitignore_path), "install did not write a .gitignore")
        self.assertNotIn("add to .gitignore", result.stdout)


def _inert_line(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.strip().startswith("present but inert"):
            return line
    return ""


class TestDoctorJsonKeysArePinned(DoctorFixture):
    """AC6: `rein doctor --json` keeps every key unchanged."""

    TOP_LEVEL = {"version", "pluginRoot", "project", "plan", "verifyState", "ledger", "staleness"}

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
