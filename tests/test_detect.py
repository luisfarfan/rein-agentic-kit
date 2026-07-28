"""Tests for stack + command resolution.

stdlib unittest on purpose: the kit promises zero runtime dependencies, and a
test suite that needs pytest installed would break that promise for anyone who
clones it.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import detect  # noqa: E402


class Project:
    """Throwaway project tree, described as {relative path: contents}."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        for rel, body in self.files.items():
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        return self.tmp.name

    def __exit__(self, *exc):
        self.tmp.cleanup()


PKG_VITEST = json.dumps(
    {"scripts": {"test": "vitest", "lint": "eslint .", "build": "vite build"}, "devDependencies": {"vitest": "^1", "vite": "^5"}}
)


class TestAutodetect(unittest.TestCase):
    def test_python_uv(self):
        with Project({"pyproject.toml": "[tool.ruff]\n[tool.mypy]\n", "uv.lock": ""}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["packageManager"], "uv")
        self.assertEqual(r["commands"]["test"], "uv run pytest -q")
        self.assertIn("{target}", r["commands"]["testOne"])
        self.assertEqual(r["commands"]["lint"], "uv run ruff check .")
        self.assertEqual(r["commands"]["typecheck"], "uv run mypy .")

    def test_node_pnpm_frontend_subtype(self):
        with Project({"package.json": PKG_VITEST, "pnpm-lock.yaml": ""}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "node")
        self.assertEqual(r["packageManager"], "pnpm")
        self.assertEqual(r["commands"]["test"], "pnpm test")
        self.assertIn("frontend", r["subtypes"], "vite must mark the project as needing rendered verification")
        self.assertIn("vite", r["subtypes"])

    def test_rust_and_go(self):
        with Project({"Cargo.toml": ""}) as root:
            self.assertEqual(detect.resolve(root)["stack"], "rust")
        with Project({"go.mod": "module x"}) as root:
            self.assertEqual(detect.resolve(root)["stack"], "go")

    def test_unknown_stack_degrades_without_raising(self):
        with Project({"README.md": "hi"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "unknown")
        self.assertEqual(r["commands"], {})
        # The caller must be able to see what is missing, not guess.
        self.assertIn("test", r["missingCommands"])

    def test_secondary_stack_is_reported_not_hidden(self):
        with Project({"pyproject.toml": "", "package.json": PKG_VITEST}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "python")
        self.assertIn("node", r["subtypes"])
        self.assertIn("frontend", r["subtypes"])

    def test_infra_files_flag_infra_subtype(self):
        with Project({"package.json": "{}", "serverless.yml": ""}) as root:
            r = detect.resolve(root)
        self.assertIn("infra", r["subtypes"])


class TestPrecedence(unittest.TestCase):
    """config > task runner > autodetect. Each layer must actually win."""

    JUSTFILE = "lint:\n\truff check .\n\ntest:\n\tpytest\n\ntypecheck:\n\tmypy .\n"

    def test_task_runner_beats_autodetect(self):
        with Project({"pyproject.toml": "[tool.ruff]\n", "justfile": self.JUSTFILE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "just")
        self.assertEqual(r["commands"]["test"], "just test")
        self.assertEqual(r["commandSources"]["test"], "just")

    def test_config_beats_task_runner(self):
        cfg = json.dumps({"commands": {"test": "make ci"}})
        with Project({"pyproject.toml": "", "justfile": self.JUSTFILE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "make ci")
        self.assertEqual(r["commandSources"]["test"], "flow.config.json")
        # Slots the config did not override still come from the runner.
        self.assertEqual(r["commandSources"]["lint"], "just")

    def test_makefile_targets_are_read(self):
        with Project({"pyproject.toml": "", "Makefile": "test:\n\tpytest\n\nbuild:\n\techo\n"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["taskRunner"], "make")
        self.assertEqual(r["commands"]["test"], "make test")

    def test_defaults_are_the_measured_routing(self):
        """Model routing defaults are a measured decision, not a preference."""
        with Project({"pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["models"], {"aux": "haiku", "impl": "sonnet", "review": "opus"})
        self.assertEqual(r["limits"]["maxTaskSteps"], 8)
        self.assertEqual(r["tracker"]["kind"], "none")

    def test_config_overrides_models_partially(self):
        cfg = json.dumps({"models": {"review": "sonnet"}})
        with Project({"pyproject.toml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["models"]["review"], "sonnet")
        self.assertEqual(r["models"]["impl"], "sonnet")
        self.assertEqual(r["models"]["aux"], "haiku")

    def test_malformed_config_does_not_break_resolution(self):
        with Project({"pyproject.toml": "", "flow.config.json": "{ not json"}) as root:
            r = detect.resolve(root)
        self.assertFalse(r["configFound"])
        self.assertEqual(r["stack"], "python")


class TestPlanSource(unittest.TestCase):
    def test_openspec_detected(self):
        with Project({"pyproject.toml": "", "openspec/changes/x/tasks.md": "- [ ] T001"}) as root:
            self.assertEqual(detect.resolve(root)["plan"]["source"], "openspec")

    def test_defaults_to_tasks_md(self):
        with Project({"pyproject.toml": ""}) as root:
            self.assertEqual(detect.resolve(root)["plan"]["source"], "tasks-md")


if __name__ == "__main__":
    unittest.main()
