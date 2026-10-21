"""`rein quick`: argv parsing and routing to quick_driver, nothing else."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")


def _load_cli():
    path = os.path.join(PLUGIN, "bin", "rein")
    loader = importlib.machinery.SourceFileLoader("rein_cli_quick", path)
    spec = importlib.util.spec_from_loader("rein_cli_quick", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


rein_cli = _load_cli()


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = rein_cli.cmd_quick(argv)
    return code, out.getvalue()


class TestQuickCli(unittest.TestCase):
    def test_quick_usage_errors(self):
        for argv in (
            [],                                              # no sentence
            ["--repo", "api"],                               # flags but no sentence
            ["arregla", "el", "botón"],                      # unquoted sentence: extra words
            ["done", "now"],                                 # extra word after done
            ["fix it", "--model", "gpt-5"],                  # --model without --agent
            ["fix it", "--agent", "codex", "--model"],       # dangling --model
            ["fix it", "--agent", "--model", "x"],           # flag taken as a value
            ["fix it", "--agent"],                           # dangling --agent
            ["fix it", "--agent=", "--model=x"],             # empty --agent=
            ["fix it", "--dirty-ok"],                        # --dirty-ok without --here
        ):
            with self.subTest(argv=argv), mock.patch.object(rein_cli._quick, "start_quick") as start, \
                    mock.patch.object(rein_cli._quick, "done_quick") as done, \
                    mock.patch.object(rein_cli._quick, "OrcaClient") as client:
                code, out = _run(argv)
                self.assertEqual(code, 2)
                self.assertTrue(out.startswith("usage: rein quick"), out)
                self.assertEqual(len(out.strip().splitlines()), 1)
                start.assert_not_called()
                done.assert_not_called()
                client.assert_not_called()

    def test_quick_routes_start_and_done(self):
        client = object()
        with mock.patch.object(rein_cli._quick, "OrcaClient", return_value=client), \
                mock.patch.object(rein_cli._quick, "start_quick", return_value=0) as start:
            code, _ = _run(["arregla el botón", "--repo", "api", "--base", "main",
                            "--agent", "codex", "--model", "gpt-5", "--linear", "PPR-12"])
            self.assertEqual(code, 0)
            start.assert_called_once_with(
                "arregla el botón", repo="api", base="main", here=False, dirty_ok=False,
                agent="codex", model="gpt-5", linear="PPR-12", cwd=".", client=client, allow_config="")
        with mock.patch.object(rein_cli._quick, "OrcaClient", return_value=client), \
                mock.patch.object(rein_cli._quick, "start_quick", return_value=1) as start:
            code, _ = _run(["--here", "--dirty-ok", "tweak the header"])
            self.assertEqual(code, 1)
            start.assert_called_once_with(
                "tweak the header", repo="", base="", here=True, dirty_ok=True,
                agent="", model="", linear="", cwd=".", client=client, allow_config="")
        with mock.patch.object(rein_cli._quick, "OrcaClient", return_value=client), \
                mock.patch.object(rein_cli._quick, "start_quick") as start, \
                mock.patch.object(rein_cli._quick, "done_quick", return_value=126) as done:
            code, _ = _run(["done"])
            self.assertEqual(code, 126)
            done.assert_called_once_with(cwd=".", client=client)
            start.assert_not_called()

    def test_quick_lone_agent_routes_with_empty_model(self):
        client = object()
        with mock.patch.object(rein_cli._quick, "OrcaClient", return_value=client), \
                mock.patch.object(rein_cli._quick, "start_quick", return_value=0) as start:
            code, _ = _run(["fix it", "--agent", "codex"])
            self.assertEqual(code, 0)
            start.assert_called_once_with(
                "fix it", repo="", base="", here=False, dirty_ok=False,
                agent="codex", model="", linear="", cwd=".", client=client, allow_config="")

    def test_quick_is_registered(self):
        self.assertIs(rein_cli.COMMANDS["quick"], rein_cli.cmd_quick)


class TestQuickCloseParsing(unittest.TestCase):
    def run_close(self, argv):
        with mock.patch.object(rein_cli._done, "run_quick_close", return_value=0) as close, \
                mock.patch.object(rein_cli._quick, "start_quick", return_value=0) as start, \
                mock.patch.object(rein_cli._quick, "OrcaClient"), \
                mock.patch.object(rein_cli._ship, "OrcaClient"):
            code, out = _run(argv)
        return code, out, close, start

    def test_close_with_quick_flags_is_usage_not_a_task(self):
        for argv in (["close", "--repo", "x"], ["close", "--base", "develop"], ["--repo", "x", "close"],
                     ["close", "--here"], ["close", "quick-a", "--repo", "x"], ["close", "--nope"]):
            with self.subTest(argv=argv):
                code, out, close, start = self.run_close(argv)
                self.assertEqual(code, 2, out)
                self.assertIn("rein quick close", out)
                start.assert_not_called()
                close.assert_not_called()

    def test_close_forms(self):
        for argv, target, dry, yes in ((["close"], "", False, False),
                                       (["close", "--dry-run"], "", True, False),
                                       (["close", "quick-a", "--yes"], "quick-a", False, True),
                                       (["close", "--yes", "/w/quick-a"], "/w/quick-a", False, True)):
            with self.subTest(argv=argv):
                code, out, close, start = self.run_close(argv)
                self.assertEqual(code, 0, out)
                start.assert_not_called()
                self.assertEqual(close.call_args[0][0], target)
                self.assertEqual((close.call_args[1]["dry_run"], close.call_args[1]["yes"]), (dry, yes))

    def test_a_quoted_sentence_starting_with_close_is_a_task(self):
        code, out, close, start = self.run_close(["close the modal on save"])
        self.assertEqual(code, 0, out)
        close.assert_not_called()
        start.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestAllowConfig(unittest.TestCase):
    def test_allow_config_reaches_the_start(self):
        with mock.patch.object(rein_cli._quick, "start_quick", return_value=0) as start, \
                mock.patch.object(rein_cli._quick, "OrcaClient"):
            code, _ = _run(["add the env", "--allow-config", "flow.config.json"])
        self.assertEqual(code, 0)
        self.assertEqual(start.call_args[1]["allow_config"], "flow.config.json")
