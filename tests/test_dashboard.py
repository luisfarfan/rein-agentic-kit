"""Tests for the dashboard view model (T001): ledger -> baseline join -> JSON,
and the local-only server that can serve it.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import dashboard as dash  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")


def row(wf_id: str, ts: str, project: str = "proj-a", turns_per_agent: float = 5.0,
        ctx_max: int = 1000, opus_share: float = 10.0, agents: list | None = None) -> dict:
    return {
        "wf_id": wf_id,
        "ts": ts,
        "project": project,
        "turns": 10,
        "turns_per_agent": turns_per_agent,
        "ctx_max": ctx_max,
        "opus_share": opus_share,
        "totals": {"cache_read": 900, "input": 50, "output": 50},
        "total": 1000,
        "opus_tokens": 100,
        "agents": agents if agents is not None else [
            {"file": "a.jsonl", "model": "sonnet-5", "turns": 5, "total": 500, "ctx_max": ctx_max}
        ],
    }


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = os.path.join(self.tmp.name, "runs.jsonl")
        self.baseline_path = os.path.join(self.tmp.name, "baseline.json")

    def _write_ledger(self, rows: list[dict]) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _write_baseline(self, record: dict) -> None:
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)


class TestViewModelShape(LedgerFixture):
    def test_runs_grouped_by_project_with_key_metrics_and_agents(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
                             row("wf_2", "2026-01-02T00:00:00Z", project="proj-b")])
        view = dash.build_view(self.ledger_path, self.baseline_path)

        self.assertEqual(view["message"], "")
        projects = {p["project"]: p for p in view["projects"]}
        self.assertEqual(set(projects), {"proj-a", "proj-b"})

        run = projects["proj-a"]["runs"][0]
        for key in ("turns_per_agent", "ctx_max", "opus_share", "totals", "agents"):
            self.assertIn(key, run)
        self.assertEqual(run["agents"][0]["file"], "a.jsonl")

    def test_runs_within_a_project_are_ordered_by_timestamp(self):
        self._write_ledger([row("wf_2", "2026-01-02T00:00:00Z"), row("wf_1", "2026-01-01T00:00:00Z")])
        view = dash.build_view(self.ledger_path, self.baseline_path)
        wf_ids = [r["wf_id"] for r in view["projects"][0]["runs"]]
        self.assertEqual(wf_ids, ["wf_1", "wf_2"])


class TestBaselineAndDeltas(LedgerFixture):
    def test_baseline_run_is_marked_and_carries_no_delta(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-a")])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        run = view["projects"][0]["runs"][0]
        self.assertTrue(run["is_baseline"])
        self.assertIsNone(run["deltas"])

    def test_later_run_in_same_project_gets_signed_deltas(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a", turns_per_agent=10.0, ctx_max=2000, opus_share=20.0),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-a", turns_per_agent=5.0, ctx_max=1000, opus_share=10.0),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 10.0, "ctx_max": 2000, "opus_share": 20.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        runs = {r["wf_id"]: r for r in view["projects"][0]["runs"]}

        later = runs["wf_2"]
        self.assertFalse(later["is_baseline"])
        self.assertIsNotNone(later["deltas"])
        # Halved every metric -> -50% -- and the sign must read as an improvement.
        self.assertEqual(later["deltas"]["turns_per_agent"], -50.0)
        self.assertEqual(later["deltas"]["ctx_max"], -50.0)
        self.assertEqual(later["deltas"]["opus_share"], -50.0)

    def test_run_recorded_before_the_baseline_carries_no_delta(self):
        self._write_ledger([
            row("wf_0", "2025-12-31T00:00:00Z", project="proj-a"),
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        runs = {r["wf_id"]: r for r in view["projects"][0]["runs"]}
        self.assertIsNone(runs["wf_0"]["deltas"])

    def test_run_in_a_different_project_carries_no_delta(self):
        self._write_ledger([
            row("wf_1", "2026-01-01T00:00:00Z", project="proj-a"),
            row("wf_2", "2026-01-02T00:00:00Z", project="proj-b"),
        ])
        self._write_baseline({"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "project": "proj-a",
                               "turns_per_agent": 5.0, "ctx_max": 1000, "opus_share": 10.0})
        view = dash.build_view(self.ledger_path, self.baseline_path)
        other_project = next(p for p in view["projects"] if p["project"] == "proj-b")
        self.assertIsNone(other_project["runs"][0]["deltas"])

    def test_corrupt_baseline_degrades_to_no_baseline_not_a_crash(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z")])
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        view = dash.build_view(self.ledger_path, self.baseline_path)
        run = view["projects"][0]["runs"][0]
        self.assertFalse(run["is_baseline"])
        self.assertIsNone(run["deltas"])


class TestEmptyAndCorruptLedger(LedgerFixture):
    def test_missing_ledger_yields_empty_view_with_message(self):
        view = dash.build_view(self.ledger_path, self.baseline_path)
        self.assertEqual(view["projects"], [])
        self.assertTrue(view["message"])

    def test_empty_ledger_file_yields_empty_view_with_message(self):
        open(self.ledger_path, "w", encoding="utf-8").close()
        view = dash.build_view(self.ledger_path, self.baseline_path)
        self.assertEqual(view["projects"], [])
        self.assertTrue(view["message"])

    def test_corrupt_line_does_not_drop_the_valid_rows_around_it(self):
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row("wf_1", "2026-01-01T00:00:00Z")) + "\n")
            fh.write("{ this is not valid json\n")
            fh.write(json.dumps(row("wf_2", "2026-01-02T00:00:00Z")) + "\n")

        view = dash.build_view(self.ledger_path, self.baseline_path)

        self.assertEqual(view["message"], "")
        wf_ids = {r["wf_id"] for r in view["projects"][0]["runs"]}
        self.assertEqual(wf_ids, {"wf_1", "wf_2"}, "a corrupt line must not silently drop the valid rows around it")

    def test_ledger_path_that_is_a_directory_does_not_raise(self):
        os.makedirs(self.ledger_path)  # a directory where a file was expected
        view = dash.build_view(self.ledger_path, self.baseline_path)
        self.assertEqual(view["projects"], [])
        self.assertTrue(view["message"])


class TestJsonCliNoSocket(LedgerFixture):
    def test_json_flag_prints_the_view_model_and_returns_without_serving(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="proj-json")])
        env = dict(os.environ)
        result = subprocess.run(
            [sys.executable, REIN_BIN, "dashboard", "--json",
             "--ledger", self.ledger_path, "--baseline", self.baseline_path],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        printed = json.loads(result.stdout)
        self.assertEqual(printed["projects"][0]["project"], "proj-json")
        # Returning at all (rather than hanging) is the proof: `serve()` never
        # runs on this path, so no socket was ever bound.

    def test_build_parser_exposes_port_flag(self):
        args = dash.build_parser().parse_args(["--port", "9999"])
        self.assertEqual(args.port, 9999)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestRealServer(LedgerFixture):
    def test_server_binds_127_0_0_1_only_and_serves_the_rendered_page(self):
        # Distinctive, ledger-only values -- if these show up in the HTTP
        # response, the data made it all the way from disk to the markup,
        # not merely through a template that happens to compile.
        self._write_ledger([row("wf_http_1", "2026-01-01T00:00:00Z", project="proj-http-9182",
                                 turns_per_agent=7.25, ctx_max=54321, opus_share=33.3)])
        view = dash.build_view(self.ledger_path, self.baseline_path)

        port = _free_port()
        httpd = dash.make_server(view, port)
        self.assertEqual(httpd.server_address[0], "127.0.0.1")

        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertIn("text/html", content_type)
        self.assertIn("<!doctype html>", body.lower())
        self.assertIn("proj-http-9182", body)
        self.assertIn("54,321", body)  # ctx_max, formatted
        self.assertIn("33.3", body)  # opus_share


class TestRenderHtmlSelfContained(unittest.TestCase):
    def test_no_external_resources_and_no_fetch(self):
        html_out = dash.render_html({"message": "", "projects": []})
        lowered = html_out.lower()
        self.assertNotIn("<link", lowered)
        self.assertNotIn("<script", lowered)
        self.assertNotIn("http://", lowered)
        self.assertNotIn("https://", lowered)
        self.assertNotIn("fetch(", lowered)
        self.assertNotIn("xmlhttprequest", lowered)
        self.assertIn("<style>", lowered)  # CSS is inline, not linked


class TestRenderHtmlEmptyStates(unittest.TestCase):
    def test_zero_runs_renders_a_message_not_a_crash(self):
        html_out = dash.render_html({"message": "no runs recorded yet", "projects": []})
        self.assertIn("no runs recorded yet", html_out)

    def test_one_run_renders_its_metrics(self):
        view = {"message": "", "projects": [{"project": "solo", "runs": [{
            "wf_id": "wf_solo", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 4.0,
            "ctx_max": 800, "opus_share": 25.0, "is_baseline": False, "deltas": None,
            "agents": [{"file": "a.jsonl", "model": "sonnet-5", "turns": 4, "total": 400, "ctx_max": 800}],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn("solo", html_out)
        self.assertIn("wf_solo", html_out)
        self.assertIn("25.0", html_out)

    def test_run_with_empty_agents_list_still_renders(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_empty", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 1.0,
            "ctx_max": 1, "opus_share": 0.0, "is_baseline": False, "deltas": None,
            "agents": [],
        }]}]}
        html_out = dash.render_html(view)  # must not raise
        self.assertIn("wf_empty", html_out)
        self.assertIn("0 agents", html_out)


class TestRenderHtmlBaselineAndDeltas(unittest.TestCase):
    def test_baseline_run_is_visibly_marked(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": True, "deltas": None,
            "agents": [],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn("BASELINE", html_out)
        self.assertIn('class="baseline"', html_out)

    def test_negative_delta_reads_unambiguously_as_an_improvement(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [
            {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 10.0,
             "ctx_max": 2000, "opus_share": 20.0, "is_baseline": True, "deltas": None, "agents": []},
            {"wf_id": "wf_later", "ts": "2026-01-02T00:00:00Z", "turns_per_agent": 5.0,
             "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False,
             "deltas": {"turns_per_agent": -50.0, "ctx_max": -50.0, "opus_share": -50.0}, "agents": []},
        ]}]}
        html_out = dash.render_html(view)
        self.assertIn("delta-better", html_out)
        self.assertIn("better", html_out)  # the word itself, not just the CSS class name
        self.assertIn("-50.0%", html_out)

    def test_positive_delta_reads_unambiguously_as_a_regression(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [
            {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
             "ctx_max": 1000, "opus_share": 10.0, "is_baseline": True, "deltas": None, "agents": []},
            {"wf_id": "wf_later", "ts": "2026-01-02T00:00:00Z", "turns_per_agent": 10.0,
             "ctx_max": 2000, "opus_share": 20.0, "is_baseline": False,
             "deltas": {"turns_per_agent": 100.0, "ctx_max": 100.0, "opus_share": 100.0}, "agents": []},
        ]}]}
        html_out = dash.render_html(view)
        self.assertIn("delta-worse", html_out)
        self.assertIn("worse", html_out)

    def test_no_baseline_shows_trend_but_no_savings_figure_anywhere(self):
        # D4: no baseline marked anywhere in the ledger -> no delta/savings
        # column or figure at all, not merely "no negative deltas".
        view = {"message": "", "projects": [{"project": "p", "runs": [
            {"wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 10.0,
             "ctx_max": 2000, "opus_share": 20.0, "is_baseline": False, "deltas": None, "agents": []},
            {"wf_id": "wf_2", "ts": "2026-01-02T00:00:00Z", "turns_per_agent": 5.0,
             "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None, "agents": []},
        ]}]}
        html_out = dash.render_html(view)
        # the trend (both runs, in order) is still visible
        self.assertIn("wf_1", html_out)
        self.assertIn("wf_2", html_out)
        # Look at the rendered body only -- the stylesheet legitimately
        # defines .delta-better/.delta-worse rules even when unused (D4 is
        # about no savings *figure* appearing, not about dead CSS rules).
        body = html_out.split("<body>", 1)[1]
        self.assertNotIn("BASELINE", body)
        self.assertNotIn('class="delta-better"', body)
        self.assertNotIn('class="delta-worse"', body)
        self.assertNotIn("Δ", body)  # the Δ column header itself
        self.assertNotIn('class="delta-na"', body)


class TestRenderHtmlAgentsReachable(unittest.TestCase):
    def test_per_agent_rows_reachable_via_details_without_leaving_the_page(self):
        view = {"message": "", "projects": [{"project": "p", "runs": [{
            "wf_id": "wf_1", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 5.0,
            "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False, "deltas": None,
            "agents": [
                {"file": "a.jsonl", "model": "sonnet-5", "turns": 3, "total": 300, "ctx_max": 900},
                {"file": "b.jsonl", "model": "opus", "turns": 2, "total": 700, "ctx_max": 1000},
            ],
        }]}]}
        html_out = dash.render_html(view)
        self.assertIn("<details>", html_out)
        self.assertIn("<summary>", html_out)
        self.assertIn("a.jsonl", html_out)
        self.assertIn("b.jsonl", html_out)
        # native disclosure widget, not a link to another page or a script
        self.assertNotIn("<a href", html_out)


if __name__ == "__main__":
    unittest.main()
