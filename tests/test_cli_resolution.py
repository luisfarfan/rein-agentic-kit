"""Every place that resolves the plugin's own CLI must survive 0.10.0.

Two defects, both found by a person running the thing rather than by a test:

  $ rein doctor
  zsh: command not found: rein

`rein` is on `$PATH` inside Claude Code sessions only -- Claude Code puts each
plugin's `bin/` there at session start -- and the README told people to run it
in a terminal. That is now a slash command instead.

And the resolution chain itself sorted lexicographically:

    ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein | tail -1

With 0.4.0 and 0.6.5 installed that happens to pick 0.6.5. With 0.10.0 it
picks 0.6.5 too, because "0.6.5" > "0.10.0" as strings -- so the day this
project reaches 0.10.0 every skill silently runs a two-year-old CLI.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")


def _files_resolving_the_cli() -> list:
    out = []
    for path in (glob.glob(os.path.join(PLUGIN, "skills", "*", "SKILL.md"))
                 + glob.glob(os.path.join(PLUGIN, "commands", "*.md"))
                 + [os.path.join(PLUGIN, "workflows", "loop.js")]):
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        if "plugins/cache/*/rein/*/bin/rein" in body:
            out.append((os.path.relpath(path, REPO), body))
    return out


class TestVersionResolutionSurvivesTwoDigitMinors(unittest.TestCase):
    def test_every_resolution_sorts_by_version_not_lexically(self):
        offenders = []
        for rel, body in _files_resolving_the_cli():
            for m in re.finditer(r"ls -d ~/\.claude/plugins/cache/\*/rein/\*/bin/rein[^\n`']*", body):
                frag = m.group(0)
                if "tail -1" in frag and "sort -V" not in frag:
                    offenders.append((rel, frag.strip()))
        self.assertEqual(
            offenders, [],
            "lexicographic `tail -1` picks 0.6.5 over 0.10.0 — these will run a stale CLI: "
            f"{offenders}",
        )

    def test_the_sweep_found_the_files_it_is_meant_to_check(self):
        """A guard on the guard: zero files scanned passes vacuously."""
        self.assertGreaterEqual(len(_files_resolving_the_cli()), 6)

    def test_sort_v_actually_orders_these_versions_correctly(self):
        """Asserting the FIX works, not just that the string is present --
        `sort -V` is a GNU/BSD feature and this repo ships to both."""
        proc = subprocess.run(
            ["sort", "-V"], input="0.4.0\n0.6.5\n0.10.0\n",
            capture_output=True, text=True,
        )
        self.assertEqual(proc.stdout.split()[-1], "0.10.0",
                         "sort -V does not order versions on this platform")


class TestTheHumanEntryPointsAreSlashCommands(unittest.TestCase):
    """The two commands a person runs by hand must not require a shell where
    `rein` is on PATH, because in a plain terminal it is not."""

    def test_ping_and_setup_ship_as_commands(self):
        names = {os.path.basename(p)[:-3]
                 for p in glob.glob(os.path.join(PLUGIN, "commands", "*.md"))}
        self.assertIn("ping", names)
        self.assertIn("setup", names)

    def test_the_readme_does_not_tell_people_to_run_rein_in_a_terminal(self):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        quickstart = readme[readme.index("## 🚀 Quickstart"):]
        quickstart = quickstart[:quickstart.index("\n---")]
        for line in quickstart.splitlines():
            stripped = line.strip()
            if stripped.startswith("rein ") or stripped == "rein":
                self.fail(f"Quickstart tells the reader to run `rein` in a shell: {stripped!r}")

    def test_setup_never_provisions_without_asking(self):
        with open(os.path.join(PLUGIN, "commands", "setup.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("--install", body)
        self.assertIn("without asking", body)
