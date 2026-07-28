"""Tests for the per-stack verification policy emitted by `detect.resolve()`.

stdlib unittest + temp project trees only, same discipline as test_detect.py:
no dependency on the operator's actual machine/home directory, so results are
reproducible for anyone who clones the project.
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


PKG_VITE = json.dumps(
    {"scripts": {"dev": "vite", "build": "vite build"}, "devDependencies": {"vite": "^5"}}
)


class TestRenderedMode(unittest.TestCase):
    def test_frontend_subtype_is_rendered(self):
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"], "rendered mode must require an observed render")
        self.assertIn("browser", " ".join(r["verifyPolicy"]["requires"]))

    def test_no_tools_named_when_none_reachable(self):
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["tools"], [])

    def test_only_reachable_tools_are_named(self):
        mcp_cfg = json.dumps({"mcpServers": {"claude-in-chrome": {"type": "stdio"}}})
        files = {
            "package.json": json.dumps(
                {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5", "@playwright/test": "^1"}}
            ),
            ".mcp.json": mcp_cfg,
            ".claude/skills/browser-testing-with-devtools/SKILL.md": "# browser testing",
        }
        with Project(files) as root:
            r = detect.resolve(root)
        tools = r["verifyPolicy"]["tools"]
        self.assertEqual(sorted(tools), ["browser-testing", "claude-in-chrome", "playwright"])

    def test_absent_tools_never_named_individually(self):
        # Only playwright is reachable -- the other two must not appear.
        files = {
            "package.json": json.dumps(
                {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5", "playwright": "^1"}}
            ),
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["tools"], ["playwright"])


class TestPlanOnlyMode(unittest.TestCase):
    def test_infra_without_test_command_is_plan_only(self):
        pkg = json.dumps({"scripts": {"build": "webpack"}})
        with Project({"serverless.yml": "", "package.json": pkg}) as root:
            r = detect.resolve(root)
        self.assertIn("infra", r["subtypes"])
        self.assertNotIn("test", r["commands"])
        policy = r["verifyPolicy"]
        self.assertEqual(policy["mode"], "plan-only")
        for op in ("deploy", "apply", "destroy"):
            self.assertIn(op, policy["forbids"])

    def test_infra_with_test_command_is_not_plan_only(self):
        with Project({"serverless.yml": "", "pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        # python autodetect always configures a `test` command.
        self.assertNotEqual(r["verifyPolicy"]["mode"], "plan-only")


class TestUnitMode(unittest.TestCase):
    def test_library_project_is_unit_mode_unchanged(self):
        with Project({"pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        policy = r["verifyPolicy"]
        self.assertEqual(policy["mode"], "unit")
        self.assertEqual(policy["requires"], [])
        self.assertEqual(policy["forbids"], [])
        self.assertEqual(policy["tools"], [])

    def test_cli_go_project_is_unit_mode(self):
        with Project({"go.mod": "module x"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")


class TestConfigOverride(unittest.TestCase):
    def test_config_mode_wins_over_detection(self):
        cfg = json.dumps({"verify": {"mode": "rendered"}})
        with Project({"pyproject.toml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"])

    def test_config_mode_overrides_frontend_detection(self):
        cfg = json.dumps({"verify": {"mode": "unit"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertIn("frontend", r["subtypes"])
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertEqual(r["verifyPolicy"]["requires"], [])

    def test_config_mode_overrides_infra_plan_only(self):
        cfg = json.dumps({"verify": {"mode": "unit"}})
        with Project({"serverless.yml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertEqual(r["verifyPolicy"]["forbids"], [])


class TestShape(unittest.TestCase):
    def test_shape_has_all_keys(self):
        with Project({"pyproject.toml": ""}) as root:
            policy = detect.resolve(root)["verifyPolicy"]
        self.assertEqual(set(policy.keys()), {"mode", "requires", "forbids", "tools"})
        self.assertIsInstance(policy["mode"], str)
        self.assertIsInstance(policy["requires"], list)
        self.assertIsInstance(policy["forbids"], list)
        self.assertIsInstance(policy["tools"], list)


if __name__ == "__main__":
    unittest.main()
