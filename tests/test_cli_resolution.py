"""The README's quickstart must not tell people to run `rein` in a bare shell.

(The skills' own `$R` path-resolution block, and the version-sort test that
guarded it, went away in v2: `rein` is installed on PATH, and
`tests/test_skill_budget.py` asserts no skill resolves the binary by hand.)
"""

from __future__ import annotations

import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")


class TestTheHumanEntryPointsAreSlashCommands(unittest.TestCase):
    """What a person runs by hand must not require a shell where `rein` is
    on PATH, because in a plain terminal it is not."""

    def test_the_readme_does_not_tell_people_to_run_rein_in_a_terminal(self):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        quickstart = readme[readme.index("## 🚀 Quickstart"):]
        quickstart = quickstart[:quickstart.index("\n---")]
        for line in quickstart.splitlines():
            stripped = line.strip()
            if stripped.startswith("rein ") or stripped == "rein":
                self.fail(f"Quickstart tells the reader to run `rein` in a shell: {stripped!r}")
