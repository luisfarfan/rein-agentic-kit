"""Tiered gates in `rein ship`: affected tests per task, the full suite once per repo before the audit.

The full suite used to run in every task's gate (and every retry's, and every fix
task's), then again in the auditor's session: 20-30 runs of a 6-12 minute suite on
one change. Now a task's gate is lint + typecheck + affected tests + its own
Verification, and each repo's full gate runs once, after its tasks, before the
audit. A red full gate is classified before any worker is spent on it: FAIL (its failing
tests still fail run alone, or lint/typecheck/build is red) becomes a fix task for that
repo; INCONCLUSIVE (the environment failed, or the failing tests pass alone) goes to the
owner and is never read as a pass. Only the failing tests run again, never the suite (in
the 2026-10-30 pilot, gate-fix workers spent 39 of a run's 65 minutes on a flaky suite and
committed test-setup changes nobody asked for).
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
import ship_state  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402
from test_ship_decide import CFG, ship as make_state, task, tick  # noqa: E402

WTS = {"api": "wt-api", "admin": "wt-admin"}


def done_state(phase="running", **kw):
    st = make_state(task("T1", "api", status="done"), task("T2", "admin", status="gating", dispatch_id="d2"),
                    worktrees=dict(WTS), **kw)
    st.phase = phase
    return st


def verified(st, repo, code, reason=""):
    return sd.decide(st, sd.RepoGateVerdict(repo, code, reason), CFG)


class TestTheRepoGateRunsBeforeTheAudit(unittest.TestCase):
    def test_the_last_task_starts_one_full_gate_per_repo_not_the_audit(self):
        new, actions = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        self.assertEqual(new.phase, "verifying")
        self.assertIn(sd.RunRepoGate("api"), actions)
        self.assertIn(sd.RunRepoGate("admin"), actions)
        self.assertNotIn(sd.ComputeChanges(), actions)
        self.assertEqual(new.repo_gates, {"api": "running", "admin": "running"})

    def test_the_audit_starts_once_every_repo_passed(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, actions = verified(st, "api", 0)
        self.assertEqual(st.phase, "verifying")
        self.assertEqual(actions, [])
        st, actions = verified(st, "admin", 0)
        self.assertEqual(st.phase, "auditing")
        self.assertEqual(actions, [sd.ComputeChanges()])

    def test_a_red_full_gate_whose_tests_still_fail_alone_becomes_a_fix_task(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        red = "test [failed] exit=1 $ pytest\nFAILED tests/unit/test_x.py::test_y - AssertionError"
        st, actions = verified(st, "api", 1, red)
        # Only the failing test runs again, alone: never the whole suite, and no worker yet.
        self.assertEqual(actions, [sd.RunRepoGate("api", ("tests/unit/test_x.py::test_y",))])
        self.assertNotIn("GATE1-api", st.tasks)
        st, actions = sd.decide(st, sd.RepoGateVerdict("api", 1, "FAILED tests/unit/test_x.py::test_y (run alone)",
                                                       ("tests/unit/test_x.py::test_y",)), CFG)
        fix = st.tasks["GATE1-api"]
        self.assertEqual((fix.repo, fix.status, fix.verification), ("api", "pending", ""))
        self.assertIn("test_y", fix.last_failure)
        self.assertEqual(st.phase, "running")
        st, _ = verified(st, "admin", 0)  # still recorded while the fix runs
        self.assertEqual(st.repo_gates["admin"], "passed")
        st, actions = tick(st)
        self.assertEqual([a.task_id for a in actions if isinstance(a, sd.Dispatch)], ["GATE1-api"])
        st.tasks["GATE1-api"].dispatch_id = "d9"
        st, actions = sd.decide(st, sd.WorkerDone("d9", "succeeded"), CFG)
        st, actions = sd.decide(st, sd.GateVerdict("GATE1-api", 0, ""), CFG)
        self.assertNotIn(sd.CloseTask("GATE1-api"), actions)  # no checkbox in the plan
        self.assertEqual(st.phase, "verifying")
        self.assertIn(sd.RunRepoGate("api"), actions)

    def test_a_red_lint_slot_is_a_fix_task_without_a_rerun(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, actions = verified(st, "api", 1, "lint [failed] exit=1 $ uv run ruff check .\nI001 unsorted imports")
        self.assertIn("GATE1-api", st.tasks)
        self.assertNotIn(sd.RunRepoGate("api"), actions)

    def test_failing_tests_that_pass_alone_are_inconclusive_never_a_pass(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, _ = verified(st, "api", 1, "FAILED tests/unit/storefront/test_pc_builds.py::test_a\n1 failed, 13567 passed")
        st, actions = sd.decide(st, sd.RepoGateVerdict("api", 0, "", ("tests/unit/storefront/test_pc_builds.py::test_a",)), CFG)
        self.assertEqual(st.repo_gates["api"], "inconclusive")  # not "passed"
        self.assertFalse(any(t.startswith("GATE") for t in st.tasks))
        gate = st.pending_gates[-1]
        self.assertEqual((gate["kind"], gate["repo"], gate["options"]),
                         ("repo_gate_inconclusive", "api", ["retry", "fix", "skip"]))
        self.assertIn("INCONCLUSIVE", gate["question"])
        self.assertIn("test_pc_builds", gate["question"])
        st.gates["g1"] = {"task_id": None, "kind": "repo_gate_inconclusive", "repo": "api", "reason": gate["reason"]}
        st.pending_gates = []
        st, _ = verified(st, "admin", 0)
        fixed, _ = sd.decide(st, sd.GateResolved("g1", "fix"), CFG)
        self.assertIn("GATE1-api", fixed.tasks)  # the owner may still send a worker
        retried, actions = sd.decide(st, sd.GateResolved("g1", "retry"), CFG)
        self.assertEqual(actions, [sd.RunRepoGate("api")])
        skipped, actions = sd.decide(st, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual(skipped.repo_gates["api"], "skipped")
        self.assertEqual(actions, [sd.ComputeChanges()])
        self.assertEqual(skipped.owner_decisions[-1]["kind"], "repo_gate_inconclusive")

    def test_an_environment_failure_is_inconclusive_at_once(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, actions = verified(st, "api", 1, "ERROR tests/unit/test_x.py - asyncpg: rejected SSL upgrade")
        self.assertEqual([type(a).__name__ for a in actions], ["OpenGate"])  # no rerun, no worker
        self.assertEqual(st.pending_gates[-1]["kind"], "repo_gate_inconclusive")
        self.assertIn("the environment failed", st.pending_gates[-1]["question"])

    def test_a_suite_that_names_no_failing_test_is_inconclusive(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, _ = verified(st, "api", 1, "test [timeout] exit=None $ uv run pytest -q")
        self.assertEqual(st.repo_gates["api"], "inconclusive")
        self.assertFalse(any(t.startswith("GATE") for t in st.tasks))

    def test_failing_tests_that_cannot_run_alone_are_inconclusive(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, _ = verified(st, "api", 1, "FAILED tests/x.py::test_y")
        st, _ = sd.decide(st, sd.RepoGateVerdict("api", 126, "no testOne command", ("tests/x.py::test_y",)), CFG)
        self.assertEqual(st.repo_gates["api"], "inconclusive")

    def test_past_the_retries_the_owner_decides(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st.repo_gate_fails = {"api": CFG.max_retries + 1}
        st, actions = verified(st, "api", 1, "lint [failed] exit=1 $ ruff\nstill red")
        self.assertNotIn("GATE%d-api" % (CFG.max_retries + 2), st.tasks)
        gate = st.pending_gates[-1]
        self.assertEqual((gate["kind"], gate["repo"], gate["options"]), ("repo_gate_red", "api", ["retry", "skip"]))
        st.gates["g1"] = {"task_id": None, "kind": "repo_gate_red", "repo": "api"}
        st.pending_gates = []
        st, _ = verified(st, "admin", 0)
        skipped, actions = sd.decide(st, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual(skipped.repo_gates["api"], "skipped")
        self.assertEqual(skipped.phase, "auditing")
        self.assertEqual(actions, [sd.ComputeChanges()])
        retried, _ = sd.decide(st, sd.GateResolved("g1", "retry"), CFG)
        self.assertTrue(any(t.startswith("GATE") and retried.tasks[t].status == "pending" for t in retried.tasks))

    def test_a_full_gate_that_cannot_run_asks_the_owner(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, _ = verified(st, "api", 126, "pytest: command not found")
        gate = st.pending_gates[-1]
        self.assertEqual((gate["kind"], gate["repo"]), ("repo_gate", "api"))
        self.assertIn("command not found", gate["question"])
        st.gates["g1"] = {"task_id": None, "kind": "repo_gate", "repo": "api"}
        st.pending_gates = []
        again, actions = sd.decide(st, sd.GateResolved("g1", "retry"), CFG)
        self.assertEqual(actions, [sd.RunRepoGate("api")])
        self.assertEqual(again.repo_gates["api"], "running")

    def test_a_fix_round_runs_the_full_gates_again_before_the_re_audit(self):
        st = make_state(task("T1", "api", status="done"),
                        task("AUDIT1-api", "api", status="gating", dispatch_id="d5", verification=""),
                        worktrees={"api": "wt-api"}, audit_rounds=1)
        st.phase = "fixing"
        st, actions = sd.decide(st, sd.GateVerdict("AUDIT1-api", 0, ""), CFG)
        self.assertEqual(st.phase, "verifying")
        self.assertEqual(actions[-1], sd.RunRepoGate("api"))
        st, actions = verified(st, "api", 1, "lint [failed] exit=1 $ ruff\nred")
        self.assertEqual(st.phase, "fixing")  # a fix round stays one: the re-audit follows

    def test_the_gate_fix_brief_asks_to_isolate_before_touching_tests(self):
        t = sd.TaskState(id="GATE1-api", repo="api", deps=[], human_review=False, verification="",
                         type="implementation", last_failure="FAILED tests/x.py::test_y")
        spec = ship.build_task_spec(t, {"tasks": []})
        self.assertIn("First run each failing test alone", spec)
        self.assertIn("do not edit tests, fixtures or test setup", spec)
        self.assertNotIn("may be outside what the change touched", spec)

    def test_the_audit_fix_brief_asks_to_reproduce_before_fixing(self):
        t = sd.TaskState(id="AUDIT1-api", repo="api", deps=[], human_review=False, verification="",
                         type="implementation", audit_findings="BLOCKING: app/x.py:12 loses a retry")
        # Rounds are bounded in code, not by the brief: maxAuditRounds, then the owner's audit_exhausted gate.
        self.assertEqual(CFG.max_audit_rounds, 2)
        spec = ship.build_task_spec(t, {"tasks": []})
        self.assertIn("confirm it first with its evidence", spec)
        self.assertIn("at most three attempts", spec)
        self.assertIn("smallest change", spec)
        self.assertIn("app/x.py:12 loses a retry", spec)

    def test_the_audit_skill_no_longer_makes_a_red_gate_a_blocker(self):
        with open(ship.AUDIT_SKILL, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("by definition a blocker", text)
        self.assertIn("A red slot is not a finding by itself", text)
        self.assertIn("permissions and scopes", text)

    def test_no_worktree_goes_straight_to_the_audit(self):
        st = make_state(task("T1", "api", status="gating", dispatch_id="d1"))
        st, actions = sd.decide(st, sd.GateVerdict("T1", 0, ""), CFG)
        self.assertEqual(st.phase, "auditing")
        self.assertIn(sd.ComputeChanges(), actions)

    def test_a_verdict_nobody_waits_for_is_ignored(self):
        st = done_state()
        new, actions = verified(st, "api", 1, "x")
        self.assertEqual(actions, [])
        self.assertNotIn("GATE1-api", new.tasks)

    def test_ticks_dispatch_nothing_while_verifying(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st.tasks["T3"] = task("T3", "api")
        _, actions = tick(st)
        self.assertEqual(actions, [])


class TestRecheckAlone(unittest.TestCase):
    """The failing tests of a red full gate, each run alone with the project's testOne."""

    def repo(self, commands):
        import json
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "flow.config.json"), "w") as fh:
            json.dump({"commands": commands}, fh)
        for name, code in (("pass.sh", 0), ("fail.sh", 1)):
            with open(os.path.join(tmp.name, name), "w") as fh:
                fh.write("echo %s; exit %d\n" % (name, code))
        return tmp.name

    def test_only_what_still_fails_alone_is_reported(self):
        path = self.repo({"test": "sh pass.sh", "testOne": "sh {target}"})
        self.assertEqual(ship.recheck_alone(("pass.sh",), path, 60), (0, ""))
        code, text = ship.recheck_alone(("pass.sh", "fail.sh"), path, 60)
        self.assertEqual(code, 1)
        self.assertIn("FAILED fail.sh (run alone)", text)
        self.assertNotIn("pass.sh (run alone)", text)

    def test_without_a_test_one_command_it_cannot_tell(self):
        path = self.repo({"test": "sh pass.sh", "testOne": "sh pass.sh"})  # no {target}
        self.assertEqual(ship.recheck_alone(("fail.sh",), path, 60)[0], 126)


class TestDriverTiers(ShipCase):
    def test_task_gates_are_task_tier_and_each_repo_gets_one_full_gate_before_the_audit(self):
        calls = []

        def gates(path, unit_only, timeout=None, **kw):
            calls.append((os.path.basename(path), kw.get("tier"), kw.get("base")))
            return 0, "ok"
        fake = FakeOrca(self.root)
        audit_seen = []
        fake.on_audit = lambda: (audit_seen.append(list(calls)), self.approve_audit()())
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        task_calls = [c for c in calls if c[1] == "task"]
        full_calls = [c for c in calls if c[1] == "full"]
        self.assertEqual(len(task_calls), 3)
        self.assertTrue(all(c[2] == "origin/develop" for c in task_calls))
        self.assertEqual(sorted(c[0] for c in full_calls), ["proxima-admin", "proxima-api"])
        self.assertEqual(audit_seen[0][-2:], calls[-2:])  # both full gates ran before the auditor started

    RED = "test [failed] exit=1 $ pytest\nFAILED tests/test_totals.py::test_x - E   assert totals() == 3"

    def full_red_once(self, state):
        def gates(path, unit_only, timeout=None, **kw):
            if kw.get("tier") == "full" and path.endswith("proxima-api"):
                state["full"] += 1
                if state["full"] == 1:
                    return 1, self.RED
            return 0, "ok"
        return gates

    def rechecks(self, answer):
        """Replace running failing tests alone with `answer`, and record what was asked."""
        asked, real = [], ship.recheck_alone
        ship.recheck_alone = lambda tests, path, timeout, env=None: (asked.append(tuple(tests)) or answer)
        self.addCleanup(setattr, ship, "recheck_alone", real)
        return asked

    def test_a_red_full_gate_dispatches_a_fix_task_then_gates_again(self):
        state = {"full": 0}
        asked = self.rechecks((1, "FAILED tests/test_totals.py::test_x (run alone)\nE   assert totals() == 3"))
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, self.full_red_once(state)), 0, self.out)
        self.assertEqual(asked, [("tests/test_totals.py::test_x",)])
        self.assertEqual(state["full"], 2)  # the red one, and the one after the fix: never a rerun
        fix_spec = next(t["spec"] for t in fake.tasks.values() if "GATE1-proxima-api" in t["title"])
        self.assertIn("assert totals() == 3", fix_spec)
        self.assertIn("full", fix_spec)

    def test_smoke_a_red_full_gate_whose_tests_pass_alone_is_skipped_by_the_owner_not_passed(self):
        state = {"full": 0}
        asked = self.rechecks((0, ""))
        fake = FakeOrca(self.root)
        fake.auto_resolve = "skip"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, self.full_red_once(state))
        self.assertEqual(state["full"], 1, self.out)  # the suite never ran again
        self.assertEqual(asked, [("tests/test_totals.py::test_x",)])
        self.assertFalse(any("GATE" in t["title"] for t in fake.tasks.values()))  # no worker
        question = next(g["question"] for g in fake.gates if "INCONCLUSIVE" in (g["question"] or ""))
        self.assertIn("pass when run alone", question)
        saved = ship_state.load(self.state_file)
        self.assertEqual(saved.repo_gates["proxima-api"], "skipped")
        self.assertEqual(saved.owner_decisions[0]["kind"], "repo_gate_inconclusive")
        audit_spec = next(t["spec"] for t in fake.tasks.values() if "audit round" in t["title"])
        self.assertIn("INCONCLUSIVE and the owner skipped it", audit_spec)

    def test_the_worker_brief_does_not_ask_for_the_full_suite(self):
        pre = ship.WORKER_PREAMBLE
        self.assertIn("affected", pre)
        self.assertIn("Do not run the project's full test suite", pre)
        self.assertNotIn("the project's gate (its configured test", pre)

    def test_default_gate_runner_passes_the_tier_and_base(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            import subprocess
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        saved = ship.subprocess.run
        ship.subprocess.run = fake_run
        self.addCleanup(setattr, ship.subprocess, "run", saved)
        ship.default_gate_runner("/wt", True, 900, tier="task", base="origin/develop")
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--tier") + 1], "task")
        self.assertEqual(argv[argv.index("--base") + 1], "origin/develop")


if __name__ == "__main__":
    unittest.main()


class TestResumeWhileVerifying(ShipCase):
    def test_an_interrupted_full_gate_runs_again_on_resume(self):
        import ship_state
        plan = ship._load_plan(self.root, "demo")
        st = ship_state.from_plan(plan, "plan-repo", "demo")
        for t in st.tasks.values():
            t.status = "done"
        for repo in ("proxima-api", "proxima-admin"):
            st.worktrees[repo] = self.wtid(repo)
            st.worktree_paths[repo] = self.wtpath(repo)
        st.phase = "verifying"
        st.repo_gates = {"proxima-api": "running", "proxima-admin": "passed"}
        ship_state.save(st, self.state_file)
        gates = __import__("test_ship").scripted_gate()
        fake = FakeOrca(self.root)
        # A saved state lists its worktrees sorted: the audit runs in the first, proxima-admin.
        fake.on_audit = self.approve_audit(root=self.wtpath("proxima-admin"))
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        self.assertEqual(gates.full_calls, [self.wtpath("proxima-api")])
        self.assertEqual(gates.calls, [])  # no task gate ran again
