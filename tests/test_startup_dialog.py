"""A worker stuck on its agent's startup dialog is named to the owner, once, and no retry is spent.

In the retest Codex opened the new pos worktree on "Hooks need review": Orca aborted the launch,
Rein adopted the failed worker as if its start had only lost its answer ("adopted worker ...
started before the driver could record it"), recovered its failure as a lost worker_done and
retried, three times, against the same dialog, then told the owner "worker failed". Now the
driver reads the worker's terminal (`orca terminal read`), recognises the dialog, and opens ONE
question that names it ("the agent waits for you to trust its hooks in <path>"); the trust itself
is the owner's to give, Rein never types into the dialog or edits the agent's settings."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
import ship_state  # noqa: E402
import startup_dialog  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
from test_ship import Drained, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402
from test_failed_start import AbortedLaunch as FailedStartOrca  # noqa: E402
from test_stalled_worker import Driver as StallDriver  # noqa: E402

CODEX_HOOKS = [
    "  Hooks need review",
    "  This repository defines hooks that run commands on your machine.",
    "  > 1. Trust all and continue",
    "    2. Review hooks",
    "    3. Quit",
]
CLAUDE_TRUST = ["Do you trust the files in this folder?", "/wt/pos", "❯ 1. Yes, proceed", "  2. No, exit"]
QUOTED = ["I investigated the startup failure.", 'The previous terminal displayed "Hooks need review".',
          "I am now checking the implementation and tests."]
ANSWERED = CODEX_HOOKS + ["Trust accepted. Continuing.", "Reading the task brief", "Running pnpm test"]
PLAN_ONE = "# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"


class Detect(unittest.TestCase):
    def test_codex_hooks_review(self):
        self.assertEqual(startup_dialog.detect(CODEX_HOOKS, "/wt/pos"),
                         "the agent waits for you to trust its hooks in /wt/pos")

    def test_a_folder_trust_prompt(self):
        self.assertEqual(startup_dialog.detect(CLAUDE_TRUST, "/wt/pos"),
                         "the agent waits for you to trust the folder /wt/pos")

    def test_a_boxed_dialog_with_hints(self):
        boxed = ["╭──────────────────────────────╮", "│ Do you trust the files in this folder? │",
                 "│ /wt/pos                      │", "│ ❯ 1. Yes, proceed            │", "│   2. No, exit │",
                 "╰──────────────────────────────╯", "  Enter to confirm · Esc to exit", ""]
        self.assertEqual(startup_dialog.detect(boxed, "/wt/pos", screen=True),
                         "the agent waits for you to trust the folder /wt/pos")

    def test_a_quote_in_the_agents_answer_is_no_dialog(self):
        for screen in (True, False):
            self.assertEqual(startup_dialog.detect(QUOTED, "/x", screen=screen), "", screen)

    def test_a_dialog_already_answered_is_no_dialog(self):
        for screen in (True, False):
            self.assertEqual(startup_dialog.detect(ANSWERED, "/x", screen=screen), "", screen)

    def test_a_title_without_options_is_no_dialog(self):
        self.assertEqual(startup_dialog.detect(["  Hooks need review", "", "  checking the hooks now"], "/x"), "")

    def test_ordinary_output_is_no_dialog(self):
        self.assertEqual(startup_dialog.detect(["Running tests...", "ok: 41 passed", "review the hooks docs"], "/x"),
                         "")

    def test_only_the_recent_screen_counts(self):
        old = CODEX_HOOKS + ["> trusted, continuing"] + ["working on T001 ..."] * 40
        self.assertEqual(startup_dialog.detect(old, "/x"), "")


class DialogOrca(FakeOrca):
    """A FakeOrca whose worker terminals show `screen`; terminal reads and sends are recorded."""

    def __init__(self, root, screen=(), **kw):
        super().__init__(root, **kw)
        self.screen = list(screen)
        self.reads, self.sent = [], []

    def _answer(self, a):
        if a[:2] == ["terminal", "read"]:
            self.reads.append(_flag(a, "--terminal"))
            return {"terminal": {"handle": _flag(a, "--terminal"), "tail": list(self.screen)}}
        if a[:2] == ["terminal", "send"]:
            self.sent.append(_flag(a, "--terminal"))
            return {"accepted": True}
        return super()._answer(a)


class AbortedLaunch(DialogOrca, FailedStartOrca):
    """Orca aborts the launch (test_failed_start) while the terminal shows the dialog."""

    def __init__(self, root, screen=(), **kw):
        FailedStartOrca.__init__(self, root, **kw)
        self.screen = list(screen)
        self.reads, self.sent = [], []


class StartFailure(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN_ONE)

    def run_until_idle(self, fake):
        fake.pending = [lambda f: []] * 4
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        return ship_state.load(self.state_file)

    def questions(self, fake):
        return [_flag(c, "--question") for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]

    def test_an_aborted_launch_on_a_dialog_asks_once_and_names_it(self):
        fake = AbortedLaunch(self.root, CODEX_HOOKS, repos=("proxima-api",), autocomplete=False)
        state = self.run_until_idle(fake)
        [question] = self.questions(fake)
        self.assertIn("the agent waits for you to trust its hooks in %s" % self.wtpath("proxima-api"), question)
        self.assertIn("never", question)  # Rein does not change the agent's trust: the owner does
        self.assertEqual(len(fake.starts()), 1, "no retry is spent relaunching against the same dialog")
        t = state.tasks["T001"]
        self.assertEqual((t.status, t.attempts, t.starting), ("gated", 0, ""))
        self.assertEqual(fake.sent, [], "Rein never answers the dialog itself")
        self.assertEqual(state.worktree_paths.get("proxima-api"), self.wtpath("proxima-api"))


class FailedWorker(ShipCase):
    """Orca reports the start fine, then the worker ends failed while its terminal shows the dialog."""

    def test_a_failed_worker_on_a_dialog_asks_instead_of_retrying(self):
        self.write_plan(PLAN_ONE)
        fake = DialogOrca(self.root, CODEX_HOOKS, repos=("proxima-api",), autocomplete=False)

        def failed(f):
            did = next(iter(f.dispatches))
            f.dispatches[did]["reported"] = True
            return [f.done_message(did, "failed")]
        fake.pending = [failed] + [lambda f: []] * 3
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        state = ship_state.load(self.state_file)
        questions = [_flag(c, "--question") for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]
        self.assertEqual(len(questions), 1, questions)
        self.assertIn("trust its hooks in", questions[0])
        self.assertEqual(len(fake.starts()), 1)
        self.assertEqual((state.tasks["T001"].status, state.tasks["T001"].attempts), ("gated", 0))

    def test_decide_a_dialog_failure_spends_no_attempt(self):
        s = ShipState(change="c", plan_path="p.md")
        s.tasks["T1"] = TaskState(id="T1", repo="a", deps=[], human_review=False, verification="",
                                  type="implementation", status="running", dispatch_id="d1")
        new, actions = sd.decide(s, sd.WorkerDone("d1", "failed", dialog="the agent waits for you"), sd.ShipConfig())
        t = new.tasks["T1"]
        self.assertEqual((t.status, t.attempts), ("gated", 0))
        [gate] = new.pending_gates
        self.assertEqual(gate["kind"], "setup")
        self.assertIn("the agent waits for you", gate["question"])
        self.assertFalse([a for a in actions if isinstance(a, sd.Dispatch)])


class NotADialog(StallDriver):
    """The agent's ordinary output, read through the driver, never opens a dialog question."""

    def probe(self, screen):
        fake = DialogOrca(self.root, screen, repos=("proxima-api",))
        original = fake._answer

        def answer(a):
            if a[:2] == ["terminal", "read"]:
                return {"terminal": {"handle": "term_d_1", "tail": list(screen), "source": "screen"}}
            if a[:2] == ["orchestration", "worker-show"]:
                return {"dispatch": {"status": "dispatched"},
                        "worker": {"state": "working", "agentTerminalHandle": "term_d_1"}}
            return original(a)
        fake._answer = answer
        drv, _ = self.driver(fake)
        with contextlib.redirect_stdout(io.StringIO()):
            drv.probe_activity()
        return [g for g in list(drv.state.gates.values()) + drv.state.pending_gates if g["kind"] == "stalled"]

    def test_a_quote_opens_no_gate(self):
        self.assertEqual(self.probe(QUOTED), [])

    def test_an_answered_dialog_opens_no_gate(self):
        self.assertEqual(self.probe(ANSWERED), [])


class FailedWorkerQuoting(ShipCase):
    def test_a_failed_worker_that_quoted_the_dialog_is_retried_as_usual(self):
        self.write_plan(PLAN_ONE)
        fake = DialogOrca(self.root, QUOTED, repos=("proxima-api",), autocomplete=False)

        def failed(f):
            did = next(iter(f.dispatches))
            f.dispatches[did]["reported"] = True
            return [f.done_message(did, "failed")]
        fake.pending = [failed] + [lambda f: []] * 3
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        self.assertEqual(ship_state.load(self.state_file).tasks["T001"].attempts, 1)


class StalledOnADialog(StallDriver):
    def test_the_terminal_names_the_dialog_when_orca_does_not(self):
        fake = DialogOrca(self.root, CODEX_HOOKS, repos=("proxima-api",))
        original = fake._answer

        def answer(a):
            if a[:2] == ["orchestration", "worker-show"]:
                return {"dispatch": {"status": "dispatched"},
                        "worker": {"state": "working", "agentTerminalHandle": "term_d_1"}}
            return original(a)
        fake._answer = answer
        drv, path = self.driver(fake)
        with contextlib.redirect_stdout(io.StringIO()):
            drv.probe_activity()
        gates = [g for g in drv.state.gates.values() if g["kind"] == "stalled"]
        self.assertEqual(len(gates), 1)
        self.assertIn("the agent waits for you to trust its hooks in %s" % path, gates[0]["question"])
        self.assertEqual(fake.reads, ["term_d_1"])
        self.assertEqual(fake.sent, [])


if __name__ == "__main__":
    unittest.main()
