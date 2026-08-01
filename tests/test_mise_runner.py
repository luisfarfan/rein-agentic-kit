"""Tests for mise as a task runner (T001).

stdlib unittest on purpose, matching tests/test_detect.py -- the kit promises
zero runtime dependencies.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "plugins", "rein", "lib"))
sys.path.insert(0, _HERE)

import detect  # noqa: E402

from test_detect import Project  # noqa: E402


MISE_TOML = (
    "[tools]\n"
    'python = "3.12"\n'
    "\n"
    "[tasks]\n"
    'test = "pytest -q"\n'
    'lint = "ruff check ."\n'
    "\n"
    "[tasks.typecheck]\n"
    'run = "mypy ."\n'
)


class TestMiseRunner(unittest.TestCase):
    def test_mise_tasks_resolve(self):
        with Project({"pyproject.toml": "", "mise.toml": MISE_TOML}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "mise run test")
        self.assertEqual(r["commandSources"]["test"], "mise")
        self.assertEqual(r["commands"]["lint"], "mise run lint")
        self.assertEqual(r["commandSources"]["lint"], "mise")
        # A `[tasks.name]` section header resolves exactly like a flat entry.
        self.assertEqual(r["commands"]["typecheck"], "mise run typecheck")
        self.assertEqual(r["commandSources"]["typecheck"], "mise")

    def test_dotfile_and_nested_config_are_also_detected(self):
        with Project({"pyproject.toml": "", ".mise.toml": MISE_TOML}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "mise run test")

        with Project({"pyproject.toml": "", ".config/mise/config.toml": MISE_TOML}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "mise run test")

    def test_config_beats_mise(self):
        cfg = json.dumps({"commands": {"test": "make ci"}})
        with Project({"pyproject.toml": "", "mise.toml": MISE_TOML, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "make ci")
        self.assertEqual(r["commandSources"]["test"], "flow.config.json")
        # Slots the config did not override still come from mise.
        self.assertEqual(r["commandSources"]["lint"], "mise")

    def test_mise_beats_autodetect(self):
        with Project({"pyproject.toml": "[tool.ruff]\n[tool.mypy]\n", "mise.toml": MISE_TOML}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "mise run test")
        self.assertEqual(r["commandSources"]["test"], "mise")

    def test_mise_with_unmatched_task_falls_back_to_autodetect(self):
        """A mise file that declares no slot-matching task still reports the
        runner, and leaves the actual command slots to autodetection."""
        mise = "[tasks]\ndocs = \"mkdocs build\"\n"
        with Project({"pyproject.toml": "[tool.ruff]\n", "mise.toml": mise}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "pytest -q")
        self.assertEqual(r["commandSources"]["test"], "autodetect")
        self.assertEqual(r["commands"]["lint"], "ruff check .")
        self.assertEqual(r["commandSources"]["lint"], "autodetect")

    def test_malformed_mise_degrades_to_autodetect(self):
        garbage = "this is not { valid toml at all [[[\n"
        with Project({"pyproject.toml": "[tool.ruff]\n", "mise.toml": garbage}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["commands"]["test"], "pytest -q")
        self.assertEqual(r["commandSources"]["test"], "autodetect")

    def test_unreadable_mise_does_not_raise(self):
        with Project({"pyproject.toml": "[tool.ruff]\n"}) as root:
            mise_path = os.path.join(root, "mise.toml")
            os.makedirs(mise_path)  # a directory where a file is expected
            r = detect.resolve(root)  # must not raise
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["commands"]["test"], "pytest -q")


if __name__ == "__main__":
    unittest.main()
