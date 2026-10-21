"""Tiered gates in `rein ship`: affected tests per task, the full suite once per repo before the audit.

The full suite used to run in every task's gate (and every retry's, and every fix
task's), then again in the auditor's session: 20-30 runs of a 6-12 minute suite on
one change. Now a task's gate is lint + typecheck + affected tests + its own
Verification, and each repo's full gate runs once, after its tasks, before the
audit; a red full gate becomes a fix task for that repo.
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

    def test_a_red_full_gate_becomes_a_fix_task_for_that_repo(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st, actions = verified(st, "api", 1, "tests/unit/test_x.py::test_y FAILED")
        fix = st.tasks["GATE1-api"]
        self.assertEqual((fix.repo, fix.status, fix.verification), ("api", "pending", ""))
        self.assertIn("test_y FAILED", fix.last_failure)
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

    def test_past_the_retries_the_owner_decides(self):
        st, _ = sd.decide(done_state(), sd.GateVerdict("T2", 0, ""), CFG)
        st.repo_gate_fails = {"api": CFG.max_retries + 1}
        st, actions = verified(st, "api", 1, "still red")
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
        st, actions = verified(st, "api", 1, "red")
        self.assertEqual(st.phase, "fixing")  # a fix round stays one: the re-audit follows

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

    def test_a_red_full_gate_dispatches_a_fix_task_then_gates_again(self):
        state = {"full": 0}

        def gates(path, unit_only, timeout=None, **kw):
            if kw.get("tier") == "full" and path.endswith("proxima-api"):
                state["full"] += 1
                if state["full"] == 1:
                    return 1, "E   assert totals() == 3"
            return 0, "ok"
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        self.assertEqual(state["full"], 2)
        fix_spec = next(t["spec"] for t in fake.tasks.values() if "GATE1-proxima-api" in t["title"])
        self.assertIn("assert totals() == 3", fix_spec)
        self.assertIn("full", fix_spec)

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
