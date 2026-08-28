#!/usr/bin/env python3
"""Tests for issue selection and ordering.

Two properties carry this module and both are invisible in a passing
smoke test:

  * **priority 0 means "none", not "most urgent".** Sorting on the raw
    number puts every unprioritised issue ahead of every urgent one. On
    this board that is all 14 groupers, so a naive sort returns a page of
    index cards before the first real bug. The ordering tests drive a
    mixture that includes a 0 and a `None` explicitly, because the bug
    only shows when both are present.
  * **an empty result has two causes.** "no bug is open for this repo" and
    "that repo does not exist" both return zero rows, and only one of them
    is good news. `unknown` is what tells them apart.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "linear")

sys.path.insert(0, LIB_DIR)
import linear_select as ls  # noqa: E402


def _corpus():
    with open(os.path.join(FIXTURES, "corpus.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _issue(identifier, priority=3, repo="proxima-api", state="Backlog",
           labels=None, parent="PPR-1", flows="D1", title="t"):
    names = labels if labels is not None else ["Bug", repo]
    return {
        "identifier": identifier,
        "title": title,
        "priority": priority,
        "state": {"name": state, "type": state.lower()},
        "labels": {"nodes": [{"name": n} for n in names]},
        "parent": {"identifier": parent} if parent else None,
        "description": (
            f"**Impacto:** i\n\n|  |  |\n| -- | -- |\n"
            f"| Repo dueño | `{repo}` |\n| Encontrado por | X |\n"
            f"| Flujos afectados | {flows} |\n\n---\n\nprose\n"
        ),
    }


class UnprioritisedSortsLastNotFirstTests(unittest.TestCase):
    def test_zero_is_absent_not_urgent(self):
        self.assertLess(ls.urgency_rank(4), ls.urgency_rank(0))
        self.assertLess(ls.urgency_rank(1), ls.urgency_rank(0))

    def test_a_missing_priority_sorts_with_zero(self):
        self.assertEqual(ls.urgency_rank(None), ls.urgency_rank(0))

    def test_the_order_is_urgent_first_then_unprioritised(self):
        issues = [
            _issue("PPR-10", priority=0), _issue("PPR-11", priority=4),
            _issue("PPR-12", priority=1), _issue("PPR-13", priority=None),
            _issue("PPR-14", priority=2),
        ]
        got = [i["identifier"] for i in ls.select(issues)["issues"]]
        self.assertEqual(got, ["PPR-12", "PPR-14", "PPR-11", "PPR-10", "PPR-13"])

    def test_ties_break_on_the_number_not_the_text(self):
        # `PPR-119` sorts before `PPR-86` as text. A list ordered that way
        # looks ordered by nothing anyone can name.
        issues = [_issue("PPR-119"), _issue("PPR-86"), _issue("PPR-9")]
        got = [i["identifier"] for i in ls.select(issues)["issues"]]
        self.assertEqual(got, ["PPR-9", "PPR-86", "PPR-119"])


class FiltersTests(unittest.TestCase):
    def test_repo_state_flow_and_label(self):
        issues = [
            _issue("PPR-1", repo="proxima-api", state="Backlog", flows="D10"),
            _issue("PPR-2", repo="proxima-hub", state="Done", flows="D2"),
            _issue("PPR-3", repo="proxima-api", state="Done", flows="D10, D12"),
        ]
        self.assertEqual(
            [i["identifier"] for i in ls.select(issues, repo="proxima-api")["issues"]],
            ["PPR-1", "PPR-3"])
        self.assertEqual(
            [i["identifier"] for i in ls.select(issues, state="done")["issues"]],
            ["PPR-2", "PPR-3"])
        self.assertEqual(
            [i["identifier"] for i in ls.select(issues, flow="d10")["issues"]],
            ["PPR-1", "PPR-3"])

    def test_max_priority_means_at_least_this_urgent(self):
        issues = [_issue("PPR-1", priority=1), _issue("PPR-2", priority=3),
                  _issue("PPR-3", priority=4)]
        got = [i["identifier"] for i in ls.select(issues, max_priority=3)["issues"]]
        self.assertEqual(got, ["PPR-1", "PPR-2"])

    def test_max_priority_never_includes_the_unprioritised(self):
        # "at least medium" cannot mean "and also the ones nobody rated".
        issues = [_issue("PPR-1", priority=3), _issue("PPR-2", priority=0),
                  _issue("PPR-3", priority=None)]
        got = [i["identifier"] for i in ls.select(issues, max_priority=3)["issues"]]
        self.assertEqual(got, ["PPR-1"])

    def test_exact_priority_zero_is_selectable(self):
        # `priority=0` is a real filter, and testing the option by
        # truthiness would silently ignore it.
        issues = [_issue("PPR-1", priority=0), _issue("PPR-2", priority=3)]
        got = [i["identifier"] for i in ls.select(issues, priority=0,
                                                  include_groupers=True)["issues"]]
        self.assertEqual(got, ["PPR-1"])


class GroupersAreExcludedByDefaultTests(unittest.TestCase):
    def test_the_corpus_lists_fifty_bugs(self):
        self.assertEqual(len(ls.select(_corpus())["issues"]), 50)

    def test_and_sixty_four_with_groupers(self):
        self.assertEqual(len(ls.select(_corpus(), include_groupers=True)["issues"]), 64)

    def test_listing_the_flows_is_a_different_question(self):
        result = ls.select(_corpus(), label="Flujo", include_groupers=True)
        self.assertEqual(len(result["issues"]), 14)


class AnEmptyResultIsExplainedTests(unittest.TestCase):
    """Zero rows from "nothing open" and zero rows from "you typed a repo
    that does not exist" need different answers from a human."""

    def test_an_unknown_repo_is_named_with_what_does_exist(self):
        result = ls.select(_corpus(), repo="proxima-app")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["unknown"]), 1)
        self.assertEqual(result["unknown"][0]["filter"], "repo")
        self.assertIn("proxima-api", result["unknown"][0]["known"])

    def test_a_real_repo_that_simply_has_no_match_is_not_unknown(self):
        result = ls.select(_corpus(), repo="proxima-hub", state="Canceled")
        self.assertEqual(result["issues"], [])
        self.assertEqual([u["filter"] for u in result["unknown"]], ["state"])

    def test_a_known_filter_reports_nothing(self):
        self.assertEqual(ls.select(_corpus(), repo="proxima-api")["unknown"], [])

    def test_the_vocabulary_comes_from_the_board_not_a_hardcoded_list(self):
        # A fourth repo must be understood the day it appears, without an
        # edit here.
        issues = [_issue("PPR-1", repo="proxima-brandnew",
                         labels=["Bug", "proxima-brandnew"])]
        self.assertEqual(ls.select(issues, repo="proxima-brandnew")["unknown"], [])


class PriorityNamesTests(unittest.TestCase):
    def test_zero_reads_as_none_not_as_a_level(self):
        self.assertEqual(ls.priority_name(0), "none")
        self.assertEqual(ls.priority_name(1), "urgent")
        self.assertEqual(ls.priority_name(4), "low")
        self.assertEqual(ls.priority_name(None), "?")


class NothingRaisesTests(unittest.TestCase):
    def test_an_empty_board(self):
        result = ls.select([])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["unknown"], [])

    def test_none(self):
        self.assertEqual(ls.select(None)["issues"], [])


if __name__ == "__main__":
    unittest.main()
