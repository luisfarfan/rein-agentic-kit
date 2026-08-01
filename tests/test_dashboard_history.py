"""Tests for T003 ("usage history: what was run, with what, and whether it is
improving"):

  1. per-skill invocation counts from events.jsonl, visually separated from
     run metrics and never summed into any run total or delta -- run rows
     stay byte-identical with and without an events file;
  2. each run row shows its `change` label beside the `wf_id`, and a run
     recorded before labels existed renders unlabelled, never as "None";
  3. the turns/agent trend is drawn as inline SVG, at least the last 10
     runs, baseline marked -- fewer than 3 runs states the reason instead;
  4. reading events is bounded to a named constant (D6) -- a file with far
     more than that many events neither crashes nor gets fully read;
  5. an absent/empty/corrupt events.jsonl still renders runs and summary
     intact, with the history section stating why it is empty.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import dashboard as dash  # noqa: E402
import events as ev  # noqa: E402

from tests.test_dashboard import LedgerFixture, row  # noqa: E402


def _event(name: str, project: str, ts: str = "2026-01-01T00:00:00Z") -> dict:
    return {"name": name, "ts": ts, "project": project}


class HistoryFixture(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")

    def _write_events(self, rows: list[dict]) -> None:
        with open(self.events_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")


class TestRunRowsUnaffectedByEvents(HistoryFixture):
    """AC1: run rows must be byte-identical with and without an events file."""

    def _run_table_body(self, html_out: str) -> str:
        # Anchored on the section boundary (the `<div class="history">` that
        # immediately follows the run table, see `_project_section`) rather
        # than a bare non-greedy `.*?`, which would stop at the FIRST nested
        # `</tbody></table>` -- the per-run `<table class="agents">` every
        # run row embeds by default (see `row()` in tests/test_dashboard.py)
        # -- and silently capture only a fragment of run row 1.
        match = re.search(
            r'<table><thead>.*?</thead><tbody>(.*?)</tbody></table>(?=<div class="history">)',
            html_out, re.DOTALL,
        )
        self.assertIsNotNone(match, "no run table found in rendered page")
        return match.group(1)

    def test_run_rows_byte_identical_with_and_without_events_file(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a"),
        ])

        missing_events_path = os.path.join(self.tmp.name, "does-not-exist.jsonl")
        view_without = dash.build_view(self.ledger_path, self.baseline_path, missing_events_path)
        html_without = dash.render_html(view_without)

        self._write_events([
            _event("plan", "proj-a"), _event("run", "proj-a"), _event("run", "proj-a"),
            _event("review", "proj-other"),
        ])
        view_with = dash.build_view(self.ledger_path, self.baseline_path, self.events_path)
        html_with = dash.render_html(view_with)

        body_without = self._run_table_body(html_without)
        body_with = self._run_table_body(html_with)
        # Sanity guard: if the regex above ever regresses back to truncating
        # at the first nested agents table, both captures would still be
        # equal (both truncated the same way) and the assertEqual below
        # would pass for the wrong reason. Confirm the second run row is
        # actually inside what got captured.
        self.assertIn("wf_2", body_without)
        self.assertIn("wf_2", body_with)

        self.assertEqual(
            body_without, body_with,
            "run rows must not change shape or content depending on events.jsonl",
        )
        self.assertEqual(
            view_without["projects"][0]["runs"], view_with["projects"][0]["runs"],
            "the view model's runs must not change shape or content depending on events.jsonl",
        )

    def test_skill_counts_never_appear_in_run_totals_or_deltas(self):
        proj_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj_dir, ignore_errors=True)
        project_key = dash._mangle_path(proj_dir)
        real_root = os.path.realpath(proj_dir)

        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project=project_key, turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_2", "2026-01-02T00:00:00Z", project=project_key, turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": project_key,
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        self._write_events([_event("plan", real_root)] * 7 + [_event("run", real_root)] * 3
                            + [_event("plan", "/some/other/unrelated/project")] * 5)

        view = dash.build_view(self.ledger_path, self.baseline_path, self.events_path)
        proj = view["projects"][0]

        self.assertEqual(proj["history"]["skill_counts"], {"plan": 7, "run": 3})
        later = next(r for r in proj["runs"] if r["wf_id"] == "wf_2")
        self.assertEqual(later["deltas"]["turns_per_agent"], -50.0, "skill counts must not perturb run deltas")
        self.assertNotIn("skill_counts", later)
        self.assertNotIn("history", later)


class TestChangeLabelBesideWfId(unittest.TestCase):
    def test_change_label_renders_beside_wf_id(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_labelled", "change": "the-dashboard-answers-the-question",
            "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None, "agents": [],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn("wf_labelled", html_out)
        self.assertIn("the-dashboard-answers-the-question", html_out)
        self.assertIn('class="change-label"', html_out)

    def test_run_recorded_before_labels_existed_renders_unlabelled_not_none(self):
        # No "change" key at all -- exactly what every pre-existing ledger
        # row looks like.
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_legacy",
            "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None, "agents": [],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn("wf_legacy", html_out)
        self.assertNotIn("None", html_out)
        self.assertNotIn('class="change-label"', html_out, "an unlabelled run must not render an empty label span")

    def test_run_view_normalizes_missing_change_to_empty_string_not_none(self):
        run = dash._run_view({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z"}, "proj-a", None)
        self.assertEqual(run["change"], "")

    def test_run_view_carries_a_recorded_change_label(self):
        run = dash._run_view({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "change": "my-change"}, "proj-a", None)
        self.assertEqual(run["change"], "my-change")


class TestTrendSvg(unittest.TestCase):
    def _runs(self, n: int, baseline_index: int | None = None) -> list[dict]:
        return [
            {
                "wf_id": f"wf_{i}", "ts": f"2026-01-{i + 1:02d}T00:00:00Z",
                "turns_per_agent": float(10 - i), "is_baseline": i == baseline_index,
            }
            for i in range(n)
        ]

    def test_fewer_than_three_runs_states_the_reason_not_a_two_point_line(self):
        result = dash._trend_svg(self._runs(2))
        self.assertIsNone(result["trend_svg"])
        self.assertIn("3", result["trend_note"])
        self.assertIn("2 run", result["trend_note"])

    def test_at_least_ten_runs_are_drawn_with_baseline_marked(self):
        runs = self._runs(12, baseline_index=4)
        result = dash._trend_svg(runs)
        self.assertIsNotNone(result["trend_svg"])
        self.assertIsNone(result["trend_note"])
        self.assertIn("<svg", result["trend_svg"])
        self.assertIn("trend-baseline", result["trend_svg"])
        points = re.search(r'<polyline points="([^"]*)"', result["trend_svg"]).group(1)
        self.assertGreaterEqual(len(points.split(" ")), 10, "must plot at least the last 10 runs")

    def test_no_external_asset_referenced(self):
        result = dash._trend_svg(self._runs(5))
        self.assertNotIn("http://", result["trend_svg"])
        self.assertNotIn("https://", result["trend_svg"])
        self.assertNotIn("<image", result["trend_svg"])

    def test_trend_note_rendered_when_svg_is_none(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None, "agents": [],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn('class="trend-note"', html_out)


class TestBoundedEventsRead(unittest.TestCase):
    """AC4: bounded to a named constant (D6) -- writing far more than that
    many events must neither crash nor require reading the whole file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")

    def test_max_events_read_is_a_named_constant(self):
        self.assertIsInstance(ev.MAX_EVENTS_READ, int)
        self.assertGreater(ev.MAX_EVENTS_READ, 0)

    def test_more_than_the_bound_does_not_crash_and_returns_at_most_the_bound(self):
        total = ev.MAX_EVENTS_READ * 5
        with open(self.events_path, "w", encoding="utf-8") as fh:
            for i in range(total):
                fh.write(json.dumps(_event("run", "proj-a", ts=f"n{i}")) + "\n")

        rows = ev.read_recent_events(self.events_path)  # must not raise
        self.assertLessEqual(len(rows), ev.MAX_EVENTS_READ)
        self.assertGreater(len(rows), 0)
        # The tail, not the head, is what a bounded read must keep.
        self.assertEqual(rows[-1]["ts"], f"n{total - 1}")

    def test_does_not_read_the_whole_file(self):
        total = ev.MAX_EVENTS_READ * 20
        with open(self.events_path, "w", encoding="utf-8") as fh:
            for i in range(total):
                fh.write(json.dumps(_event("run", "proj-a", ts=f"n{i}")) + "\n")
        file_size = os.path.getsize(self.events_path)

        real_open = open
        bytes_read = {"n": 0}

        def counting_open(file, mode="r", *args, **kwargs):
            fh = real_open(file, mode, *args, **kwargs)
            if file == self.events_path and "b" in mode:
                orig_read = fh.read

                def _counting_read(*a, **kw):
                    data = orig_read(*a, **kw)
                    bytes_read["n"] += len(data)
                    return data

                fh.read = _counting_read
            return fh

        with mock.patch("events.open", counting_open, create=True):
            rows = ev.read_recent_events(self.events_path)

        self.assertEqual(len(rows), ev.MAX_EVENTS_READ)
        self.assertLess(
            bytes_read["n"], file_size / 4,
            "a bounded read must not touch anywhere near the whole file",
        )

    def test_dashboard_build_view_survives_an_events_file_far_larger_than_the_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = os.path.join(tmp, "proj")
            os.makedirs(proj_dir)
            project_key = dash._mangle_path(proj_dir)
            real_root = os.path.realpath(proj_dir)

            ledger_path = os.path.join(tmp, "runs.jsonl")
            baseline_path = os.path.join(tmp, "baseline.json")
            events_path = os.path.join(tmp, "events.jsonl")
            with open(ledger_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(row("wf_1", "2026-01-01T00:00:00Z", project=project_key)) + "\n")
            with open(events_path, "w", encoding="utf-8") as fh:
                for i in range(ev.MAX_EVENTS_READ * 10):
                    fh.write(json.dumps(_event("run", real_root, ts=f"n{i}")) + "\n")

            view = dash.build_view(ledger_path, baseline_path, events_path)  # must not raise
            self.assertEqual(view["message"], "")
            counts = view["projects"][0]["history"]["skill_counts"]
            self.assertGreater(sum(counts.values()), 0, "sanity: the bounded window must still show real counts")
            self.assertLessEqual(sum(counts.values()), ev.MAX_EVENTS_READ)


class TestEventsFileAbsentEmptyOrCorrupt(HistoryFixture):
    """AC5: runs and summary stay intact; the history section always states
    why it is empty, never a traceback, never a silently missing section."""

    def test_absent_events_file_renders_runs_and_states_the_reason(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        missing = os.path.join(self.tmp.name, "nope.jsonl")
        view = dash.build_view(self.ledger_path, self.baseline_path, missing)  # must not raise
        proj = view["projects"][0]
        self.assertEqual(proj["history"]["skill_counts"], {})
        self.assertTrue(proj["history"]["events_note"])

        html_out = dash.render_html(view)
        self.assertIn("wf_1", html_out)
        self.assertIn('class="events-note"', html_out)

    def test_empty_events_file_renders_runs_and_states_the_reason(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        open(self.events_path, "w", encoding="utf-8").close()
        view = dash.build_view(self.ledger_path, self.baseline_path, self.events_path)  # must not raise
        proj = view["projects"][0]
        self.assertEqual(proj["history"]["skill_counts"], {})
        self.assertTrue(proj["history"]["events_note"])

    def test_corrupt_events_file_degrades_without_crashing_summary_or_runs(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        with open(self.events_path, "w", encoding="utf-8") as fh:
            fh.write("{ not json at all\n")
            fh.write("\xff\xfe garbage bytes not valid utf-8 on their own\n")
            fh.write('"a-json-string-not-an-object"\n')

        view = dash.build_view(self.ledger_path, self.baseline_path, self.events_path)  # must not raise
        self.assertEqual(view["message"], "")
        proj = view["projects"][0]
        self.assertEqual(len(proj["runs"]), 2)
        self.assertIsNotNone(proj["summary"])
        self.assertEqual(proj["history"]["skill_counts"], {})

    def test_events_path_that_is_a_directory_does_not_raise(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        os.makedirs(self.events_path)
        view = dash.build_view(self.ledger_path, self.baseline_path, self.events_path)  # must not raise
        self.assertEqual(view["projects"][0]["history"]["skill_counts"], {})


class TestHistorySectionStatesWhyItIsEmptyWithNoHistoryKey(unittest.TestCase):
    """AC5's "state why it is empty" applies wherever the section can render,
    not only through `build_view` (which always populates `history`).
    `_project_section` reads it as `project.get("history") or {}`, so a
    project dict with no "history" key at all -- reachable in-repo, not just
    hypothetical -- must still render a stated reason, never a heading over
    a blank `<p>`."""

    def _view_without_history_key(self) -> dict:
        return {"message": "", "projects": [{
            "project": "proj-no-history",
            "root": None,
            "models": {},
            "runs": [{
                "wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
                "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None, "agents": [],
            }],
            "summary": {"text": "irrelevant to this test"},
            "baseline_info": "irrelevant to this test",
            # deliberately no "history" key
        }]}

    def test_skill_counts_html_states_a_default_reason_for_no_history_key(self):
        html_out = dash.render_html(self._view_without_history_key())
        match = re.search(r'<p class="events-note">([^<]*)</p>', html_out)
        self.assertIsNotNone(match, "no events-note paragraph rendered")
        self.assertTrue(match.group(1).strip(), "events-note is blank -- does not state why the section is empty")

    def test_trend_html_states_a_default_reason_for_no_history_key(self):
        html_out = dash.render_html(self._view_without_history_key())
        match = re.search(r'<p class="trend-note">([^<]*)</p>', html_out)
        self.assertIsNotNone(match, "no trend-note paragraph rendered")
        self.assertTrue(match.group(1).strip(), "trend-note is blank -- does not state why the section is empty")

    def test_skill_counts_html_unit_falls_back_when_note_is_empty_string(self):
        out = dash._skill_counts_html({}, "")
        self.assertNotEqual(out, '<p class="events-note"></p>', "blank note with no stated reason")
        self.assertIn(dash._DEFAULT_EVENTS_NOTE, out)

    def test_trend_html_unit_falls_back_when_note_is_none(self):
        out = dash._trend_html(None, None)
        self.assertNotEqual(out, '<p class="trend-note"></p>', "blank note with no stated reason")
        self.assertIn(dash._DEFAULT_TREND_NOTE, out)


if __name__ == "__main__":
    unittest.main()
