#!/usr/bin/env python3
"""Tests for T005 -- "only what is live, and only what changed".

  AC1  `select()` splits changes into live (all their work items projected)
       and history (one closed Module, no work items).
  AC2  the window defaults to 30, is read from `plane.json`, and a change
       whose age equals the window exactly is still live (D10).
  AC3  a history Module carries its task counts in its description, is
       created in a completed state, and emits no work items.
  AC4  `select()` emits only entities whose content hash differs from the
       last recorded projection.
  AC5  a sync that fails partway leaves the previous projection record
       intact, and the retry set carries the entity that failed.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "rein", "lib"))

import plane_projection as pp  # noqa: E402


def _change_state(name, age_days, tasks):
    return {
        "root": "/repo",
        "change": name,
        "planPath": f"/repo/changes/{name}/tasks.md",
        "planExists": True,
        "tasks": tasks,
        "lastTouchedDays": age_days,
    }


def _task(task_id, title, transition):
    return {
        "taskId": task_id,
        "title": title,
        "checked": transition in ("verified", "merged"),
        "transition": transition,
        "when": "2026-01-01T00:00:00",
        "commit": "abc123",
    }


def _fixture_state():
    """One live change (touched 5 days ago) and one history change (touched
    90 days ago, well past the 30-day default window)."""
    live = _change_state(
        "add-widget",
        5,
        [
            _task("T001", "Build the widget", "verified"),
            _task("T002", "Wire it up", "started"),
        ],
    )
    history = _change_state(
        "old-refactor",
        90,
        [
            _task("T001", "Old task one", "verified"),
            _task("T002", "Old task two", "verified"),
            _task("T003", "Old task three", "blocked"),
        ],
    )
    return [live, history]


class SelectSplitTests(unittest.TestCase):
    """AC1: live changes project with all their work items; history changes
    collapse to a Module and nothing else."""

    def test_live_change_emits_module_and_every_work_item(self):
        entities = pp.select(_fixture_state(), window_days=30)
        live_entities = [e for e in entities if e["change"] == "add-widget"]
        types = [e["type"] for e in live_entities]
        self.assertEqual(types.count("module"), 1)
        self.assertEqual(types.count("work_item"), 2)
        module = next(e for e in live_entities if e["type"] == "module")
        self.assertEqual(module["scope"], "live")

    def test_history_change_emits_only_a_module(self):
        entities = pp.select(_fixture_state(), window_days=30)
        history_entities = [e for e in entities if e["change"] == "old-refactor"]
        self.assertEqual(len(history_entities), 1)
        self.assertEqual(history_entities[0]["type"], "module")
        self.assertEqual(history_entities[0]["scope"], "history")


class WindowTests(unittest.TestCase):
    """AC2: default 30, override from plane.json, boundary is live."""

    def test_default_window_is_30(self):
        self.assertEqual(pp.DEFAULT_WINDOW_DAYS, 30)

    def test_load_window_days_defaults_to_30_when_plane_json_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pp.load_window_days(tmp), 30)

    def test_load_window_days_reads_override_from_plane_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "plane.json"), "w", encoding="utf-8") as fh:
                json.dump({"window": 14}, fh)
            self.assertEqual(pp.load_window_days(tmp), 14)

    def test_select_without_window_days_uses_the_default(self):
        state = [_change_state("recent", 29, [_task("T001", "A", "planned")])]
        entities = pp.select(state)
        module = next(e for e in entities if e["type"] == "module")
        self.assertEqual(module["scope"], "live")

    def test_age_equal_to_window_is_still_live(self):
        state = [_change_state("edge", 30, [_task("T001", "A", "planned")])]
        entities = pp.select(state, window_days=30)
        module = next(e for e in entities if e["type"] == "module")
        self.assertEqual(module["scope"], "live")
        work_items = [e for e in entities if e["type"] == "work_item"]
        self.assertEqual(len(work_items), 1)

    def test_age_one_day_past_window_is_history(self):
        state = [_change_state("just-over", 31, [_task("T001", "A", "planned")])]
        entities = pp.select(state, window_days=30)
        module = next(e for e in entities if e["type"] == "module")
        self.assertEqual(module["scope"], "history")
        self.assertFalse([e for e in entities if e["type"] == "work_item"])


class HistoryModuleTests(unittest.TestCase):
    """AC3: task counts in the description, completed state, no work items."""

    def test_description_carries_task_counts(self):
        entities = pp.select(_fixture_state(), window_days=30)
        module = next(e for e in entities if e["change"] == "old-refactor")
        description = module["payload"]["description"]
        self.assertIn("2 verified", description)
        self.assertIn("1 blocked", description)
        self.assertIn("3 task", description)

    def test_status_field_is_completed_not_state(self):
        """Plane's Module resource carries `status`, not `state` -- `state`
        is the Issue field (a uuid), and a DRF serializer silently drops an
        unknown field, so a `state` key here would create the Module in
        whatever Plane defaults to rather than `completed` (finding 4,
        round-1 review). The field name itself is pinned, not just its
        value, since that is exactly what a stray `state` key would pass
        while still being wrong."""
        entities = pp.select(_fixture_state(), window_days=30)
        module = next(e for e in entities if e["change"] == "old-refactor")
        self.assertEqual(module["payload"]["status"], "completed")
        self.assertNotIn("state", module["payload"])

    def test_live_module_carries_no_guessed_status(self):
        """`"active"` was never a legal Module status (backlog/planned/
        in-progress/paused/completed/cancelled) -- a live module's status
        is omitted rather than sent as an un-measured value."""
        entities = pp.select(_fixture_state(), window_days=30)
        module = next(e for e in entities if e["change"] == "add-widget")
        self.assertNotIn("status", module["payload"])
        self.assertNotIn("state", module["payload"])

    def test_no_work_item_emitted_for_a_history_change(self):
        entities = pp.select(_fixture_state(), window_days=30)
        work_items = [e for e in entities if e["change"] == "old-refactor" and e["type"] == "work_item"]
        self.assertEqual(work_items, [])


class ContentHashDedupTests(unittest.TestCase):
    """AC4: only entities whose hash differs from the last recorded
    projection are emitted."""

    def test_second_run_over_unchanged_state_emits_nothing(self):
        state = _fixture_state()
        first = pp.select(state, window_days=30, record={})
        self.assertTrue(first)
        record = {e["key"]: e["hash"] for e in first}

        second = pp.select(state, window_days=30, record=record)
        self.assertEqual(second, [])

    def test_mutating_one_task_emits_exactly_one_entity(self):
        state = _fixture_state()
        first = pp.select(state, window_days=30, record={})
        record = {e["key"]: e["hash"] for e in first}

        mutated = copy.deepcopy(state)
        mutated[0]["tasks"][1]["transition"] = "verified"

        third = pp.select(mutated, window_days=30, record=record)
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["taskId"], "T002")
        self.assertEqual(third[0]["payload"]["transition"], "verified")


class SyncRetryTests(unittest.TestCase):
    """AC5: a sync that fails partway leaves the previous projection record
    intact, and the retry set carries the entity that failed."""

    def test_failure_partway_leaves_record_intact_and_returns_retry_set(self):
        state = _fixture_state()
        with tempfile.TemporaryDirectory() as tmp:
            record_path = os.path.join(tmp, "plane_projection.json")

            applied_calls = []

            def failing_apply(entity):
                applied_calls.append(entity["key"])
                if entity["type"] == "work_item":
                    raise RuntimeError("simulated network failure")

            applied, retry = pp.sync(state, 30, failing_apply, record_path)

            self.assertFalse(os.path.exists(record_path))
            self.assertTrue(retry)
            self.assertIn(applied_calls[-1], [e["key"] for e in retry])

    def test_successful_sync_writes_the_record_and_next_run_emits_nothing(self):
        state = _fixture_state()
        with tempfile.TemporaryDirectory() as tmp:
            record_path = os.path.join(tmp, "plane_projection.json")

            applied, retry = pp.sync(state, 30, lambda entity: None, record_path)
            self.assertFalse(retry)
            self.assertTrue(applied)
            self.assertTrue(os.path.exists(record_path))

            second_applied, second_retry = pp.sync(state, 30, lambda entity: None, record_path)
            self.assertEqual(second_applied, [])
            self.assertEqual(second_retry, [])

    def test_partial_success_then_success_retries_only_the_remainder(self):
        state = [_change_state("solo", 5, [_task("T001", "A", "planned"), _task("T002", "B", "planned")])]
        with tempfile.TemporaryDirectory() as tmp:
            record_path = os.path.join(tmp, "plane_projection.json")

            calls = {"n": 0}

            def fail_on_second_call(entity):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("simulated failure")

            applied, retry = pp.sync(state, 30, fail_on_second_call, record_path)
            self.assertEqual(len(applied), 1)
            self.assertTrue(retry)
            self.assertFalse(os.path.exists(record_path))

            # Retry with an always-succeeding apply_fn: the remainder goes out.
            applied2, retry2 = pp.sync(state, 30, lambda entity: None, record_path)
            self.assertFalse(retry2)
            self.assertTrue(os.path.exists(record_path))
            record = pp.load_record(record_path)
            self.assertEqual(len(record), 3)  # module + 2 work items


if __name__ == "__main__":
    unittest.main()
