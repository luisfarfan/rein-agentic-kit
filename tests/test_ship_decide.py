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


def copy_state(state):
    import copy
    return copy.deepcopy(state)


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
    def test_human_review_without_a_verification_opens_the_gate_and_runs_no_worker(self):
        state = ship(task("1", human_review=True, verification="", status="pending"),
                     task("2", "b", deps=["1"]))
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
        state = ship(task("1"), task("2", "b", deps=["1"], human_review=True, verification=""))
        new, actions = tick(state)
        self.assertEqual([type(a) for a in actions], [sd.Dispatch])
        self.assertEqual(new.tasks["2"].status, "pending")

    def test_human_review_with_a_verification_is_dispatched_like_any_task(self):
        state = ship(task("1", human_review=True, verification="make test"))
        new, actions = tick(state)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "new-top-level", "claude", "sonnet")])
        self.assertEqual(new.tasks["1"].status, "running")

    def test_a_direct_human_review_task_does_not_hold_a_slot_or_its_repo(self):
        state = ship(task("1", "a", human_review=True, verification="", status="gated"),
                     task("2", "a"), task("3", "b"), task("4", "c"), task("5", "d"))
        _, actions = tick(state)
        self.assertEqual([a.task_id for a in actions], ["2", "3", "4"])

    def test_a_braked_claude_session_falls_to_codex_without_a_gate(self):
        limits = {"claude": {"session": 95.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}
        _, actions = tick(ship(task("1")), limits)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "new-top-level", "codex", "gpt-6.1-sol")])

    def test_a_braked_codex_falls_to_antigravity(self):
        limits = {"claude": {"session": 1.0, "weekly": 97.0}, "codex": {"session": 92.0, "weekly": 5.0},
                  "antigravity": {"session": 0.0, "weekly": 2.0}}
        new, actions = tick(ship(task("1")), limits)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "new-top-level",
                                               "antigravity", "gemini-4-argon-medium")])
        self.assertEqual(new.implementer_agents, ["antigravity"])

    def test_quota_brake(self):
        hot = {"session": 95.0, "weekly": 10.0}
        limits = {"claude": hot, "codex": hot, "antigravity": hot}
        state = ship(task("1"))
        new, actions = tick(state, limits)
        self.assertEqual(len(actions), 2)
        brake, gate = actions
        self.assertIsInstance(brake, sd.Brake)
        self.assertIn("claude", brake.reason)
        self.assertEqual(gate, sd.OpenGate(None, brake.reason, ["retry", "pause"], "brake"))
        for provider in ("claude", "codex", "antigravity"):
            self.assertIn(provider, brake.reason, "the owner sees every braked provider")
        self.assertEqual(new.phase, "braked")
        self.assertEqual(new.tasks["1"].status, "pending")
        _, again = tick(new, limits)
        self.assertEqual(again, [], "a braked run waits for its gate")

    def test_no_brake_when_nothing_is_waiting_to_dispatch(self):
        limits = {"claude": {"session": 95.0, "weekly": 10.0}, "codex": {}}
        new, actions = tick(ship(running("1")), limits)
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")

    def test_no_brake_when_the_cap_leaves_nothing_to_dispatch(self):
        limits = {"claude": {"session": 95.0, "weekly": 10.0}, "codex": {}}
        state = ship(running("1", "a"), running("2", "b", dispatch_id="d2"),
                     running("3", "c", dispatch_id="d3"), task("4", "d"))
        new, actions = tick(state, limits)
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")
        # and not when the only waiting task is blocked behind its repo
        new, actions = tick(ship(running("1", "a"), task("2", "a")), limits)
        self.assertEqual((actions, new.phase), ([], "running"))

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

    def test_failed_outcome_is_red_and_the_retry_goes_through_tick(self):
        state = ship(running("1", attempts=0), worktrees={"a": "wt-a"})
        new, actions = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual(actions, [sd.Release("d1")], "a retry is not dispatched by the event itself")
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, t.dispatch_id, t.started_at), ("pending", 1, "", 0.0))
        self.assertTrue(t.last_failure)
        again, actions = tick(new)
        self.assertEqual(actions, [sd.Dispatch("1", "a", "id:wt-a", "claude", "sonnet")])
        self.assertEqual((again.tasks["1"].status, again.tasks["1"].attempts), ("running", 1))

    def test_a_retry_meets_the_brake(self):
        hot = {"session": 95.0, "weekly": 10.0}
        limits = {"claude": hot, "codex": hot}
        state = ship(running("1", attempts=0), worktrees={"a": "wt-a"})
        red, _ = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        new, actions = tick(red, limits)
        self.assertEqual([type(a) for a in actions], [sd.Brake, sd.OpenGate])
        self.assertEqual((new.phase, new.tasks["1"].status), ("braked", "pending"))

    def test_a_retry_obeys_the_cap_and_one_worker_per_repo(self):
        state = ship(running("1", "a", attempts=0), running("2", "b", dispatch_id="d2"),
                     running("3", "c", dispatch_id="d3"), task("4", "d"))
        red, _ = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        _, actions = tick(red)
        self.assertEqual([a.task_id for a in actions], ["1"], "the freed slot goes to the first ready task")
        busy = ship(running("1", "a", attempts=0), task("2", "a"))
        red, _ = sd.decide(busy, sd.WorkerDone("d1", "failed"), CFG)
        _, actions = tick(red)
        self.assertEqual([a.task_id for a in actions], ["1"], "task 1 keeps its place; 2 waits on the repo")

    def test_a_retry_honours_the_forced_implementer(self):
        state = ship(running("1", attempts=0), forced_implementer=["codex", "gpt-6.1-sol"])
        red, _ = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        _, actions = tick(red)
        self.assertEqual((actions[0].agent, actions[0].model), ("codex", "gpt-6.1-sol"))

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
        self.assertEqual((new.tasks["1"].status, new.tasks["1"].dispatch_id), ("done", ""))

        # red with retries left: back to pending for the next Tick
        state = ship(gating(attempts=1), worktrees={"a": "wt-a"})
        new, actions = sd.decide(state, sd.GateVerdict("1", 1, "x" * 4500 + "LATE" + "x" * 3000), CFG)
        self.assertEqual(actions, [sd.Release("d1")])
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, len(t.last_failure)), ("pending", 2, sd.GATE_FAILURE_CAP))
        self.assertIn("LATE", t.last_failure)  # text past 2000 chars survives

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
        self.assertEqual(actions, [sd.CloseTask("1"), sd.Release("d1"), sd.ComputeChanges()])
        self.assertEqual(new.phase, "auditing")

    def test_green_fix_task_is_not_closed_in_the_plan(self):
        state = ship(task("AUDIT1-a", status="gating", dispatch_id="d1", agent="claude", model="sonnet"),
                     task("1", status="done"), phase="fixing", implementer_agents=["claude"])
        new, actions = sd.decide(state, sd.GateVerdict("AUDIT1-a", 0, ""), CFG)
        self.assertEqual(actions, [sd.Release("d1"), sd.ComputeChanges()])
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

    def test_question_resolution_restarts_the_task_clock(self):
        state = ship(task("1", status="gated", dispatch_id="d1", started_at=5.0))
        state.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m"}
        new, _ = sd.decide(state, sd.GateResolved("g", "yes"), CFG)
        self.assertEqual(new.tasks["1"].started_at, 0.0)
        later, _ = tick(new, now=NOW * 10)
        self.assertEqual(later.tasks["1"].status, "running")
        self.assertEqual(later.tasks["1"].started_at, NOW * 10)

    def test_question_while_not_running_is_ignored(self):
        state = ship(task("1", status="gating", dispatch_id="d1"))
        new, actions = sd.decide(state, sd.Question("d1", "m1", "hi?"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new, state)

    def test_duplicate_question_opens_one_gate(self):
        state = ship(running("1"))
        state, actions = sd.decide(state, sd.Question("d1", "m1", "hi?"), CFG)
        self.assertEqual(len(actions), 1)
        state.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m1"}
        # the worker is reported running again (e.g. replayed event) with the gate still open
        state.tasks["1"].status = "running"
        again, actions = sd.decide(state, sd.Question("d1", "m1", "hi?"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(again, state)

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
        self.assertEqual(actions, [sd.CloseTask("1"), sd.ComputeChanges()])
        self.assertEqual(new.phase, "auditing")

    def test_retry_resets_attempts_and_releases_the_held_worker(self):
        for kind, status in (("retry_exhausted", "failed"), ("setup", "gated")):
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

    # -- I4: a timed-out worker is stopped, and only proof that it exited frees the task

    def timed_out(self, resolution):
        state = ship(task("1", status="gated", dispatch_id="d1", attempts=1))
        return self.resolve(state, "timeout", "1", resolution)

    def test_timeout_retry_or_skip_stops_the_worker_before_anything_else(self):
        for resolution in ("retry", "skip"):
            new, actions = self.timed_out(resolution)
            self.assertEqual(actions, [sd.StopWorker("1", "d1", resolution)], resolution)
            t = new.tasks["1"]
            self.assertEqual((t.status, t.dispatch_id, t.attempts), ("gated", "d1", 1), "nothing changes yet")

    def test_a_confirmed_stop_then_retries_or_fails_the_task(self):
        gated, _ = self.timed_out("retry")
        new, actions = sd.decide(gated, sd.WorkerStopped("1", "retry", True), CFG)
        self.assertEqual(actions, [sd.Release("d1")])
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, t.dispatch_id), ("pending", 0, ""))
        gated, _ = self.timed_out("skip")
        new, actions = sd.decide(gated, sd.WorkerStopped("1", "skip", True), CFG)
        self.assertEqual(actions, [sd.Release("d1"), sd.Finish()])
        self.assertEqual(new.tasks["1"].status, "failed")

    def test_an_unconfirmed_stop_keeps_the_task_and_asks_again(self):
        for resolution in ("retry", "skip"):
            gated, _ = self.timed_out(resolution)
            new, actions = sd.decide(gated, sd.WorkerStopped("1", resolution, False, "still working"), CFG)
            self.assertEqual([(a.kind, a.task_id, a.options) for a in actions],
                             [("timeout", "1", ["retry", "skip"])], resolution)
            self.assertIn("still working", actions[0].question)
            t = new.tasks["1"]
            self.assertEqual((t.status, t.dispatch_id), ("gated", "d1"))
            self.assertEqual(len(new.pending_gates), 1)

    def test_the_first_unconfirmed_stop_flags_the_dispatch(self):
        gated, _ = self.timed_out("retry")
        new, _ = sd.decide(gated, sd.WorkerStopped("1", "retry", False, "x"), CFG)
        self.assertEqual(new.tasks["1"].stop_unconfirmed, "d1")

    def test_an_owner_answer_after_an_unconfirmed_stop_abandons_instead_of_stopping_again(self):
        for resolution in ("retry", "skip"):
            gated, _ = self.timed_out(resolution)
            flagged, _ = sd.decide(gated, sd.WorkerStopped("1", resolution, False, "x"), CFG)
            self.assertEqual(flagged.tasks["1"].stop_unconfirmed, "d1")
            flagged.gates["g"] = {"task_id": "1", "kind": "timeout", "message_id": ""}
            flagged.pending_gates = []
            new, actions = sd.decide(flagged, sd.GateResolved("g", resolution), CFG)
            self.assertEqual(actions[0], sd.AbandonWorker("1", "d1"), resolution)
            self.assertNotIn(sd.StopWorker("1", "d1", resolution), actions)
            t = new.tasks["1"]
            self.assertEqual(t.stop_unconfirmed, "", "cleared for the next attempt")
            if resolution == "retry":
                self.assertEqual((t.status, t.attempts, t.dispatch_id), ("pending", 0, ""))
            else:
                self.assertEqual(t.status, "failed")

    def test_a_second_unconfirmed_stop_of_the_same_dispatch_abandons(self):
        gated, _ = self.timed_out("retry")
        gated.tasks["1"].stop_unconfirmed = "d1"
        new, actions = sd.decide(gated, sd.WorkerStopped("1", "retry", False, "x"), CFG)
        self.assertEqual(actions, [sd.AbandonWorker("1", "d1"), sd.Release("d1")])
        self.assertEqual((new.tasks["1"].status, new.tasks["1"].stop_unconfirmed), ("pending", ""))

    def test_a_flag_for_an_older_dispatch_does_not_skip_the_stop(self):
        state = ship(task("1", status="gated", dispatch_id="d2", attempts=1, stop_unconfirmed="d1"))
        _, actions = self.resolve(state, "timeout", "1", "retry")
        self.assertEqual(actions, [sd.StopWorker("1", "d2", "retry")])

    def test_a_timeout_gate_without_a_worker_needs_no_stop(self):
        state = ship(task("1", status="gated", dispatch_id=""))
        new, actions = self.resolve(state, "timeout", "1", "retry")
        self.assertEqual(actions, [])
        self.assertEqual(new.tasks["1"].status, "pending")

    def test_a_late_worker_done_cancels_the_timeout_gate_and_runs_the_gate(self):
        state = ship(task("1", status="gated", dispatch_id="d1", agent="claude", model="sonnet"))
        state.gates["g"] = {"task_id": "1", "kind": "timeout", "message_id": ""}
        new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual(actions, [sd.RunGate("1", True)])
        self.assertEqual((new.tasks["1"].status, new.gates), ("gating", {}))
        # The same for a request the driver has not created in Orca yet.
        queued = ship(task("1", status="gated", dispatch_id="d1", agent="claude", model="sonnet"),
                      pending_gates=[{"task_id": "1", "kind": "timeout"}])
        new, actions = sd.decide(queued, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual((new.pending_gates, new.tasks["1"].status), ([], "pending"))

    def test_a_worker_done_for_a_task_gated_on_something_else_is_still_ignored(self):
        for kind in ("human_review", "retry_exhausted", "setup"):
            state = ship(task("1", status="gated", dispatch_id="d1"))
            state.gates["g"] = {"task_id": "1", "kind": kind, "message_id": ""}
            new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
            self.assertEqual((actions, new), ([], state), kind)
        # A question gate of ANOTHER task does not make this one's done count.
        state = ship(task("1", status="gated", dispatch_id="d1"))
        state.gates["g"] = {"task_id": "2", "kind": "question", "message_id": "m"}
        new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual((actions, new), ([], state))

    def test_a_worker_done_while_a_question_is_open_runs_the_gate_and_drops_the_question(self):
        state = ship(task("1", status="gated", dispatch_id="d1", agent="claude", model="sonnet"))
        state.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m"}
        new, actions = sd.decide(state, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual(actions, [sd.RunGate("1", True)])
        self.assertEqual((new.tasks["1"].status, new.gates, new.pending_gates), ("gating", {}, []))
        # The same for a question the driver has not created in Orca yet.
        queued = ship(task("1", status="gated", dispatch_id="d1", agent="claude", model="sonnet"),
                      pending_gates=[{"task_id": "1", "kind": "question", "message_id": "m"}])
        new, actions = sd.decide(queued, sd.WorkerDone("d1", "succeeded"), CFG)
        self.assertEqual((actions, new.pending_gates, new.tasks["1"].status),
                         ([sd.RunGate("1", True)], [], "gating"))

    def test_a_failed_worker_done_while_a_question_is_open_takes_the_red_path(self):
        state = ship(task("1", status="gated", dispatch_id="d1", agent="claude", model="sonnet", attempts=0))
        state.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m"}
        new, actions = sd.decide(state, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual(actions, [sd.Release("d1")])
        t = new.tasks["1"]
        self.assertEqual((t.status, t.attempts, t.dispatch_id), ("pending", 1, ""))
        self.assertEqual(new.gates, {})
        spent = ship(task("1", status="gated", dispatch_id="d1", attempts=CFG.max_retries))
        spent.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m"}
        new, _ = sd.decide(spent, sd.WorkerDone("d1", "failed"), CFG)
        self.assertEqual(new.tasks["1"].status, "failed")
        self.assertEqual([g["kind"] for g in new.pending_gates], ["retry_exhausted"])
        self.assertEqual(new.gates, {})

    def test_a_question_answered_after_its_task_moved_on_is_not_relayed(self):
        state = ship(task("1", status="gating", dispatch_id="d1"))
        state.gates["g"] = {"task_id": "1", "kind": "question", "message_id": "m"}
        new, actions = sd.decide(state, sd.GateResolved("g", "REST"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual((new.gates, new.tasks["1"].status), ({}, "gating"))

    def test_a_stop_report_for_a_task_that_moved_on_is_ignored(self):
        state = ship(task("1", status="pending"))
        new, actions = sd.decide(state, sd.WorkerStopped("1", "retry", True), CFG)
        self.assertEqual((actions, new), ([], state))

    # -- I8: human review with a verification closes only after the gate is green and a person approves

    def test_green_gate_of_a_human_review_task_opens_the_approval_gate_before_closing(self):
        state = ship(task("1", status="gating", dispatch_id="d1", human_review=True, agent="claude", model="sonnet"),
                     task("2", "b", deps=["1"]))
        new, actions = sd.decide(state, sd.GateVerdict("1", 0, ""), CFG)
        self.assertEqual([type(a) for a in actions], [sd.Release, sd.OpenGate])
        self.assertEqual((actions[1].task_id, actions[1].options, actions[1].kind),
                         ("1", ["approve", "reject"], "human_review"))
        self.assertNotIn(sd.CloseTask("1"), actions)
        self.assertEqual((new.tasks["1"].status, new.tasks["1"].dispatch_id), ("gated", ""))
        # approve closes it; reject fails it
        new.gates["g"] = {"task_id": "1", "kind": "human_review", "message_id": ""}
        done, actions = sd.decide(new, sd.GateResolved("g", "approve"), CFG)
        self.assertEqual((actions, done.tasks["1"].status), ([sd.CloseTask("1")], "done"))

    def test_a_red_gate_of_a_human_review_task_retries_without_asking_a_person(self):
        state = ship(task("1", status="gating", dispatch_id="d1", human_review=True, attempts=0))
        new, actions = sd.decide(state, sd.GateVerdict("1", 1, "red"), CFG)
        self.assertEqual((actions, new.tasks["1"].status), ([sd.Release("d1")], "pending"))

    def test_a_failed_task_with_an_open_gate_does_not_end_the_run_on_tick(self):
        state = ship(task("1", status="failed"))
        state.gates["g"] = {"task_id": "1", "kind": "retry_exhausted", "message_id": ""}
        new, actions = tick(state)
        self.assertEqual(actions, [])
        self.assertEqual(new.phase, "running")

    def test_brake_retry_and_pause(self):
        state = ship(task("1"), phase="braked")
        new, actions = self.resolve(state, "brake", None, "retry")
        self.assertEqual(actions, [])
        self.assertEqual((new.phase, new.forced_implementer), ("running", []))
        _, actions = tick(new)
        self.assertEqual(actions[0].agent, "claude", "retry routes by quota again")

        new, actions = self.resolve(ship(task("1"), phase="braked"), "brake", None, "pause")
        self.assertEqual(actions, [sd.Finish()], "nothing is running: leave now")
        self.assertEqual((new.phase, new.paused), ("running", False), "paused work resumes on the next run")

    def test_a_legacy_switch_to_codex_answer_clears_a_forced_implementer(self):
        # A run braked before this change has a "switch-to-codex" gate, and maybe a forced Codex
        # that would keep re-braking after Codex fills up while the other providers recover.
        state = ship(task("1"), phase="braked", forced_implementer=["codex", "gpt-6.1-sol"])
        new, _ = self.resolve(state, "brake", None, "switch-to-codex")
        self.assertEqual((new.phase, new.forced_implementer), ("running", []))
        limits = {"claude": {"session": 1.0, "weekly": 97.0}, "codex": {"session": 95.0, "weekly": 5.0},
                  "antigravity": {"session": 0.0, "weekly": 1.0}}
        _, actions = tick(new, limits)
        self.assertEqual(actions[0].agent, "antigravity")

    def test_pause_keeps_supervising_running_workers_until_they_settle(self):
        state = ship(running("1"), task("2", "b"), phase="braked")
        new, actions = self.resolve(state, "brake", None, "pause")
        self.assertEqual((actions, new.paused, new.phase), ([], True, "running"))
        # Ticks dispatch nothing new while paused...
        later, actions = tick(new)
        self.assertEqual(actions, [])
        # ...a timeout is still noticed...
        late, actions = tick(new, now=NOW + 3600)
        self.assertEqual([a.kind for a in actions], ["timeout"])
        # ...the worker settling (green gate) lets the run leave, without starting task 2 or the audit.
        gating = copy_state(new)
        gating.tasks["1"].status, gating.tasks["1"].agent = "gating", "claude"
        done, actions = sd.decide(gating, sd.GateVerdict("1", 0, ""), CFG)
        self.assertEqual([type(a) for a in actions], [sd.CloseTask, sd.Release])
        done, actions = tick(done)
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual((done.paused, done.phase, done.tasks["2"].status), (False, "running", "pending"))

    def test_a_paused_run_never_starts_the_audit(self):
        state = ship(task("1", status="gating", dispatch_id="d1", agent="claude", model="sonnet"),
                     phase="running", paused=True)
        new, actions = sd.decide(state, sd.GateVerdict("1", 0, ""), CFG)
        self.assertNotIn(sd.ComputeChanges(), actions)
        self.assertEqual(new.phase, "running")
        _, actions = tick(new)
        self.assertEqual(actions, [sd.Finish()])

    def test_audit_exhausted_accept_approves_and_reject_rejects(self):
        for resolution, result in (("accept", "approved"), ("reject", "rejected")):
            state = ship(task("1", status="done"), phase="auditing", audit_rounds=1)
            new, actions = self.resolve(state, "audit_exhausted", None, resolution)
            self.assertEqual(actions, [sd.Finish()])
            self.assertEqual((new.phase, new.audit_result), ("done", result))

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
        self.assertEqual(actions, [sd.ComputeChanges()])
        self.assertEqual(new.phase, "auditing")
        _, again = tick(new)
        self.assertEqual(again, [])

    def test_a_failed_task_blocks_the_audit(self):
        state = ship(task("1", status="done"), task("2", "b", status="failed"))
        new, actions = tick(state)
        self.assertEqual(actions, [sd.Finish()])
        self.assertEqual(new.phase, "done")

    def changes(self, files, agents, cfg=CFG, phase="auditing"):
        state = ship(task("1", status="done"), phase=phase, implementer_agents=agents)
        return sd.decide(state, sd.ChangesComputed(files), cfg)

    def test_changes_computed_routes_risky_paths_to_opus(self):
        new, actions = self.changes(["src/auth/login.py"], ["codex"])
        self.assertEqual(actions, [sd.StartAudit(*quota.OPUS)])
        self.assertEqual(new.changed_files, ["src/auth/login.py"])
        self.assertEqual(new.phase, "auditing")

    def test_changes_computed_routes_large_changes_to_opus(self):
        _, actions = self.changes(["f%d.py" % i for i in range(26)], ["claude"])
        self.assertEqual(actions, [sd.StartAudit(*quota.OPUS)])

    def test_changes_computed_picks_the_other_provider(self):
        _, actions = self.changes(["src/x.py"], ["codex"])
        self.assertEqual(actions, [sd.StartAudit(*quota.SONNET)])
        _, actions = self.changes(["src/x.py"], ["claude"])
        self.assertEqual(actions, [sd.StartAudit(*quota.CODEX)])

    def test_changes_computed_uses_the_callers_config(self):
        cfg = sd.ShipConfig(risky_paths=["**/secrets/**"])
        _, actions = self.changes(["src/auth/x.py"], ["codex"], cfg)
        self.assertEqual(actions, [sd.StartAudit(*quota.SONNET)])
        _, actions = self.changes(["a/secrets/k"], ["codex"], cfg)
        self.assertEqual(actions, [sd.StartAudit(*quota.OPUS)])

    def test_changes_computed_outside_auditing_is_ignored(self):
        for phase in ("running", "fixing", "braked", "done"):
            new, actions = self.changes(["src/auth/x.py"], ["codex"], phase=phase)
            self.assertEqual(actions, [], phase)
            self.assertEqual(new.changed_files, [], phase)

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
        self.assertEqual(am(["src/x.py"], ["antigravity"], CFG), quota.SONNET)
        self.assertEqual(am(["src/x.py"], ["codex", "antigravity"], CFG), quota.SONNET)
        self.assertEqual(am(["src/x.py"], ["claude", "antigravity"], CFG), quota.CODEX)
        self.assertEqual(am([], [], CFG), quota.SONNET)
        custom = sd.ShipConfig(risky_paths=["**/secrets/**"], large_change_files=1)
        self.assertEqual(am(["a/secrets/k"], ["codex"], custom), quota.OPUS)
        self.assertEqual(am(["a", "b"], ["codex"], custom), quota.OPUS)

    def test_touches_risky(self):
        patterns = ["**/auth/**", "*.sql"]
        self.assertTrue(sd.touches_risky(["src/auth/login.py"], patterns))
        self.assertTrue(sd.touches_risky(["auth/x.py"], patterns))
        self.assertTrue(sd.touches_risky(["a.py", "db/m.sql"], patterns))
        self.assertFalse(sd.touches_risky(["src/app.py"], patterns))
        self.assertFalse(sd.touches_risky([], patterns))
        self.assertFalse(sd.touches_risky(["src/auth/x.py"], []))

    def test_audit_model_skips_a_braked_reviewer(self):
        am = sd.audit_model
        hot, cool = {"session": 92.0, "weekly": 10.0}, {"session": 1.0, "weekly": 1.0}
        self.assertEqual(am(["src/x.py"], ["claude", "antigravity"], CFG,
                            {"claude": cool, "codex": hot}), quota.SONNET)
        self.assertEqual(am(["src/x.py"], ["antigravity"], CFG,
                            {"claude": {"session": 1.0, "weekly": 97.0}, "codex": cool}), quota.CODEX)
        self.assertEqual(am(["src/x.py"], ["codex"], CFG, {"claude": hot, "codex": hot}), quota.SONNET,
                         "all braked: the usual reviewer, so the audit gates handle it")
        self.assertEqual(am(["src/auth/x.py"], ["codex"], CFG, {"claude": hot}), quota.OPUS,
                         "risky changes still go to Opus")

    def test_the_audit_reads_the_limits_carried_by_changes_computed(self):
        state = ship(task("1", "a", status="done"), phase="auditing", implementer_agents=["claude"])
        limits = {"claude": {"session": 1.0, "weekly": 1.0}, "codex": {"session": 95.0, "weekly": 1.0}}
        _, actions = sd.decide(state, sd.ChangesComputed(["a/x.py"], limits), CFG)
        self.assertEqual(actions, [sd.StartAudit(*quota.SONNET)])

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

        # the fix tasks dispatch on the next tick, then the second (final) audit starts when they are done
        new.worktrees = {"a": "wt-a", "b": "wt-b"}
        new, actions = tick(new)
        self.assertEqual([a.task_id for a in actions], ["AUDIT1-a", "AUDIT1-b"])
        self.assertEqual(actions[0].worktree, "id:wt-a")
        for tid in ("AUDIT1-a", "AUDIT1-b"):
            new.tasks[tid].status = "done"
        new.implementer_agents = ["codex"]
        new, actions = tick(new)
        self.assertEqual(actions, [sd.ComputeChanges()])
        self.assertEqual(new.phase, "auditing")

        # maxAuditRounds counts audits: the second audit is the last, so its findings go to the owner
        for t in new.tasks.values():
            t.status = "done"
        new.phase = "auditing"
        new, actions = sd.decide(new, verdict, CFG)
        self.assertEqual([type(a) for a in actions], [sd.OpenGate], "the run does not finish before the owner answers")
        self.assertEqual((actions[0].task_id, actions[0].options, actions[0].kind),
                         (None, ["accept", "reject"], "audit_exhausted"))
        self.assertIn("bug one", actions[0].question)
        self.assertIn("2 audit(s)", actions[0].question)
        self.assertEqual((new.audit_rounds, new.phase, new.audit_result), (1, "auditing", ""))
        self.assertEqual(set(new.tasks) - {"1", "2", "AUDIT1-a", "AUDIT1-b"}, set(), "no second fix round")

    def test_max_audit_rounds_counts_total_audits(self):
        verdict = sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/x.py"], ["bug"])
        for cap, rounds_done, expect_gate in ((1, 0, True), (2, 0, False), (2, 1, True),
                                              (3, 1, False), (3, 2, True)):
            with self.subTest(cap=cap, fix_rounds=rounds_done):
                state = self.audited(audit_rounds=rounds_done)
                _, actions = sd.decide(state, verdict, sd.ShipConfig(max_audit_rounds=cap))
                self.assertEqual(any(isinstance(a, sd.OpenGate) for a in actions), expect_gate)

    def test_audit_exhausted_waits_for_the_owner_and_the_answer_sets_the_result(self):
        state = self.audited(audit_rounds=1)
        gated, actions = sd.decide(state, sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/x"], ["bug"]), CFG)
        self.assertNotIn(sd.Finish(), actions)
        # While the gate is queued or open, a replayed verdict opens no second one and no audit starts.
        _, again = sd.decide(gated, sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/x"], ["bug"]), CFG)
        self.assertEqual(again, [])
        _, started = sd.decide(gated, sd.ChangesComputed(["a/x"]), CFG)
        self.assertEqual(started, [])
        for resolution, result in (("accept", "approved"), ("reject", "rejected")):
            state = self.audited(audit_rounds=1)
            state.gates["g"] = {"task_id": None, "kind": "audit_exhausted", "message_id": ""}
            new, actions = sd.decide(state, sd.GateResolved("g", resolution), CFG)
            self.assertEqual(actions, [sd.Finish()])
            self.assertEqual((new.phase, new.audit_result), ("done", result))

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

    def test_gate_timeout_default_and_override(self):
        self.assertEqual(sd.ShipConfig().gate_timeout_seconds, 900.0)
        self.assertEqual(sd.ShipConfig.from_flow_config({}).gate_timeout_seconds, 900.0)
        c = sd.ShipConfig.from_flow_config({"ship": {"gateTimeoutSeconds": 1200}})
        self.assertEqual(c.gate_timeout_seconds, 1200)

    def test_gate_timeout_invalid_falls_back_to_default(self):
        for bad in (0, -5, "x", True, float("nan"), [1]):
            c = sd.ShipConfig.from_flow_config({"ship": {"gateTimeoutSeconds": bad}})
            self.assertEqual(c.gate_timeout_seconds, 900.0, bad)

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


class AuditSupervision(unittest.TestCase):
    """The audit worker can stall, ask, or produce nothing; none of that may be silent."""

    def auditing(self, **kw):
        kw.setdefault("phase", "auditing")
        kw.setdefault("audit_dispatch", "da")
        return ship(task("1", status="done"), **kw)

    def open_gate(self, state, gate_id="g1", kind="audit_timeout", **extra):
        state.gates[gate_id] = {"task_id": None, "kind": kind, "message_id": extra.get("message_id", ""),
                                "audit": extra.get("audit", state.audit_dispatch)}
        return state

    def test_first_tick_stamps_the_audit_clock_and_nothing_else(self):
        new, actions = tick(self.auditing())
        self.assertEqual(new.audit_started_at, NOW)
        self.assertEqual(actions, [])

    def test_no_clock_without_an_audit_dispatch(self):
        new, actions = tick(self.auditing(audit_dispatch=""))
        self.assertEqual((new.audit_started_at, actions), (0.0, []))

    def test_timeout_opens_one_owner_gate(self):
        state = self.auditing(audit_started_at=NOW - 45 * 60 - 1)
        new, actions = tick(state)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].kind, actions[0].task_id, actions[0].options),
                         ("audit_timeout", None, ["retry", "skip"]))
        self.assertEqual(len(new.pending_gates), 1)
        # Not reopened while the request is still queued or the gate is open.
        _, again = tick(new)
        self.assertEqual(again, [])
        opened = self.auditing(audit_started_at=NOW - 99999)
        _, again = tick(self.open_gate(opened))
        self.assertEqual(again, [])

    def test_within_the_limit_nothing_happens(self):
        _, actions = tick(self.auditing(audit_started_at=NOW - 60))
        self.assertEqual(actions, [])

    def test_timeout_retry_releases_clears_and_recomputes(self):
        state = self.open_gate(self.auditing(audit_started_at=5.0, changed_files=["a/x"]))
        new, actions = sd.decide(state, sd.GateResolved("g1", "retry"), CFG)
        self.assertEqual(actions, [sd.Release("da"), sd.ComputeChanges()])
        self.assertEqual((new.audit_dispatch, new.audit_started_at, new.phase), ("", 0.0, "auditing"))
        self.assertEqual(new.gates, {})

    def test_timeout_skip_finishes_not_approved(self):
        state = self.open_gate(self.auditing(audit_started_at=5.0))
        new, actions = sd.decide(state, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual(actions, [sd.Release("da"), sd.Finish()])
        self.assertEqual((new.phase, new.audit_result, new.audit_dispatch), ("done", "skipped", ""))

    def test_a_stale_audit_gate_is_dropped_when_the_audit_already_moved_on(self):
        state = self.open_gate(self.auditing(phase="fixing", audit_dispatch=""))
        new, actions = sd.decide(state, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual((actions, new.phase, new.gates), ([], "fixing", {}))

    def test_question_from_the_auditor_opens_a_taskless_gate_and_relays(self):
        new, actions = sd.decide(self.auditing(), sd.Question("da", "m1", "which file?"), CFG)
        self.assertEqual(actions, [sd.OpenGate(None, "which file?", [], "question", "m1")])
        # A second question while one is pending is ignored.
        _, again = sd.decide(new, sd.Question("da", "m2", "again?"), CFG)
        self.assertEqual(again, [])
        state = self.open_gate(self.auditing(audit_started_at=7.0), kind="question", message_id="m1")
        new, actions = sd.decide(state, sd.GateResolved("g1", "the api one"), CFG)
        self.assertEqual(actions, [sd.Relay("m1", "the api one")])
        self.assertEqual(new.audit_started_at, 0.0)  # time spent waiting on the owner does not count

    def test_no_timeout_gate_while_the_auditor_waits_on_a_question(self):
        state = self.open_gate(self.auditing(audit_started_at=NOW - 99999), kind="question")
        _, actions = tick(state)
        self.assertEqual(actions, [])

    def test_no_verdict_asks_the_owner_and_starts_no_fix_round(self):
        state = self.auditing(audit_dispatch="", audit_started_at=3.0, audit_rounds=0)
        new, actions = sd.decide(state, sd.AuditVerdict("NO_VERDICT", 1, ["a/x"], ["no episode"]), CFG)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].kind, actions[0].task_id, actions[0].options),
                         ("audit_failed", None, ["retry-audit", "accept", "reject"]))
        self.assertEqual((new.phase, new.audit_rounds, sorted(new.tasks)), ("auditing", 0, ["1"]))
        self.assertEqual(new.audit_started_at, 0.0)

    def test_audit_failed_resolutions(self):
        state = self.open_gate(self.auditing(audit_dispatch=""), kind="audit_failed")
        new, actions = sd.decide(state, sd.GateResolved("g1", "retry-audit"), CFG)
        self.assertEqual((actions, new.phase), ([sd.ComputeChanges()], "auditing"))
        new, actions = sd.decide(state, sd.GateResolved("g1", "accept"), CFG)
        self.assertEqual((actions, new.phase, new.audit_result), ([sd.Finish()], "done", "approved"))
        new, actions = sd.decide(state, sd.GateResolved("g1", "reject"), CFG)
        self.assertEqual((actions, new.phase, new.audit_result), ([sd.Finish()], "done", "rejected"))
        _, ignored = sd.decide(state, sd.GateResolved("g1", "bogus"), CFG)
        self.assertEqual(ignored, [])

    # -- a resumed or repeated audit must not run behind an open owner gate

    def test_no_second_audit_starts_behind_an_open_or_queued_owner_gate(self):
        for kind in ("audit_failed", "audit_timeout"):
            opened = self.open_gate(self.auditing(audit_dispatch=""), kind=kind)
            _, actions = sd.decide(opened, sd.ChangesComputed(["a/x"]), CFG)
            self.assertEqual(actions, [], kind)
            queued = self.auditing(audit_dispatch="", pending_gates=[{"task_id": None, "kind": kind}])
            _, actions = sd.decide(queued, sd.ChangesComputed(["a/x"]), CFG)
            self.assertEqual(actions, [], kind)

    # -- C2(c): a failed diff is an owner decision, never "nothing changed"

    def test_changes_failed_opens_a_taskless_setup_gate_once(self):
        state = self.auditing(audit_dispatch="")
        new, actions = sd.decide(state, sd.ChangesFailed("a: bad revision"), CFG)
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].kind, actions[0].task_id, actions[0].options),
                         ("setup", None, ["retry", "skip"]))
        self.assertIn("a: bad revision", actions[0].question)
        _, again = sd.decide(new, sd.ChangesFailed("a: bad revision"), CFG)
        self.assertEqual(again, [])
        _, started = sd.decide(new, sd.ChangesComputed(["a/x"]), CFG)
        self.assertEqual(started, [], "no audit behind an open setup gate")

    def test_changes_failed_is_ignored_outside_auditing_or_behind_a_running_audit(self):
        _, actions = sd.decide(ship(task("1", status="done"), phase="running"), sd.ChangesFailed("x"), CFG)
        self.assertEqual(actions, [])
        _, actions = sd.decide(self.auditing(), sd.ChangesFailed("x"), CFG)
        self.assertEqual(actions, [])

    def test_changes_setup_gate_retry_recomputes_and_skip_ends_the_audit_unapproved(self):
        state = self.open_gate(self.auditing(audit_dispatch=""), kind="setup")
        new, actions = sd.decide(state, sd.GateResolved("g1", "retry"), CFG)
        self.assertEqual((actions, new.phase), ([sd.ComputeChanges()], "auditing"))
        new, actions = sd.decide(state, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual((actions, new.phase, new.audit_result), ([sd.Finish()], "done", "skipped"))

    def test_no_audit_starts_while_one_is_running(self):
        _, actions = sd.decide(self.auditing(), sd.ChangesComputed(["a/x"]), CFG)
        self.assertEqual(actions, [])

    def test_changes_computed_starts_the_audit_once_nothing_blocks_it(self):
        _, actions = sd.decide(self.auditing(audit_dispatch=""), sd.ChangesComputed(["a/x"]), CFG)
        self.assertEqual([type(a) for a in actions], [sd.StartAudit])

    def test_a_second_no_verdict_does_not_open_a_duplicate_gate(self):
        state = self.auditing(audit_dispatch="")
        first, actions = sd.decide(state, sd.AuditVerdict("NO_VERDICT", 1, [], ["x"]), CFG)
        self.assertEqual(len(actions), 1)
        second, again = sd.decide(first, sd.AuditVerdict("NO_VERDICT", 1, [], ["y"]), CFG)
        self.assertEqual((again, len(second.pending_gates)), ([], 1))
        opened = self.open_gate(self.auditing(audit_dispatch=""), kind="audit_failed")
        _, again = sd.decide(opened, sd.AuditVerdict("NO_VERDICT", 1, [], ["y"]), CFG)
        self.assertEqual(again, [])

    def test_ending_the_audit_releases_a_held_auditor(self):
        for resolution, result in (("accept", "approved"), ("reject", "rejected")):
            state = self.open_gate(self.auditing(), kind="audit_failed")
            new, actions = sd.decide(state, sd.GateResolved("g1", resolution), CFG)
            self.assertEqual(actions, [sd.Release("da"), sd.Finish()])
            self.assertEqual((new.audit_dispatch, new.audit_result), ("", result))

    # -- a timeout gate belongs to one audit

    def test_a_verdict_clears_the_timeout_gate_open_or_queued(self):
        state = self.open_gate(self.auditing(audit_dispatch=""))
        state.pending_gates = [{"task_id": None, "kind": "audit_timeout"}, {"task_id": "1", "kind": "setup"}]
        new, _ = sd.decide(state, sd.AuditVerdict("CHANGES_REQUESTED", 1, ["a/x"], ["bug"]), CFG)
        self.assertEqual(new.gates, {})
        self.assertEqual([g["kind"] for g in new.pending_gates], ["setup"])

    def test_resolving_a_stale_timeout_gate_does_nothing_to_a_later_audit(self):
        state = self.open_gate(self.auditing(audit_dispatch="d_new", audit_started_at=5.0), audit="d_old")
        new, actions = sd.decide(state, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual((new.phase, new.audit_dispatch, new.audit_started_at, new.audit_result),
                         ("auditing", "d_new", 5.0, ""))
        self.assertEqual(new.gates, {})

    def test_the_timeout_gate_remembers_which_audit_it_is_about(self):
        new, _ = tick(self.auditing(audit_started_at=NOW - 99999))
        self.assertEqual(new.pending_gates[0]["audit"], "da")

    def test_approval_records_the_result(self):
        new, _ = sd.decide(self.auditing(), sd.AuditVerdict("APPROVED", 0, []), CFG)
        self.assertEqual(new.audit_result, "approved")

    def test_every_opened_gate_is_queued_until_the_driver_creates_it(self):
        state = ship(task("1", status="gating", dispatch_id="d1", agent="claude", model="sonnet"))
        new, actions = sd.decide(state, sd.GateVerdict("1", 126, "no docker"), CFG)
        self.assertEqual([g["kind"] for g in new.pending_gates], ["setup"])
        self.assertEqual(new.pending_gates[0]["task_id"], "1")
        # While a request is queued the run cannot finish around it.
        done = ship(task("1", status="failed"), pending_gates=[{"task_id": "1", "kind": "setup"}])
        _, actions = sd.decide(done, sd.Tick(NOW, OK), CFG)
        self.assertNotIn(sd.Finish(), actions)


class Purity(unittest.TestCase):
    def test_decide_does_not_mutate_its_input(self):
        state = ship(task("1"), running("2", "b", dispatch_id="d2"))
        before = repr(state)
        tick(state)
        sd.decide(state, sd.WorkerDone("d2", "failed"), CFG)
        self.assertEqual(repr(state), before)


if __name__ == "__main__":
    unittest.main()
