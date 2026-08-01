"""Tests for T001 -- a run records how long it took, and whether anything
overlapped.

D1: overlap is measured from clocks (each agent transcript's own ISO
`timestamp` field), never claimed from a status flag.
D2: absent is absent -- a run whose timestamps cannot be read (missing,
unparseable, or out-of-order) records NO duration fields, never a fabricated
zero or a negative span.
D3: the 14 rows already written stay valid and readable -- new fields are
added, never required.
D4: anything the dashboard shows needs a GLOSSARY entry.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import dashboard as dash  # noqa: E402
import token_report as tr  # noqa: E402


def turn(model: str = "claude-sonnet-5", ts: str | None = None, out: int = 1) -> str:
    """One transcript line. `ts` (when given) is the record's own top-level
    `timestamp` field -- exactly where Claude Code's real transcripts carry
    it, sibling to `message`, not nested inside it."""
    rec = {"message": {"model": model, "usage": {"cache_read_input_tokens": 0, "output_tokens": out}}}
    if ts is not None:
        rec["timestamp"] = ts
    return json.dumps(rec)


class Run:
    """A fake workflow run laid out exactly like Claude Code's transcript tree
    (mirrors `test_token_report.Run`)."""

    def __init__(self, agents: dict[str, list[str]], wf_id: str = "wf_test123"):
        self.agents, self.wf_id = agents, wf_id

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "projects", "-proj", "sess-1", "subagents", "workflows", self.wf_id)
        os.makedirs(self.dir)
        for name, lines in self.agents.items():
            with open(os.path.join(self.dir, f"{name}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return self.dir

    def __exit__(self, *exc):
        self.tmp.cleanup()


class TestKnownTimestampsProduceExactValues(unittest.TestCase):
    """AC1: `summarize` derives each agent's first/last ISO timestamp and
    computes wall_clock_min, agent_min and overlap -- a serial run reads
    1.00x, an overlapping one reads above it."""

    def test_serial_run_has_no_overlap(self):
        with Run({
            "a": [turn(ts="2026-01-01T00:00:00Z"), turn(ts="2026-01-01T00:10:00Z")],
            "b": [turn(ts="2026-01-01T00:10:00Z"), turn(ts="2026-01-01T00:20:00Z")],
        }) as d:
            s = tr.summarize(d)
        self.assertEqual(s["wall_clock_min"], 20.0)
        self.assertEqual(s["agent_min"], 20.0)
        self.assertEqual(s["overlap"], 1.0)

    def test_genuinely_overlapping_run_reads_above_one(self):
        with Run({
            "a": [turn(ts="2026-01-01T00:00:00Z"), turn(ts="2026-01-01T00:10:00Z")],
            "b": [turn(ts="2026-01-01T00:05:00Z"), turn(ts="2026-01-01T00:15:00Z")],
        }) as d:
            s = tr.summarize(d)
        # wall clock: 00:00 -> 00:15 = 15m. agent minutes: 10 + 10 = 20m.
        self.assertEqual(s["wall_clock_min"], 15.0)
        self.assertEqual(s["agent_min"], 20.0)
        self.assertEqual(s["overlap"], round(20 / 15, 2))
        self.assertGreater(s["overlap"], 1.0)


class TestLedgerRoundTripAndOldShapeRows(unittest.TestCase):
    """AC2/D3: the three values persist in the ledger row, and a
    pre-existing-shape row (no duration keys at all) reads back with no key
    error and no invented zero."""

    def _ledger(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return os.path.join(self.tmp.name, "runs.jsonl")

    def test_duration_fields_survive_append_and_read(self):
        path = self._ledger()
        with Run({
            "a": [turn(ts="2026-01-01T00:00:00Z"), turn(ts="2026-01-01T00:10:00Z")],
        }, wf_id="wf_dur") as d:
            tr.append_to_ledger(tr.summarize(d), path)
        row = tr.read_ledger(path)[0]
        self.assertEqual(row["wall_clock_min"], 10.0)
        self.assertEqual(row["agent_min"], 10.0)
        self.assertEqual(row["overlap"], 1.0)

    def test_fixture_row_in_the_pre_existing_shape_reads_back_cleanly(self):
        # Exactly the shape of the 14 rows already on disk before this task:
        # no wall_clock_min/agent_min/overlap key at all.
        path = self._ledger()
        old_row = {
            "wf_id": "wf_old", "project": "-proj", "ts": "2026-01-01T00:00:00Z",
            "turns": 10, "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 0.0,
            "total": 100, "opus_tokens": 0, "agents": [],
        }
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(old_row) + "\n")
        rows = tr.read_ledger(path)  # must not raise
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn("wall_clock_min", row)
        self.assertIsNone(row.get("wall_clock_min"), "absent, never an invented 0")
        # render_ledger and the dashboard view must also tolerate it (AC5/AC6).
        rendered = tr.render_ledger(rows, path)
        self.assertIn("wf_old", rendered)
        view = dash._run_view(row, "-proj", None)
        self.assertIsNone(view["wall_clock_min"])


class TestUnreadableTimestampsYieldNoDurationFields(unittest.TestCase):
    """AC3/D2: missing, unparseable, or out-of-order timestamps for even one
    counted agent invalidate the whole run's duration figures -- never a
    zero, never a negative span."""

    def test_missing_timestamps_omit_all_three_fields(self):
        with Run({"a": [turn(ts=None), turn(ts=None)]}) as d:
            s = tr.summarize(d)
        self.assertNotIn("wall_clock_min", s)
        self.assertNotIn("agent_min", s)
        self.assertNotIn("overlap", s)

    def test_unparseable_timestamps_omit_all_three_fields(self):
        with Run({"a": [turn(ts="not-a-timestamp"), turn(ts="also-not-one")]}) as d:
            s = tr.summarize(d)
        self.assertNotIn("wall_clock_min", s)
        self.assertNotIn("agent_min", s)
        self.assertNotIn("overlap", s)

    def test_out_of_order_timestamps_omit_all_three_fields(self):
        # Last-seen record's timestamp is EARLIER than the first-seen one's --
        # a naive last-minus-first would compute a negative span.
        with Run({"a": [turn(ts="2026-01-01T00:10:00Z"), turn(ts="2026-01-01T00:00:00Z")]}) as d:
            s = tr.summarize(d)
        self.assertNotIn("wall_clock_min", s)
        self.assertNotIn("agent_min", s)
        self.assertNotIn("overlap", s)

    def test_one_bad_agent_among_good_ones_still_omits_the_run_level_fields(self):
        # agent_min is a SUM of per-agent spans -- one agent with no usable
        # span cannot be silently treated as contributing 0 without
        # understating the total and poisoning the overlap ratio (D2).
        with Run({
            "good": [turn(ts="2026-01-01T00:00:00Z"), turn(ts="2026-01-01T00:10:00Z")],
            "bad": [turn(ts=None), turn(ts=None)],
        }) as d:
            s = tr.summarize(d)
        self.assertNotIn("wall_clock_min", s)

    def test_zone_less_timestamp_mixed_with_zoned_in_same_transcript_does_not_raise(self):
        # A valid ISO-8601 stamp with no trailing Z/offset must not parse into
        # a naive datetime that then blows up comparing against an aware one
        # from the same transcript (end_dt < start_dt).
        with Run({
            "a": [turn(ts="2026-01-01T00:00:00"), turn(ts="2026-01-01T00:10:00Z")],
        }) as d:
            s = tr.summarize(d)  # must not raise TypeError
        self.assertEqual(s["wall_clock_min"], 10.0)

    def test_zone_less_timestamps_across_agents_does_not_raise(self):
        # One agent entirely zone-less, another entirely zoned -- run_start/
        # run_end comparisons across agents must not raise either.
        with Run({
            "naive": [turn(ts="2026-01-01T00:00:00"), turn(ts="2026-01-01T00:05:00")],
            "zoned": [turn(ts="2026-01-01T00:10:00Z"), turn(ts="2026-01-01T00:20:00Z")],
        }) as d:
            s = tr.summarize(d)  # must not raise TypeError
        self.assertEqual(s["wall_clock_min"], 20.0)


class TestGroupsAreRecordedNotRederived(unittest.TestCase):
    """AC4: the measure step passes the run's task grouping; token-report
    records it verbatim rather than deriving it itself."""

    def test_build_parser_accepts_groups(self):
        args = tr.build_parser().parse_args(["--groups", '[["T001"]]'])
        self.assertEqual(args.groups, '[["T001"]]')

    def test_main_records_the_grouping_it_was_given(self):
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_groups") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = tr.main([d, "--groups", '[["T001"],["T002"]]', "--ledger", ledger, "--json"])
        self.assertEqual(code, 0)
        row = tr.read_ledger(ledger)[0]
        # Recorded exactly as given -- not re-derived from anything summarize()
        # itself can see (it has no notion of task ids at all).
        self.assertEqual(row["groups"], [["T001"], ["T002"]])

    def test_fully_serial_plan_records_groups_of_one(self):
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_serial") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                tr.main([d, "--groups", '[["T001"],["T002"],["T003"]]', "--ledger", ledger, "--json"])
        row = tr.read_ledger(ledger)[0]
        self.assertEqual(row["groups"], [["T001"], ["T002"], ["T003"]])

    def test_without_groups_flag_no_groups_key_is_recorded(self):
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_nogroups") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                tr.main([d, "--ledger", ledger, "--json"])
        self.assertNotIn("groups", tr.read_ledger(ledger)[0])

    # ── round 1, finding #3: the loop's didTakeParallelPath verdict rides ──
    # through as --parallel-path, recorded verbatim -- token-report has no
    # way to re-derive it (it never sees worktrees, only transcripts).

    def test_build_parser_accepts_parallel_path(self):
        args = tr.build_parser().parse_args(["--parallel-path", "true"])
        self.assertEqual(args.parallel_path, "true")

    def test_parallel_path_true_is_recorded_as_the_loops_own_verdict(self):
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_par_true") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = tr.main([d, "--parallel-path", "true", "--ledger", ledger, "--json"])
        self.assertEqual(code, 0)
        self.assertIs(tr.read_ledger(ledger)[0]["parallel_path"], True)

    def test_parallel_path_false_is_recorded_verbatim_even_with_a_two_task_group(self):
        # The exact ambiguous case the finding names: a group of two tasks
        # that fell back to serial. token-report cannot see worktrees or
        # runtimes, so it must record exactly what it was told, not infer
        # "grouped" means "parallel happened".
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_par_false") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = tr.main([
                    d, "--groups", '[["T001","T002"]]', "--parallel-path", "false",
                    "--ledger", ledger, "--json",
                ])
        self.assertEqual(code, 0)
        row = tr.read_ledger(ledger)[0]
        self.assertEqual(row["groups"], [["T001", "T002"]])
        self.assertIs(row["parallel_path"], False)

    def test_without_parallel_path_flag_no_key_is_recorded(self):
        ledger = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        self.addCleanup(lambda: shutil.rmtree(os.path.dirname(ledger), ignore_errors=True))
        with Run({"a": [turn(out=1)]}, wf_id="wf_nopar") as d:
            with contextlib.redirect_stdout(io.StringIO()):
                tr.main([d, "--ledger", ledger, "--json"])
        self.assertNotIn("parallel_path", tr.read_ledger(ledger)[0])


class TestRenderLedgerShowsDurationAndOverlap(unittest.TestCase):
    """AC5: `rein ledger` prints duration and overlap next to each run, and
    neither for a row that has none -- older rows render unchanged."""

    def test_row_with_duration_shows_wall_clock_and_overlap(self):
        rows = [{
            "wf_id": "wf_dur1", "project": "-proj", "ts": "2026-01-01T00:00:00Z",
            "turns": 10, "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 0.0,
            "total": 100, "opus_tokens": 0,
            "wall_clock_min": 15.0, "agent_min": 20.0, "overlap": 1.33,
        }]
        out = tr.render_ledger(rows, "/tmp/runs.jsonl", None)
        self.assertIn("wf_dur1", out)
        self.assertIn("wall=15.0m", out)
        self.assertIn("agent=20.0m", out)
        self.assertIn("overlap=1.33x", out)

    def test_row_without_duration_shows_neither(self):
        rows = [{
            "wf_id": "wf_nodur", "project": "-proj", "ts": "2026-01-01T00:00:00Z",
            "turns": 10, "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 0.0,
            "total": 100, "opus_tokens": 0,
        }]
        out = tr.render_ledger(rows, "/tmp/runs.jsonl", None)
        self.assertIn("wf_nodur", out)
        self.assertNotIn("wall=", out)
        self.assertNotIn("overlap=", out)

    def test_row_with_only_wall_clock_min_renders_without_key_error(self):
        # A hand-edited or partially-merged row can carry wall_clock_min
        # without its two siblings -- render_ledger must not KeyError on
        # r['agent_min'] / r['overlap'].
        rows = [{
            "wf_id": "wf_partial", "project": "-proj", "ts": "2026-01-01T00:00:00Z",
            "turns": 10, "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 0.0,
            "total": 100, "opus_tokens": 0,
            "wall_clock_min": 15.0,
        }]
        out = tr.render_ledger(rows, "/tmp/runs.jsonl", None)  # must not raise
        self.assertIn("wf_partial", out)
        self.assertIn("wall=15.0m", out)

    def test_older_rows_render_unchanged(self):
        # The exact 13-pre-existing-row shape (see test_token_report.py's
        # own `test_row_without_change_renders_without_a_tag`), now also
        # asserted to carry no duration tag at all.
        rows = [{
            "wf_id": "wf_ca4b1e78", "project": "-proj", "ts": "2026-07-30T00:00:00Z",
            "turns": 242, "turns_per_agent": 30.0, "ctx_max": 5000, "opus_share": 10.0,
            "total": 100, "opus_tokens": 0,
        }]
        out = tr.render_ledger(rows, "/tmp/runs.jsonl", None)
        line = out.split("wf_ca4b1e78")[1].split("\n")[0]
        self.assertNotIn("[", line)
        self.assertNotIn("wall=", line)
        self.assertNotIn("overlap=", line)


class TestDashboardShowsDurationWithGlossaryEntry(unittest.TestCase):
    """AC6: the dashboard shows run duration, with its GLOSSARY entry, and
    the completeness ratchet (test_dashboard_glossary.py) passes."""

    def test_duration_has_a_glossary_entry(self):
        self.assertIn("duration", dash.GLOSSARY)
        entry = dash.GLOSSARY["duration"]
        self.assertTrue(entry["meaning"])
        self.assertTrue(entry["why"])

    def test_run_view_carries_wall_clock_min_without_defaulting_to_zero(self):
        row_with = {"wf_id": "wf_1", "wall_clock_min": 12.5}
        row_without = {"wf_id": "wf_2"}
        self.assertEqual(dash._run_view(row_with, "p", None)["wall_clock_min"], 12.5)
        self.assertIsNone(dash._run_view(row_without, "p", None)["wall_clock_min"])

    def test_rendered_run_row_shows_the_duration_value(self):
        run = {
            "wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None,
            "agents": [], "wall_clock_min": 42.3,
        }
        row_html = dash._run_row(run, show_deltas=False)
        self.assertIn("42.3m", row_html)

    def test_rendered_run_row_without_duration_does_not_fabricate_zero(self):
        run = {
            "wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None,
            "agents": [],
        }
        row_html = dash._run_row(run, show_deltas=False)
        self.assertNotIn("0.0m", row_html)
        self.assertIn("not recorded", row_html)


if __name__ == "__main__":
    unittest.main()
