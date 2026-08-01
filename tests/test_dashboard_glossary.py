"""Tests for T002 (every number says what it means):

  1. a single GLOSSARY defines every metric the page renders, and a missing
     entry is named in the failure, not swallowed;
  2. each metric header carries a '?' reachable by keyboard focus, exposed
     as an accessible description (aria-describedby), never a hover-only
     `title`;
  3. the delta columns state "negative = better" in the same header cell as
     the delta values they label, not merely somewhere else on the page;
  4. the baseline is identified where it is used: which run, when it was
     marked, or -- for a project with none -- what to run to mark one;
  5. the rendered page stays offline and self-contained: no external
     src/href, no @import.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import dashboard as dash  # noqa: E402

from tests.test_dashboard import LedgerFixture, row  # noqa: E402

# The authoritative list of metric keys this page renders anywhere: the
# per-run table (turns_per_agent, ctx_max, opus_share and their deltas) and
# the per-agent detail table (turns, total, ctx_max). Used only by the tests
# below that assert presence of *known* buttons -- the actual completeness
# ratchet (`TestGlossaryCompleteness`) derives its list from render output
# instead of trusting this tuple, so a new metric column added without a
# glossary call fails there even if nobody updates this list.
RENDERED_METRIC_KEYS = (
    "turns",
    "turns_per_agent",
    "ctx_max",
    "opus_share",
    "total",
    "delta_turns_per_agent",
    "delta_ctx_max",
    "delta_opus_share",
)

# `<th>` labels that are structural (row/column identifiers), not metrics --
# they have nothing numeric to explain, so `TestGlossaryCompleteness` exempts
# exactly these from needing a glossary button. Any other <th> with no
# `aria-describedby` is a metric that shipped unexplained.
_NON_METRIC_HEADER_LABELS = {"run", "agents", "file", "model"}


def _strip_tags(cell_html: str) -> str:
    return re.sub(r"<[^>]+>", "", cell_html).strip()


def _th_cells(html_out: str) -> list[str]:
    """Every `<th>...</th>` cell's inner HTML, from every table on the page
    (the per-run table and every per-agent detail table)."""
    return re.findall(r"<th>(.*?)</th>", html_out, re.DOTALL)


def _full_view(with_baseline: bool = True) -> dict:
    """A project with a baseline run plus a later, delta-bearing run --
    everything `_project_section` can render, in one shot. With
    `with_baseline=False`, neither run is flagged `is_baseline` and there
    are no deltas -- a project with no baseline marked at all, matching
    what `_baseline_identity_text(None, ...)` describes."""
    baseline_run = {
        "wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 10.0,
        "ctx_max": 2000, "opus_share": 20.0, "is_baseline": with_baseline, "deltas": None,
        "agents": [{"file": "a.jsonl", "model": "sonnet-5", "turns": 5, "total": 500, "ctx_max": 2000}],
    }
    later_run = {
        "wf_id": "wf_later", "ts": "2026-01-02T00:00:00Z", "turns_per_agent": 5.0,
        "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False,
        "deltas": {"turns_per_agent": -50.0, "ctx_max": -50.0, "opus_share": -50.0} if with_baseline else None,
        "agents": [],
    }
    return {"message": "", "projects": [{
        "project": "proj-full",
        "root": None,
        "models": {},
        "runs": [baseline_run, later_run],
        "summary": {"text": "irrelevant to this test"},
        "baseline_info": dash._baseline_identity_text(
            {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "project": "proj-full"} if with_baseline else None,
            "proj-full",
        ),
    }]}


class TestGlossaryCompleteness(unittest.TestCase):
    """D3: one glossary, one source -- every rendered metric key has an
    entry, and a missing one is named, not silently skipped.

    This is a ratchet against the page's actual render output, not a
    hand-maintained list: it fails the moment a new metric `<th>` ships
    without a call to `_glossary_html`, which a hand-maintained
    `RENDERED_METRIC_KEYS` tuple cannot catch (nothing derives it from what
    `_project_section`/`_agents_detail` actually render).
    """

    def test_every_rendered_th_has_a_glossary_button_or_is_structural(self):
        html_out = dash.render_html(_full_view())
        cells = _th_cells(html_out)
        self.assertGreater(len(cells), 0, "sanity: the fixture must render at least one <th>")
        for cell in cells:
            # ids are "glossary-<key>-<uid>" (uid is digits, see
            # `_glossary_html`); keys themselves only ever use underscores.
            match = re.search(r'aria-describedby="glossary-([a-zA-Z0-9_]+)-\d+"', cell)
            if match is None:
                label = _strip_tags(cell)
                self.assertIn(
                    label, _NON_METRIC_HEADER_LABELS,
                    f"<th> {label!r} has no glossary button and is not a known structural column -- "
                    "a metric shipped unexplained (D3)",
                )
                continue
            key = match.group(1)
            self.assertIn(key, dash.GLOSSARY, f"<th> references a glossary key with no GLOSSARY entry: {key!r}")

    def test_missing_glossary_entry_is_named_in_the_failure(self):
        # Prove the completeness check itself -- not unittest's own
        # assertIn message -- names the key that GLOSSARY is missing: patch
        # a real entry out of the actual `dash.GLOSSARY` and confirm the
        # product's own render path names it.
        with mock.patch.dict(dash.GLOSSARY):
            del dash.GLOSSARY["ctx_max"]
            with self.assertRaises(KeyError) as ctx:
                dash.render_html(_full_view())
        self.assertIn("ctx_max", str(ctx.exception))

    def test_every_entry_has_a_one_line_meaning_and_a_one_line_why(self):
        for key, entry in dash.GLOSSARY.items():
            self.assertEqual(set(entry), {"meaning", "why"}, f"GLOSSARY[{key!r}] must have exactly meaning/why")
            for field in ("meaning", "why"):
                text = entry[field]
                self.assertTrue(text, f"GLOSSARY[{key!r}][{field!r}] is empty")
                self.assertNotIn("\n", text, f"GLOSSARY[{key!r}][{field!r}] is not one line")

    def test_glossary_html_raises_naming_the_key_for_an_unknown_metric(self):
        with self.assertRaises(KeyError):
            dash._glossary_html("not_a_real_metric")


class TestGlossaryAffordanceIsAccessible(unittest.TestCase):
    """D4: reachable by keyboard focus, exposed as an accessible
    description -- never a hover-only `title`."""

    def setUp(self):
        self.html_out = dash.render_html(_full_view())

    def test_every_metric_header_carries_a_glossary_button(self):
        # ids are disambiguated per occurrence ("glossary-<key>-<uid>", see
        # `_glossary_html`), so match the key as a prefix rather than the
        # whole id.
        for key in RENDERED_METRIC_KEYS:
            self.assertRegex(
                self.html_out, rf'aria-describedby="glossary-{re.escape(key)}-\d+"',
                f"no glossary button wired to metric {key!r}",
            )

    def test_every_glossary_button_is_a_native_focusable_button(self):
        # A native <button> is reachable by Tab/Shift-Tab with no explicit
        # tabindex and no JavaScript needed to make it focusable. `ctx_max`
        # renders twice (the per-run table and the per-agent detail table),
        # so there are more buttons than unique metric keys -- but never
        # fewer, and never anything but a plain focusable <button>.
        buttons = re.findall(r'<button type="button" class="glossary-toggle"[^>]*>', self.html_out)
        self.assertGreaterEqual(len(buttons), len(RENDERED_METRIC_KEYS))
        for button in buttons:
            self.assertNotIn("tabindex=\"-1\"", button)

    def test_every_aria_describedby_target_exists_and_holds_glossary_text(self):
        for key in RENDERED_METRIC_KEYS:
            entry = dash.GLOSSARY[key]
            match = re.search(
                rf'<span id="glossary-{re.escape(key)}-\d+"[^>]*>([^<]*)</span>', self.html_out,
            )
            self.assertIsNotNone(match, f"no description element for metric {key!r}")
            self.assertIn(entry["meaning"], match.group(1))
            self.assertIn(entry["why"], match.group(1))

    def test_no_hover_only_title_attribute_anywhere(self):
        self.assertNotIn(" title=", self.html_out, "a hover-only title is not an accessible explanation")


class TestGlossaryIdsAreUniqueAcrossThePage(unittest.TestCase):
    """Every occurrence of a metric header (the per-run table's header,
    repeated per project, and the per-agent detail table, repeated per run)
    must get its own id -- duplicate ids are invalid HTML, and every
    `aria-describedby` past the first would resolve to whichever span
    happens to sit first in the DOM rather than the one beside it."""

    def _multi_project_multi_run_view(self, n_projects: int, n_runs: int) -> dict:
        projects = []
        for p in range(n_projects):
            runs = []
            for r in range(n_runs):
                runs.append({
                    "wf_id": f"wf_p{p}_r{r}", "ts": f"2026-01-{r + 1:02d}T00:00:00Z",
                    "turns_per_agent": 5.0 + r, "ctx_max": 1000 + r, "opus_share": 10.0,
                    "is_baseline": r == 0, "deltas": {"turns_per_agent": -1.0, "ctx_max": -1.0, "opus_share": 0.0} if r > 0 else None,
                    "agents": [{"file": f"a{r}.jsonl", "model": "sonnet-5", "turns": 5, "total": 500, "ctx_max": 1000}],
                })
            projects.append({
                "project": f"proj-{p}", "root": None, "models": {}, "runs": runs,
                "summary": {"text": "irrelevant"}, "baseline_info": "irrelevant",
            })
        return {"message": "", "projects": projects}

    def test_every_id_attribute_in_the_rendered_page_is_unique(self):
        # Mirrors the reviewer-reported scenario: multiple projects, many
        # runs each -- the shape that previously produced 59 id attributes
        # with only 5 distinct values.
        view = self._multi_project_multi_run_view(n_projects=2, n_runs=16)
        html_out = dash.render_html(view)
        ids = re.findall(r'\bid="([^"]+)"', html_out)
        self.assertGreater(len(ids), 5, "sanity: the fixture must render many id attributes")
        duplicates = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(duplicates, set(), f"duplicate id attribute(s) in rendered page: {sorted(duplicates)}")


class TestDeltaColumnsStateNegativeIsBetter(unittest.TestCase):
    """Criterion 3: proximity, not mere presence -- the note must sit inside
    the same header cell as the delta column it describes."""

    def test_negative_is_better_appears_inside_each_delta_header_cell(self):
        html_out = dash.render_html(_full_view(with_baseline=True))
        all_headers = re.findall(r"<th>(.*?)</th>", html_out, re.DOTALL)
        delta_headers = [cell for cell in all_headers if cell.startswith("Δ")]
        self.assertEqual(len(delta_headers), 3, "expected exactly the three Δ column headers")
        for cell in delta_headers:
            self.assertIn("negative = better", cell, f"missing proximate note in delta header: {cell!r}")

    def test_negative_is_better_is_absent_when_there_are_no_deltas_to_label(self):
        # A project with no baseline shows no Δ columns at all (D4/T001) --
        # nothing should claim "negative = better" about a column that
        # isn't there.
        html_out = dash.render_html(_full_view(with_baseline=False))
        self.assertNotIn("negative = better", html_out)


class TestBaselineIdentifiedWhereItIsUsed(unittest.TestCase):
    """Criterion 4: which run, when it was marked -- or, for none, what to
    run to mark one."""

    def test_applicable_baseline_states_which_run_and_when(self):
        text = dash._baseline_identity_text(
            {"wf_id": "wf_base_123", "ts": "2026-01-01T00:00:00Z", "project": "proj-a"}, "proj-a",
        )
        self.assertIn("wf_base_123", text)
        self.assertIn("2026-01-01T00:00:00Z", text)

    def test_no_baseline_at_all_says_what_to_run(self):
        text = dash._baseline_identity_text(None, "proj-a")
        self.assertIn("rein baseline mark", text)

    def test_baseline_for_a_different_project_is_not_claimed_as_this_ones(self):
        text = dash._baseline_identity_text(
            {"wf_id": "wf_other", "ts": "2026-01-01T00:00:00Z", "project": "proj-other"}, "proj-a",
        )
        self.assertNotIn("wf_other", text)
        self.assertIn("rein baseline mark", text)

    def test_baseline_predating_project_tracking_is_not_claimed_as_this_ones(self):
        # Mirrors `_run_view`'s own `baseline_stale` handling: a baseline
        # marked before `project` was tracked has no `project` field at all.
        text = dash._baseline_identity_text({"wf_id": "wf_old", "ts": "2025-01-01T00:00:00Z"}, "proj-a")
        self.assertIn("rein baseline mark", text)

    def test_rendered_page_shows_the_baseline_identity_near_its_table(self):
        html_out = dash.render_html(_full_view(with_baseline=True))
        self.assertIn("wf_base", html_out)
        self.assertIn('class="baseline-info"', html_out)

    def test_rendered_page_with_no_baseline_tells_the_user_what_to_run(self):
        view = _full_view(with_baseline=False)
        view["projects"][0]["baseline_info"] = dash._baseline_identity_text(None, "proj-full")
        html_out = dash.render_html(view)
        self.assertIn("rein baseline mark", html_out)


class TestRenderedPageStaysSelfContained(LedgerFixture):
    """Criterion 5: no external host, no @import -- including the new
    glossary/baseline markup this task adds."""

    def test_no_external_src_or_href_and_no_at_import(self):
        self._write_ledger([
            row("wf_a", "2026-01-01T00:00:00Z", project="proj-x", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_b", "2026-01-02T00:00:00Z", project="proj-x", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_a", "ts": "2026-01-01T00:00:00Z", "project": "proj-x",
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        html_out = dash.render_html(view)

        self.assertNotIn("@import", html_out)
        for value in re.findall(r'\b(?:src|href)="([^"]*)"', html_out):
            self.assertFalse(
                value.startswith(("http://", "https://", "//")),
                f"external resource reference found: {value!r}",
            )


if __name__ == "__main__":
    unittest.main()
