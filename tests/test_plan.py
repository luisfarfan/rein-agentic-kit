"""Tests for plan parsing, closing and ordering.

The plan is the loop's state when `tracker` is "none". A parser that silently
drops a task means the loop silently skips work — so the assertions here are
about what must NEVER be lost, as much as about happy paths.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import plan  # noqa: E402

FULL = """\
# Change: add-widget

- [x] T001 Already done
  - Type: docs

- [ ] T002 Parse the config file
  - Type: implementation
  - Depends on: T001
  - Human review: false
  - Verification: `python3 -m unittest tests.test_config`
  - Acceptance:
    - reads flow.config.json when present
    - falls back to autodetect otherwise

- [ ] T003 Wire it up
  - Depends on: T002, T001
  - Human review: true
  - Verification: pytest tests/test_wire.py
"""


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.tasks = plan.parse_tasks_md(FULL)

    def test_all_tasks_found_with_state(self):
        self.assertEqual([t["id"] for t in self.tasks], ["T001", "T002", "T003"])
        self.assertEqual([t["checked"] for t in self.tasks], [True, False, False])

    def test_fields_are_read_literally(self):
        t = self.tasks[1]
        self.assertEqual(t["title"], "Parse the config file")
        self.assertEqual(t["type"], "implementation")
        self.assertEqual(t["dependsOn"], ["T001"])
        self.assertFalse(t["humanReview"])
        # Backticks stripped: this string is executed as a command.
        self.assertEqual(t["verification"], "python3 -m unittest tests.test_config")

    def test_acceptance_criteria_collected(self):
        self.assertEqual(
            self.tasks[1]["acceptance"],
            ["reads flow.config.json when present", "falls back to autodetect otherwise"],
        )
        # A later field must stop the collection.
        self.assertEqual(self.tasks[2]["acceptance"], [])

    def test_multiple_dependencies_and_human_review(self):
        t = self.tasks[2]
        self.assertEqual(t["dependsOn"], ["T002", "T001"])
        self.assertTrue(t["humanReview"])

    def test_bare_checkbox_still_parses(self):
        """A plan the parser rejects is a plan the loop cannot run."""
        tasks = plan.parse_tasks_md("- [ ] do the thing\n- [ ] and another\n")
        self.assertEqual([t["id"] for t in tasks], ["T001", "T002"])
        self.assertEqual(tasks[0]["title"], "do the thing")
        self.assertEqual(tasks[0]["verification"], "")
        self.assertEqual(tasks[0]["dependsOn"], [])

    def test_depends_on_none_is_empty(self):
        for spelling in ("none", "None", "-", "n/a", ""):
            tasks = plan.parse_tasks_md(f"- [ ] T001 x\n  - Depends on: {spelling}\n")
            self.assertEqual(tasks[0]["dependsOn"], [], spelling)

    def test_field_spelling_variants(self):
        tasks = plan.parse_tasks_md(
            "- [ ] T001 x\n  - DependsOn: T000\n  - Verify: `make test`\n  - Supervised: yes\n"
        )
        self.assertEqual(tasks[0]["dependsOn"], ["T000"])
        self.assertEqual(tasks[0]["verification"], "make test")
        self.assertTrue(tasks[0]["humanReview"])

    def test_prose_between_tasks_is_ignored(self):
        tasks = plan.parse_tasks_md("Some intro.\n\n- [ ] T001 a\n\nA paragraph.\n\n- [ ] T002 b\n")
        self.assertEqual(len(tasks), 2)

    def test_non_task_top_level_bullets_do_not_become_fields(self):
        tasks = plan.parse_tasks_md("- [ ] T001 a\n  - Type: docs\n- not a field, same indent as task\n")
        self.assertEqual(tasks[0]["type"], "docs")
        self.assertEqual(len(tasks), 1)

    def test_empty_plan(self):
        self.assertEqual(plan.parse_tasks_md(""), [])


class TestOrdering(unittest.TestCase):
    def _tasks(self, spec: dict[str, list[str]]) -> list[dict]:
        return [{"id": k, "dependsOn": v} for k, v in spec.items()]

    def test_dependencies_come_first(self):
        ordered, stuck = plan.order_by_dependencies(self._tasks({"C": ["B"], "A": [], "B": ["A"]}))
        self.assertEqual([t["id"] for t in ordered], ["A", "B", "C"])
        self.assertEqual(stuck, [])

    def test_unknown_dependency_is_ignored_not_fatal(self):
        ordered, stuck = plan.order_by_dependencies(self._tasks({"A": ["GONE"]}))
        self.assertEqual([t["id"] for t in ordered], ["A"])
        self.assertEqual(stuck, [])

    def test_cycle_goes_last_and_is_reported_never_dropped(self):
        ordered, stuck = plan.order_by_dependencies(self._tasks({"A": [], "B": ["C"], "C": ["B"]}))
        self.assertEqual(len(ordered), 3, "a cycle must never lose tasks")
        self.assertEqual(ordered[0]["id"], "A")
        self.assertEqual(set(stuck), {"B", "C"})

    def test_order_is_deterministic(self):
        spec = {"D": ["A"], "C": ["A"], "B": ["A"], "A": []}
        first = [t["id"] for t in plan.order_by_dependencies(self._tasks(spec))[0]]
        for _ in range(5):
            self.assertEqual([t["id"] for t in plan.order_by_dependencies(self._tasks(spec))[0]], first)


class TestReadAndClose(unittest.TestCase):
    def _project(self, body: str, name: str = "tasks.md") -> str:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return self.tmp.name

    def test_read_plan_separates_pending(self):
        root = self._project(FULL)
        p = plan.read_plan(root)
        self.assertTrue(p["exists"])
        self.assertEqual(p["source"], "tasks-md")
        self.assertEqual([t["id"] for t in p["pending"]], ["T002", "T003"])

    def test_missing_plan_reports_instead_of_raising(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = plan.read_plan(self.tmp.name)
        self.assertFalse(p["exists"])
        self.assertEqual(p["tasks"], [])
        self.assertIn("no plan", p["error"])

    def test_openspec_source_finds_artifacts(self):
        root = self._project(FULL, os.path.join("openspec", "changes", "add-widget", "tasks.md"))
        for name in ("proposal.md", "design.md"):
            with open(os.path.join(root, "openspec", "changes", "add-widget", name), "w") as fh:
                fh.write("x")
        p = plan.read_plan(root, source="openspec", change="add-widget")
        self.assertTrue(p["exists"])
        self.assertEqual(len(p["tasks"]), 3)
        self.assertIn(os.path.join("openspec", "changes", "add-widget", "proposal.md"), p["artifacts"])

    def test_close_ticks_the_right_box_only(self):
        root = self._project(FULL)
        path = os.path.join(root, "tasks.md")
        self.assertTrue(plan.close_task(path, "T002"))
        after = {t["id"]: t["checked"] for t in plan.read_plan(root)["tasks"]}
        self.assertEqual(after, {"T001": True, "T002": True, "T003": False})

    def test_close_is_idempotent_and_honest(self):
        root = self._project(FULL)
        path = os.path.join(root, "tasks.md")
        self.assertFalse(plan.close_task(path, "T001"), "already closed -> no change reported")
        self.assertFalse(plan.close_task(path, "T999"), "unknown id -> no change reported")

    def test_close_preserves_the_rest_of_the_file(self):
        root = self._project(FULL)
        path = os.path.join(root, "tasks.md")
        plan.close_task(path, "T002")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("# Change: add-widget", body)
        self.assertIn("reads flow.config.json when present", body)
        self.assertIn("- [ ] T003 Wire it up", body)


HEADER_PLAN = """\
# Change: add-widget

## Why
The criteria had nothing above them, so the reviewer had to infer intent.

## Scope
- In: the parser and its tests
- Out: the CLI surface — that is a separate change
- Out: anything touching the loop

## Decisions
- D1 Header lives in tasks.md — a separate file would be another artifact nobody reads
- D2 Every section optional - ceremony that blocks a small change is worse than none

---

- [ ] T001 Do the thing
  - Verification: `pytest x.py`
"""


class TestHeader(unittest.TestCase):
    def test_why_scope_and_decisions_are_parsed(self):
        h = plan.parse_header(HEADER_PLAN)
        self.assertIn("nothing above them", h["why"])
        self.assertEqual(h["scopeIn"], ["the parser and its tests"])
        self.assertEqual(len(h["scopeOut"]), 2)
        self.assertIn("CLI surface", h["scopeOut"][0])
        self.assertEqual([d["id"] for d in h["decisions"]], ["D1", "D2"])

    def test_decision_splits_title_from_rationale_on_either_dash(self):
        h = plan.parse_header(HEADER_PLAN)
        self.assertEqual(h["decisions"][0]["title"], "Header lives in tasks.md")
        self.assertIn("another artifact", h["decisions"][0]["rationale"])
        # The second uses an ASCII hyphen rather than an em dash.
        self.assertEqual(h["decisions"][1]["title"], "Every section optional")

    def test_header_does_not_swallow_task_content(self):
        """Everything below the first checkbox belongs to tasks, not the header."""
        h = plan.parse_header(HEADER_PLAN)
        self.assertNotIn("Do the thing", h["why"])
        self.assertEqual(len(plan.parse_tasks_md(HEADER_PLAN)), 1)

    def test_a_plan_with_no_header_still_parses_exactly_as_before(self):
        """The regression that matters: ceremony must not become mandatory."""
        h = plan.parse_header(FULL)
        self.assertEqual(h, {"why": "", "scopeIn": [], "scopeOut": [], "decisions": []})
        self.assertEqual(len(plan.parse_tasks_md(FULL)), 3)

    def test_read_plan_exposes_the_header(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write(HEADER_PLAN)
        p = plan.read_plan(tmp.name)
        self.assertTrue(p["why"])
        self.assertEqual(len(p["scopeOut"]), 2)
        self.assertEqual(len(p["decisions"]), 2)

    def test_a_scope_bullet_without_in_or_out_is_kept_not_dropped(self):
        h = plan.parse_header("## Scope\n- just this module\n\n- [ ] T001 x\n")
        self.assertEqual(h["scopeIn"], ["just this module"])


if __name__ == "__main__":
    unittest.main()
