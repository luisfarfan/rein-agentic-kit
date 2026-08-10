#!/usr/bin/env python3
"""Tests for T001 -- a backlog the plan can eat, that comes back if the plan
dies.

Two properties carry this change and both are easy to fake:

  * an id is never reused (D4). A fixture that only ever appends cannot see
    the failure, so the id tests DELETE a line before adding again.
  * an item returns to the backlog when its change is abandoned (D1). A
    fixture whose changes are all fresh never crosses that branch, so the
    derivation tests drive all four combinations of live/stale and
    finished/unfinished explicitly.

The third property is structural: a backlog must never be executable. That
one is asserted against a real git repo through the shipped CLI, because the
whole risk is that some OTHER code path (`read_plan`, `rein next`) treats the
file as a plan -- which is exactly what happens today if the same list is put
under `openspec/changes/`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")

sys.path.insert(0, LIB_DIR)
import backlog as bl  # noqa: E402
import plan as _plan  # noqa: E402
import plane_projection as pp  # noqa: E402
import product_state as ps  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True)


def _init_repo(repo):
    os.makedirs(repo, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")


def _change(name, absorbs, age_days, unfinished):
    """A `product_state.state()`-shaped record, only the fields derive_states reads."""
    return {
        "change": name,
        "absorbs": list(absorbs),
        "lastTouchedDays": age_days,
        "tasks": [{
            "taskId": "T001",
            "checked": not unfinished,
            "transition": "planned" if unfinished else "verified",
        }],
    }


class BacklogFileRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_add_creates_the_file_when_absent(self):
        self.assertFalse(os.path.exists(bl.backlog_path(self.root)))
        self.assertEqual(bl.add(self.root, "first idea"), "B001")
        self.assertTrue(os.path.exists(bl.backlog_path(self.root)))

    def test_items_round_trip_in_order(self):
        for text in ("one", "two", "three"):
            bl.add(self.root, text)
        self.assertEqual(
            [(i["id"], i["text"]) for i in bl.items(self.root)],
            [("B001", "one"), ("B002", "two"), ("B003", "three")],
        )

    def test_a_line_the_parser_does_not_recognise_survives(self):
        """The file is hand-edited by design; a note must not be eaten."""
        bl.add(self.root, "first")
        path = bl.backlog_path(self.root)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n> a note somebody left, not an item\n")
        bl.add(self.root, "second")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("> a note somebody left, not an item", body)
        self.assertEqual([i["id"] for i in bl.items(self.root)], ["B001", "B002"])

    def test_empty_text_is_refused(self):
        with self.assertRaises(ValueError):
            bl.add(self.root, "   ")


class IdsAreNeverReusedTests(unittest.TestCase):
    """D4. An id recycled onto a different idea silently repoints a live
    Plane card, because `external_id` is built from it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _delete_line(self, item_id):
        path = bl.backlog_path(self.root)
        with open(path, encoding="utf-8") as fh:
            kept = [l for l in fh.read().splitlines() if f"[{item_id}]" not in l]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")

    def test_deleting_the_middle_line_does_not_free_its_id(self):
        for text in ("one", "two", "three"):
            bl.add(self.root, text)
        self._delete_line("B002")
        self.assertEqual(bl.add(self.root, "four"), "B004")
        self.assertEqual([i["id"] for i in bl.items(self.root)], ["B001", "B003", "B004"])

    def test_deleting_the_highest_line_does_not_free_its_id(self):
        """The case the marker exists for: without it, the file's own maximum
        drops and the next add hands out an id that already means something."""
        for text in ("one", "two"):
            bl.add(self.root, text)
        self._delete_line("B002")
        self.assertEqual(bl.add(self.root, "three"), "B003")

    def test_emptying_the_file_entirely_still_does_not_restart_at_one(self):
        bl.add(self.root, "one")
        bl.add(self.root, "two")
        for item_id in ("B001", "B002"):
            self._delete_line(item_id)
        self.assertEqual(bl.items(self.root), [])
        self.assertEqual(bl.add(self.root, "later"), "B003")


class AbsorbsIsParsedWithoutTouchingParseHeaderTests(unittest.TestCase):
    def test_each_accepted_spelling(self):
        for text in ("## Why\n\nAbsorbs: B001, B002\n\n- [ ] T001 x",
                     "## Why\n\nabsorbs: B001 and B002\n\n- [ ] T001 x",
                     "## Why\n\nCloses backlog: B001; B002\n\n- [ ] T001 x"):
            self.assertEqual(_plan.absorbs_from_text(text), ["B001", "B002"], text)

    def test_none_and_absent_both_yield_empty(self):
        self.assertEqual(_plan.absorbs_from_text("Absorbs: none\n\n- [ ] T001 x"), [])
        self.assertEqual(_plan.absorbs_from_text("## Why\n\nnothing\n\n- [ ] T001 x"), [])

    def test_a_token_that_is_not_an_id_is_ignored(self):
        self.assertEqual(_plan.absorbs_from_text("Absorbs: B001, TODO\n\n- [ ] T001 x"), ["B001"])

    def test_parse_header_keys_are_unchanged(self):
        """Pinned by test_plan.py; a new key there breaks every existing plan."""
        header = _plan.parse_header("## Why\n\nbecause\n\n- [ ] T001 x")
        self.assertEqual(sorted(header), ["decisions", "scopeIn", "scopeOut", "why"])


class DerivedStateReturnsAnAbandonedItemTests(unittest.TestCase):
    """D1, and the reason this module exists.

    Measured on this repo's own corpus: 71 of 118 changes in one workspace
    were untouched for one to four months. An item closed at planning time
    and then abandoned is neither in the backlog nor fixed -- it vanished
    BECAUSE the first step was done properly.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        bl.add(self.root, "the bug")

    def _state(self, changes):
        return bl.derive_states(self.root, changes, window_days=30)["B001"]

    def test_unclaimed_is_backlog(self):
        self.assertEqual(self._state([]), "backlog")

    def test_absorbed_by_a_live_change_is_completed(self):
        self.assertEqual(self._state([_change("c", ["B001"], 1, True)]), "completed")

    def test_absorbed_by_an_abandoned_change_returns_to_backlog(self):
        self.assertEqual(self._state([_change("c", ["B001"], 400, True)]), "backlog")

    def test_a_stale_but_finished_change_keeps_it_closed(self):
        """Stale and done is shipped, not abandoned."""
        self.assertEqual(self._state([_change("c", ["B001"], 400, False)]), "completed")

    def test_one_live_change_is_enough_when_another_went_stale(self):
        self.assertEqual(
            self._state([_change("dead", ["B001"], 400, True),
                         _change("alive", ["B001"], 2, True)]),
            "completed",
        )

    def test_a_change_absorbing_an_unknown_id_changes_nothing(self):
        self.assertEqual(self._state([_change("c", ["B999"], 1, True)]), "backlog")

    def test_nothing_is_stored_so_the_return_needs_no_reset(self):
        """Recomputed from scratch: the same input twice, then aged, with no
        deletion or reset in between."""
        live = [_change("c", ["B001"], 1, True)]
        self.assertEqual(self._state(live), "completed")
        self.assertEqual(self._state(live), "completed")
        self.assertEqual(self._state([_change("c", ["B001"], 400, True)]), "backlog")
        self.assertEqual(self._state(live), "completed")


class TheBacklogProjectsThroughTheExistingSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        bl.add(self.root, "paginate the work items")
        bl.add(self.root, "a dry-run mode")

    def test_the_record_is_what_select_consumes(self):
        """Asserted by PASSING it to `select()`, not by checking field names
        and hoping they line up."""
        record = bl.as_change_record(self.root, [])
        entities = pp.select([record], window_days=30, record={})
        keys = {e["key"] for e in entities}
        self.assertIn("module:backlog", keys)
        self.assertIn("item:backlog:B001", keys)
        self.assertIn("item:backlog:B002", keys)

    def test_an_empty_backlog_emits_no_module(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.assertIsNone(bl.as_change_record(empty.name, []))

    def test_the_backlog_never_ages_out_of_the_window(self):
        """An idea nobody picked up is still an idea; collapsing it to a
        closed history Module would hide the items most in need of attention."""
        record = bl.as_change_record(self.root, [])
        self.assertIsNone(record["lastTouchedDays"])
        module = next(e for e in pp.select([record], window_days=1, record={})
                      if e["type"] == "module")
        self.assertEqual(module["scope"], "live")

    def test_an_absorbed_item_projects_as_completed(self):
        record = bl.as_change_record(self.root, [_change("c", ["B001"], 1, True)])
        by_id = {t["taskId"]: t for t in record["tasks"]}
        self.assertEqual(by_id["B001"]["transition"], "verified")
        self.assertEqual(by_id["B002"]["transition"], "planned")

    def test_state_all_includes_it_last(self):
        records = ps.state_all(self.root)
        self.assertEqual(records[-1]["change"], "backlog")


class ABacklogIsNeverExecutableTests(unittest.TestCase):
    """The structural guard. Measured: the same list under
    `openspec/changes/backlog/tasks.md` IS offered by `rein next` as
    claimable, with no verification and no acceptance criteria -- the worst
    possible input to the loop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "repo")
        _init_repo(self.root)
        bl.add(self.root, "an idea nobody planned yet")
        # And the other place a person might reasonably put one.
        with open(os.path.join(self.root, "backlog.md"), "w", encoding="utf-8") as fh:
            fh.write("# Backlog\n\n- [B900] a stray idea at the repo root\n")

    def _rein(self, *args):
        return subprocess.run([sys.executable, REIN_BIN, *args],
                              capture_output=True, text=True, timeout=60)

    def test_the_gate_offers_nothing_to_claim(self):
        out = self._rein("next", self.root).stdout
        # The gate reports "" for nothing to claim, not null.
        self.assertFalse(json.loads(out).get("taskId"))

    def test_the_plan_parser_finds_no_tasks(self):
        out = self._rein("tasks", self.root).stdout
        self.assertEqual(json.loads(out).get("tasks"), [])

    def test_read_plan_does_not_resolve_either_file_as_the_plan(self):
        resolved = _plan.read_plan(self.root).get("path") or ""
        self.assertNotIn("backlog", os.path.basename(resolved))


class BacklogCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _rein(self, *args):
        return subprocess.run([sys.executable, REIN_BIN, "backlog", *args],
                              capture_output=True, text=True, timeout=60)

    def test_add_prints_the_assigned_id_and_list_shows_it(self):
        added = self._rein("add", "the sync should paginate", self.root)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("B001", added.stdout)
        listed = self._rein("list", self.root)
        self.assertIn("B001", listed.stdout)
        self.assertIn("the sync should paginate", listed.stdout)

    def test_list_reports_the_derived_state_per_item(self):
        self._rein("add", "unclaimed idea", self.root)
        out = self._rein("list", self.root).stdout
        self.assertRegex(out, r"B001\s+backlog\s+unclaimed idea")

    def test_an_empty_backlog_exits_zero_saying_so(self):
        result = self._rein("list", self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn("empty", result.stdout)

    def test_add_with_no_text_exits_nonzero_without_writing(self):
        result = self._rein("add", self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(bl.backlog_path(self.root)))

    def test_an_unknown_action_explains_itself(self):
        result = self._rein("groom", self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stdout + result.stderr)


class DuplicateIdsAreRefusedTests(unittest.TestCase):
    """Found on a real repo, not here.

    `IdsAreNeverReusedTests` proves `add` never hands out a used id -- and
    then the file is hand-edited BY DESIGN, so a copy-paste puts two lines
    under one id in two seconds. Both build the same `external_id`, so the
    second silently overwrites the first's card and one idea disappears with
    no error anywhere. The door was bolted and the window left open.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        bl.add(self.root, "first")
        bl.add(self.root, "second")

    def _duplicate_a_line(self):
        path = bl.backlog_path(self.root)
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        dup = next(l for l in lines if "[B002]" in l)
        lines.insert(lines.index(dup) + 1, dup)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_a_clean_file_reports_no_duplicates(self):
        self.assertEqual(bl.duplicate_ids(self.root), [])
        bl.check(self.root)  # must not raise

    def test_duplicates_are_named(self):
        self._duplicate_a_line()
        self.assertEqual(bl.duplicate_ids(self.root), ["B002"])

    def test_the_projection_refuses_rather_than_overwriting_a_card(self):
        self._duplicate_a_line()
        with self.assertRaises(bl.BacklogCorrupt) as ctx:
            bl.as_change_record(self.root, [])
        self.assertIn("B002", str(ctx.exception))
        self.assertIn(bl.backlog_path(self.root), str(ctx.exception))

    def test_the_cli_reports_it_instead_of_listing_a_corrupt_backlog(self):
        self._duplicate_a_line()
        result = subprocess.run([sys.executable, REIN_BIN, "backlog", "list", self.root],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("B002", output)
        # A MESSAGE, not a stack trace. The first version of this test passed
        # on an unhandled exception -- non-zero exit, id in stderr, and a
        # traceback the operator has to read to learn what to fix.
        self.assertNotIn("Traceback", output)
        self.assertIn("rein backlog:", output)


class ADeletedItemDoesNotHauntTheBoardTests(unittest.TestCase):
    """Found on a real repo: three cards for two remaining ideas.

    Deleting a line is the most natural way to drop an idea, and D6 forbids
    deleting the card -- so it sat in `backlog` forever, indistinguishable
    from a live item. The derived state covered captured and absorbed and
    never asked what happens when someone simply removes the line.

    The card is closed once, as `cancelled`, and with NO name: the local text
    that titled it is gone, so anything sent would replace the real title
    with a placeholder built from the id.
    """

    def _state(self, task_ids):
        return [{
            "change": "backlog",
            "lastTouchedDays": None,
            "tasks": [{"taskId": t, "title": t, "checked": False,
                       "transition": "planned", "dependsOn": []} for t in task_ids],
        }]

    def test_a_key_with_no_local_item_is_emitted_once_as_cancelled(self):
        first = pp.select(self._state(["B001", "B002"]), window_days=30, record={})
        record = {e["key"]: e["hash"] for e in first}

        after = pp.select(self._state(["B002"]), window_days=30, record=record)
        dropped = [e for e in after if e.get("dropped")]
        self.assertEqual([e["taskId"] for e in dropped], ["B001"])
        self.assertEqual(dropped[0]["payload"], {"transition": "cancelled"})
        self.assertNotIn("name", dropped[0]["payload"])

    def test_it_happens_once_not_every_run(self):
        first = pp.select(self._state(["B001", "B002"]), window_days=30, record={})
        record = {e["key"]: e["hash"] for e in first}
        second = pp.select(self._state(["B002"]), window_days=30, record=record)
        record.update({e["key"]: e["hash"] for e in second})
        third = pp.select(self._state(["B002"]), window_days=30, record=record)
        self.assertEqual(third, [])

    def test_a_task_the_window_suppressed_is_not_mistaken_for_a_deleted_one(self):
        """The regression this fix caused on its first attempt: the history
        branch omits FINISHED tasks on purpose, and treating every unemitted
        key as gone marked shipped work `cancelled`."""
        live = [{"change": "c", "lastTouchedDays": 1, "tasks": [
            {"taskId": "T001", "title": "done", "checked": True,
             "transition": "verified", "dependsOn": []}]}]
        first = pp.select(live, window_days=30, record={})
        record = {e["key"]: e["hash"] for e in first}
        aged = [{**live[0], "lastTouchedDays": 400}]
        out = pp.select(aged, window_days=30, record=record)
        self.assertEqual([e for e in out if e.get("dropped")], [])


if __name__ == "__main__":
    unittest.main()
