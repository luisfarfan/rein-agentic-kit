"""`rein ship --detach` and `rein answer`: the owner never types Orca commands.

A fake runner records argv and answers like Orca; nothing shells out to `orca`.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import orca_client  # noqa: E402
import ship  # noqa: E402
import ship_answer  # noqa: E402
import ship_state  # noqa: E402
from orca_client import OrcaClient  # noqa: E402

PLAN = """# Change: demo

## Why
Ship the demo.

- [ ] T001 Add the api endpoint
  - Verification: `pnpm test`
  - Acceptance:
    - the endpoint answers 200
"""


class FakeOrca:
    def __init__(self, repo_name="plan-repo", gates=None, terminals=None, coordinator="term_coord"):
        self.calls = []
        self.repo_name = repo_name
        self.gates = gates or []
        self.terminals = terminals or []
        self.coordinator = coordinator

    def __call__(self, argv):
        self.calls.append(list(argv))
        a = [x for x in argv[1:] if x != "--json"]
        result = {}
        if a[:1] == ["status"]:
            result = {"runtime": {"capabilities": [ship.CONTRACT]}}
        elif a[:2] == ["repo", "list"]:
            result = {"repos": [{"id": "r1", "displayName": self.repo_name}]}
        elif a[:2] == ["terminal", "list"]:
            result = {"terminals": self.terminals}
        elif a[:2] == ["terminal", "create"]:
            result = {"terminal": {"handle": "term_new"}}
        elif a[1:2] == ["run-show"]:
            result = {"run": {"id": a[3], "coordinator_handle": self.coordinator}}
        elif a[1:2] == ["gate-list"]:
            result = {"gates": self.gates}
        elif a[1:2] == ["gate-resolve"]:
            result = {"gate": {"id": a[3], "status": "resolved"}}
        return 0, json.dumps({"ok": True, "result": result})

    def commands(self, *prefix):
        return [c for c in self.calls if c[1:1 + len(prefix)] == list(prefix)]


def _gate(gid, task="holder_1", question="Which db?", options=None, status="pending"):
    return {"id": gid, "run_id": "run_1", "task_id": task, "question": question,
            "options": json.dumps(options) if options is not None else None,
            "status": status, "resolution": None}


class UxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "plan-repo")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write(PLAN)

    def client(self, fake):
        return OrcaClient(executable="ORCA", runner=fake)

    def write_state(self, change, gates, run_id="run_1"):
        state = ship_state.ShipState(change=change, plan_path="", run_id=run_id)
        state.gates = {gid: {"task_id": "T001", "kind": kind, "message_id": "", "audit": "",
                             "holder_task": "h"} for gid, kind in gates.items()}
        ship_state.save(state, ship_state.state_path(self.root, change))

    def capture(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fn(*args, **kw)
        return code, buf.getvalue()


class TestClientArgv(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOrca()
        self.c = OrcaClient(executable="ORCA", runner=self.fake)

    def test_terminal_create(self):
        self.c.terminal_create("path:/w", "rein ship demo", "rein ship demo --root /w")
        self.assertEqual(self.fake.calls[-1], [
            "ORCA", "terminal", "create", "--worktree", "path:/w", "--title", "rein ship demo",
            "--command", "rein ship demo --root /w", "--json"])

    def test_terminal_list(self):
        self.c.terminal_list()
        self.assertEqual(self.fake.calls[-1], ["ORCA", "terminal", "list", "--json"])
        self.c.terminal_list("path:/w")
        self.assertEqual(self.fake.calls[-1], ["ORCA", "terminal", "list", "--worktree", "path:/w", "--json"])

    def test_run_show(self):
        self.c.run_show("run_1")
        self.assertEqual(self.fake.calls[-1], ["ORCA", "orchestration", "run-show", "--id", "run_1", "--json"])

    def test_gate_resolve_carries_from(self):
        self.c.gate_resolve("g1", "yes please", "term_coord")
        self.assertEqual(self.fake.calls[-1], [
            "ORCA", "orchestration", "gate-resolve", "--id", "g1", "--resolution", "yes please",
            "--from", "term_coord", "--json"])

    def test_gate_list_defaults_are_unchanged_and_run_is_optional(self):
        self.c.gate_list()
        self.assertEqual(self.fake.calls[-1], ["ORCA", "orchestration", "gate-list", "--json"])
        self.c.gate_list("run_1")
        self.assertEqual(self.fake.calls[-1],
                         ["ORCA", "orchestration", "gate-list", "--run", "run_1", "--json"])

    def test_accessors(self):
        self.assertEqual(orca_client.terminal_handle_of({"terminal": {"handle": "t1"}}), "t1")
        self.assertEqual(orca_client.terminal_handle_of({}), "")
        self.assertEqual(orca_client.terminals_of({"terminals": [{"handle": "a"}, 3]}), [{"handle": "a"}])
        self.assertEqual(orca_client.coordinator_of({"run": {"coordinator_handle": "term_c"}}), "term_c")
        self.assertEqual(orca_client.coordinator_of({}), "")


class TestDetach(UxCase):
    def detach(self, fake, **kw):
        return self.capture(ship.detach_ship, self.root, "demo", self.client(fake), **kw)

    def test_override_puts_agent_and_model_in_the_command(self):
        fake = FakeOrca()
        code, out = self.detach(fake, override=("codex", "gpt-6.1-sol"))
        self.assertEqual(code, 0, out)
        argv = fake.commands("terminal", "create")[0]
        command = shlex.split(argv[argv.index("--command") + 1])
        self.assertEqual(command[-4:], ["--agent", "codex", "--model", "gpt-6.1-sol"])

    def test_opens_exactly_one_terminal_with_the_quoted_command(self):
        fake = FakeOrca()
        code, out = self.detach(fake)
        self.assertEqual(code, 0, out)
        created = fake.commands("terminal", "create")
        self.assertEqual(len(created), 1)
        argv = created[0]
        self.assertEqual(argv[argv.index("--worktree") + 1], "path:" + os.path.abspath(self.root))
        self.assertEqual(argv[argv.index("--title") + 1], "rein ship demo")
        command = argv[argv.index("--command") + 1]
        self.assertEqual(command, shlex.join([sys.executable, ship.BIN, "ship", "demo",
                                              "--root", os.path.abspath(self.root)]))
        self.assertTrue(command.startswith(shlex.quote(sys.executable) + " " + shlex.quote(ship.BIN)))
        listed = fake.commands("terminal", "list")
        self.assertEqual(listed[0][3:5], ["--worktree", "path:" + os.path.abspath(self.root)])
        self.assertIn("term_new", out)
        self.assertIn("rein ship is running in the Orca tab 'rein ship demo'; you can close this session.", out)
        self.assertEqual(fake.commands("orchestration"), [], "detach must not run the loop")

    def test_paths_with_spaces_are_shell_quoted(self):
        spaced = os.path.join(self.tmp.name, "my plan")
        os.makedirs(spaced)
        with open(os.path.join(spaced, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write(PLAN)
        fake = FakeOrca(repo_name="my plan")
        code, out = self.capture(ship.detach_ship, spaced, "demo", self.client(fake))
        self.assertEqual(code, 0, out)
        command = fake.commands("terminal", "create")[0]
        command = command[command.index("--command") + 1]
        self.assertTrue(command.endswith("ship demo --root '%s'" % os.path.abspath(spaced)))

    def test_agent_override_is_forwarded(self):
        fake = FakeOrca()
        code, out = self.detach(fake, override=("codex", "gpt-6.1-sol"))
        self.assertEqual(code, 0, out)
        command = fake.commands("terminal", "create")[0]
        self.assertTrue(command[command.index("--command") + 1].endswith("--agent codex --model gpt-6.1-sol"))

    def test_refuses_when_a_live_driver_tab_exists(self):
        live = {"title": "rein ship demo", "handle": "term_old", "connected": True,
                "worktreePath": os.path.abspath(self.root)}
        fake = FakeOrca(terminals=[live])
        code, out = self.detach(fake)
        self.assertEqual(code, 1)
        self.assertIn("a 'rein ship demo' tab already exists in %s; if its run finished, "
                      "close that tab and try again" % os.path.abspath(self.root), out)
        self.assertEqual(fake.commands("terminal", "create"), [])

    def test_a_dead_or_foreign_tab_does_not_block(self):
        others = [
            {"title": "rein ship demo", "handle": "t1", "connected": False,
             "worktreePath": os.path.abspath(self.root)},
            {"title": "rein ship demo", "handle": "t2", "connected": True, "worktreePath": "/elsewhere"},
            {"title": "rein ship other", "handle": "t3", "connected": True,
             "worktreePath": os.path.abspath(self.root)},
        ]
        fake = FakeOrca(terminals=others)
        code, out = self.detach(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(fake.commands("terminal", "create")), 1)

    def test_plan_errors_surface_before_any_terminal_exists(self):
        os.remove(os.path.join(self.root, "tasks.md"))
        fake = FakeOrca()
        code, out = self.detach(fake)
        self.assertEqual(code, 126)
        self.assertIn("preflight", out)
        self.assertEqual(fake.commands("terminal", "create"), [])

    def test_does_not_need_to_run_inside_an_orca_terminal(self):
        env = {k: v for k, v in os.environ.items() if k != "ORCA_TERMINAL_HANDLE"}
        fake = FakeOrca()
        old = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            code, out = self.detach(fake)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(code, 0, out)

    def test_terminal_create_failure_exits_one(self):
        class Boom(FakeOrca):
            def __call__(self, argv):
                if argv[1:3] == ["terminal", "create"]:
                    return 1, json.dumps({"ok": False, "error": {"code": "nope", "message": "no worktree"}})
                return super().__call__(argv)
        code, out = self.detach(Boom())
        self.assertEqual(code, 1)
        self.assertIn("nope", out)


class TestDetachCli(unittest.TestCase):
    def test_detach_with_dry_run_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, BIN, "ship", "demo", "--detach", "--dry-run"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--detach", proc.stdout)


class TestAnswerCli(unittest.TestCase):
    def setUp(self):
        from importlib.machinery import SourceFileLoader
        import importlib.util
        loader = SourceFileLoader("rein_cli_ux", BIN)
        spec = importlib.util.spec_from_file_location("rein_cli_ux", BIN, loader=loader)
        self.cli = importlib.util.module_from_spec(spec)
        loader.exec_module(self.cli)
        self.seen = []
        self._real = self.cli._answer.run_answer
        self.cli._answer.run_answer = lambda *a: self.seen.append(a) or 0
        self.addCleanup(lambda: setattr(self.cli._answer, "run_answer", self._real))

    def test_argument_shapes(self):
        cases = [
            ([], ("." , "", "", "")),
            (["demo"], (".", "demo", "", "")),
            (["demo", "yes", "--gate", "g1", "--root", "/r"], ("/r", "demo", "yes", "g1")),
            (["yes", "--change", "demo"], (".", "demo", "yes", "")),
            (["--root=/r", "demo"], ("/r", "demo", "", "")),
        ]
        for argv, expected in cases:
            self.seen.clear()
            self.assertEqual(self.cli.main(["answer", *argv]), 0, argv)
            self.assertEqual(self.seen[0][:4], expected, argv)

    def test_too_many_words_or_unknown_flag_is_usage(self):
        for argv in (["a", "b", "c"], ["demo", "--nope"], ["demo", "--gate"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(self.cli.main(["answer", *argv]), 2, argv)
            self.assertIn("usage: rein answer", buf.getvalue())


class TestAnswerList(UxCase):
    def test_lists_an_open_question_gate(self):
        self.write_state("demo", {"g1": "question", "g2": "timeout"})
        fake = FakeOrca(gates=[
            _gate("g1", question="Which db?" + "x" * 600),
            _gate("g2", question="Retry?", options=["retry", "skip"], status="resolved")])
        code, out = self.capture(ship_answer.run_answer, self.root, "demo", None, "", self.client(fake))
        self.assertEqual(code, 0, out)
        self.assertIn("g1", out)
        self.assertIn("question", out)
        self.assertIn("T001", out)
        self.assertIn("Which db?", out)
        self.assertNotIn("x" * 501, out)
        self.assertNotIn("g2", out)
        self.assertEqual(fake.commands("orchestration", "gate-list")[0][-3:-1], ["--run", "run_1"])

    def test_lists_options(self):
        self.write_state("demo", {"g1": "timeout"})
        fake = FakeOrca(gates=[_gate("g1", question="Retry?", options=["retry", "skip"])])
        code, out = self.capture(ship_answer.run_answer, self.root, "demo", None, "", self.client(fake))
        self.assertEqual(code, 0)
        self.assertIn("retry, skip", out)

    def test_no_open_gates(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1", status="resolved")])
        code, out = self.capture(ship_answer.run_answer, self.root, "demo", None, "", self.client(fake))
        self.assertEqual(code, 0)
        self.assertIn("no questions pending", out)

    def test_no_state_at_all(self):
        code, out = self.capture(ship_answer.run_answer, self.root, "", None, "", self.client(FakeOrca()))
        self.assertEqual(code, 0)
        self.assertIn("no questions pending", out)

    def test_change_is_detected_when_exactly_one_has_open_gates(self):
        self.write_state("alpha", {"g1": "question"})
        self.write_state("beta", {"g9": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g9", status="resolved")])
        code, out = self.capture(ship_answer.run_answer, self.root, "", None, "", self.client(fake))
        self.assertEqual(code, 0, out)
        self.assertIn("g1", out)

    def test_several_changes_with_open_gates_exit_two(self):
        self.write_state("alpha", {"g1": "question"})
        self.write_state("beta", {"g2": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2")])
        code, out = self.capture(ship_answer.run_answer, self.root, "", None, "", self.client(fake))
        self.assertEqual(code, 2)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_a_corrupt_state_file_is_left_alone(self):
        self.write_state("demo", {"g1": "question"})
        bad = os.path.join(self.root, ".rein", "ship", "bad.json")
        with open(bad, "w") as fh:
            fh.write("{nope")
        fake = FakeOrca(gates=[_gate("g1")])
        code, out = self.capture(ship_answer.run_answer, self.root, "", None, "", self.client(fake))
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(bad), "a read-only command must not quarantine files")


class TestAnswerMailbox(UxCase):
    def answer(self, fake, env, text="REST"):
        return self.capture(ship_answer.run_answer, self.root, "demo", text, "", self.client(fake), env=env)

    def test_a_foreign_orca_terminal_queues_the_answer(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")], coordinator="term_coord")
        code, out = self.answer(fake, {"ORCA_TERMINAL_HANDLE": "term_other"})
        self.assertEqual(code, 0, out)
        self.assertIn("answered g1 (question): Rein will apply it within a few seconds", out)
        self.assertEqual(fake.commands("orchestration", "gate-resolve"), [])
        answers = ship_state.read_answers(self.root, "demo")
        self.assertEqual([(a["gate"], a["resolution"]) for a in answers], [("g1", "REST")])

    def test_outside_orca_resolves_directly(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")], coordinator="term_coord")
        code, out = self.answer(fake, {})
        self.assertEqual(code, 0, out)
        self.assertEqual(len(fake.commands("orchestration", "gate-resolve")), 1)
        self.assertEqual(ship_state.read_answers(self.root, "demo"), [])
        self.assertIn("answered g1 (question)", out)
        self.assertNotIn("Rein will apply", out)

    def test_the_coordinator_terminal_resolves_directly(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")], coordinator="term_coord")
        code, out = self.answer(fake, {"ORCA_TERMINAL_HANDLE": "term_coord"})
        self.assertEqual(code, 0, out)
        self.assertEqual(len(fake.commands("orchestration", "gate-resolve")), 1)

    def test_a_fenced_resolve_falls_back_to_the_mailbox(self):
        class Fenced(FakeOrca):
            def __call__(self, argv):
                if argv[1:3] == ["orchestration", "gate-resolve"]:
                    self.calls.append(list(argv))
                    return 1, json.dumps({"ok": False, "error": {
                        "code": "consumer_fenced", "message": "This terminal is attested as term_A"}})
                return super().__call__(argv)
        self.write_state("demo", {"g1": "question"})
        fake = Fenced(gates=[_gate("g1")], coordinator="term_coord")
        code, out = self.answer(fake, {})
        self.assertEqual(code, 0, out)
        self.assertIn("Rein will apply it within a few seconds", out)
        self.assertEqual([a["gate"] for a in ship_state.read_answers(self.root, "demo")], ["g1"])


class TestMailboxFiles(UxCase):
    def test_roundtrip_drop_and_malformed(self):
        ship_state.write_answer(self.root, "demo", "g1", "yes", 5)
        d = ship_state.answers_dir(self.root, "demo")
        with open(os.path.join(d, "bad.json"), "w") as fh:
            fh.write("{nope")
        with open(os.path.join(d, "worse.json"), "w") as fh:
            fh.write("[1]")
        self.assertEqual(ship_state.read_answers(self.root, "demo"),
                         [{"gate": "g1", "resolution": "yes", "at": 5}])
        ship_state.drop_answer(self.root, "demo", "g1")
        self.assertEqual(ship_state.read_answers(self.root, "demo"), [])
        self.assertTrue(os.path.exists(os.path.join(d, "bad.json")))
        ship_state.drop_answer(self.root, "demo", "missing")  # never raises
        self.assertEqual(ship_state.read_answers(self.root, "nothing-here"), [])


class TestAnswerResolve(UxCase):
    def resolve(self, fake, text, gate="", change="demo"):
        return self.capture(ship_answer.run_answer, self.root, change, text, gate, self.client(fake), env={})

    def test_free_text_on_a_question_gate_uses_the_coordinator(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")], coordinator="term_coordinator")
        path = ship_state.state_path(self.root, "demo")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        code, out = self.resolve(fake, "Use Postgres, please")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.commands("orchestration", "run-show")[0][-3:-1], ["--id", "run_1"])
        resolve = fake.commands("orchestration", "gate-resolve")[0]
        self.assertEqual(resolve, ["ORCA", "orchestration", "gate-resolve", "--id", "g1",
                                   "--resolution", "Use Postgres, please",
                                   "--from", "term_coordinator", "--json"])
        self.assertIn("answered g1 (question)", out)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_option_gate_matches_case_insensitively(self):
        self.write_state("demo", {"g1": "timeout"})
        fake = FakeOrca(gates=[_gate("g1", options=["retry", "skip"])])
        code, out = self.resolve(fake, "  Retry ")
        self.assertEqual(code, 0, out)
        resolve = fake.commands("orchestration", "gate-resolve")[0]
        self.assertEqual(resolve[resolve.index("--resolution") + 1], "retry")
        self.assertIn("answered g1 (timeout)", out)

    def test_invalid_option_exits_two_and_lists_options(self):
        self.write_state("demo", {"g1": "timeout"})
        fake = FakeOrca(gates=[_gate("g1", options=["retry", "skip"])])
        code, out = self.resolve(fake, "maybe")
        self.assertEqual(code, 2)
        self.assertIn("retry, skip", out)
        self.assertEqual(fake.commands("orchestration", "gate-resolve"), [])

    def test_several_open_gates_require_gate(self):
        self.write_state("demo", {"g1": "question", "g2": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2")])
        code, out = self.resolve(fake, "x")
        self.assertEqual(code, 2)
        self.assertIn("--gate", out)
        self.assertIn("g1", out)
        self.assertEqual(fake.commands("orchestration", "gate-resolve"), [])

    def test_gate_flag_picks_one(self):
        self.write_state("demo", {"g1": "question", "g2": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2")])
        code, out = self.resolve(fake, "x", gate="g2")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.commands("orchestration", "gate-resolve")[0][4], "g2")

    def test_unknown_gate_exits_two(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")])
        code, out = self.resolve(fake, "x", gate="g9")
        self.assertEqual(code, 2)
        self.assertIn("g1", out)

    def test_nothing_to_answer_exits_one(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1", status="resolved")])
        code, out = self.resolve(fake, "x")
        self.assertEqual(code, 1)
        self.assertIn("no questions pending", out)

    def test_change_detection_applies_to_answers_too(self):
        self.write_state("alpha", {"g1": "question"})
        self.write_state("beta", {"g2": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2", status="resolved")])
        code, out = self.resolve(fake, "go", change="")
        self.assertEqual(code, 0, out)

    def test_orca_error_prints_code_and_exits_one(self):
        self.write_state("demo", {"g1": "question"})

        class Boom(FakeOrca):
            def __call__(self, argv):
                if argv[2:3] == ["gate-resolve"]:
                    return 1, json.dumps({"ok": False, "error": {"code": "gate_resolved", "message": "already"}})
                return super().__call__(argv)
        code, out = self.resolve(Boom(gates=[_gate("g1")]), "x")
        self.assertEqual(code, 1)
        self.assertIn("gate_resolved", out)
        self.assertIn("already", out)

    def test_missing_run_id_exits_one(self):
        self.write_state("demo", {"g1": "question"}, run_id="")
        code, out = self.resolve(FakeOrca(gates=[_gate("g1")]), "x")
        self.assertEqual(code, 1)

    def test_missing_coordinator_exits_one(self):
        self.write_state("demo", {"g1": "question"})
        code, out = self.resolve(FakeOrca(gates=[_gate("g1")], coordinator=""), "x")
        self.assertEqual(code, 1)
        self.assertIn("coordinator", out)


if __name__ == "__main__":
    unittest.main()
