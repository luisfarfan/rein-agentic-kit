"""Tests for T002, "Never report a problem that does not exist".

Two real defects, found by installing the kit into two real repositories:

  1. `proxima-engineering` has no manifest of its own and exactly ONE
     manifest-bearing directory (`harness/`) -- reporting "choose a
     sub-project" over a list of one sends the operator to a problem that
     does not exist (D2). It is a project in a subdirectory, not a monorepo.
  2. `make-montages`, an openspec project, was asked for its plan with no
     change named -- `plan.path` joined to `openspec/changes/tasks.md`, a
     location that can never exist, instead of naming the six real changes
     sitting right there (D3).

Two or more manifest-bearing directories must keep TODAY's behaviour
exactly: that regression is covered here side by side with the fix so one
does not come back while fixing the other.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import detect  # noqa: E402
import plan  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PROXIMA_SHAPE = os.path.join(FIXTURES, "proxima_engineering_shape")


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


# ------------------------------------------------------- one candidate -----


class TestSingleSubprojectResolvesDirectly(unittest.TestCase):
    """D2: a root with no manifest and exactly ONE manifest-bearing
    directory is a project in a subdirectory, not a monorepo."""

    def _project(self):
        return Project({"README.md": "x\n", "services/only/package.json": PKG_TEST})

    def test_stack_is_the_subprojects_own_not_monorepo(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "node")
        self.assertNotEqual(r["stack"], "monorepo")

    def test_commands_resolve_carrying_the_path_from_the_root(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"]["test"], "cd services/only && npm run test")
        self.assertEqual(r["commands"]["lint"], "cd services/only && npm run lint")

    def test_command_sources_name_the_subproject(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["commandSources"]["test"], "subproject")

    def test_no_choose_a_subproject_hint(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertFalse(any("subproject" in m for m in r["missingCommands"]), r["missingCommands"])

    def test_proxima_engineering_real_shape_resolves_harnesss_commands(self):
        """Acceptance 5: `rein detect` on the checked-in fixture reproducing
        proxima-engineering's real shape (governance content + mise.toml at
        the root, exactly one manifest at `harness/pyproject.toml`)."""
        r = detect.resolve(PROXIMA_SHAPE)
        self.assertEqual(r["stack"], "python")
        self.assertEqual(r["taskRunner"], "mise")
        self.assertEqual(r["commands"]["test"], "cd harness && uv run pytest -q")
        self.assertEqual(r["commands"]["lint"], "cd harness && uv run ruff check .")
        self.assertEqual(r["commands"]["typecheck"], "cd harness && uv run mypy .")
        self.assertEqual(r["commandSources"]["test"], "subproject")
        self.assertFalse(any("subproject" in m for m in r["missingCommands"]), r["missingCommands"])


# ------------------------------------------------- two or more candidates --


class TestTwoOrMoreSubprojectsUnchanged(unittest.TestCase):
    """Today's monorepo behaviour must stay byte-identical once there is a
    genuine choice -- the proxima shape (one) and this shape (two) side by
    side so the fix for one cannot silently regress the other."""

    def _project(self):
        return Project(
            {
                "README.md": "monorepo\n",
                "apps/api/package.json": PKG_TEST,
                "apps/web/package.json": PKG_TEST,
            }
        )

    def test_stack_is_monorepo(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["stack"], "monorepo")

    def test_root_commands_stay_empty(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertEqual(r["commands"], {})
        self.assertEqual(r["commandSources"], {})

    def test_choose_a_subproject_hint_present(self):
        with self._project() as root:
            r = detect.resolve(root)
        self.assertTrue(any("subproject" in m for m in r["missingCommands"]), r["missingCommands"])
        self.assertEqual(len(r["subprojects"]), 2)


# --------------------------------------------------------------- openspec --


class TestOpenspecNoChangeNamed(unittest.TestCase):
    """D3: never report a problem at a location that cannot exist."""

    def _project(self, changes: dict[str, str]):
        files = {"README.md": "x\n"}
        for name, tasks_body in changes.items():
            files[os.path.join("openspec", "changes", name, "tasks.md")] = tasks_body
        return Project(files)

    def test_path_is_never_the_impossible_join(self):
        with self._project({"add-widget": "- [ ] T001 x\n"}) as root:
            p = plan.read_plan(root, source="openspec")
        impossible = os.path.join(root, "openspec", "changes", "tasks.md")
        self.assertNotEqual(p["path"], impossible)
        self.assertFalse(os.path.exists(impossible))

    def test_report_names_the_changes_that_exist(self):
        with self._project({"add-widget": "- [ ] T001 x\n", "fix-thing": "- [ ] T001 y\n"}) as root:
            p = plan.read_plan(root, source="openspec")
        self.assertFalse(p["exists"])
        self.assertEqual(sorted(p["availableChanges"]), ["add-widget", "fix-thing"])
        self.assertIn("add-widget", p["error"])
        self.assertIn("fix-thing", p["error"])

    def test_error_says_a_change_must_be_chosen(self):
        with self._project({"add-widget": "- [ ] T001 x\n"}) as root:
            p = plan.read_plan(root, source="openspec")
        self.assertIn("choose", p["error"].lower())

    def test_empty_changes_directory_is_reported_distinctly(self):
        with Project({"README.md": "x\n"}) as root:
            os.makedirs(os.path.join(root, "openspec", "changes"))
            p = plan.read_plan(root, source="openspec")
        self.assertFalse(p["exists"])
        self.assertEqual(p["availableChanges"], [])
        self.assertNotIn("choose", p["error"].lower())
        self.assertIn("no changes", p["error"].lower())

    def test_gate_next_task_reason_is_the_same_named_problem_not_a_path(self):
        import gate  # noqa: E402 -- deferred so `sys.path` above applies first

        with self._project({"add-widget": "- [ ] T001 x\n"}) as root:
            r = gate.next_task(root, source="openspec")
        self.assertFalse(r["ready"])
        self.assertIn("add-widget", r["reason"])
        self.assertNotIn(os.path.join("changes", "tasks.md"), r["reason"])


if __name__ == "__main__":
    unittest.main()
