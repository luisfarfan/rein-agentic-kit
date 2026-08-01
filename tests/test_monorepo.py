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
        # Exercises `_find_subprojects` directly, not `resolve()` -- with
        # only ONE real candidate here, `resolve()` now takes the D2
        # single-subproject path (tests/test_single_subproject.py) and
        # never populates `subprojects[]` at all. The bounded-scan behaviour
        # under test is orthogonal to that branch.
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
            found = detect._find_subprojects(root)
        self.assertEqual(set(found), {"apps/api"})


class TestSingleCandidateMonorepo(unittest.TestCase):
    """Superseded by D2 ("One is not many"), landed in T002: a root with
    exactly ONE manifest-bearing directory is a project in a subdirectory,
    not a monorepo, and resolves that directory's commands directly rather
    than demanding a choice between one option. See
    tests/test_single_subproject.py::TestSingleSubprojectResolvesDirectly
    for the current behaviour."""

    def test_single_candidate_resolves_its_own_commands_not_monorepo(self):
        with Project({"README.md": "x\n", "services/only/package.json": PKG_TEST}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "node")
        self.assertNotIn("subprojects", r)
        self.assertEqual(r["commands"]["test"], "cd services/only && npm run test")


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


PKG_FRONTEND = json.dumps(
    {
        "scripts": {"test": "vitest", "dev": "vite"},
        "dependencies": {"vite": "^5"},
        "devDependencies": {"vitest": "^1"},
    }
)


class TestSubprojectDrivesVerifyPolicyAndServe(unittest.TestCase):
    """A chosen sub-project's OWN subtypes must drive the root's verify
    policy / serve block (finding 6) -- the manifest-less monorepo root has
    none of its own, so `rendered` mode must not silently degrade to `unit`
    with no `serve` block for every monorepo whose chosen sub-project is a
    frontend.
    """

    def test_frontend_subproject_gets_rendered_mode_and_a_serve_block(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_FRONTEND,
                "flow.config.json": json.dumps({"subproject": "apps/web"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertIn("frontend", r["subtypes"])
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertIsNotNone(r.get("serve"))
        # Round-2 finding 1: the sub-project path must actually be IN the serve
        # command -- loop.js's serve-probe runs it from the monorepo ROOT (which
        # by definition has no package.json), so a bare "npm run dev" cannot
        # start and every rendered verification fails at boot. `serve.command`
        # is truthy either way, which is exactly why that assertion alone passed
        # review-by-tests before this fix.
        self.assertEqual(r["serve"]["command"], "cd apps/web && npm run dev")

    def test_backend_subproject_keeps_unit_mode_and_no_serve(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_FRONTEND,
                "flow.config.json": json.dumps({"subproject": "apps/api"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertNotIn("frontend", r["subtypes"])
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertIsNone(r.get("serve"))


class TestInvalidSubprojectChoice(unittest.TestCase):
    """A `subproject` naming nothing real must not silently reproduce the
    'zero commands, wrong problem' dead end (finding 2) -- the operator gets
    a warning naming the bad value and the valid choices, and the
    'choose a sub-project' hint stays instead of being suppressed.
    """

    def test_nonexistent_path_stays_a_choice_hint_plus_a_named_warning(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_TEST,
                "flow.config.json": json.dumps({"subproject": "apps/ap"}),  # typo
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "monorepo")
        self.assertEqual(r["commands"], {})
        self.assertTrue(
            any("subproject" in m for m in r["missingCommands"]),
            r["missingCommands"],
        )
        warnings = r.get("verifyWarnings", [])
        self.assertTrue(any("apps/ap" in w for w in warnings), warnings)
        self.assertTrue(any("apps/api" in w and "apps/web" in w for w in warnings), warnings)

    def test_existing_dir_with_no_manifest_is_also_invalid(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_TEST,
                "apps/docs/index.md": "no manifest here\n",
                "flow.config.json": json.dumps({"subproject": "apps/docs"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"], {})
        self.assertTrue(any("subproject" in m for m in r["missingCommands"]), r["missingCommands"])
        warnings = r.get("verifyWarnings", [])
        self.assertTrue(any("apps/docs" in w for w in warnings), warnings)

    def test_trailing_slash_and_leading_dot_slash_are_normalized(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "flow.config.json": json.dumps({"subproject": "./apps/api/"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "cd apps/api && npm run test")
        self.assertNotIn("verifyWarnings", r)


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


class TestSubprojectPathWithSpaceIsQuoted(unittest.TestCase):
    """Round-2 finding 3: `f"cd {normalized_choice} && ..."` interpolated an
    operator-supplied path into a shell command unquoted. A sub-project
    directory containing a space passes the isdir + manifest check and must
    still produce a correctly-quoted, single-token `cd` argument -- both for
    the ordinary command slots and for `serve` (finding 1's fix reuses the
    same construction).
    """

    def test_test_command_quotes_a_space_in_the_path(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/my web/package.json": PKG_TEST,
                "flow.config.json": json.dumps({"subproject": "apps/my web"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "cd 'apps/my web' && npm run test")

    def test_serve_command_quotes_a_space_in_the_path(self):
        with Project(
            {
                "README.md": "monorepo\n",
                "apps/my web/package.json": PKG_FRONTEND,
                "flow.config.json": json.dumps({"subproject": "apps/my web"}),
            }
        ) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "cd 'apps/my web' && npm run dev")


if __name__ == "__main__":
    unittest.main()
