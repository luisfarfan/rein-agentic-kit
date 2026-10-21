"""A worker that never starts is noticed, and the owner is asked.

Codex opened on its "Hooks need review" dialog, never read its brief and sat there for 25
minutes; nothing told the owner. A worker shows a sign of work when its worktree changes
(a new commit or an edited file) or it asks a question; Orca also says when a worker is
parked on a prompt only a person can answer (`observation.agentWait`). With no sign for
`ship.stallMinutes` (10) after its launch, or parked on such a prompt, ship opens a
"stalled" gate: wait (ask again later), resend (type the brief again) or retry (a fresh
worker). `rein quick watch` prints the same alert for a quick task."""

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

import quick  # noqa: E402
import quick_inbox  # noqa: E402
import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
from test_quick_inbox import Case, Orca  # noqa: E402
from test_ship import FakeOrca, ShipCase, scripted_gate  # noqa: E402

CFG = sd.ShipConfig()
OK = {"claude": {"session": 10.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}
NOW = 100_000.0


def state(**task_kw):
    s = ShipState(change="c", plan_path="p.md")
    kw = dict(id="T001", repo="a", deps=[], human_review=False, verification="", type="implementation",
              status="running", dispatch_id="d1", agent="codex", model="gpt", started_at=NOW - 60,
              launched_at=NOW - 60)
    kw.update(task_kw)
    s.tasks["T001"] = TaskState(**kw)
    return s


def tick(s, now=NOW, cfg=CFG):
    return sd.decide(s, sd.Tick(now=now, limits=OK), cfg)


def stalled_gates(s):
    return [g for g in s.pending_gates if g.get("kind") == "stalled"]


class Decide(unittest.TestCase):
    def test_a_dispatch_stamps_the_launch_and_clears_activity(self):
        s = ShipState(change="c", plan_path="p")
        s.tasks["T001"] = TaskState(id="T001", repo="a", deps=[], human_review=False, verification="",
                                    type="implementation", active=True)
        new, _ = tick(s)
        self.assertEqual((new.tasks["T001"].launched_at, new.tasks["T001"].active), (NOW, False))

    def test_no_sign_within_the_stall_window_asks_nothing(self):
        new, _ = tick(state(), now=NOW + 9 * 60)
        self.assertEqual(stalled_gates(new), [])

    def test_no_sign_past_the_stall_window_asks_the_owner(self):
        new, actions = tick(state(), now=NOW + 11 * 60)
        [gate] = stalled_gates(new)
        self.assertEqual(gate["options"], ["wait", "resend", "retry"])
        self.assertEqual(gate["task_id"], "T001")
        self.assertEqual(gate["dispatch"], "d1")
        self.assertIn("startup dialog", gate["question"])
        self.assertEqual(new.tasks["T001"].status, "running", "the worker keeps running while the owner looks")
        _, again = tick(new, now=NOW + 12 * 60)
        self.assertFalse(any(isinstance(a, sd.OpenGate) for a in again), "asked once")

    def test_an_active_worker_is_never_asked(self):
        new, _ = tick(state(active=True), now=NOW + 60 * 60)
        self.assertEqual(stalled_gates(new), [])

    def test_activity_marks_the_task_and_drops_an_open_stall_gate(self):
        s, _ = tick(state(), now=NOW + 11 * 60)
        new, _ = sd.decide(s, sd.WorkerActivity("d1", True), CFG)
        self.assertTrue(new.tasks["T001"].active)
        self.assertEqual(stalled_gates(new), [])

    def test_a_worker_parked_on_a_human_prompt_is_asked_about_at_once(self):
        new, _ = sd.decide(state(), sd.WorkerActivity("d1", False, "prompt-text: Hooks need review"), CFG)
        [gate] = stalled_gates(new)
        self.assertIn("Hooks need review", gate["question"])

    def test_a_question_is_a_sign_of_life(self):
        new, _ = sd.decide(state(), sd.Question("d1", "m1", "REST?"), CFG)
        self.assertTrue(new.tasks["T001"].active)

    def test_zero_minutes_turns_it_off(self):
        new, _ = tick(state(), now=NOW + 99 * 60, cfg=sd.ShipConfig(stall_minutes=0))
        self.assertEqual(stalled_gates(new), [])

    def test_the_setting_is_read_from_the_ship_block(self):
        self.assertEqual(sd.ShipConfig.from_flow_config({"ship": {"stallMinutes": 4}}).stall_minutes, 4)
        self.assertEqual(sd.ShipConfig.from_flow_config({"ship": {"stallMinutes": "x"}}).stall_minutes, 10)


def resolved(answer, **task_kw):
    s, _ = tick(state(**task_kw), now=NOW + 11 * 60)
    s.gates["g1"] = dict(s.pending_gates.pop(), holder_task="h")
    return sd.decide(s, sd.GateResolved("g1", answer), CFG)


class Answers(unittest.TestCase):
    def test_wait_asks_again_after_another_window(self):
        new, actions = resolved("wait")
        self.assertEqual(actions, [])
        self.assertEqual(new.tasks["T001"].launched_at, 0.0)
        new, _ = tick(new, now=NOW + 12 * 60)
        self.assertEqual(new.tasks["T001"].launched_at, NOW + 12 * 60)

    def test_resend_types_the_brief_again(self):
        new, actions = resolved("resend")
        self.assertEqual(actions, [sd.ResendBrief("T001", "d1")])
        self.assertEqual(new.tasks["T001"].status, "running")

    def test_retry_stops_the_worker_for_a_fresh_one(self):
        new, actions = resolved("retry")
        self.assertEqual(actions, [sd.StopWorker("T001", "d1", "retry")])
        self.assertEqual(new.tasks["T001"].status, "gated")
        after, _ = sd.decide(new, sd.WorkerStopped("T001", "retry", True), CFG)
        self.assertEqual(after.tasks["T001"].status, "pending")

    def test_an_answer_after_the_worker_moved_on_is_moot(self):
        s, _ = tick(state(), now=NOW + 11 * 60)
        s.gates["g1"] = dict(s.pending_gates.pop(), holder_task="h")
        s.tasks["T001"].dispatch_id = "d2"  # a new attempt
        new, actions = sd.decide(s, sd.GateResolved("g1", "retry"), CFG)
        self.assertEqual(actions, [])
        self.assertEqual(new.tasks["T001"].status, "running")


def git(path, *args):
    return subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


class Driver(ShipCase):
    def plan_one(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")

    def driver(self, fake, show=None):
        import ship_state
        self.plan_one()
        client = OrcaClient(executable="ORCA", runner=fake)
        ctx, errors = ship.preflight(self.root, "demo", client, {"ORCA_TERMINAL_HANDLE": "t"})
        self.assertEqual(errors, [])
        st = ship_state.from_plan(ctx["plan"], "plan-repo", "demo")
        path = self.wtpath("proxima-api")
        os.makedirs(path)
        subprocess.run(["git", "init", "-q", path], check=True)
        git(path, "commit", "-q", "--allow-empty", "-m", "base")
        t = st.tasks["T001"]
        t.status, t.dispatch_id, t.launched_at = "running", "d_1", 1.0
        t.launch_head = git(path, "rev-parse", "HEAD").strip()
        st.worktrees["proxima-api"], st.worktree_paths["proxima-api"] = self.wtid("proxima-api"), path
        drv = ship._Driver(ctx, st, client, scripted_gate(), self.clock, self.state_file, False)
        return drv, path

    def test_an_edit_in_the_worktree_is_activity(self):
        drv, path = self.driver(FakeOrca(self.root, repos=("proxima-api",)))
        with contextlib.redirect_stdout(io.StringIO()):
            drv.probe_activity()
            self.assertFalse(drv.state.tasks["T001"].active)
            with open(os.path.join(path, "x.py"), "w") as fh:
                fh.write("x")
            drv.probe_activity()
        self.assertTrue(drv.state.tasks["T001"].active)

    def test_orcas_agent_wait_opens_the_gate(self):
        fake = FakeOrca(self.root, repos=("proxima-api",))
        original = fake._answer

        def answer(a):
            if a[:2] == ["orchestration", "worker-show"]:
                return {"dispatch": {"status": "dispatched"}, "worker": {"state": "working"},
                        "observation": {"agentWait": {"evidence": "prompt-text", "text": "Hooks need review"}}}
            return original(a)
        fake._answer = answer
        drv, _ = self.driver(fake)
        with contextlib.redirect_stdout(io.StringIO()):
            drv.probe_activity()
        gates = [g for g in drv.state.gates.values() if g["kind"] == "stalled"]
        self.assertEqual(len(gates), 1)
        self.assertIn("Hooks need review", gates[0]["question"])

    def test_resend_types_the_task_spec_into_the_workers_terminal(self):
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.dispatches["d_1"] = {"task": "task_x", "worktree": self.wtid("proxima-api"), "reported": False,
                                  "live": True}
        sent = []
        original = fake._answer

        def answer(a):
            if a[:2] == ["terminal", "send"]:
                sent.append((a[a.index("--terminal") + 1], a[a.index("--text") + 1]))
                return {"accepted": True}
            return original(a)
        fake._answer = answer
        drv, _ = self.driver(fake)
        with contextlib.redirect_stdout(io.StringIO()):
            drv.execute(sd.ResendBrief("T001", "d_1"))
        [(handle, text)] = sent
        self.assertEqual(handle, "term_d_1")
        self.assertIn("Task T001: Add the api endpoint", text)


class QuickWatch(Case):
    def client(self):
        return OrcaClient(executable="ORCA", runner=Orca())

    def watch_once(self, now):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch(self.ws, self.client(), once=True, clock=lambda: now)
        return code, out.getvalue()

    def test_a_quick_agent_without_a_sign_of_work_is_reported(self):
        code, out = self.watch_once(1.0 + 11 * 60)
        self.assertEqual(code, 0)
        self.assertIn("STALLED", out)
        self.assertIn("rein quick resend", out)
        self.assertIn("term_worker", out)
        code, out = self.watch_once(1.0 + 12 * 60)
        self.assertEqual(code, 1, "reported once per window")

    def test_within_the_window_nothing(self):
        self.assertEqual(self.watch_once(1.0 + 5 * 60)[0], 1)

    def test_an_edit_is_a_sign_of_work(self):
        with open(os.path.join(self.ws, "x.py"), "w") as fh:
            fh.write("x")
        self.assertEqual(self.watch_once(1.0 + 60 * 60)[0], 1)
        with open(os.path.join(self.ws, quick.RECORD)) as fh:
            self.assertTrue(json.load(fh)["active"])

    def test_a_resend_restarts_the_window(self):
        record = self.record()
        record["resent_at"] = 1.0 + 10 * 60
        with open(os.path.join(self.ws, quick.RECORD), "w") as fh:
            json.dump(record, fh)
        self.assertEqual(self.watch_once(1.0 + 15 * 60)[0], 1)


if __name__ == "__main__":
    unittest.main()
