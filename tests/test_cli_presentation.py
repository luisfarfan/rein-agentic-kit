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
            f"  capabilities : {', '.join(caps) or '(none)'}\n"
            f"{optional_lines}"
            "\n"
            f"  ledger : {ledger_path} (0 run(s) recorded)\n"
            "  latest workflow run : (none found)\n"
        )
        self.assertEqual(
            result.stdout, expected,
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
        # Same guard as tests/test_setup.py's TestSetupInstallIsUnattended:
        # `--install` shells out to real network installs (npm/uv). On a
        # host missing a tool it would really try to fetch, running it for
        # real is unsafe -- skip instead of reaching the network.
        state = setup.probe(".")
        if state["missing"]:
            self.skipTest("this host is missing a tool --install would really "
                           "try to fetch (npm/uv) -- unsafe to run for real here")
        self.home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_tmp.cleanup)
        self.home = self.home_tmp.name

    def _env(self) -> dict:
        env = dict(os.environ)
        env["HOME"] = self.home
        return env

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
                env=self._env(),
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
                env=self._env(),
            )
            gitignore_path = os.path.join(root, ".gitignore")
            self.assertTrue(os.path.exists(gitignore_path), "install did not write a .gitignore")
        self.assertNotIn("add to .gitignore", result.stdout)

    def test_activate_alone_is_not_reported_inert_for_serena_in_the_summary(self):
        """The `--activate`-without-`--install` path (the one the loop's
        worktree actually runs, per loop.js) must apply D5 too: the
        `.serena/project.yml` this run just wrote must not still be reported
        as "present but inert: serena" in the SAME run's summary."""
        if not setup._which("serena"):
            self.skipTest("serena not installed on this machine")
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("x")
            result = subprocess.run(
                [sys.executable, REIN_BIN, "setup", root, "--activate"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._env(),
            )
            self.assertTrue(
                os.path.exists(os.path.join(root, ".serena", "project.yml")),
                "activation did not write .serena/project.yml -- " + result.stdout + result.stderr,
            )
        self.assertNotIn("serena", _inert_line(result.stdout))

    def test_install_and_activate_together_is_not_reported_inert_for_serena(self):
        """`setup --install --activate` differs from `--activate` alone only
        by a no-op flag (this branch never actually runs an install -- see
        the comment at the call site) -- so it must reach the identical D5
        conclusion: activation IS work, and the `.serena/project.yml` this
        run just wrote must not still be reported as "present but inert:
        serena" in the SAME run's summary."""
        if not setup._which("serena"):
            self.skipTest("serena not installed on this machine")
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("x")
            result = subprocess.run(
                [sys.executable, REIN_BIN, "setup", root, "--install", "--activate"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._env(),
            )
            self.assertTrue(
                os.path.exists(os.path.join(root, ".serena", "project.yml")),
                "activation did not write .serena/project.yml -- " + result.stdout + result.stderr,
            )
        self.assertNotIn("serena", _inert_line(result.stdout))


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
