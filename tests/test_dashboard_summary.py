"""Tests for T001 (the plain-language project summary): each project section
opens with a sentence stating how the latest run compares to the baseline on
the three cost-predicting metrics, or states its own LIMIT and shows no
comparison when the ledger does not prove one.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import dashboard as dash  # noqa: E402

from tests.test_dashboard import LedgerFixture, row  # noqa: E402

_COMPARATIVE_WORDS = ("%", "better", "worse", "fewer", "more ", "less ")


def _assert_no_comparative_wording(testcase: unittest.TestCase, text: str) -> None:
    for word in _COMPARATIVE_WORDS:
        testcase.assertNotIn(word, text, f"unexpected comparative wording {word!r} in LIMIT summary: {text!r}")


class TestDirectionHelper(unittest.TestCase):
    def test_none_delta_is_no_comparison(self):
        self.assertIsNone(dash.direction("turns_per_agent", None))

    def test_zero_delta_is_unchanged_regardless_of_table(self):
        self.assertEqual(dash.direction("turns_per_agent", 0), "unchanged")
        self.assertEqual(dash.direction("ctx_max", 0.0), "unchanged")

    def test_lower_is_better_metrics_negative_delta_is_better(self):
        for key in ("turns_per_agent", "ctx_max", "opus_share"):
            self.assertEqual(dash.direction(key, -12.3), "better")
            self.assertEqual(dash.direction(key, 12.3), "worse")

    def test_opus_share_down_is_better(self):
        # Named explicitly per the acceptance criterion.
        self.assertEqual(dash.direction("opus_share", -5.0), "better")
        self.assertEqual(dash.direction("opus_share", 5.0), "worse")

    def test_sign_inversion_for_a_higher_is_better_metric(self):
        # A helper that hardcoded "negative is better" for every metric would
        # get this backwards. `direction` takes the lookup table as data, so
        # a metric where a higher value is the win can be proven here without
        # touching module state or any other call site.
        table = {"cache_hit_rate": False}
        self.assertEqual(dash.direction("cache_hit_rate", 10.0, table), "better")
        self.assertEqual(dash.direction("cache_hit_rate", -10.0, table), "worse")
        self.assertEqual(dash.direction("cache_hit_rate", 0.0, table), "unchanged")

    def test_default_table_is_metric_direction(self):
        for key, lower_is_better in dash.METRIC_DIRECTION.items():
            expected = "worse" if lower_is_better else "better"
            self.assertEqual(dash.direction(key, 1.0), expected)


class TestMetricPhraseGrammar(unittest.TestCase):
    """`_metric_phrase` must read as a grammatical English phrase in every
    branch, for every metric -- not just the non-zero, non-None deltas the
    ledger-backed tests above happen to construct. `opus_share`'s noun
    ("of the Opus quota") only composes after a more/less word; the pct==0
    and pct is None branches don't have one, so they need their own bare
    phrasing (see `_METRIC_WORDS`)."""

    def test_zero_delta_reads_as_a_grammatical_sentence_fragment(self):
        self.assertEqual(dash._metric_phrase("turns_per_agent", 0), "the same turns per agent")
        self.assertEqual(dash._metric_phrase("ctx_max", 0), "the same context per turn")
        self.assertEqual(dash._metric_phrase("opus_share", 0), "the same Opus quota use")
        for key in ("turns_per_agent", "ctx_max", "opus_share"):
            self.assertTrue(dash._metric_phrase(key, 0).startswith("the same "))

    def test_none_delta_reads_as_a_grammatical_sentence_fragment(self):
        self.assertEqual(dash._metric_phrase("turns_per_agent", None), "no comparable turns per agent recorded")
        self.assertEqual(dash._metric_phrase("ctx_max", None), "no comparable context per turn recorded")
        self.assertEqual(dash._metric_phrase("opus_share", None), "no comparable Opus quota use recorded")

    def test_nonzero_delta_still_uses_the_comparative_noun(self):
        # The comparative noun ("of the Opus quota") is only correct beside
        # a more/less word -- confirm the fix didn't remove that branch.
        self.assertEqual(dash._metric_phrase("opus_share", 12.0), "12.0% more of the Opus quota")
        self.assertEqual(dash._metric_phrase("opus_share", -12.0), "12.0% less of the Opus quota")

    def test_rendered_summary_is_grammatical_when_opus_share_is_flat(self):
        # Reproduces the reported real-page sentence: opus_share unchanged
        # between baseline and latest run.
        metrics = [
            {"key": "turns_per_agent", "pct": -33.0, "direction": "better", "phrase": dash._metric_phrase("turns_per_agent", -33.0)},
            {"key": "ctx_max", "pct": 0.6, "direction": "worse", "phrase": dash._metric_phrase("ctx_max", 0.6)},
            {"key": "opus_share", "pct": 0, "direction": "unchanged", "phrase": dash._metric_phrase("opus_share", 0)},
        ]
        phrases = [f"{m['phrase']} ({m['direction']})" for m in metrics]
        sentence = "Since the baseline, this run took " + ", ".join(phrases[:-1]) + f", and {phrases[-1]}."
        self.assertIn("the same Opus quota use (unchanged)", sentence)
        self.assertNotIn("the same of the Opus quota", sentence)

    def test_rendered_summary_is_grammatical_when_baseline_opus_share_is_zero(self):
        # Reproduces the reported real-page sentence: baseline's opus_share
        # is 0/missing, so `token_report._pct_change` returns None.
        phrase = dash._metric_phrase("opus_share", None)
        sentence = f"Since the baseline, this run took {phrase} (n/a)."
        self.assertIn("no comparable Opus quota use recorded", sentence)
        self.assertNotIn("no comparable of the Opus quota recorded", sentence)


class TestProjectSummaryComparison(LedgerFixture):
    def test_summary_states_the_three_metrics_with_percentage_and_words(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]

        self.assertIsNone(summary["limit"])
        text = summary["text"]
        # Percentage always sits beside the word that gives it meaning --
        # never a bare signed number.
        self.assertIn("50.0% fewer turns per agent", text)
        self.assertIn("50.0% less context per turn", text)
        self.assertIn("50.0% less of the Opus quota", text)
        self.assertNotIn("-50.0%", text)
        self.assertNotIn("+50.0%", text)
        # What the numbers are FOR, in one sentence.
        self.assertIn("turns", text.lower())
        self.assertIn("context", text.lower())
        self.assertIn("predict", text.lower())

    def test_direction_word_accompanies_each_metric(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]
        self.assertEqual([m["direction"] for m in summary["metrics"]], ["worse", "worse", "worse"])
        self.assertIn("worse", summary["text"])


class TestProjectSummaryLimits(LedgerFixture):
    """D2: no baseline, only one run, or a baseline that does not apply to the
    latest run shown each produce a stated LIMIT and no comparison."""

    def test_no_baseline_marked(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a"),
        ])
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]
        self.assertIsNotNone(summary["limit"])
        self.assertIsNone(summary["comparison"])
        self.assertEqual(summary["metrics"], [])
        self.assertIn("no baseline", summary["limit"])
        _assert_no_comparative_wording(self, summary["text"])

    def test_only_one_run(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]
        self.assertIsNotNone(summary["limit"])
        self.assertIsNone(summary["comparison"])
        self.assertEqual(summary["metrics"], [])
        self.assertIn("only one run", summary["limit"])
        _assert_no_comparative_wording(self, summary["text"])

    def test_baseline_does_not_apply_to_the_latest_run_shown(self):
        # The baseline is not older than either run shown -- it was marked
        # using a timestamp at least as new as the latest run in the
        # project, so there is no later run to hold it up against.
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a"),
        ])
        self._write_baseline({"wf_id": "wf_9", "ts": "2026-01-03T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]
        self.assertIsNotNone(summary["limit"])
        self.assertIsNone(summary["comparison"])
        self.assertEqual(summary["metrics"], [])
        _assert_no_comparative_wording(self, summary["text"])

    def test_why_sentence_still_present_in_a_limit_summary(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        view = dash.build_view(self.ledger_path, self.baseline_path)
        summary = view["projects"][0]["summary"]
        self.assertIn("predict", summary["text"].lower())


class TestRenderHtmlSummary(LedgerFixture):
    def test_summary_paragraph_precedes_the_table(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        html_out = dash.render_html(view)
        summary_pos = html_out.index('class="summary"')
        table_pos = html_out.index("<table>")
        self.assertLess(summary_pos, table_pos)
        self.assertIn("fewer turns per agent", html_out)


class TestJsonKeysPinned(LedgerFixture):
    """`rein dashboard --json` keeps every key it has today; `summary` is
    added alongside, not in place of anything."""

    PRE_EXISTING_TOP_LEVEL_KEYS = {"message", "projects"}
    PRE_EXISTING_PROJECT_KEYS = {"project", "root", "models", "runs"}
    PRE_EXISTING_RUN_KEYS = {
        "wf_id", "ts", "turns", "turns_per_agent", "ctx_max", "opus_share",
        "totals", "total", "agents", "is_baseline", "deltas",
    }

    def test_pre_existing_keys_survive_with_summary_added_alongside(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)

        self.assertTrue(self.PRE_EXISTING_TOP_LEVEL_KEYS.issubset(view.keys()))
        project = view["projects"][0]
        self.assertTrue(self.PRE_EXISTING_PROJECT_KEYS.issubset(project.keys()))
        self.assertIn("summary", project)
        run = project["runs"][0]
        self.assertTrue(self.PRE_EXISTING_RUN_KEYS.issubset(run.keys()))


if __name__ == "__main__":
    unittest.main()
