"""`decide`: the pure core of `rein ship`.

Every case builds a ShipState by hand, feeds one event, and pins the exact action
list plus the state mutation. Nothing here touches Orca, a clock or a disk.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "lib",
))

import quota  # noqa: E402
import ship_decide as sd  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402

CFG = sd.ShipConfig()
OK = {"claude": {"session": 10.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}
NOW = 10_000.0


def task(tid, repo="a", deps=(), status="pending", **kw):
    kw.setdefault("human_review", False)
    kw.setdefault("verification", "make test")
    kw.setdefault("type", "implementation")
    return TaskState(id=tid, repo=repo, deps=list(deps), status=status, **kw)


def ship(*tasks, **kw):
    state = ShipState(change="c", plan_path="p.md", **kw)
    for t in tasks:
        state.tasks[t.id] = t
    return state


def running(tid, repo="a", dispatch_id="d1", started_at=NOW - 60, **kw):
    return task(tid, repo, status="running", dispatch_id=dispatch_id, started_at=started_at,
                agent="claude", model="sonnet", **kw)


def tick(state, limits=OK, now=NOW, cfg=CFG):
    return sd.decide(state, sd.Tick(now=now, limits=limits), cfg)


class TickDispatch(unittest.TestCase):
    def test_dispatch_respects_deps_repo_serial_and_cap(self):
        state = ship(
            task("1", "a"),
            task("2", "a"),                 # blocked: repo a already taken by 1
            task("3", "b", deps=["1"]),     # blocked: dep not done
            task("4", "b"),
            task("5", "c"),
            task("6", "d"),                 # blocked: cap of 3 reached
            worktrees={"b": "wt-b"},
        )
        new, actions = tick(state)
        self.assertEqual(actions, [
            sd.Dispatch("1", "a", "new-top-level", "claude", "sonnet"),
            sd.Dispatch("4", "b", "id:wt-b", "claude", "sonnet"),
            sd.Dispatch("5", "c", "new-top-level", "claude", "sonnet"),
        ])
        for tid in ("1", "4", "5"):
            self.assertEqual(new.tasks[tid].status, "running")
            self.assertEqual(new.tasks[tid].started_at, NOW)
            self.assertEqual(new.tasks[tid].attempts, 0)
        for tid in ("2", "3", "6"):
            self.assertEqual(new.tasks[tid].status, "pending")
        self.assertEqual(new.implementer_agents, ["claude"])
        self.assertEqual(state.tasks["1"].status, "pending", "the input state is not mutated")

    def test_running_tasks_count_against_repo_and_cap(self):
        state = ship(running("1", "a"), running("2", "b", dispatch_id="d2"), task("3", "a"),
                     task("4", "c"), task("5", "d"))
        _, actions = tick(state)
        self.assertEqual(actions, [sd.Dispatch("4", "c", "new-top-level", "claude", "sonnet")])

    def test_gating_task_still_holds_its_repo(self):
        state = ship(task("1", "a", status="gating", dispatch_id="d1"), task("2", "a"))
        _, actions = tick(state)
        self.assertEqual(actions, [])

    def test_done_dep_unlocks_and_missing_dep_does_not(self):
        state = ship(task("1", status="done"), task("2", "b", deps=["1"]),
                     task("3", "c", deps=["nope"]))
        _, actions = tick(state)
        self.assertEqual(actions, [sd.Dispatch("2", "b", "new-top-level", "claude", "sonnet")])

    def test_quota_picks_codex_when_claude_weekly_is_high(self):
        limits = {"claude": {"session": 10.0, "weekly": 70.0}, "codex": {"session": 5.0, "weekly": 5.0}}
        new, actions = tick(ship(task("1")), limits)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "new-top-level", "codex", "gpt-6.1-sol")])
        self.assertEqual(new.implementer_agents, ["codex"])

    def test_forced_implementer_beats_override_and_quota(self):
        state = ship(task("1"), forced_implementer=["codex", "gpt-6.1-sol"])
        cfg = sd.ShipConfig(override=quota.OPUS)
        _, actions = tick(state, cfg=cfg)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "new-top-level", "codex", "gpt-6.1-sol")])

    def test_override_is_passed_to_choose_implementer(self):
        _, actions = tick(ship(task("1")), cfg=sd.ShipConfig(override=quota.CODEX))
        self.assertEqual(actions[0].agent, "codex")

    def test_implementer_agents_not_duplicated(self):
        state = ship(task("1"), implementer_agents=["claude"])
        new, _ = tick(state)
        self.assertEqual(new.implementer_agents, ["claude"])

    def test_no_dispatch_outside_running_or_fixing(self):
        for phase in ("auditing", "braked", "done"):
            _, actions = tick(ship(task("1"), phase=phase))
            self.assertEqual(actions, [], phase)
        new, actions = tick(ship(task("1"), phase="fixing"))
        self.assertEqual(len(actions), 1)

    def test_tick_stamps_started_at_of_a_redispatched_task(self):
        state = ship(running("1", started_at=0.0))
        new, actions = tick(state)
        self.assertEqual(actions, [])
        self.assertEqual(new.tasks["1"].started_at, NOW)


class TickGates(unittest.TestCase):
    def test_human_review_opens_gate(self):
        state = ship(task("1", human_review=True, status="pending"), task("2", "b", deps=["1"]))
        new, actions = tick(state)
        self.assertEqual(len(actions), 1)
        gate = actions[0]
        self.assertIsInstance(gate, sd.OpenGate)
        self.assertEqual((gate.task_id, gate.options, gate.kind), ("1", ["approve", "reject"], "human_review"))
        self.assertEqual(new.tasks["1"].status, "gated")
        # a gated review task never dispatches a worker and does not hold its repo
        self.assertEqual(new.tasks["2"].status, "pending")
        _, again = tick(new)
        self.assertEqual(again, [])

    def test_human_review_waits_for_deps(self):
        state = ship(task("1"), task("2", "b", deps=["1"], human_review=True))
        new, actions = tick(state)
        self.assertEqual([type(a) for a in actions], [sd.Dispatch])
        self.assertEqual(new.tasks["2"].status, "pending")

    def test_quota_brake(self):
        limits = {"claude": {"session": 95.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}
        state = ship(task("1"))
        new, actions = tick(state, limits)
        self.assertEqual(len(actions), 2)
        brake, gate = actions
        self.assertIsInstance(brake, sd.Brake)
        self.assertIn("claude", brake.reason)
        self.assertEqual(gate, sd.OpenGate(None, brake.reason, ["switch-to-codex", "pause"], "brake"))
        self.assertEqual(new.phase, "braked")
        self.assertEqual(new.tasks["1"].status, "pending")
        _, again = tick(new, limits)
        self.assertEqual(again, [], "a braked run waits for its gate")

    def test_no_brake_when_nothing_is_waiting_to_dispatch(self):
        limits = {"claude": {"session": 95.0, "weekly": 10.0}, "codex": {}}
        new, actions = tick(ship(running("1")), limits)
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")

    def test_brake_reads_the_forced_implementer(self):
        limits = {"claude": {"session": 99.0, "weekly": 99.0}, "codex": {"session": 5.0, "weekly": 5.0}}
        state = ship(task("1"), forced_implementer=["codex", "gpt-6.1-sol"])
        _, actions = tick(state, limits)
        self.assertEqual([type(a) for a in actions], [sd.Dispatch])

    def test_task_timeout_opens_gate(self):
        started = NOW - 45 * 60 - 1
        state = ship(running("1", started_at=started), running("2", "b", dispatch_id="d2", started_at=NOW - 45 * 60))
        new, actions = tick(state)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         ("1", ["retry", "skip"], "timeout"))
        self.assertEqual(new.tasks["1"].status, "gated")
        self.assertEqual(new.tasks["2"].status, "running", "exactly at the limit is not yet late")


class WorkerEvents(unittest.TestCase):
    def test_stale_dispatch_event_is_ignored(self):
        state = ship(running("1", dispatch_id="live"))
        for event in (sd.WorkerDone("old", "succeeded"), sd.WorkerDone("", "succeeded"),
                      sd.Question("old", "m1", "hi?")):
            new, actions = sd.decide(state, event, CFG)
            self.assertEqual(actions, [])
            self.assertEqual(new, state)

    def test_worker_done_for_a_task_no_longer_running_is_ignored(self):
        state = ship(task("1", status="gated", dispatch_id="d1"))
        new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)

    def test_succeeded_runs_the_gate(self):
        state = ship(running("1"), running("2", "b", dispatch_id="d2", type="verification"))
        new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual(actions, [sd.RunGate("1", True)])
        self.assertEqual(new.tasks["1"].status, "gating")
        new, actions = sd.decide(new, sd.WorkerDone("d2", "succeeded"), CFG)
        self.assertEqual(actions, [sd.RunGate("2", False)])

    def test_failed_outcome_is_red(self):
        state = ship(running("1", attempts=0), worktrees={"a": "wt-a"})
        new, actions = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual(actions, [
            sd.Release("d1"),
            sd.Dispatch("1", "a", "id:wt-a", "claude", "sonnet"),
        ])
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, t.dispatch_id, t.started_at), ("running", 1, "", 0.0))
        self.assertTrue(t.last_failure)

    def test_failed_outcome_with_retries_exhausted_opens_gate(self):
        state = ship(running("1", attempts=2))
        new, actions = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         ("1", ["retry", "skip"], "retry_exhausted"))
        self.assertEqual(new.tasks["1"].status, "failed")

    def test_gate_green_red_retry_and_setup(self):
        gating = lambda **kw: task("1", status="gating", dispatch_id="d1", agent="claude", model="sonnet", **kw)  # noqa: E731
        other = task("2", "b", deps=["1"])

        # green
        new, actions = sd.decide(ship(gating(), other), sd.GateVerdict("1", 0, ""), CFG)
        self.assertEqual(actions, [sd.CloseTask("1"), sd.Release("d1")])
        self.assertEqual(new.tasks["1"].status, "done")

        # red with retries left
        state = ship(gating(attempts=1), worktrees={"a": "wt-a"})
        new, actions = sd.decide(state, sd.GateVerdict("1", 1, "x" * 3000), CFG)
        self.assertEqual(actions, [sd.Release("d1"), sd.Dispatch("1", "a", "id:wt-a", "claude", "sonnet")])
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, len(t.last_failure)), ("running", 2, 2000))

        # red, retries exhausted
        new, actions = sd.decide(ship(gating(attempts=2)), sd.GateVerdict("1", 1, "boom"), CFG)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         ("1", ["retry", "skip"], "retry_exhausted"))
        self.assertEqual(new.tasks["1"].status, "failed")

        # setup: the environment could not run the check
        new, actions = sd.decide(ship(gating()), sd.GateVerdict("1", 126, "no make"), CFG)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         ("1", ["retry", "skip"], "setup"))
        self.assertEqual(new.tasks["1"].status, "gated")

    def test_gate_verdict_for_a_task_not_gating_is_ignored(self):
        state = ship(running("1"))
        new, actions = sd.decide(state, sd.GateVerdict("1", 0, ""), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)
        _, actions = sd.decide(state, sd.GateVerdict("nope", 0, ""), CFG)
        self.assertEqual(actions, [])

    def test_green_on_last_task_starts_the_audit(self):
        state = ship(task("1", status="gating", dispatch_id="d1", agent="codex", model="gpt-6.1-sol"),
                     task("2", "b", status="done"), implementer_agents=["codex"])
        new, actions = sd.decide(state, sd.GateVerdict("1", 0, ""), CFG)
        self.assertEqual(actions, [sd.CloseTask("1"), sd.Release("d1"), sd.StartAudit(*quota.SONNET)])
        self.assertEqual(new.phase, "auditing")

    def test_green_fix_task_is_not_closed_in_the_plan(self):
        state = ship(task("AUDIT1-a", status="gating", dispatch_id="d1", agent="claude", model="sonnet"),
                     task("1", status="done"), phase="fixing", implementer_agents=["claude"])
        new, actions = sd.decide(state, sd.GateVerdict("AUDIT1-a", 0, ""), CFG)
        self.assertEqual(actions, [sd.Release("d1"), sd.StartAudit(*quota.CODEX)])
        self.assertEqual(new.phase, "auditing")


class QuestionsAndGates(unittest.TestCase):
    def test_question_round_trip(self):
        state = ship(running("1"))
        new, actions = sd.decide(state, sd.Question("d1", "m7", "Which db?"), CFG)
        self.assertEqual(actions, [sd.OpenGate("1", "Which db?", [], "question", "m7")])
        self.assertEqual(new.tasks["1"].status, "gated")

        new.gates["g1"] = {"task_id": "1", "kind": "question", "message_id": "m7"}
        done, actions = sd.decide(new, sd.GateResolved("g1", "postgres"), CFG)
        self.assertEqual(actions, [sd.Relay("m7", "postgres")])
        self.assertEqual(done.tasks["1"].status, "running")
        self.assertNotIn("g1", done.gates)

    def test_unknown_gate_is_ignored(self):
        state = ship(running("1"))
        new, actions = sd.decide(state, sd.GateResolved("ghost", "approve"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)

    def resolve(self, state, kind, task_id, resolution):
        state.gates["g"] = {"task_id": task_id, "kind": kind, "message_id": ""}
        return sd.decide(state, sd.GateResolved("g", resolution), CFG)

    def test_human_review_approve_and_reject(self):
        base = lambda: ship(task("1", status="gated", human_review=True), task("2", "b", deps=["1"]))  # noqa: E731
        new, actions = self.resolve(base(), "human_review", "1", "approve")
        self.assertEqual(actions, [sd.CloseTask("1")])
        self.assertEqual(new.tasks["1"].status, "done")
        new, actions = self.resolve(base(), "human_review", "1", "reject")
        self.assertEqual(new.tasks["1"].status, "failed")

    def test_human_review_approve_of_the_last_task_starts_audit(self):
        state = ship(task("1", status="gated", human_review=True), implementer_agents=["claude"])
        new, actions = self.resolve(state, "human_review", "1", "approve")
        self.assertEqual(actions, [sd.CloseTask("1"), sd.StartAudit(*quota.CODEX)])
        self.assertEqual(new.phase, "auditing")

    def test_retry_resets_attempts_and_releases_the_held_worker(self):
        for kind, status in (("retry_exhausted", "failed"), ("setup", "gated"), ("timeout", "gated")):
            state = ship(task("1", status=status, attempts=2, dispatch_id="d1", last_failure="x"))
            new, actions = self.resolve(state, kind, "1", "retry")
            self.assertEqual(actions, [sd.Release("d1")], kind)
            t = new.tasks["1"]
            self.assertEqual((t.status, t.attempts, t.dispatch_id), ("pending", 0, ""), kind)

    def test_retry_without_a_held_worker_emits_nothing(self):
        state = ship(task("1", status="failed", attempts=2))
        new, actions = self.resolve(state, "retry_exhausted", "1", "retry")
        self.assertEqual(actions, [])
        self.assertEqual(new.tasks["1"].status, "pending")

    def test_skip_fails_the_task_and_ends_a_stuck_run(self):
        state = ship(task("1", status="failed", attempts=2), task("2", "b", deps=["1"]))
        new, actions = self.resolve(state, "retry_exhausted", "1", "skip")
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual((new.tasks["1"].status, new.phase), ("failed", "done"))

    def test_skip_with_other_work_in_flight_does_not_finish(self):
        state = ship(task("1", status="failed"), running("2", "b", dispatch_id="d2"))
        new, actions = self.resolve(state, "retry_exhausted", "1", "skip")
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")

    def test_skip_of_a_timed_out_task_releases_it(self):
        state = ship(task("1", status="gated", dispatch_id="d1"))
        new, actions = self.resolve(state, "timeout", "1", "skip")
        self.assertEqual(actions, [sd.Release("d1"), sd.Finish()])

    def test_a_failed_task_with_an_open_gate_does_not_end_the_run_on_tick(self):
        state = ship(task("1", status="failed"))
        state.gates["g"] = {"task_id": "1", "kind": "retry_exhausted", "message_id": ""}
        new, actions = tick(state)
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")

    def test_brake_switch_to_codex_and_pause(self):
        state = ship(task("1"), phase="braked")
        new, actions = self.resolve(state, "brake", None, "switch-to-codex")
        self.assertEqual(actions, [])
        self.assertEqual((new.phase, new.forced_implementer), ("running", ["codex", "gpt-6.1-sol"]))
        _, actions = tick(new)
        self.assertEqual(actions[0].agent, "codex")

        new, actions = self.resolve(ship(task("1"), phase="braked"), "brake", None, "pause")
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual(new.phase, "running", "paused work resumes on the next run")

    def test_audit_exhausted_accept_and_reject_both_finish(self):
        for resolution in ("accept", "reject"):
            state = ship(task("1", status="done"), phase="auditing", audit_rounds=2)
            new, actions = self.resolve(state, "audit_exhausted", None, resolution)
            self.assertEqual(actions, [sd.Finish()])
            self.assertEqual(new.phase, "done")

    def test_an_invalid_resolution_changes_nothing(self):
        state = ship(task("1", status="gated", human_review=True))
        state.gates["g"] = {"task_id": "1", "kind": "human_review", "message_id": ""}
        new, actions = sd.decide(state, sd.GateResolved("g", "maybe"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)


class Audit(unittest.TestCase):
    def test_all_done_starts_the_audit_on_tick(self):
        state = ship(task("1", status="done"), task("2", "b", status="done"), implementer_agents=["codex", "claude"])
        new, actions = tick(state)
        self.assertEqual(actions, [sd.StartAudit(*quota.SONNET)])
        self.assertEqual(new.phase, "auditing")
        _, again = tick(new)
        self.assertEqual(again, [])

    def test_a_failed_task_blocks_the_audit(self):
        state = ship(task("1", status="done"), task("2", "b", status="failed"))
        new, actions = tick(state)
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual(new.phase, "done")

    def test_audit_model_selection(self):
        am = sd.audit_model
        self.assertEqual(am(["src/auth/login.py"], ["codex"], CFG), quota.OPUS)
        self.assertEqual(am(["auth/login.py"], ["claude"], CFG), quota.OPUS)
        self.assertEqual(am(["a/payments/x.py"], ["codex"], CFG), quota.OPUS)
        self.assertEqual(am(["db/migrations/001.sql"], ["claude"], CFG), quota.OPUS)
        self.assertEqual(am(["f%d.py" % i for i in range(26)], ["codex"], CFG), quota.OPUS)
        self.assertNotEqual(am(["f%d.py" % i for i in range(25)], ["codex"], CFG), quota.OPUS)
        self.assertEqual(am(["src/x.py"], ["codex"], CFG), quota.SONNET)
        self.assertEqual(am(["src/x.py"], ["claude"], CFG), quota.CODEX)
        self.assertEqual(am(["src/x.py"], ["codex", "claude"], CFG), quota.SONNET)
        self.assertEqual(am([], [], CFG), quota.SONNET)
        custom = sd.ShipConfig(risky_paths=["**/secrets/**"], large_change_files=1)
        self.assertEqual(am(["a/secrets/k"], ["codex"], custom), quota.OPUS)
        self.assertEqual(am(["a", "b"], ["codex"], custom), quota.OPUS)

    def audited(self, **kw):
        return ship(task("1", "a", status="done"), task("2", "b", status="done"),
                    phase="auditing", **kw)

    def test_audit_approved_finishes(self):
        new, actions = sd.decide(self.audited(), sd.AuditVerdict("APPROVED", 0, ["x.py"]), CFG)
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual(new.phase, "done")

    def test_changes_requested_without_blockers_finishes(self):
        _, actions = sd.decide(self.audited(), sd.AuditVerdict("CHANGES_REQUESTED", 0, ["x.py"], ["nit"]), CFG)
        self.assertEqual(actions, [sd.Finish()])

    def test_audit_verdict_outside_auditing_is_ignored(self):
        state = ship(task("1", status="done"))
        new, actions = sd.decide(state, sd.AuditVerdict("APPROVED", 0, []), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)

    def test_audit_rounds_cap(self):
        files = ["a/x.py", "b/y.py"]
        state = self.audited(worktree_paths={})
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 2, files, ["bug one", "bug two"])

        # round 1 -> a fix task per repo, no actions yet
        new, actions = sd.decide(state, verdict, CFG)
        self.assertEqual(actions, [])
        self.assertEqual((new.phase, new.audit_rounds), ("fixing", 1))
        self.assertEqual(set(new.tasks), {"1", "2", "AUDIT1-a", "AUDIT1-b"})
        fix = new.tasks["AUDIT1-a"]
        self.assertEqual((fix.repo, fix.type, fix.deps, fix.verification, fix.human_review, fix.status),
                         ("a", "implementation", [], "", False, "pending"))
        self.assertEqual(fix.last_failure, "bug one\nbug two")

        # the fix tasks dispatch on the next tick, then the audit restarts when they are done
        new.worktrees = {"a": "wt-a", "b": "wt-b"}
        new, actions = tick(new)
        self.assertEqual([a.task_id for a in actions], ["AUDIT1-a", "AUDIT1-b"])
        self.assertEqual(actions[0].worktree, "id:wt-a")
        for tid in ("AUDIT1-a", "AUDIT1-b"):
            new.tasks[tid].status = "done"
        new.implementer_agents = ["codex"]
        new, actions = tick(new)
        self.assertEqual(actions, [sd.StartAudit(*quota.SONNET)])
        self.assertEqual(new.phase, "auditing")

        # round 2 is still allowed
        new, actions = sd.decide(new, verdict, CFG)
        self.assertEqual((new.phase, new.audit_rounds), ("fixing", 2))
        self.assertIn("AUDIT2-a", new.tasks)

        # round 3 is not: the cap is two rounds
        for t in new.tasks.values():
            t.status = "done"
        new.phase = "auditing"
        new, actions = sd.decide(new, verdict, CFG)
        self.assertEqual([type(a) for a in actions], [sd.OpenGate, sd.Finish])
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         (None, ["accept", "reject"], "audit_exhausted"))
        self.assertIn("bug one", actions[0].question)
        self.assertEqual(new.audit_rounds, 2)

    def test_fix_tasks_only_for_repos_with_changed_files(self):
        state = self.audited(worktree_paths={"a": "/w/a", "b": "/w/b"})
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 1, ["/w/b/src/y.py"], ["f"])
        new, _ = sd.decide(state, verdict, CFG)
        self.assertEqual(set(new.tasks) - {"1", "2"}, {"AUDIT1-b"})

    def test_repo_relative_paths_map_to_their_repo(self):
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/src/y.py"], ["f"])
        new, _ = sd.decide(self.audited(), verdict, CFG)
        self.assertEqual(set(new.tasks) - {"1", "2"}, {"AUDIT1-a"})

    def test_unmatched_changed_files_fall_back_to_every_repo(self):
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 1, ["src/y.py"], ["f"])
        new, _ = sd.decide(self.audited(), verdict, CFG)
        self.assertEqual(set(new.tasks) - {"1", "2"}, {"AUDIT1-a", "AUDIT1-b"})

    def test_last_failure_is_capped(self):
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/x"], ["y" * 5000])
        new, _ = sd.decide(self.audited(), verdict, CFG)
        self.assertEqual(len(new.tasks["AUDIT1-a"].last_failure), 2000)

    def test_findings_default_is_not_shared(self):
        a, b = sd.AuditVerdict("APPROVED", 0, []), sd.AuditVerdict("APPROVED", 0, [])
        a.findings.append("x")
        self.assertEqual(b.findings, [])


class Config(unittest.TestCase):
    def test_defaults(self):
        c = sd.ShipConfig()
        self.assertEqual((c.claude_weekly_threshold, c.max_concurrent_workers, c.max_retries,
                          c.max_audit_rounds, c.task_timeout_minutes, c.large_change_files, c.override),
                         (60, 3, 2, 2, 45, 25, None))
        self.assertEqual(c.risky_paths, ["**/auth/**", "**/payments/**", "**/migrations/**"])
        self.assertIsNot(c.risky_paths, sd.ShipConfig().risky_paths)

    def test_from_flow_config_reads_ship_block_and_keeps_defaults(self):
        c = sd.ShipConfig.from_flow_config({"ship": {"maxRetries": 5, "riskyPaths": ["x/**"],
                                                      "taskTimeoutMinutes": 10}})
        self.assertEqual((c.max_retries, c.risky_paths, c.task_timeout_minutes), (5, ["x/**"], 10))
        self.assertEqual((c.max_concurrent_workers, c.claude_weekly_threshold), (3, 60))

    def test_from_flow_config_tolerates_missing_or_malformed_block(self):
        for cfg in ({}, {"ship": None}, {"ship": []}, {"ship": {"maxRetries": None}}):
            self.assertEqual(sd.ShipConfig.from_flow_config(cfg), sd.ShipConfig())

    def test_config_values_drive_decisions(self):
        cfg = sd.ShipConfig(max_concurrent_workers=1, max_retries=0)
        _, actions = tick(ship(task("1", "a"), task("2", "b")), cfg=cfg)
        self.assertEqual(len(actions), 1)
        state = ship(task("1", status="gating", dispatch_id="d1", agent="claude", model="sonnet"))
        _, actions = sd.decide(state, sd.GateVerdict("1", 1, "r"), cfg)
        self.assertEqual(actions[0].kind, "retry_exhausted")


class Purity(unittest.TestCase):
    def test_decide_does_not_mutate_its_input(self):
        state = ship(task("1"), running("2", "b", dispatch_id="d2"))
        before = repr(state)
        tick(state)
        sd.decide(state, sd.WorkerDone("d2", "failed"), CFG)
        self.assertEqual(repr(state), before)


if __name__ == "__main__":
    unittest.main()
