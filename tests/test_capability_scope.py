"""Which capabilities travel between directories, and which do not.

`capabilities` is one flat list holding two different kinds of fact: PATH
probes, true anywhere on this machine, and markers that are only true for the
directory that was passed.

loop.js hit this for real. It detected capabilities in the base repo and then
had its agents work in a WORKTREE, so every graph command answered "graph file
not found" -- the capability was true, about the wrong directory. Its fix was
`decideGraphAvailable` / `decideSerenaAvailable`, two functions whose whole job
was refusing to trust that list.

The rule underneath needs no function: re-detect where the tools will run. What
it needs is for the distinction to be a FACT nobody has to remember, which is
what the sync test below enforces -- it derives the answer from behaviour, not
from reading the source, so a new root-checked capability that nobody adds to
the constant fails here.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import detect  # noqa: E402


def _with_every_marker(root: str) -> None:
    """Every directory-scoped marker `_capabilities` looks for."""
    os.makedirs(os.path.join(root, ".serena"), exist_ok=True)
    os.makedirs(os.path.join(root, "graphify-out"), exist_ok=True)
    os.makedirs(os.path.join(root, ".codegraph"), exist_ok=True)
    with open(os.path.join(root, ".codegraph", "codegraph.db"), "w", encoding="utf-8") as fh:
        fh.write("")


class CapabilityScopeTestCase(unittest.TestCase):

    def test_the_constant_matches_what_actually_depends_on_the_directory(self):
        """Derived from behaviour, not from parsing the source: whatever appears
        for a directory with the markers and not for a bare one IS directory
        scoped, and must be declared as such."""
        with tempfile.TemporaryDirectory() as bare, tempfile.TemporaryDirectory() as marked:
            _with_every_marker(marked)
            only_when_marked = (set(detect.resolve(marked)["capabilities"])
                                - set(detect.resolve(bare)["capabilities"]))

        self.assertEqual(
            only_when_marked, set(detect.DIRECTORY_SCOPED_CAPABILITIES),
            "a capability that depends on the directory must be listed in "
            "DIRECTORY_SCOPED_CAPABILITIES -- otherwise a caller carrying this "
            "list into a worktree will believe something untrue about it",
        )

    def test_a_bare_directory_claims_none_of_them(self):
        with tempfile.TemporaryDirectory() as bare:
            caps = detect.resolve(bare)["capabilities"]
        for scoped in detect.DIRECTORY_SCOPED_CAPABILITIES:
            self.assertNotIn(scoped, caps)

    def test_a_marked_directory_claims_all_of_them(self):
        with tempfile.TemporaryDirectory() as marked:
            _with_every_marker(marked)
            caps = detect.resolve(marked)["capabilities"]
        for scoped in detect.DIRECTORY_SCOPED_CAPABILITIES:
            self.assertIn(scoped, caps)

    def test_the_machine_wide_ones_are_identical_in_both(self):
        """The half that DOES travel -- so the split is real in both directions,
        not just an assertion about the scoped half."""
        with tempfile.TemporaryDirectory() as bare, tempfile.TemporaryDirectory() as marked:
            _with_every_marker(marked)
            a = set(detect.resolve(bare)["capabilities"])
            b = set(detect.resolve(marked)["capabilities"])
        self.assertEqual(a - set(detect.DIRECTORY_SCOPED_CAPABILITIES),
                         b - set(detect.DIRECTORY_SCOPED_CAPABILITIES))

    def test_an_interrupted_codegraph_init_does_not_claim_an_index(self):
        """`.codegraph/` without the db is the stale-lock case codegraph ships
        an `unlock` subcommand for -- a bare-directory check would call it
        usable."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".codegraph"))
            self.assertNotIn("codegraph-index", detect.resolve(root)["capabilities"])

    # -- the helper --------------------------------------------------------

    def test_directory_scoped_keeps_only_the_ones_that_do_not_travel(self):
        mixed = ["git", "node", "serena", "serena-project", "codegraph-index"]
        self.assertEqual(detect.directory_scoped(mixed),
                         ["serena-project", "codegraph-index"])

    def test_directory_scoped_tolerates_nothing(self):
        for empty in (None, []):
            with self.subTest(caps=empty):
                self.assertEqual(detect.directory_scoped(empty), [])


if __name__ == "__main__":
    unittest.main()
