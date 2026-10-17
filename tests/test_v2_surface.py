"""v2 surface: the CLI keeps what Rein decides and drops what Orca replaces."""

from __future__ import annotations

import importlib.util
import os
import unittest
from importlib.machinery import SourceFileLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")
LIB = os.path.join(PLUGIN, "lib")
REIN_BIN = os.path.join(PLUGIN, "bin", "rein")


def _load_cli():
    # An explicit loader: `bin/rein` has no `.py` extension.
    loader = SourceFileLoader("rein_cli", REIN_BIN)
    spec = importlib.util.spec_from_file_location("rein_cli", REIN_BIN, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


rein_cli = _load_cli()


class TestV2Surface(unittest.TestCase):
    def test_cli_commands_are_the_v2_surface(self):
        self.assertEqual(sorted(rein_cli.COMMANDS), sorted([
            "detect", "verify", "gate", "role", "tasks", "plan-check", "context", "close",
            "next", "review", "token-report", "ledger", "baseline", "serve-probe", "doctor", "ship",
            "migrate-config"]))

    def test_removed_modules_are_gone(self):
        for name in ("dashboard", "events", "product_state", "backlog", "workspace", "setup",
                     "linear_client", "linear_issue", "linear_select", "linear_intake",
                     "version_staleness", "ansi"):
            self.assertFalse(os.path.exists(os.path.join(LIB, name + ".py")), name)
        self.assertFalse(os.path.exists(os.path.join(PLUGIN, "workflows", "loop.js")))


if __name__ == "__main__":
    unittest.main()
