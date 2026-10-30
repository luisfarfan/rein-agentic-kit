"""failure_triage: what a red task's failure says before Rein spends another worker on it.

In 53 real `rein ship` runs a fresh worker met exactly the failure its predecessor had met 26 times
in a row (mostly the guard's "wrote outside its worktree" about a commit no new worker can undo),
and 74% of the test failures handed to a retry carried ANSI colour codes.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import failure_triage as ft  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_ship import PLAN, Drained, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402
from test_ship_decide import CFG, ship, task  # noqa: E402

# Shapes of real failures (the guard's and a gate's), with their run-specific parts.
OUTSIDE = ("Rein's guard rejected the change:\n- it wrote outside its worktree, in /Users/me/proxima-admin: "
           "new commit(s) (HEAD 7dae450b -> c9e82ba3): c9e82ba32ecb; a worker may only write inside its "
           "worktree -- revert that (if you changed it yourself meanwhile, retry)")
HOOK = ("Rein's guard rejected the change:\n- commit 1690555e 'feat: x' passes its pre-commit hook only after "
        "the hook rewrites .beads/issues.jsonl -- the commit as made does not; amend it with the hook's fix")
NG = ("test [failed] exit=1 $ pnpm exec ng test --watch=false\n"
      "\x1b[33m▲ \x1b[43;33m[\x1b[43;30mWARNING\x1b[43;33m]\x1b[0m NG8107: optional chain\n"
      "\x1b[31m×\x1b[39m no se presenta en una pantalla (6ms)\n"
      "FAIL src/app/help.spec.ts > help > hides it  in 0.42s at /var/folders/yh/T/rein-verify-target-ab12.tmp")


class TestClean(unittest.TestCase):
    def test_strips_colour_codes(self):
        out = ft.clean(NG)
        self.assertNotIn("\x1b", out)
        self.assertIn("[WARNING] NG8107", out)
        self.assertIn("× no se presenta", out)

    def test_keeps_only_the_last_redraw_of_a_line(self):
        self.assertEqual(ft.clean("50%\r75%\rdone\nnext"), "done\nnext")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(ft.clean(OUTSIDE), OUTSIDE)
        self.assertEqual(ft.clean(""), "")
        self.assertEqual(ft.clean(None), "")


class TestSignature(unittest.TestCase):
    def test_the_same_failure_in_another_run_has_the_same_signature(self):
        again = (NG.replace("0.42s", "1.97s").replace("rein-verify-target-ab12", "rein-verify-target-zz99")
                 .replace("(6ms)", "(11ms)"))
        self.assertEqual(ft.signature(NG), ft.signature(again))
        self.assertEqual(ft.signature(NG), ft.signature(ft.clean(NG)), "colour codes do not count")

    def test_a_different_failure_has_another_signature(self):
        self.assertNotEqual(ft.signature(NG), ft.signature(NG.replace("hides it", "shows it")))
        self.assertNotEqual(ft.signature(OUTSIDE), ft.signature(OUTSIDE.replace("proxima-admin", "proxima-api")))

    def test_shas_ids_and_timestamps_do_not_count(self):
        a = "worker ctx_5199a91f3f02 at 2026-10-24T07:14:26.312Z: commit 1690555e failed"
        b = "worker ctx_09f0d9331c5a at 2026-10-25T09:01:02Z: commit 0363969456b7 failed"
        self.assertEqual(ft.signature(a), ft.signature(b))

    def test_nothing_has_no_signature(self):
        self.assertEqual(ft.signature(""), "")
        self.assertEqual(ft.signature("\x1b[0m \n  "), "")


class TestKindAndHint(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(ft.kind(OUTSIDE), "outside")
        self.assertEqual(ft.kind(HOOK), "hook")
        self.assertEqual(ft.kind(NG), "test")
        self.assertEqual(ft.kind("worker failed"), "other")

    def test_an_environment_failure_is_infra_even_inside_a_test_slot(self):
        # Texts the 2026-10-30 pilot's flaky API suite printed.
        for text in ("test [failed] exit=1 $ uv run pytest -q tests/unit\nE   asyncpg.exceptions: rejected SSL upgrade",
                     "test [failed] exit=1 $ pytest\nE   ConnectionRefusedError: [Errno 61] Connect call failed",
                     "FATAL:  sorry, too many clients already"):
            self.assertEqual(ft.kind(text), "infra", text)
        self.assertIn("environment", ft.hint("connection was closed in the middle of operation"))

    def test_an_assertion_stays_a_test_failure(self):
        self.assertEqual(ft.kind("test [failed] exit=1 $ pytest\nE   AssertionError: assert 1 == 2"), "test")

    def test_the_same_failure_is_the_same_failing_tests(self):
        a = "FAILED tests/x.py::test_y - assert 1 == 2\n1 failed, 13567 passed, 30721 warnings in 67.69s"
        b = "FAILED tests/x.py::test_y - assert 1 == 2\n1 failed, 13567 passed, 30713 warnings in 71.02s"
        c = "FAILED tests/z.py::test_w - assert 0\n1 failed, 13567 passed"
        self.assertEqual(ft.failing_tests(a), ["tests/x.py::test_y"])
        self.assertTrue(ft.same_failure(a, b))
        self.assertFalse(ft.same_failure(a, c))
        self.assertTrue(ft.same_failure("lint [failed] exit=1\nI001 unsorted", "lint [failed] exit=1\nI001 unsorted"))

    def test_the_outside_hint_names_accept_outside(self):
        self.assertIn("rein accept-outside", ft.hint(OUTSIDE))
        self.assertIn("hook", ft.hint(HOOK))


def gating(**kw):
    return task("1", status="gating", dispatch_id="d1", agent="claude", model="sonnet", **kw)


class TestDecideRepeatedFailure(unittest.TestCase):
    def red(self, state, reason):
        return sd.decide(state, sd.GateVerdict("1", 1, reason), CFG)

    def test_the_first_red_retries_and_remembers_its_signature(self):
        new, actions = self.red(ship(gating()), OUTSIDE)
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts), ("pending", 1))
        self.assertEqual(t.failure_sig, ft.signature(OUTSIDE))
        self.assertEqual(actions, [sd.Release("d1")])

    def test_the_same_failure_twice_asks_the_owner_instead_of_a_third_worker(self):
        state = ship(gating(attempts=1, failure_sig=ft.signature(OUTSIDE)))
        new, actions = self.red(state, OUTSIDE.replace("c9e82ba32ecb", "c9e82ba32ecb"))
        self.assertEqual(new.tasks["1"].status, "failed")
        self.assertEqual(len(actions), 1)
        gate = actions[0]
        self.assertEqual((gate.task_id, gate.options, gate.kind), ("1", ["retry", "skip"], "retry_exhausted"))
        self.assertIn("failed the same way twice", gate.question)
        self.assertIn("rein accept-outside", gate.question)
        self.assertEqual(new.tasks["1"].attempts, 1, "no retry was spent")

    def test_a_different_failure_still_retries(self):
        state = ship(gating(attempts=1, failure_sig=ft.signature(OUTSIDE)))
        new, _ = self.red(state, HOOK)
        self.assertEqual((new.tasks["1"].status, new.tasks["1"].attempts), ("pending", 2))

    def test_after_the_owners_retry_the_same_failure_asks_again_at_once(self):
        state = ship(gating(attempts=1, failure_sig=ft.signature(OUTSIDE)))
        new, _ = self.red(state, OUTSIDE)
        gid = "g1"
        new.gates[gid] = {"task_id": "1", "kind": "retry_exhausted", "options": ["retry", "skip"]}
        new, _ = sd.decide(new, sd.GateResolved(gid, "retry"), CFG)
        self.assertEqual((new.tasks["1"].status, new.tasks["1"].attempts), ("pending", 0))
        new.tasks["1"].status, new.tasks["1"].dispatch_id = "gating", "d2"
        new, actions = sd.decide(new, sd.GateVerdict("1", 1, OUTSIDE, "d2"), CFG)
        self.assertEqual(new.tasks["1"].status, "failed", "one worker per owner retry, not maxRetries more")
        self.assertEqual(actions[-1].kind, "retry_exhausted")

    def test_the_failure_a_worker_reads_has_no_colour_codes(self):
        new, _ = self.red(ship(gating()), NG)
        self.assertNotIn("\x1b", new.tasks["1"].last_failure)
        self.assertIn("FAIL src/app/help.spec.ts", new.tasks["1"].last_failure)

    def test_a_state_saved_before_signatures_retries_as_before(self):
        new, _ = self.red(ship(gating(attempts=1)), OUTSIDE)  # failure_sig "" from an old state
        self.assertEqual(new.tasks["1"].status, "pending")


class ShowOrca(FakeOrca):
    """The first worker start fails the way the pilot's did and leaves an exited terminal behind."""

    PREVIEW = ("lean-pilot on main\n\x1b[33m└ permissions.deny: Invalid permission rule \"advisor\" was "
               "skipped: Tool names must start with uppercase.\x1b[0m\n%\n❯")

    def _answer(self, a):
        if a[:2] == ["terminal", "show"]:
            return {"terminal": {"handle": a[a.index("--terminal") + 1], "status": "exited", "preview": self.PREVIEW}}
        return super()._answer(a)


class StartFailure(ShipCase):
    def start_question(self, fake):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake.fail_starts = 1
        fake.start_error = FailEnvelope({"ok": False, "error": {
            "code": "exit_1", "message": "orca command failed",
            "residualResources": [{"kind": "terminal", "id": "term_dead"}]}})
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        creates = [c for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]
        return _flag(creates[0], "--question")

    def test_the_owner_question_carries_what_the_dead_terminal_showed(self):
        """The pilot's second worker died on a settings warning; Orca only said `exit_1`."""
        question = self.start_question(ShowOrca(self.root, autocomplete=False))
        self.assertIn("orca command failed", question)
        self.assertIn('Its terminal last showed: lean-pilot on main | \u2514 permissions.deny: Invalid permission rule "advisor"',
                      question)
        self.assertNotIn("\x1b", question)

    def test_no_preview_leaves_the_question_as_it_was(self):
        question = self.start_question(FakeOrca(self.root, autocomplete=False))
        self.assertIn("orca command failed", question)
        self.assertNotIn("Its terminal last showed", question)


if __name__ == "__main__":
    unittest.main()
