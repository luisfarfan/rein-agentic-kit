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


def row(wf_id: str, ts: str, turns_per_agent: float = 5.0, ctx_max: int = 1000, opus_share: float = 10.0) -> dict:
    return {
        "wf_id": wf_id,
        "ts": ts,
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
