"""The README drifted from the CLI twice, and both times a person hit it.

It told readers to run `rein doctor` in a terminal where `rein` does not
exist, and it printed `claude plugin update rein`, which answers
`Plugin "rein" not found`. Both were reported by someone following it.

Documentation goes stale silently because nothing executes it. These
assertions are the cheapest part that can be mechanised: that the commands
it lists are the commands that exist.
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(REPO, "README.md")
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")
PLUGINS_LIB = os.path.join(REPO, "plugins", "rein", "lib")
if PLUGINS_LIB not in sys.path:
    sys.path.insert(0, PLUGINS_LIB)


def _readme() -> str:
    with open(README, encoding="utf-8") as fh:
        return fh.read()


class TestTheCommandListMatchesTheCli(unittest.TestCase):
    def _sets(self) -> tuple:
        block = re.search(r"### CLI.*?```bash\n(.*?)```", _readme(), re.S)
        self.assertIsNotNone(block, "the README no longer has a CLI block to check")
        listed = set(re.findall(r"^rein ([\w-]+)", block.group(1), re.M))
        usage = subprocess.run([sys.executable, REIN, "--help"], capture_output=True, text=True).stdout
        real = set(re.findall(r"^  rein ([\w-]+)", usage, re.M))
        return listed, real

    def test_no_subcommand_is_missing_from_the_readme(self):
        listed, real = self._sets()
        self.assertEqual(sorted(real - listed), [], "shipped but undocumented")

    def test_the_readme_lists_no_command_that_does_not_exist(self):
        listed, real = self._sets()
        self.assertEqual(sorted(listed - real), [], "documented but not shipped")

    def test_both_sides_were_actually_parsed(self):
        """Guard on the guard: two empty sets compare equal."""
        listed, real = self._sets()
        self.assertGreater(len(real), 10)
        self.assertGreater(len(listed), 10)


class TestTheInstallInstructionsAreTheOnesThatWork(unittest.TestCase):
    def test_the_plugin_update_command_is_qualified(self):
        """`claude plugin update rein` fails with Plugin "rein" not found."""
        readme = _readme()
        self.assertNotIn("claude plugin update rein`", readme)
        self.assertIn("claude plugin update rein@rein-agentic-kit", readme)

    def test_the_terminal_channel_is_documented(self):
        """A plugin cannot put `rein` on a shell PATH; the package can."""
        self.assertIn("uv tool install rein-agentic-kit", _readme())

    def test_the_pypi_name_matches_what_is_packaged(self):
        with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as fh:
            name = re.search(r'^name\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)
        self.assertIn(f"install {name}", _readme())


class TestThePrecedenceLineListsEveryTaskRunner(unittest.TestCase):
    """T001 added mise to _task_runner()'s recognised runners. If a future
    change adds another one and forgets the README, this catches it instead
    of a person finding out the hard way -- same failure mode the module
    docstring warns about, mechanised for this specific line."""

    def test_every_runner_prefix_is_named_in_the_precedence_line(self):
        from detect import _task_runner

        source = inspect.getsource(_task_runner)
        prefixes = set(re.findall(r'return\s+"([a-z]+)"', source))
        self.assertGreater(len(prefixes), 0, "guard on the guard: nothing was parsed")

        line = re.search(r"\*\*Precedence:\*\*.*", _readme())
        self.assertIsNotNone(line, "the README no longer has a Precedence line to check")
        lowered = line.group(0).lower()
        for prefix in prefixes:
            self.assertIn(prefix, lowered, f"{prefix!r} runner is missing from the Precedence line")
