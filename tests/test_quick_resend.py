"""`rein quick resend`: send a quick agent its brief again.

Codex opened on a "Hooks need review" dialog and the brief Orca typed at launch was lost
behind it; it had to be rebuilt by hand from `.rein/quick.json` and pasted through Orca.
`rein quick` now keeps the brief it sent in the record, and `rein quick resend` types that
same text into the agent's terminal once the owner has cleared the dialog."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import quick  # noqa: E402
import quick_inbox  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from test_quick_cli import rein_cli  # noqa: E402
from test_quick_driver import DriverCase, _Git  # noqa: E402
from test_quick_inbox import Case, Orca  # noqa: E402


class StartKeepsTheBrief(DriverCase):
    def test_the_record_holds_the_brief_that_was_sent(self):
        code, orca, _ = self.run_quick(here=True, git=_Git(branch="feature/x"))
        self.assertEqual(code, 0, self.text())
        [argv] = orca.launches()
        self.assertEqual(self.record(self.repo_path)["brief"], argv[argv.index("--prompt") + 1])


class Resend(Case):
    def client(self, orca):
        return OrcaClient(executable="ORCA", runner=orca)

    def set(self, **fields):
        record = self.record()
        record.update(fields)
        for k in [k for k, v in fields.items() if v is None]:
            record.pop(k)
        with open(os.path.join(self.ws, quick.RECORD), "w") as fh:
            json.dump(record, fh)

    def test_resend_types_the_saved_brief_into_the_agents_terminal(self):
        self.set(brief="arregla x\nIteramos juntos")
        orca = Orca()
        code = quick_inbox.resend(self.ws, self.client(orca), now=lambda: 9.0, say=self.out.append)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.sent(), [("term_worker", "arregla x\nIteramos juntos")])
        self.assertEqual(self.record()["resent_at"], 9.0)

    def test_a_record_without_a_brief_rebuilds_it(self):
        orca = Orca()
        self.assertEqual(quick_inbox.resend(self.ws, self.client(orca), say=self.out.append), 0, self.text())
        (_, text), = orca.sent()
        self.assertTrue(text.startswith("arregla x"))
        self.assertIn("rein quick done", text)

    def test_without_a_recorded_terminal_the_only_agent_tab_is_used(self):
        self.set(terminal=None)
        orca = Orca(terminals=[{"handle": "term_setup", "title": "Setup", "worktreePath": self.ws},
                               {"handle": "term_codex", "title": "codex", "worktreePath": self.ws}])
        self.assertEqual(quick_inbox.resend(self.ws, self.client(orca), say=self.out.append), 0, self.text())
        self.assertEqual(orca.sent()[0][0], "term_codex")

    def test_several_candidate_tabs_are_not_guessed(self):
        self.set(terminal=None)
        orca = Orca(terminals=[{"handle": "term_a", "title": "codex", "worktreePath": self.ws},
                               {"handle": "term_b", "title": "zsh", "worktreePath": self.ws}])
        self.assertEqual(quick_inbox.resend(self.ws, self.client(orca), say=self.out.append), 2)
        self.assertEqual(orca.sent(), [])
        self.assertIn("--terminal", self.text())
        self.assertEqual(quick_inbox.resend(self.ws, self.client(orca), terminal="term_b",
                                            say=self.out.append), 0)

    def test_an_orca_failure_is_126(self):
        self.assertEqual(quick_inbox.resend(self.ws, self.client(Orca(fail_send=True)), say=self.out.append), 126)

    def test_no_quick_task_is_1(self):
        os.remove(os.path.join(self.ws, quick.RECORD))
        self.assertEqual(quick_inbox.resend(self.ws, self.client(Orca()), say=self.out.append), 1)


class Cli(unittest.TestCase):
    def test_routing(self):
        from unittest import mock
        import contextlib
        import io
        with mock.patch.object(rein_cli._inbox, "resend", return_value=0) as fn, \
                mock.patch.object(rein_cli._quick, "OrcaClient"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rein_cli.cmd_quick(["resend", "quick-fix", "--terminal", "term_x"]), 0)
        self.assertEqual(fn.call_args[0][0], "quick-fix")
        self.assertEqual(fn.call_args[1]["terminal"], "term_x")


if __name__ == "__main__":
    unittest.main()
