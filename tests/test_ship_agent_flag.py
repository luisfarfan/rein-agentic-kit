"""`rein ship --agent <id>` alone picks that agent's default model."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")
sys.path.insert(0, os.path.join(PLUGIN, "lib"))

import quick_driver  # noqa: E402
import quota  # noqa: E402
import ship  # noqa: E402


def _load_cli():
    path = os.path.join(PLUGIN, "bin", "rein")
    loader = importlib.machinery.SourceFileLoader("rein_cli_ship_agent", path)
    spec = importlib.util.spec_from_loader("rein_cli_ship_agent", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


rein_cli = _load_cli()


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = rein_cli.cmd_ship(argv)
    return code, out.getvalue()


class TestDefaultModel(unittest.TestCase):
    def test_table_and_lookup(self):
        self.assertEqual(quota.DEFAULT_MODELS, {
            "claude": "sonnet", "codex": "gpt-6.1-sol", "antigravity": "gemini-4-argon-medium"})
        self.assertEqual(quota.default_model("codex"), "gpt-6.1-sol")
        self.assertEqual(quota.default_model("nope"), "")

    def test_quick_driver_shares_the_table(self):
        self.assertIs(quick_driver.DEFAULT_MODELS, quota.DEFAULT_MODELS)


class TestShipAgentFlag(unittest.TestCase):
    def test_lone_agent_fills_the_default_model(self):
        with mock.patch.object(rein_cli._ship, "run_ship", return_value=0) as run, \
                mock.patch.object(rein_cli._ship, "OrcaClient"):
            code, _ = _run(["c1", "--agent", "codex"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["override"], ("codex", "gpt-6.1-sol"))

    def test_explicit_model_wins(self):
        with mock.patch.object(rein_cli._ship, "run_ship", return_value=0) as run, \
                mock.patch.object(rein_cli._ship, "OrcaClient"):
            _run(["c1", "--agent", "codex", "--model", "x"])
        self.assertEqual(run.call_args.kwargs["override"], ("codex", "x"))

    def test_detach_carries_the_filled_model(self):
        with mock.patch.object(rein_cli._ship, "detach_ship", return_value=0) as det, \
                mock.patch.object(rein_cli._ship, "OrcaClient"):
            code, _ = _run(["c1", "--agent", "antigravity", "--detach"])
        self.assertEqual(code, 0)
        self.assertEqual(det.call_args.kwargs["override"], ("antigravity", "gemini-4-argon-medium"))

    def test_dry_run_routes_with_default_model(self):
        with mock.patch.object(rein_cli._ship, "run_ship", return_value=0) as run, \
                mock.patch.object(rein_cli._ship, "dry_run_client"):
            _run(["c1", "--agent", "claude", "--dry-run"])
        self.assertEqual(run.call_args.kwargs["override"], ("claude", "sonnet"))

    def test_unknown_agent_without_model_is_usage(self):
        with mock.patch.object(rein_cli._ship, "run_ship") as run:
            code, out = _run(["c1", "--agent", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("usage: rein ship", out)
        for known in quota.DEFAULT_MODELS:
            self.assertIn(known, out)
        run.assert_not_called()

    def test_unknown_agent_with_model_is_allowed(self):
        with mock.patch.object(rein_cli._ship, "run_ship", return_value=0) as run, \
                mock.patch.object(rein_cli._ship, "OrcaClient"):
            code, _ = _run(["c1", "--agent", "nope", "--model", "m"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["override"], ("nope", "m"))

    def test_model_alone_is_usage(self):
        with mock.patch.object(rein_cli._ship, "run_ship") as run:
            code, out = _run(["c1", "--model", "x"])
        self.assertEqual(code, 2)
        self.assertIn("usage: rein ship", out)
        run.assert_not_called()


class TestShipFlagValues(unittest.TestCase):
    def test_flag_without_its_value_is_usage(self):
        for argv in (["c1", "--agent"], ["c1", "--agent", "--model", "x"], ["c1", "--agent="],
                     ["c1", "--agent", "codex", "--model"], ["c1", "--agent", "codex", "--model="]):
            with self.subTest(argv=argv), mock.patch.object(rein_cli._ship, "run_ship") as run, \
                    mock.patch.object(rein_cli._ship, "OrcaClient"):
                code, out = _run(argv)
                self.assertEqual(code, 2)
                self.assertIn("usage: rein ship", out)
                run.assert_not_called()


class TestUsageText(unittest.TestCase):
    def test_usage_shows_optional_model(self):
        _, out = _run([])
        self.assertIn("[--agent <id> [--model <m>]]", out)


if __name__ == "__main__":
    unittest.main()
