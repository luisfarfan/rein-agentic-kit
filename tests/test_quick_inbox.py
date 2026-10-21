"""Quick tasks ask through Rein, like ship workers do.

A Codex quick worker stopped to ask "necesito un ejemplo del payload" in its own terminal:
nobody was woken, because the orchestrator only hears what Rein tells it. Now:

- the worker runs `rein quick ask "<question>"`: the question lands in `.rein/quick.json`
  (the one file `rein quick close` deletes) and the worker waits;
- the orchestrator's `rein quick watch` (run in the background, like `rein watch`) prints it;
- `rein quick answer <workspace> "<text>"` records the answer and types it into the
  worker's terminal, whose handle the start recorded.
The answers are the owner's decisions, so a quick audit's brief lists them too."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import quick  # noqa: E402
import quick_inbox  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from test_quick_cli import rein_cli  # noqa: E402


class Orca:
    def __init__(self, worktrees=(), terminals=(), fail_send=False):
        self.calls, self.worktrees, self.terminals, self.fail_send = [], list(worktrees), list(terminals), fail_send

    def __call__(self, argv):
        self.calls.append(list(argv))
        head = argv[1:3]
        if head == ["worktree", "list"]:
            return 0, json.dumps({"ok": True, "result": {"worktrees": [{"path": p} for p in self.worktrees]}})
        if head == ["terminal", "list"]:
            return 0, json.dumps({"ok": True, "result": {"terminals": self.terminals}})
        if head == ["terminal", "send"]:
            if self.fail_send:
                return 1, json.dumps({"ok": False, "error": {"code": "terminal_gone", "message": "no such terminal"}})
            return 0, json.dumps({"ok": True, "result": {"accepted": True}})
        return 1, json.dumps({"ok": False, "error": {"code": "unknown", "message": "no"}})

    def sent(self):
        return [(c[c.index("--terminal") + 1], c[c.index("--text") + 1]) for c in self.calls
                if c[1:3] == ["terminal", "send"]]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = os.path.join(self.tmp, "quick-fix")
        os.makedirs(self.ws)
        subprocess.run(["git", "init", "-q", self.ws], check=True)
        subprocess.run(["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "base"], check=True)
        sha = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
        record = quick.new_record("api", self.ws, "develop", sha, "codex", "gpt", "arregla x", "", 1.0)
        record["terminal"] = "term_worker"
        os.makedirs(os.path.join(self.ws, ".rein"))
        with open(os.path.join(self.ws, quick.RECORD), "w") as fh:
            json.dump(record, fh)
        self.out = []

    def record(self):
        with open(os.path.join(self.ws, quick.RECORD)) as fh:
            return json.load(fh)

    def text(self):
        return "\n".join(self.out)


class Ask(Case):
    def test_ask_records_the_question_and_tells_the_worker_to_wait(self):
        code = quick_inbox.ask(os.path.join(self.ws), "¿Me pasas un ejemplo del payload?", now=lambda: 50.0,
                               say=self.out.append)
        self.assertEqual(code, 0)
        questions = self.record()["questions"]
        self.assertEqual(questions, [{"id": "q1", "text": "¿Me pasas un ejemplo del payload?", "asked_at": 50.0,
                                      "answer": None, "answered_at": None}])
        self.assertIn("q1", self.text())
        self.assertIn("espera", self.text())

    def test_ask_outside_a_quick_task_is_usage(self):
        os.remove(os.path.join(self.ws, quick.RECORD))
        self.assertEqual(quick_inbox.ask(self.ws, "x", say=self.out.append), 2)

    def test_an_empty_question_is_usage(self):
        self.assertEqual(quick_inbox.ask(self.ws, "  ", say=self.out.append), 2)


class Answer(Case):
    def client(self, orca):
        return OrcaClient(executable="ORCA", runner=orca)

    def test_answer_records_it_and_types_it_into_the_workers_terminal(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 1.0, say=self.out.append)
        orca = Orca()
        code = quick_inbox.answer(self.ws, "usa el de tests/fixtures/receipt.json", self.client(orca),
                                  now=lambda: 2.0, say=self.out.append)
        self.assertEqual(code, 0, self.text())
        q = self.record()["questions"][0]
        self.assertEqual((q["answer"], q["answered_at"]), ("usa el de tests/fixtures/receipt.json", 2.0))
        (handle, text), = orca.sent()
        self.assertEqual(handle, "term_worker")
        self.assertIn("usa el de tests/fixtures/receipt.json", text)
        self.assertIn("q1", text)

    def test_answer_by_quick_name_from_outside(self):
        quick_inbox.ask(self.ws, "¿payload?", say=self.out.append)
        orca = Orca(worktrees=[self.ws])
        code = quick_inbox.answer("quick-fix", "sí", self.client(orca), cwd=self.tmp, say=self.out.append)
        self.assertEqual(code, 0, self.text())

    def test_no_open_question_is_a_failure(self):
        code = quick_inbox.answer(self.ws, "sí", self.client(Orca()), say=self.out.append)
        self.assertEqual(code, 1)
        self.assertIn("no open question", self.text())

    def test_a_terminal_that_cannot_be_reached_keeps_the_answer_and_says_so(self):
        quick_inbox.ask(self.ws, "¿payload?", say=self.out.append)
        code = quick_inbox.answer(self.ws, "sí", self.client(Orca(fail_send=True)), say=self.out.append)
        self.assertEqual(code, 126)
        self.assertEqual(self.record()["questions"][0]["answer"], "sí")
        self.assertIn("tell the agent", self.text())


class Watch(Case):
    def client(self, orca):
        return OrcaClient(executable="ORCA", runner=orca)

    def test_watch_prints_a_pending_question(self):
        quick_inbox.ask(self.ws, "¿payload?", say=self.out.append)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch(self.ws, self.client(Orca()), once=True, clock=lambda: 2.0)
        self.assertEqual(code, 0)
        self.assertIn("QUESTION", out.getvalue())
        self.assertIn("¿payload?", out.getvalue())
        self.assertIn("rein quick answer", out.getvalue())

    def test_watch_once_with_nothing_pending_is_1(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(quick_inbox.watch(self.ws, self.client(Orca()), once=True, clock=lambda: 2.0), 1)

    def test_watch_blocks_until_a_question_arrives(self):
        polls = []

        def sleep(_s):
            polls.append(1)
            if len(polls) == 2:
                quick_inbox.ask(self.ws, "¿ahora?", say=lambda _l: None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch(self.ws, self.client(Orca()), sleep=sleep, clock=lambda: 2.0)
        self.assertEqual(code, 0)
        self.assertIn("¿ahora?", out.getvalue())

    def test_watch_without_argument_finds_the_quick_worktrees(self):
        quick_inbox.ask(self.ws, "¿payload?", say=self.out.append)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch("", self.client(Orca(worktrees=[self.ws])), cwd=self.tmp, once=True,
                                     clock=lambda: 2.0)
        self.assertEqual(code, 0)
        self.assertIn(self.ws, out.getvalue())


class Brief(unittest.TestCase):
    def test_the_brief_says_to_ask_through_rein(self):
        self.assertIn("rein quick ask", quick.build_brief("x"))


class Audit(unittest.TestCase):
    def test_answered_questions_are_owner_decisions(self):
        record = {"questions": [{"id": "q1", "text": "¿payload?", "answer": "el de fixtures"},
                                {"id": "q2", "text": "¿y esto?", "answer": None}]}
        self.assertEqual(quick_inbox.owner_decisions(record), [
            {"gate": "q1", "kind": "question", "task_id": None, "question": "¿payload?", "answer": "el de fixtures"}])


class Cli(unittest.TestCase):
    def run_cli(self, argv, target):
        out = io.StringIO()
        with mock.patch.object(rein_cli._inbox, target, return_value=0) as fn, \
                mock.patch.object(rein_cli._quick, "OrcaClient"), contextlib.redirect_stdout(out):
            code = rein_cli.cmd_quick(argv)
        return code, fn, out.getvalue()

    def test_routing(self):
        code, fn, _ = self.run_cli(["ask", "¿payload?"], "ask")
        self.assertEqual((code, fn.call_args[0][1]), (0, "¿payload?"))
        code, fn, _ = self.run_cli(["answer", "quick-fix", "sí"], "answer")
        self.assertEqual((code, fn.call_args[0][:2]), (0, ("quick-fix", "sí")))
        code, fn, _ = self.run_cli(["answer", "sí"], "answer")
        self.assertEqual(fn.call_args[0][:2], ("", "sí"))
        code, fn, _ = self.run_cli(["watch"], "watch")
        self.assertEqual(code, 0)
        code, fn, _ = self.run_cli(["watch", "quick-fix", "--once"], "watch")
        self.assertEqual(fn.call_args[0][0], "quick-fix")
        self.assertTrue(fn.call_args[1]["once"])

    def test_ask_without_a_question_is_usage(self):
        code, fn, out = self.run_cli(["ask"], "ask")
        self.assertEqual(code, 2)
        fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()


from test_quick_driver import TERMINAL, DoneCase, _AuditOrca  # noqa: E402


class QuickAudit(DoneCase):
    def test_the_quick_audit_brief_lists_the_owners_answers(self):
        self.make_repo(questions=[{"id": "q1", "text": "¿Formato del recibo?", "answer": "el de core/money"}])
        self.write("src/auth/login.ts", "v2\n")
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0, self.text())
        [task] = orca.called("orchestration", "task-create")
        spec = task[task.index("--spec") + 1]
        self.assertIn("Owner decisions", spec)
        self.assertIn("el de core/money", spec)


from test_quick_driver import DriverCase, _Git  # noqa: E402


class StartRecordsTheTerminal(DriverCase):
    def test_the_agents_terminal_is_kept_for_the_answers(self):
        code, _, _ = self.run_quick(here=True, git=_Git(branch="feature/x"))
        self.assertEqual(code, 0, self.text())
        self.assertTrue(self.record(self.repo_path)["terminal"].startswith("term_"))
