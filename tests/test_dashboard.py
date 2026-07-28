"""Tests for the dashboard view model (T001): ledger -> baseline join -> JSON,
and the local-only server that can serve it.
"""

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
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

    def test_non_object_json_line_does_not_crash_or_drop_valid_rows(self):
        # A line that is valid JSON but not an object (e.g. a truncated
        # write) parses fine in `read_ledger` -- only the type check in
        # `_read_ledger_safe` catches it. Must degrade like any other corrupt
        # line, never raise `AttributeError` from a bare `.get()` downstream.
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row("wf_1", "2026-01-01T00:00:00Z")) + "\n")
            fh.write('"truncated-but-valid-json"\n')
            fh.write(json.dumps(row("wf_2", "2026-01-02T00:00:00Z")) + "\n")

        view = dash.build_view(self.ledger_path, self.baseline_path)  # must not raise

        self.assertEqual(view["message"], "")
        wf_ids = {r["wf_id"] for r in view["projects"][0]["runs"]}
        self.assertEqual(wf_ids, {"wf_1", "wf_2"}, "a non-object JSON line must not drop the valid rows around it")

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

    def test_no_external_resources_and_no_fetch_on_a_fully_populated_page(self):
        # The empty page (`{"projects": []}`) can never contain a script, a
        # link, or a URL -- it is the one render guaranteed clean by
        # construction. Run the same assertions over a populated project
        # section (runs, agents, baseline, deltas, models, and the models
        # edit form) -- that is the markup D2 is actually about.
        view = {"message": "", "projects": [{
            "project": "proj-full", "root": "/tmp/does-not-matter-for-this-test",
            "models": {
                "aux": {"value": "haiku", "source": "default"},
                "impl": {"value": "opus", "source": "config"},
                "review": {"value": "opus", "source": "default"},
            },
            "runs": [
                {"wf_id": "wf_base", "ts": "2026-01-01T00:00:00Z", "turns_per_agent": 10.0,
                 "ctx_max": 2000, "opus_share": 20.0, "is_baseline": True, "deltas": None,
                 "agents": [{"file": "a.jsonl", "model": "sonnet-5", "turns": 5, "total": 500, "ctx_max": 2000}]},
                {"wf_id": "wf_later", "ts": "2026-01-02T00:00:00Z", "turns_per_agent": 5.0,
                 "ctx_max": 1000, "opus_share": 10.0, "is_baseline": False,
                 "deltas": {"turns_per_agent": -50.0, "ctx_max": -50.0, "opus_share": -50.0}, "agents": []},
            ],
        }]}
        html_out = dash.render_html(view)
        lowered = html_out.lower()
        self.assertIn("proj-full", html_out)  # sanity: this really is the populated render
        self.assertIn("<form", lowered)  # the editing form (T003) really renders here
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


class TestModelsResolutionInViewModel(LedgerFixture):
    def test_project_shows_resolved_models_and_source(self):
        proj_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj_dir, ignore_errors=True)
        with open(os.path.join(proj_dir, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"models": {"impl": "opus"}}, fh)
        project_key = dash._mangle_path(proj_dir)
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project=project_key)])

        view = dash.build_view(self.ledger_path, self.baseline_path)
        proj = view["projects"][0]

        self.assertEqual(proj["root"], os.path.realpath(proj_dir))
        self.assertEqual(proj["models"]["impl"], {"value": "opus", "source": "config"})
        self.assertEqual(proj["models"]["aux"], {"value": "haiku", "source": "default"})
        self.assertEqual(proj["models"]["review"], {"value": "opus", "source": "default"})

    def test_project_with_no_config_reads_as_all_defaults(self):
        proj_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj_dir, ignore_errors=True)
        project_key = dash._mangle_path(proj_dir)
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project=project_key)])

        view = dash.build_view(self.ledger_path, self.baseline_path)
        proj = view["projects"][0]

        self.assertEqual(proj["root"], os.path.realpath(proj_dir))
        for key, value in dash.DEFAULT_MODELS.items():
            self.assertEqual(proj["models"][key], {"value": value, "source": "default"})

    def test_unresolvable_project_has_no_root_and_still_reads_as_defaults(self):
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project="-nonexistent-path-does-not-exist-xyz")])

        view = dash.build_view(self.ledger_path, self.baseline_path)
        proj = view["projects"][0]

        self.assertIsNone(proj["root"])
        for key, value in dash.DEFAULT_MODELS.items():
            self.assertEqual(proj["models"][key], {"value": value, "source": "default"})


class TestModelsWriteDiffThenConfirm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.known_roots = {self.root}

    def _config_path(self):
        return os.path.join(self.root, "flow.config.json")

    def test_first_call_never_writes_second_confirms(self):
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        result1 = dash.write_models(self.root, models, self.known_roots, confirmed=False)
        self.assertIn("impl", result1["diff"])
        self.assertIn("opus", result1["diff"])
        self.assertTrue(result1["token"], "a preview must mint a token for the confirm to present back")
        self.assertFalse(os.path.exists(self._config_path()), "a first request must never write")

        result2 = dash.write_models(self.root, models, self.known_roots, confirmed=True, token=result1["token"])
        self.assertEqual(result1["diff"], result2["diff"])
        self.assertTrue(os.path.exists(self._config_path()))
        with open(self._config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg, {"models": models})
        # creating from absent must contain ONLY the models block
        self.assertEqual(set(cfg.keys()), {"models"})

    def test_confirm_without_prior_preview_is_refused(self):
        # The CRITICAL case: a single, first-and-only request carrying
        # `confirm: true` and no token at all must never write -- `confirmed`
        # supplied by the caller is not evidence a preview ever happened.
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        with self.assertRaises(dash.ModelsWriteError):
            dash.write_models(self.root, models, self.known_roots, confirmed=True)
        self.assertFalse(
            os.path.exists(self._config_path()),
            "a first-and-only confirmed request must never write to disk",
        )

    def test_confirm_with_forged_or_unknown_token_is_refused(self):
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        dash.write_models(self.root, models, self.known_roots, confirmed=False)  # mints a real token, unused
        with self.assertRaises(dash.ModelsWriteError):
            dash.write_models(self.root, models, self.known_roots, confirmed=True, token="not-a-real-token")
        self.assertFalse(os.path.exists(self._config_path()))

    def test_confirm_is_refused_as_stale_once_the_file_changed_underneath(self):
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        result = dash.write_models(self.root, models, self.known_roots, confirmed=False)
        # someone/something else writes to the file after the preview was shown
        with open(self._config_path(), "w", encoding="utf-8") as fh:
            fh.write('{"models": {"aux": "sonnet", "impl": "sonnet", "review": "sonnet"}}')
        with self.assertRaises(dash.ModelsWriteError):
            dash.write_models(self.root, models, self.known_roots, confirmed=True, token=result["token"])
        with open(self._config_path(), encoding="utf-8") as fh:
            self.assertIn("sonnet", fh.read(), "the refused confirm must not have clobbered the newer content")

    def test_unknown_keys_and_comments_preserved_byte_for_byte(self):
        example_path = os.path.join(REPO_ROOT, "plugins", "rein", "flow.config.example.json")
        with open(example_path, encoding="utf-8") as fh:
            original_text = fh.read()
        original = json.loads(original_text)
        with open(self._config_path(), "w", encoding="utf-8") as fh:
            fh.write(original_text)

        new_models = {"aux": "opus", "impl": "opus", "review": "haiku"}
        preview = dash.write_models(self.root, new_models, self.known_roots, confirmed=False)
        dash.write_models(self.root, new_models, self.known_roots, confirmed=True, token=preview["token"])

        with open(self._config_path(), encoding="utf-8") as fh:
            updated = json.load(fh)

        for key in original:
            if key == "models":
                continue
            self.assertEqual(updated[key], original[key], f"key {key!r} must be preserved byte-for-byte")
        self.assertEqual(set(updated.keys()), set(original.keys()))

        # the $comment entries the example config ships -- top-level model
        # comment plus every other section's -- must survive untouched.
        self.assertEqual(updated["models"]["$comment"], original["models"]["$comment"])
        self.assertEqual(updated["commands"]["$comment"], original["commands"]["$comment"])
        self.assertEqual(updated["models"]["aux"], "opus")
        self.assertEqual(updated["models"]["impl"], "opus")
        self.assertEqual(updated["models"]["review"], "haiku")

    def test_refusal_outside_known_roots_traversal_attempt(self):
        secret = tempfile.TemporaryDirectory()
        self.addCleanup(secret.cleanup)
        secret_root = os.path.realpath(secret.name)
        secret_config = os.path.join(secret_root, "flow.config.json")
        with open(secret_config, "w", encoding="utf-8") as fh:
            fh.write('{"models": {"aux": "haiku", "impl": "sonnet", "review": "opus"}}')
        with open(secret_config, encoding="utf-8") as fh:
            before = fh.read()

        traversal = os.path.join(self.root, "..", os.path.basename(secret_root))
        self.assertEqual(os.path.realpath(traversal), secret_root, "sanity: the traversal really escapes self.root")

        with self.assertRaises(dash.ModelsWriteError):
            dash.write_models(
                traversal, {"aux": "opus", "impl": "opus", "review": "opus"}, self.known_roots, confirmed=True
            )

        with open(secret_config, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after, "a refused write must leave the filesystem untouched")
        self.assertFalse(os.path.exists(self._config_path()), "the refused root itself must gain nothing either")

    def test_missing_model_slot_is_rejected(self):
        with self.assertRaises(ValueError):
            dash.write_models(self.root, {"aux": "haiku", "impl": "sonnet"}, self.known_roots, confirmed=True)
        self.assertFalse(os.path.exists(self._config_path()))


class TestModelsHttpEndpoint(LedgerFixture):
    def _serve(self) -> tuple[str, int]:
        proj_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj_dir, ignore_errors=True)
        project_key = dash._mangle_path(proj_dir)
        self._write_ledger([row("wf_1", "2026-01-01T00:00:00Z", project=project_key)])
        view = dash.build_view(self.ledger_path, self.baseline_path)
        root = os.path.realpath(proj_dir)

        port = _free_port()
        httpd = dash.make_server(view, port)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def _stop():
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.addCleanup(_stop)
        return root, port

    def test_post_diff_then_confirm_and_unknown_root_is_non_2xx(self):
        root, port = self._serve()
        url = f"http://127.0.0.1:{port}/models"
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}

        first = urllib.request.Request(
            url, data=json.dumps({"root": root, "models": models}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(first, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            payload = json.loads(resp.read())
        self.assertFalse(payload["written"])
        self.assertIn("diff", payload)
        self.assertTrue(payload["token"], "a preview response must carry a server-minted token")
        self.assertFalse(os.path.exists(os.path.join(root, "flow.config.json")))

        second = urllib.request.Request(
            url, data=json.dumps(
                {"root": root, "models": models, "confirm": True, "token": payload["token"]}
            ).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(second, timeout=5) as resp2:
            self.assertEqual(resp2.status, 200)
            payload2 = json.loads(resp2.read())
        self.assertTrue(payload2["written"])
        self.assertTrue(os.path.exists(os.path.join(root, "flow.config.json")))

        bad = urllib.request.Request(
            url,
            data=json.dumps(
                {"root": "/definitely/not/a/known/project/root", "models": models, "confirm": True}
            ).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad, timeout=5)
        self.assertGreaterEqual(ctx.exception.code, 400)

    def test_first_and_only_confirm_request_is_refused_and_writes_nothing(self):
        # Reproduces the CRITICAL finding directly: a single POST carrying
        # `confirm: true`, with no preceding preview, must be refused
        # (non-2xx) and must never create/modify the config file.
        root, port = self._serve()
        url = f"http://127.0.0.1:{port}/models"
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        req = urllib.request.Request(
            url, data=json.dumps({"root": root, "models": models, "confirm": True}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertGreaterEqual(ctx.exception.code, 400)
        self.assertFalse(os.path.exists(os.path.join(root, "flow.config.json")))

    def test_foreign_origin_post_is_refused_and_writes_nothing(self):
        # Reproduces the CRITICAL finding directly: any website open in the
        # user's browser while `rein dashboard` runs must not be able to
        # write, even with a well-formed JSON body and content type.
        root, port = self._serve()
        url = f"http://127.0.0.1:{port}/models"
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}
        req = urllib.request.Request(
            url, data=json.dumps({"root": root, "models": models, "confirm": True}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertGreaterEqual(ctx.exception.code, 400)
        self.assertFalse(os.path.exists(os.path.join(root, "flow.config.json")))

    def test_invalid_content_length_header_is_a_400_not_a_traceback(self):
        _root, port = self._serve()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(conn.close)
        conn.putrequest("POST", "/models")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        resp = conn.getresponse()
        self.assertGreaterEqual(resp.status, 400)
        resp.read()

    def test_get_after_confirmed_write_reflects_the_new_value(self):
        # Coherence finding: the page must never contradict a write this same
        # server just performed -- rendering once at construction time can't
        # do that.
        root, port = self._serve()
        url = f"http://127.0.0.1:{port}/models"
        models = {"aux": "haiku", "impl": "opus", "review": "opus"}

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            before = resp.read().decode("utf-8")
        self.assertIn("impl=<b>sonnet</b> <small>(default)</small>", before)

        preview_req = urllib.request.Request(
            url, data=json.dumps({"root": root, "models": models}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(preview_req, timeout=5) as resp:
            token = json.loads(resp.read())["token"]

        confirm_req = urllib.request.Request(
            url, data=json.dumps(
                {"root": root, "models": models, "confirm": True, "token": token}
            ).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(confirm_req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            after = resp.read().decode("utf-8")
        self.assertIn("impl=<b>opus</b> <small>(config)</small>", after)

    def test_unknown_path_get_is_404(self):
        _root, port = self._serve()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_form_post_previews_then_its_own_confirm_form_completes_the_write(self):
        # T003's actual editing affordance: a plain <form> post, zero
        # JavaScript -- the response is HTML, not JSON, and the second
        # (confirm) form carries the server-minted token.
        root, port = self._serve()
        url = f"http://127.0.0.1:{port}/models"
        preview_body = urllib.parse.urlencode(
            {"root": root, "aux": "haiku", "impl": "opus", "review": "opus"}
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=preview_body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            page = resp.read().decode("utf-8")
        self.assertIn("Confirm write", page)
        self.assertIn('name="token"', page)
        self.assertFalse(os.path.exists(os.path.join(root, "flow.config.json")))

        token = re.search(r'name="token" value="([^"]+)"', page).group(1)
        confirm_body = urllib.parse.urlencode(
            {"root": root, "aux": "haiku", "impl": "opus", "review": "opus",
             "confirm": "true", "token": token}
        ).encode("utf-8")
        confirm_req = urllib.request.Request(
            url, data=confirm_body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(confirm_req, timeout=5) as resp2:
            self.assertEqual(resp2.status, 200)
            page2 = resp2.read().decode("utf-8")
        self.assertIn("written", page2)
        self.assertTrue(os.path.exists(os.path.join(root, "flow.config.json")))


if __name__ == "__main__":
    unittest.main()
