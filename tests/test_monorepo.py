"""Tests for monorepo-aware detection (T001, "See into monorepos").

stdlib unittest on purpose -- same reason as test_detect.py: the kit promises
zero runtime dependencies.
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


PKG_TEST = json.dumps({"scripts": {"test": "vitest", "lint": "eslint ."}, "devDependencies": {"vitest": "^1"}})


class TestFirecrawlShaped(unittest.TestCase):
    """apps/api, apps/web, no root manifest -- the shape that motivated this task."""

    def _project(self):
        return Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_TEST,
            }
        )

    def test_stack_is_monorepo_not_unknown(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "monorepo")

    def test_subprojects_reported_with_stack_and_commands(self):
        with self._project() as root:
            r = detect.resolve(root)
        paths = {s["path"]: s for s in r["subprojects"]}
        self.assertEqual(set(paths), {"apps/api", "apps/web"})
        for entry in paths.values():
            self.assertEqual(entry["stack"], "node")
            self.assertEqual(entry["commands"]["test"], "npm run test")

    def test_root_commands_stay_empty(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"], {})
        self.assertEqual(r["commandSources"], {})

    def test_missing_commands_explains_subproject_choice(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertIn("test", r["missingCommands"])
        self.assertTrue(
            any("subproject" in m for m in r["missingCommands"]),
            r["missingCommands"],
        )

    def test_depth_bound_and_skip_dirs_respected(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                # three levels down -- must NOT be found (depth is bounded at 2)
                "apps/deep/nested/pkg/package.json": PKG_TEST,
                # inside a skipped dir -- must NOT be found
                "node_modules/somelib/package.json": PKG_TEST,
            }
        ) as root:
            r = detect.resolve(root)
        paths = {s["path"] for s in r["subprojects"]}
        self.assertEqual(paths, {"apps/api"})


class TestSingleCandidateMonorepo(unittest.TestCase):
    """Even ONE candidate must not be auto-picked (D1)."""

    def test_single_candidate_still_reports_monorepo_with_empty_root_commands(self):
        with Project({"README.md": "x\n", "services/only/package.json": PKG_TEST}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "monorepo")
        self.assertEqual(len(r["subprojects"]), 1)
        self.assertEqual(r["subprojects"][0]["path"], "services/only")
        self.assertEqual(r["commands"], {})


class TestSubprojectOverride(unittest.TestCase):
    def test_subproject_key_resolves_commands_from_repo_root(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_TEST,
                "flow.config.json": json.dumps({"subproject": "apps/api"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "monorepo")
        self.assertEqual(r["commands"]["test"], "cd apps/api && npm run test")
        self.assertEqual(r["commandSources"]["test"], "subproject")
        # missingCommands must NOT carry the "choose a sub-project" note once
        # one has been named.
        self.assertFalse(any("subproject" in m for m in r["missingCommands"]))

    def test_explicit_commands_still_win_over_subproject_choice(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "flow.config.json": json.dumps(
                    {"subproject": "apps/api", "commands": {"test": "cd apps/api && npm run test:ci"}}
                ),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "cd apps/api && npm run test:ci")
        self.assertEqual(r["commandSources"]["test"], "flow.config.json")


class TestPlainRepoUnaffected(unittest.TestCase):
    """A single-project repo must produce byte-identical output to today."""

    def test_no_subprojects_key(self):
        with Project({"pyproject.toml": "[tool.ruff]\n", "uv.lock": ""}) as root:
            r = detect.resolve(root)
        self.assertNotIn("subprojects", r)
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["commands"]["test"], "uv run pytest -q")

    def test_unknown_stack_without_subdirs_stays_unknown(self):
        with Project({"README.md": "hi\n"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "unknown")
        self.assertNotIn("subprojects", r)


if __name__ == "__main__":
    unittest.main()
