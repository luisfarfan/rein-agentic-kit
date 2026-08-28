#!/usr/bin/env python3
"""Tests for the Linear issue parser.

Every fixture is a REAL issue captured from the `pproxima` board, chosen
because it is the case that breaks a plausible parser -- a hand-written
fixture cannot contradict an assumption it was built from:

  * `PPR-142` -- body shape A (33 of 50): table, `---`, prose.
  * `PPR-86`  -- body shape B (17 of 50): shape A with a full copy of the
    document appended, so a SECOND `# title` and a second `**Impacto:**`.
    Reading the frame from the first match returns the metadata table as
    this bug's prose.
  * `PPR-89`  -- Linear autolinked `notification.email` three times. Kept
    at the state it was captured in: the board has since been fixed, but
    the repair still has to cover the shape, and this is real data rather
    than a hand-made approximation of it.
  * `PPR-84`  -- the only bug with no `Flujos afectados`, AND parent-less
    while still being real work. It is what separates "grouper" from
    "filed standalone".
  * `PPR-137` -- a grouper: `Flujo` label, no parent, 177 bytes of index.

`corpus.json` is the whole board. The sweep over it is the test that
actually pins the parser: the five fixtures show the shapes, the sweep
proves nothing else in 64 issues behaves differently.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "linear")

sys.path.insert(0, LIB_DIR)
import linear_issue as li  # noqa: E402


def _fixture(name: str):
    with open(os.path.join(FIXTURES, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _corpus():
    return _fixture("corpus")


class TheStructuredFieldsComeFromTheApiTests(unittest.TestCase):
    """Title, priority, state and labels are typed data. No regex can get
    them wrong, which is why none is applied to them."""

    def test_the_frame_is_read_from_every_bug_in_the_corpus(self):
        for issue in _corpus():
            if li.is_grouper(issue):
                continue
            with self.subTest(issue=issue["identifier"]):
                self.assertEqual(li.parse(issue)["missing"], [])

    def test_priority_is_linears_own_scale_untouched(self):
        # The document body numbers P0..P3 and Linear reserves 0 for "no
        # priority", so the two are one apart. Deriving it from the body
        # would silently re-order the queue.
        parsed = li.parse(_fixture("PPR-86"))
        self.assertEqual(parsed["priority"], _fixture("PPR-86")["priority"])

    def test_the_repo_comes_from_the_label(self):
        for name, repo in (("PPR-142", "proxima-admin"), ("PPR-86", "proxima-api")):
            with self.subTest(issue=name):
                self.assertEqual(li.parse(_fixture(name))["repo"], repo)

    def test_every_bug_routes_to_exactly_one_known_repo(self):
        for issue in _corpus():
            if li.is_grouper(issue):
                continue
            with self.subTest(issue=issue["identifier"]):
                repo = li.parse(issue)["repo"]
                self.assertIn(repo, {"proxima-api", "proxima-admin", "proxima-hub"})


class BothBodyShapesYieldOnlyTheProseTests(unittest.TestCase):
    """The body is taken from the LAST `**Impacto:**`: past the duplicated
    header in shape B, a no-op in shape A. One rule, no shape detection."""

    def test_shape_b_does_not_leak_the_duplicated_header(self):
        body = li.parse(_fixture("PPR-86"))["body"]
        # The prose starts at the first line, whatever the board has done
        # to its formatting since -- asserting an exact prefix here broke
        # the day backticks were added around the identifier.
        self.assertIn("AdminOrderCreateRequest", body.splitlines()[0])
        self.assertNotIn("**Impacto:**", body)
        self.assertNotIn("Repo dueño", body)
        self.assertNotIn("Encontrado por la suite", body)
        self.assertNotRegex(body, r"^# ")

    def test_shape_a_keeps_its_sections(self):
        body = li.parse(_fixture("PPR-142"))["body"]
        self.assertTrue(body.startswith("## Qué pasa"))
        self.assertIn("## Repro", body)
        self.assertNotIn("| Repo dueño |", body)

    def test_no_body_in_the_corpus_carries_frame_debris(self):
        for issue in _corpus():
            if li.is_grouper(issue):
                continue
            with self.subTest(issue=issue["identifier"]):
                body = li.parse(issue)["body"]
                self.assertNotIn("**Impacto:**", body)
                self.assertNotIn("| Repo dueño |", body)
                self.assertNotRegex(body, r"(?m)^# ")
                self.assertTrue(body.strip(), "a bug with no prose at all")


class TheProvenanceFooterIsNotProseTests(unittest.TestCase):
    """All 50 bugs end with the same `---`, a pointer to the long form, and
    a "Reportado por la suite E2E" line. It is identical on every issue and
    it is the last thing an implementer reads before starting."""

    def test_no_body_in_the_corpus_ends_in_the_footer(self):
        for issue in _corpus():
            if li.is_grouper(issue):
                continue
            with self.subTest(issue=issue["identifier"]):
                body = li.parse(issue)["body"]
                self.assertNotIn("Reportado por la suite", body)
                self.assertNotIn("Repro completa", body)
                self.assertTrue(body.strip(), "trimming took the whole body")

    def test_the_document_pointer_survives_as_a_field(self):
        # Trimmed from the prose, kept as `docPath`: it is the one fact in
        # the footer worth having, and it belongs in a field rather than in
        # the middle of the text an agent reads.
        docs = [li.parse(i)["docPath"] for i in _corpus() if not li.is_grouper(i)]
        self.assertEqual(len([d for d in docs if d]), 50)

    def test_a_rule_inside_the_prose_is_kept(self):
        # Trimming happens from the END only, so a bug whose own prose uses
        # a horizontal rule keeps it.
        issue = {
            "identifier": "X-1", "title": "t", "priority": 3,
            "labels": {"nodes": [{"name": "proxima-api"}]},
            "description": (
                "**Impacto:** i\n\n## A\n\nfirst\n\n---\n\n## B\n\nsecond\n\n"
                "---\n\n*Reportado por la suite E2E (proxima-qa).*"
            ),
        }
        body = li.parse(issue)["body"]
        self.assertIn("---", body)
        self.assertTrue(body.endswith("second"))


class LinearsAutolinkingIsRepairedTests(unittest.TestCase):
    """`notification.email` reaches the API as a markdown link. It is an
    identifier the implementer greps for, so a broken one costs a search
    that finds nothing."""

    def test_a_dotted_identifier_comes_back_bare(self):
        body = li.parse(_fixture("PPR-89"))["body"]
        self.assertIn("notification.email", body)
        self.assertNotIn("](<http", body)

    def test_no_body_in_the_corpus_keeps_an_autolink(self):
        for issue in _corpus():
            with self.subTest(issue=issue["identifier"]):
                self.assertNotRegex(li.parse(issue)["body"], r"\]\(<?https?://")

    def test_a_real_link_is_not_mangled(self):
        # Repair only fires when the link TEXT equals its target. An
        # ordinary link must survive, or the repair would eat real URLs.
        kept = li.repair_autolinks("see [the docs](https://linear.app/docs)")
        self.assertEqual(kept, "see [the docs](https://linear.app/docs)")


class AGrouperIsNotWorkTests(unittest.TestCase):
    """14 of the 64 issues are indexes. An intake that takes one produces
    nothing, so they are named rather than left to fail downstream."""

    def test_a_flujo_parent_is_a_grouper(self):
        self.assertTrue(li.is_grouper(_fixture("PPR-137")))

    def test_a_parentless_bug_is_not_a_grouper(self):
        # PPR-84 has no parent but is real work. Testing "no parent" alone
        # would have discarded it; testing the label alone would keep a
        # grouper that happens to be nested.
        issue = _fixture("PPR-84")
        self.assertIsNone(issue.get("parent"))
        self.assertFalse(li.is_grouper(issue))
        self.assertEqual(li.parse(issue)["missing"], [])

    def test_a_grouper_is_not_reported_as_malformed(self):
        parsed = li.parse(_fixture("PPR-137"))
        self.assertTrue(parsed["isGrouper"])
        self.assertEqual(parsed["missing"], [])

    def test_the_corpus_splits_into_fifty_bugs_and_fourteen_groupers(self):
        corpus = _corpus()
        groupers = [i for i in corpus if li.is_grouper(i)]
        self.assertEqual(len(corpus), 64)
        self.assertEqual(len(groupers), 14)


class FlowsIsNotAFrameFieldTests(unittest.TestCase):
    def test_the_one_bug_with_no_flow_is_still_complete(self):
        parsed = li.parse(_fixture("PPR-84"))
        self.assertEqual(parsed["flows"], [])
        self.assertEqual(parsed["missing"], [])

    def test_several_flows_split_on_the_comma(self):
        flows = li.parse(_fixture("PPR-142"))["flows"]
        self.assertEqual(flows, ["D15"])
        many = next(i for i in _corpus() if i["identifier"] == "PPR-119")
        self.assertEqual(li.parse(many)["flows"], ["D10", "D12", "D15"])


class OnlyTheThreeKnownTableKeysAreReadTests(unittest.TestCase):
    """The prose contains `| ... | ... |` rows of its own -- UI paths like
    "| Producto -> Add or remove units |". Matching any row picked one up
    as the repo to route the work to."""

    def test_a_prose_table_row_is_not_mistaken_for_metadata(self):
        issue = dict(_fixture("PPR-142"))
        issue["description"] += "\n\n| Producto → Add or remove units | x |\n"
        parsed = li.parse(issue)
        self.assertEqual(parsed["repo"], "proxima-admin")
        self.assertEqual(parsed["foundBy"], "G-D15")


class ADisagreementIsReportedNotResolvedTests(unittest.TestCase):
    """The repo is recorded twice -- as a label and as a table row. They
    agree on all 50. If they ever stop, picking one silently lands a fix in
    the wrong repository."""

    def test_the_corpus_has_no_disagreement_today(self):
        for issue in _corpus():
            with self.subTest(issue=issue["identifier"]):
                self.assertEqual(li.parse(issue)["warnings"], [])

    def test_a_drift_is_surfaced(self):
        issue = dict(_fixture("PPR-86"))  # label proxima-api
        issue["description"] = issue["description"].replace(
            "| Repo dueño | `proxima-api` |", "| Repo dueño | `proxima-hub` |"
        )
        parsed = li.parse(issue)
        self.assertEqual(len(parsed["warnings"]), 1)
        self.assertIn("proxima-api", parsed["warnings"][0])
        self.assertIn("proxima-hub", parsed["warnings"][0])
        # The label still wins, because it is the field the board validates.
        self.assertEqual(parsed["repo"], "proxima-api")


class NothingRaisesTests(unittest.TestCase):
    def test_an_empty_issue_names_what_is_missing(self):
        self.assertEqual(set(li.parse({})["missing"]), set(li.FRAME_FIELDS))

    def test_none_is_survivable(self):
        self.assertEqual(set(li.parse(None)["missing"]), set(li.FRAME_FIELDS))

    def test_a_description_of_none_does_not_crash(self):
        parsed = li.parse({"identifier": "X-1", "title": "t", "description": None})
        self.assertEqual(parsed["body"], "")
        self.assertIn("body", parsed["missing"])


if __name__ == "__main__":
    unittest.main()
