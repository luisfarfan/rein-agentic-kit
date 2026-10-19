"""`rein gate` end to end: the three exit codes, against real temp projects.

The pure decision is covered by test_gate_decision. This covers the wiring,
which is where the mistakes live: the first draft of `cmd_gate` read `--root`
while every sibling command takes root POSITIONALLY, and `_positional` skips
anything starting with `-`. So `rein gate --root=/elsewhere` resolved `.` and
printed a confident verdict about the wrong repository -- silently. A test
that runs the binary against a directory it just built catches that; a test of
the decision function alone never can.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")

EXIT_GREEN, EXIT_RED, EXIT_SETUP = 0, 1, 126


class _Project:
    """A throwaway project whose `test` slot does exactly what we say."""

    def __init__(self, test_command: str, body: str = "self.assertEqual(1, 1)",
                 verify: dict | None = None):
        self.test_command = test_command
        self.body = body
        self.verify = verify

    def __enter__(self) -> str:
        self.tmp = tempfile.mkdtemp()
        root = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(root, "tests"))
        with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write('[project]\nname = "demo"\nversion = "0"\n')
        with open(os.path.join(root, "tests", "test_x.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\n\n\nclass T(unittest.TestCase):\n"
                     f"    def test_it(self):\n        {self.body}\n")
        with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
            cfg = {"stack": "python", "commands": {"test": self.test_command}}
            if self.verify:
                cfg["verify"] = self.verify
            json.dump(cfg, fh)
        self.root = root
        return root

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _gate(root: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, REIN, "gate", root, "--only=test", *extra],
        capture_output=True, text=True,
    )


PASSING = "python3 -m unittest discover -s tests -q"


class GateCliTestCase(unittest.TestCase):

    def test_a_passing_suite_exits_green(self):
        with _Project(PASSING) as root:
            res = _gate(root)
        self.assertEqual(res.returncode, EXIT_GREEN, res.stdout + res.stderr)
        self.assertIn("PASS", res.stdout)

    def test_a_failing_suite_exits_red(self):
        """The case `rein verify` reports as 0, by design -- it asks whether the
        command could be invoked, and a failing suite was invoked just fine."""
        with _Project(PASSING, body="self.assertEqual(1, 2)") as root:
            res = _gate(root)
        self.assertEqual(res.returncode, EXIT_RED, res.stdout + res.stderr)
        self.assertIn("FAIL", res.stdout)

    def test_a_missing_binary_exits_setup_not_red(self):
        with _Project("definitely-not-a-real-binary -q") as root:
            res = _gate(root)
        self.assertEqual(res.returncode, EXIT_SETUP, res.stdout + res.stderr)
        self.assertIn("SETUP", res.stdout)
        self.assertIn("not a verdict on the code", res.stdout)

    def test_nothing_configured_exits_setup_never_green(self):
        with _Project("") as root:
            res = _gate(root)
        self.assertEqual(res.returncode, EXIT_SETUP, res.stdout + res.stderr)
        self.assertIn("nothing is proven", res.stdout)

    def test_the_root_is_positional_like_every_sibling_command(self):
        """The regression this file exists for: a flag form must NOT silently
        resolve the current directory instead."""
        with _Project(PASSING, body="self.assertEqual(1, 2)") as root:
            positional = _gate(root)
            self.assertEqual(positional.returncode, EXIT_RED)
            self.assertIn(os.path.realpath(root), os.path.realpath(positional.stdout.split("\n")[0].split(": ", 1)[1]))

    def test_json_carries_the_same_exit_code_as_the_human_output(self):
        with _Project(PASSING, body="self.assertEqual(1, 2)") as root:
            human = _gate(root)
            as_json = _gate(root, "--json")
        self.assertEqual(human.returncode, as_json.returncode)
        payload = json.loads(as_json.stdout)
        self.assertEqual(payload["decision"]["exit"], as_json.returncode)

    def test_unit_only_skips_the_render_demand_of_a_rendered_repo(self):
        """Per-task gates must not demand a browser render; render evidence
        belongs to `Type: verification` tasks. Without the flag the same repo
        stays at 126 -- nobody has looked at the UI."""
        with _Project(PASSING, verify={"mode": "rendered"}) as root:
            rendered = _gate(root)
            unit_only = _gate(root, "--unit-only")
        self.assertEqual(rendered.returncode, EXIT_SETUP, rendered.stdout + rendered.stderr)
        self.assertEqual(unit_only.returncode, EXIT_GREEN, unit_only.stdout + unit_only.stderr)

    def test_unit_only_does_not_hide_a_red_suite(self):
        with _Project(PASSING, body="self.assertEqual(1, 2)", verify={"mode": "rendered"}) as root:
            res = _gate(root, "--unit-only")
        self.assertEqual(res.returncode, EXIT_RED, res.stdout + res.stderr)

    def test_gate_appears_in_its_own_help(self):
        res = subprocess.run([sys.executable, REIN, "--help"], capture_output=True, text=True)
        self.assertIn("rein gate", res.stdout)


if __name__ == "__main__":
    unittest.main()
