"""Orca aborts a worker's start on its agent's dialog (Codex "Hooks need review"): Rein names it.

Orca reports the abort as `agent-hooks-review-prompt` and the launch exits non-zero before any
worker exists, so there is no live terminal to read the dialog from. Rein opens a question that
names the dialog, the repo and the worktree (no retry spent), opens a terminal of the owner's with
that agent in that worktree -- the one place the owner can answer the dialog, since Orca closes the
terminals it launched itself -- and retries on its own once a launch is no longer aborted. It never
answers the dialog nor edits the agent's trust settings."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship_state  # noqa: E402
from test_failed_start import PLAN_ONE  # noqa: E402
from test_ship import Drained, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402


class BlockedLaunch(FakeOrca):
    """The first `blocks` worker starts are aborted by Orca on a startup dialog."""

    def __init__(self, root, blocks=1, **kw):
        super().__init__(root, **kw)
        self.blocks = blocks
        self.terminals = []

    def _answer(self, a):
        if a[:2] == ["terminal", "create"]:
            self.terminals.append(a)
            return {"terminal": {"handle": "term_owner"}}
        if a[:2] == ["orchestration", "worker-start"] and self.blocks:
            self.blocks -= 1
            wt = "wtid-proxima-api::%s/proxima-api" % self.wt_base
            did = self._id("d")
            self.dispatches[did] = {"task": _flag(a, "--task"), "worktree": wt, "reported": True, "live": False,
                                    "failed": True}
            raise FailEnvelope({"ok": False, "error": {"code": "worker_start_failed",
                                                       "message": "orca command failed",
                                                       "details": {"reason": "agent-hooks-review-prompt"},
                                                       "failedStage": "agent"}})
        if a[:2] == ["orchestration", "worker-list"] and self.rows_override is None:
            rows = super()._answer(a)["workers"]
            for r in rows:
                if self.dispatches[r["dispatchId"]].get("failed"):
                    r.update(workerState="failed", dispatchStatus="failed", projection={"outcome": "failed"})
            return {"workers": rows}
        return super()._answer(a)

    def questions(self):
        return [_flag(c, "--question") for c in self.calls if c[:2] == ["orchestration", "gate-create"]]


class Base(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN_ONE)

    def config(self, **ship):
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"ship": ship}, fh)

    def idle(self, fake):
        fake.pending = [lambda f: []] * 4
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        return ship_state.load(self.state_file)


class Named(Base):
    def test_the_question_names_the_dialog_the_repo_and_the_path_and_spends_no_retry(self):
        fake = BlockedLaunch(self.root, blocks=99, repos=("proxima-api",), autocomplete=False)
        self.config(trustProbeSeconds=100000)
        state = self.idle(fake)
        [question] = fake.questions()
        self.assertIn("agent-hooks-review-prompt", question)
        self.assertIn("hooks", question.lower())
        self.assertIn("proxima-api", question)
        self.assertIn(self.wtpath("proxima-api"), question)
        self.assertEqual(len(fake.starts()), 1)
        t = state.tasks["T001"]
        self.assertEqual((t.status, t.attempts), ("gated", 0))

    def test_an_aborted_start_that_is_not_a_dialog_keeps_the_generic_question(self):
        fake = FakeOrca(self.root, repos=("proxima-api",), autocomplete=False)
        fake.fail_starts, fake.start_error = 1, OSError("boom")
        self.idle(fake)
        [question] = [_flag(c, "--question") for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]
        self.assertIn("Orca could not start a worker for T001", question)
        self.assertEqual([c for c in fake.calls if c[:2] == ["terminal", "create"]], [])


class OwnerTerminal(Base):
    def test_rein_opens_a_terminal_with_the_agent_in_the_worktree_and_says_so(self):
        fake = BlockedLaunch(self.root, blocks=99, repos=("proxima-api",), autocomplete=False)
        self.config(trustProbeSeconds=100000)
        state = self.idle(fake)
        [create] = fake.terminals
        self.assertEqual(_flag(create, "--worktree"), "path:%s" % self.wtpath("proxima-api"))
        agent = state.tasks["T001"].agent
        self.assertTrue(agent)
        self.assertEqual(_flag(create, "--command"), agent)
        [question] = fake.questions()
        self.assertIn("term_owner", question)
        self.assertIn("never", question)  # Rein never answers the dialog


class Reprobe(Base):
    def test_rein_retries_by_itself_once_a_launch_is_no_longer_aborted(self):
        fake = BlockedLaunch(self.root, blocks=2, repos=("proxima-api",), autocomplete=True)
        self.config(trustProbeSeconds=1)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(len(fake.starts()) - 1, 3, "two aborted starts, then the one that runs, plus the audit")
        self.assertEqual(len(fake.questions()), 2, "a gate per aborted start; only the first rings the owner")
        self.assertEqual(self.out.count("ship: waiting for the owner (setup)"), 1, self.out)
        self.assertEqual(len(fake.terminals), 1, "the owner's terminal is opened once")

    def test_no_probe_before_its_interval(self):
        fake = BlockedLaunch(self.root, blocks=99, repos=("proxima-api",), autocomplete=False)
        self.config(trustProbeSeconds=100000)
        self.idle(fake)
        self.assertEqual(len(fake.starts()), 1)

    def test_a_skip_by_the_owner_ends_the_probing(self):
        fake = BlockedLaunch(self.root, blocks=99, repos=("proxima-api",), autocomplete=False)
        self.config(trustProbeSeconds=100000)
        fake.auto_resolve = "skip"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, scripted_gate())
        self.assertEqual(len(fake.starts()), 1, self.out)


if __name__ == "__main__":
    unittest.main()
