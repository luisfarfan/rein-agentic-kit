"""Quick lane, three gaps of the retest.

- `rein quick allow-config <workspace> <path,...>` grants --allow-config to a quick already
  running: the record is updated and the agent is told in its own terminal (the orchestrator had
  to discard and relaunch before);
- the brief of a quick (and of a ship task) names the config files its task is authorized to
  change, so the agent does not ask about them;
- the agent's report travels as text: `rein quick report "<text or file>"` stores it in the
  record (and `.rein/quick-report.md`), the brief asks for it, and `rein quick watch` prints it
  without anyone reading the agent's screen."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import guard  # noqa: E402
import quick  # noqa: E402
import quick_inbox  # noqa: E402
import ship  # noqa: E402
import ship_state as ss  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from test_quick_cli import _run, rein_cli  # noqa: E402
from test_quick_driver import DriverCase  # noqa: E402
from test_quick_inbox import Case, Orca  # noqa: E402


class AllowConfig(Case):
    def client(self, orca):
        return OrcaClient(executable="ORCA", runner=orca)

    def test_it_updates_the_record_and_tells_the_agent(self):
        orca = Orca()
        code = quick_inbox.allow_config(self.ws, "flow.config.json, mise.toml", self.client(orca),
                                        say=self.out.append)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(self.record()["allow_config"], ["flow.config.json", "mise.toml"])
        [(handle, message)] = orca.sent()
        self.assertEqual(handle, "term_worker")
        self.assertIn("flow.config.json", message)
        self.assertIn("mise.toml", message)

    def test_it_adds_to_what_was_granted_at_the_start(self):
        record = self.record()
        record["allow_config"] = ["flow.config.json"]
        with open(os.path.join(self.ws, quick.RECORD), "w") as fh:
            json.dump(record, fh)
        quick_inbox.allow_config(self.ws, "flow.config.json,Makefile", self.client(Orca()), say=self.out.append)
        self.assertEqual(self.record()["allow_config"], ["flow.config.json", "Makefile"])

    def test_the_guard_then_lets_that_file_change(self):
        quick_inbox.allow_config(self.ws, "flow.config.json", self.client(Orca()), say=self.out.append)
        allowed = guard.declared_paths(self.record()["allow_config"])
        self.assertEqual(guard.config_changes(["flow.config.json", "mise.toml"], ["flow.config.json", "mise.toml"],
                                              allowed), ["mise.toml"])

    def test_an_unreachable_terminal_still_records_and_says_what_to_tell_the_agent(self):
        code = quick_inbox.allow_config(self.ws, "flow.config.json", self.client(Orca(fail_send=True)),
                                        say=self.out.append)
        self.assertEqual(code, 126)
        self.assertEqual(self.record()["allow_config"], ["flow.config.json"])
        self.assertIn("flow.config.json", self.text())

    def test_no_paths_or_no_quick_task(self):
        self.assertEqual(quick_inbox.allow_config(self.ws, "  ", self.client(Orca()), say=self.out.append), 2)
        os.remove(os.path.join(self.ws, quick.RECORD))
        self.assertEqual(quick_inbox.allow_config(self.ws, "a", self.client(Orca()), say=self.out.append), 1)

    def test_the_command_routes_to_it(self):
        with unittest.mock.patch.object(rein_cli._inbox, "allow_config", return_value=0) as called:
            code, _ = _run(["allow-config", "/w/quick-x", "flow.config.json"])
        self.assertEqual(code, 0)
        self.assertEqual(called.call_args[0][:2], ("/w/quick-x", "flow.config.json"))
        self.assertEqual(_run(["allow-config", "/w/quick-x"])[0], 2)


class Brief(unittest.TestCase):
    def test_a_quick_brief_names_the_authorized_config(self):
        brief = quick.build_brief("x", allowed_config=["flow.config.json", "mise.toml"])
        self.assertIn("AUTORIZA", brief)
        self.assertIn("flow.config.json, mise.toml", brief)

    def test_without_authorization_it_says_nothing_is_authorized(self):
        self.assertNotIn("AUTORIZA", quick.build_brief("x"))

    def test_the_brief_asks_for_the_report_through_rein(self):
        self.assertIn("rein quick report", quick.build_brief("x"))

    def test_a_ship_task_names_its_authorized_config(self):
        task = ss.TaskState(id="T001", repo="api", deps=[], human_review=False, verification="x", type="code")
        spec = ship.build_task_spec(task, {"tasks": []}, allowed_config=["flow.config.json"])
        self.assertIn("flow.config.json", spec.split("Gate config you may change")[1])
        self.assertNotIn("Gate config you may change", ship.build_task_spec(task, {"tasks": []}))


class StartBrief(DriverCase):
    def test_allow_config_at_the_start_reaches_the_brief(self):
        code, orca, _ = self.run_quick(allow_config="flow.config.json,mise.toml")
        self.assertEqual(code, 0, self.text())
        record = self.record(self.worktree)
        self.assertIn("flow.config.json, mise.toml", record["brief"])


class Report(Case):
    def test_a_text_report_is_recorded_and_written(self):
        code = quick_inbox.report(self.ws, "Cambié foo.ts; tests verdes.", now=lambda: 9.0, say=self.out.append)
        self.assertEqual(code, 0, self.text())
        [r] = self.record()["reports"]
        self.assertEqual((r["id"], r["text"], r["at"]), ("r1", "Cambié foo.ts; tests verdes.", 9.0))
        with open(os.path.join(self.ws, ".rein", "quick-report.md")) as fh:
            self.assertIn("Cambié foo.ts", fh.read())

    def test_a_file_argument_is_read(self):
        path = os.path.join(self.ws, "informe.md")
        with open(path, "w") as fh:
            fh.write("## Informe\nTodo bien\n")
        self.assertEqual(quick_inbox.report(self.ws, path, say=self.out.append), 0)
        self.assertIn("Todo bien", self.record()["reports"][0]["text"])

    def test_a_second_report_gets_the_next_id(self):
        quick_inbox.report(self.ws, "uno", say=self.out.append)
        quick_inbox.report(self.ws, "dos", say=self.out.append)
        self.assertEqual([r["id"] for r in self.record()["reports"]], ["r1", "r2"])

    def test_empty_or_no_quick_task_is_usage(self):
        self.assertEqual(quick_inbox.report(self.ws, " ", say=self.out.append), 2)
        os.remove(os.path.join(self.ws, quick.RECORD))
        self.assertEqual(quick_inbox.report(self.ws, "x", say=self.out.append), 2)

    def test_watch_prints_each_report_once(self):
        quick_inbox.report(self.ws, "Cambié foo.ts", say=self.out.append)
        client = OrcaClient(executable="ORCA", runner=Orca(worktrees=[self.ws]))
        buf = io.StringIO()
        printed = set()
        with contextlib.redirect_stdout(buf):
            first = quick_inbox._poll("", client, self.ws, printed)
            second = quick_inbox._poll("", client, self.ws, printed)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertIn("REPORT %s r1" % self.ws, buf.getvalue())
        self.assertIn("Cambié foo.ts", buf.getvalue())

    def test_the_command_routes_to_it(self):
        with unittest.mock.patch.object(rein_cli._inbox, "report", return_value=0) as called:
            code, _ = _run(["report", "listo"])
        self.assertEqual(code, 0)
        self.assertEqual(called.call_args[0][1], "listo")
        self.assertEqual(_run(["report"])[0], 2)


if __name__ == "__main__":
    unittest.main()
