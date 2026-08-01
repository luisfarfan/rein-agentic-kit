"""Four files now carry the version. They must never disagree.

The plugin ships through the Claude Code marketplace; the CLI ships through
PyPI. Two channels of the same code is how a project ends up with the CLI on
0.8 and the skills on 0.7, and nobody notices until a user reports something
that "was fixed weeks ago".

`claude plugin tag` already validates two of the four (plugin.json and the
marketplace entry) and refuses to tag when they differ. It knows nothing
about pyproject.toml or the CLI's own VERSION constant, so those two are
pinned here.
"""

from __future__ import annotations

import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts: str) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestEveryVersionStringAgrees(unittest.TestCase):
    def _versions(self) -> dict:
        marketplace = json.loads(_read(".claude-plugin", "marketplace.json"))
        return {
            "pyproject.toml": re.search(r'^version\s*=\s*"([^"]+)"', _read("pyproject.toml"), re.M).group(1),
            "plugin.json": json.loads(_read("plugins", "rein", ".claude-plugin", "plugin.json"))["version"],
            "marketplace.json (plugin entry)": marketplace["plugins"][0]["version"],
            "marketplace.json (metadata)": marketplace["metadata"]["version"],
            "bin/rein VERSION": re.search(r'^VERSION\s*=\s*"([^"]+)"', _read("plugins", "rein", "bin", "rein"), re.M).group(1),
        }

    def test_all_of_them_are_the_same(self):
        versions = self._versions()
        self.assertEqual(
            len(set(versions.values())), 1,
            f"version strings disagree — the two distribution channels will drift: {versions}",
        )

    def test_the_version_is_a_plain_dotted_number(self):
        """`claude plugin tag` builds a git tag from it, and `sort -V`
        orders the install cache by it."""
        for name, value in self._versions().items():
            self.assertRegex(value, r"^\d+\.\d+\.\d+$", f"{name} is not a dotted release number")
