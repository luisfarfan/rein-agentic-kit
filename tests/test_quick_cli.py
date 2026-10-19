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
                agent="codex", model="gpt-5", linear="PPR-12", cwd=".", client=client)
        with mock.patch.object(rein_cli._quick, "OrcaClient", return_value=client), \
                mock.patch.object(rein_cli._quick, "start_quick", return_value=1) as start:
            code, _ = _run(["--here", "--dirty-ok", "tweak the header"])
            self.assertEqual(code, 1)
            start.assert_called_once_with(
                "tweak the header", repo="", base="", here=True, dirty_ok=True,
                agent="", model="", linear="", cwd=".", client=client)
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
                agent="codex", model="", linear="", cwd=".", client=client)

    def test_quick_is_registered(self):
        self.assertIs(rein_cli.COMMANDS["quick"], rein_cli.cmd_quick)


if __name__ == "__main__":
    unittest.main()
