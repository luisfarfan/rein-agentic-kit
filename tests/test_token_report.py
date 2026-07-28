"""Tests for token accounting and the run ledger.

The numbers this module produces are the evidence every claim in this repo rests
on. If the accounting is wrong, so is everything else -- hence real assertions on
hand-built transcripts rather than smoke tests.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import token_report as tr  # noqa: E402


def turn(model: str, cache_read: int = 0, cache_write: int = 0, inp: int = 0, out: int = 0) -> str:
    return json.dumps(
        {
            "message": {
                "model": model,
                "usage": {
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                    "input_tokens": inp,
                    "output_tokens": out,
                },
            }
        }
    )


class Run:
    """A fake workflow run laid out exactly like Claude Code's transcript tree."""

    def __init__(self, agents: dict[str, list[str]], wf_id: str = "wf_test123"):
        self.agents, self.wf_id = agents, wf_id

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "projects", "-proj", "sess-1", "subagents", "workflows", self.wf_id)
        os.makedirs(self.dir)
        for name, turns in self.agents.items():
            with open(os.path.join(self.dir, f"{name}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(turns) + "\n")
        return self.dir

    def __exit__(self, *exc):
        self.tmp.cleanup()


class TestAnalysis(unittest.TestCase):
    def test_totals_and_context_peak(self):
        with Run({"agent-a": [turn("claude-sonnet-5", cache_read=100, cache_write=10, inp=5, out=1),
                              turn("claude-sonnet-5", cache_read=300, out=2)]}) as d:
            s = tr.summarize(d)
        self.assertEqual(s["turns"], 2)
        self.assertEqual(s["totals"]["cache_read"], 400)
        self.assertEqual(s["total"], 418)
        # ctx_max excludes output: it is what gets re-read, not what was written.
        self.assertEqual(s["ctx_max"], 300)

    def test_provenance_from_transcript_path(self):
        with Run({"agent-a": [turn("claude-haiku-4-5", out=1)]}, wf_id="wf_abc") as d:
            s = tr.summarize(d)
        self.assertEqual(s["wf_id"], "wf_abc")
        self.assertEqual(s["session"], "sess-1")
        self.assertEqual(s["project"], "-proj")

    def test_opus_share_is_the_headline_metric(self):
        with Run({"a": [turn("claude-opus-5", cache_read=900, out=0)],
                  "b": [turn("claude-sonnet-5", cache_read=100, out=0)]}) as d:
            s = tr.summarize(d)
        self.assertEqual(s["opus_tokens"], 900)
        self.assertEqual(s["opus_share"], 90.0)

    def test_turns_per_agent_surfaces_runaway(self):
        with Run({"short": [turn("claude-sonnet-5", out=1)] * 2,
                  "runaway": [turn("claude-sonnet-5", cache_read=1000, out=1)] * 8}) as d:
            s = tr.summarize(d)
        self.assertEqual(s["turns_per_agent"], 5.0)
        # Loudest agent first: the runaway is what you are looking for.
        self.assertEqual(s["agents"][0]["file"], "runaway.jsonl")
        self.assertEqual(s["agents"][0]["turns"], 8)

    def test_agent_attributed_to_its_dominant_model(self):
        with Run({"a": [turn("claude-haiku-4-5", out=1)] + [turn("claude-sonnet-5", out=1)] * 3}) as d:
            s = tr.summarize(d)
        self.assertEqual(s["agents"][0]["model"], "sonnet-5")

    def test_model_names_group_across_date_suffixes(self):
        self.assertEqual(tr._short_model("claude-haiku-4-5-20251001"), "haiku-4-5")
        self.assertEqual(tr._short_model("claude-opus-5"), "opus-5")

    def test_malformed_lines_and_turns_without_usage_are_skipped(self):
        with Run({"a": ["{ not json", json.dumps({"message": {"model": "claude-opus-5"}}),
                        turn("claude-opus-5", cache_read=50, out=1)]}) as d:
            s = tr.summarize(d)
        self.assertEqual(s["turns"], 1)
        self.assertEqual(s["total"], 51)


class TestLedger(unittest.TestCase):
    def _ledger(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return os.path.join(self.tmp.name, "runs.jsonl")

    def test_append_then_reanalyze_replaces_not_duplicates(self):
        path = self._ledger()
        with Run({"a": [turn("claude-opus-5", cache_read=10, out=1)]}, wf_id="wf_1") as d:
            tr.append_to_ledger(tr.summarize(d), path)
            tr.append_to_ledger(tr.summarize(d), path)
        rows = tr.read_ledger(path)
        self.assertEqual(len(rows), 1, "re-running the report must not duplicate a run")
        self.assertEqual(rows[0]["wf_id"], "wf_1")

    def test_distinct_runs_accumulate(self):
        path = self._ledger()
        for wf in ("wf_1", "wf_2"):
            with Run({"a": [turn("claude-opus-5", out=1)]}, wf_id=wf) as d:
                tr.append_to_ledger(tr.summarize(d), path)
        self.assertEqual({r["wf_id"] for r in tr.read_ledger(path)}, {"wf_1", "wf_2"})

    def test_run_without_identity_is_not_recorded(self):
        """An ad-hoc folder has no stable id, so it cannot be deduped -- skip it."""
        path = self._ledger()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "a.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(turn("claude-opus-5", out=1) + "\n")
        self.assertEqual(tr.append_to_ledger(tr.summarize(tmp.name), path), "")
        self.assertEqual(tr.read_ledger(path), [])

    def test_ledger_record_keeps_agents_but_trimmed(self):
        path = self._ledger()
        with Run({"a": [turn("claude-opus-5", cache_read=10, out=1)]}, wf_id="wf_1") as d:
            tr.append_to_ledger(tr.summarize(d), path)
        agent = tr.read_ledger(path)[0]["agents"][0]
        self.assertEqual(set(agent), {"file", "model", "turns", "total", "ctx_max"})

    def test_corrupt_ledger_line_does_not_lose_the_rest(self):
        path = self._ledger()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"wf_id": "wf_old", "total": 1}\n{ corrupt\n')
        self.assertEqual(len(tr.read_ledger(path)), 1)


if __name__ == "__main__":
    unittest.main()
