"""Tests for the baseline marker (`rein baseline mark|show|clear`).

All of these run against a temporary ledger and a temporary baseline path --
never `~/.claude/rein/` -- so the suite cannot pollute or depend on real state.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import token_report as tr  # noqa: E402


def row(
    wf_id: str,
    ts: str,
    turns_per_agent: float = 5.0,
    ctx_max: int = 1000,
    opus_share: float = 10.0,
    project: str = "proj-A",
) -> dict:
    return {
        "wf_id": wf_id,
        "ts": ts,
        "project": project,
        "turns": int(turns_per_agent * 2),
        "turns_per_agent": turns_per_agent,
        "ctx_max": ctx_max,
        "opus_share": opus_share,
    }


class BaselineFixture(unittest.TestCase):
    """A temp ledger.jsonl and a temp baseline.json, never the real ~/.claude/rein."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = os.path.join(self.tmp.name, "runs.jsonl")
        self.baseline_path = os.path.join(self.tmp.name, "baseline.json")

    def _write_ledger(self, rows: list[dict]) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")


class TestMark(BaselineFixture):
    def test_mark_by_explicit_id_records_it_without_touching_ledger(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z"), row("wf_2", "2026-01-02T00:00:00Z")])
        with open(self.ledger_path, encoding="utf-8") as fh:
            before = fh.read()

        record = tr.mark_baseline("wf_1", self.ledger_path, self.baseline_path)

        self.assertEqual(record["wf_id"], "wf_1")
        self.assertTrue(os.path.exists(self.baseline_path))
        stored = tr.read_baseline(self.baseline_path)
        self.assertEqual(stored["wf_id"], "wf_1")
        # Ledger must be byte-for-byte untouched by marking a baseline.
        with open(self.ledger_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_mark_with_no_argument_picks_most_recent(self):
        self._write_ledger(
            [row("wf_old", "2026-01-01T00:00:00Z"), row("wf_new", "2026-01-03T00:00:00Z"), row("wf_mid", "2026-01-02T00:00:00Z")]
        )
        record = tr.mark_baseline(None, self.ledger_path, self.baseline_path)
        self.assertEqual(record["wf_id"], "wf_new")

    def test_mark_unknown_id_fails_and_names_the_id(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        with self.assertRaises(ValueError) as ctx:
            tr.mark_baseline("wf_does_not_exist", self.ledger_path, self.baseline_path)
        self.assertIn("wf_does_not_exist", str(ctx.exception))
        self.assertFalse(os.path.exists(self.baseline_path), "must not store a dangling reference")

    def test_mark_against_empty_ledger_fails(self):
        with self.assertRaises(ValueError):
            tr.mark_baseline(None, self.ledger_path, self.baseline_path)
        self.assertFalse(os.path.exists(self.baseline_path))


class TestShow(BaselineFixture):
    def test_show_with_no_baseline_is_a_clear_message(self):
        text = tr.render_baseline(tr.read_baseline(self.baseline_path))
        self.assertIn("no baseline", text.lower())

    def test_show_prints_the_key_metrics(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", turns_per_agent=7.5, ctx_max=12345, opus_share=42.0)])
        tr.mark_baseline("wf_1", self.ledger_path, self.baseline_path)

        text = tr.render_baseline(tr.read_baseline(self.baseline_path))
        self.assertIn("wf_1", text)
        self.assertIn("7.5", text)
        self.assertIn("12,345", text)
        self.assertIn("42.0", text)


class TestLedgerDelta(BaselineFixture):
    def test_without_baseline_shows_no_delta_and_keeps_the_existing_line(self):
        rows = [row("wf_1", "2026-01-01T00:00:00Z"), row("wf_2", "2026-01-02T00:00:00Z")]
        self._write_ledger(rows)

        text = tr.render_ledger(rows, self.ledger_path, baseline=None)

        self.assertNotIn("vs baseline", text)
        self.assertNotIn("better", text)
        self.assertNotIn("worse", text)
        self.assertIn("Savings require a marked baseline -- without one this is a trend, not a saving.", text)

    def test_with_baseline_marks_it_and_shows_signed_delta_for_later_runs(self):
        rows = [
            row("wf_base", "2026-01-02T00:00:00Z", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_after", "2026-01-03T00:00:00Z", turns_per_agent=8.0, ctx_max=1000, opus_share=10.0),
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)

        self.assertIn("[baseline]", text)
        # 8 vs 10 -> -20%, 1000 vs 2000 -> -50%, 10 vs 20 -> -50%; all improvements.
        self.assertIn("turns/agent -20.0% better", text)
        self.assertIn("ctx_max -50.0% better", text)
        self.assertIn("opus_share -50.0% better", text)

    def test_run_before_the_baseline_gets_no_delta(self):
        rows = [
            row("wf_earlier", "2026-01-01T00:00:00Z", turns_per_agent=20.0),
            row("wf_base", "2026-01-02T00:00:00Z", turns_per_agent=10.0),
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)
        lines = text.splitlines()
        earlier_idx = next(i for i, ln in enumerate(lines) if "wf_earlier" in ln)
        # The line for the earlier run carries no "vs baseline" delta of its own.
        self.assertNotIn("vs baseline", lines[earlier_idx])

    def test_a_worse_metric_is_labelled_worse_not_just_positive(self):
        rows = [
            row("wf_base", "2026-01-01T00:00:00Z", turns_per_agent=5.0),
            row("wf_after", "2026-01-02T00:00:00Z", turns_per_agent=10.0),
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)
        self.assertIn("turns/agent +100.0% worse", text)

    def test_baseline_from_one_project_never_decorates_another_project(self):
        # Reproduction from the review finding: a baseline marked in proj-A must
        # not fabricate a delta for an unrelated run in proj-B just because it
        # comes later by timestamp.
        rows = [
            row("wfA", "2026-01-01T00:00:00Z", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0, project="proj-A"),
            row("wfB", "2026-01-02T00:00:00Z", turns_per_agent=40.0, ctx_max=90000, opus_share=99.0, project="proj-B"),
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wfA", self.ledger_path, self.baseline_path)
        self.assertEqual(baseline["project"], "proj-A")

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)
        lines = text.splitlines()
        b_idx = next(i for i, ln in enumerate(lines) if "wfB" in ln)
        self.assertNotIn("vs baseline", lines[b_idx])
        # And the fabricated numbers from the finding must not appear anywhere.
        self.assertNotIn("+300.0%", text)
        self.assertNotIn("+4400.0%", text)
        self.assertNotIn("+395.0%", text)

    def test_baseline_missing_project_field_suppresses_all_deltas(self):
        # A baseline.json written before `project` was tracked. Must not fall
        # back to comparing across projects.
        rows = [row("wf_base", "2026-01-01T00:00:00Z"), row("wf_after", "2026-01-02T00:00:00Z")]
        self._write_ledger(rows)
        stale_baseline = {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z"}  # no "project" key

        text = tr.render_ledger(rows, self.ledger_path, baseline=stale_baseline)

        self.assertNotIn("vs baseline", text)
        self.assertIn("rein baseline mark", text)

    def test_baseline_older_than_the_displayed_window_still_shows_the_tag(self):
        rows = [row("wf_base", "2026-01-01T00:00:00Z", turns_per_agent=5.0)]
        rows += [row(f"wf_{i}", f"2026-01-{i:02d}T00:00:00Z", turns_per_agent=5.0) for i in range(2, 9)]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)

        self.assertIn("[baseline]", text)
        self.assertIn("wf_base", text)


class TestReadBaselineCorrupt(BaselineFixture):
    def test_corrupt_file_raises_distinctly_from_missing_file(self):
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")

        with self.assertRaises(tr.BaselineCorruptError) as ctx:
            tr.read_baseline(self.baseline_path)
        self.assertIn(self.baseline_path, str(ctx.exception))

        # A genuinely absent file must still read as None, not raise.
        os.remove(self.baseline_path)
        self.assertIsNone(tr.read_baseline(self.baseline_path))


class TestMissingMetric(BaselineFixture):
    def test_render_baseline_with_missing_ctx_max_does_not_crash(self):
        # Reproduction from the review finding: mark_baseline snapshots a KEY
        # with value None for any BASELINE_FIELDS the ledger row lacks -- must
        # not raise TypeError when formatting.
        baseline = {"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "p", "turns": 10, "turns_per_agent": 5.0, "ctx_max": None, "opus_share": None}
        text = tr.render_baseline(baseline)
        self.assertIsInstance(text, str)
        self.assertIn("wf_1", text)
        self.assertIn("n/a", text)

    def test_row_missing_metric_reports_na_not_fabricated_delta(self):
        # A later row missing ctx_max must not render as "-100.0% better".
        rows = [
            {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "project": "proj-A", "turns": 10, "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0},
            {"wf_id": "wf_after", "ts": "2026-01-02T00:00:00Z", "project": "proj-A", "turns": 10, "turns_per_agent": 5.0, "opus_share": 10.0},  # no ctx_max
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)

        self.assertNotIn("-100.0%", text)
        self.assertIn("ctx_max n/a (row missing metric)", text)


class TestBaselineShowsProject(BaselineFixture):
    def test_render_baseline_includes_the_project(self):
        rows = [row("wf_1", "2026-01-01T00:00:00Z", project="proj-A")]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_1", self.ledger_path, self.baseline_path)

        text = tr.render_baseline(baseline)
        self.assertIn("proj-A", text)


class TestReadBaselineWrongShape(BaselineFixture):
    def test_wrong_shape_raises_corrupt_error(self):
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            fh.write("[]")
        with self.assertRaises(tr.BaselineCorruptError):
            tr.read_baseline(self.baseline_path)

    def test_json_null_still_reads_as_no_baseline(self):
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            fh.write("null")
        self.assertIsNone(tr.read_baseline(self.baseline_path))


class TestZeroBaselineMetric(BaselineFixture):
    def test_zero_baseline_metric_explains_itself_instead_of_bare_na(self):
        rows = [
            row("wf_base", "2026-01-01T00:00:00Z", opus_share=0.0),
            row("wf_after", "2026-01-02T00:00:00Z", opus_share=5.0),
        ]
        self._write_ledger(rows)
        baseline = tr.mark_baseline("wf_base", self.ledger_path, self.baseline_path)

        text = tr.render_ledger(rows, self.ledger_path, baseline=baseline)
        self.assertIn("opus_share n/a (baseline 0)", text)


class TestClear(BaselineFixture):
    def test_clear_removes_the_marker(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        tr.mark_baseline("wf_1", self.ledger_path, self.baseline_path)
        self.assertTrue(os.path.exists(self.baseline_path))

        cleared = tr.clear_baseline(self.baseline_path)
        self.assertTrue(cleared)
        self.assertFalse(os.path.exists(self.baseline_path))
        self.assertIsNone(tr.read_baseline(self.baseline_path))

    def test_clear_with_no_baseline_is_a_no_op(self):
        cleared = tr.clear_baseline(self.baseline_path)
        self.assertFalse(cleared)


if __name__ == "__main__":
    unittest.main()
