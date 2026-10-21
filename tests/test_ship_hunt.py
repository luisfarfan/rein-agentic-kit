"""Regressions from the ship-driver bug hunt: failure context, audit closure, resume, quota.

Decide-level cases build a ShipState by hand; driver-level cases reuse test_ship's FakeOrca.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
from test_ship import PLAN, FakeOrca, FailEnvelope, ShipCase, _flag, scripted_gate  # noqa: E402,F401

CFG = sd.ShipConfig()
OK = {"claude": {"session": 10.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}


def task(tid, repo="a", status="pending", **kw):
    kw.setdefault("human_review", False)
    kw.setdefault("verification", "make test")
    kw.setdefault("type", "implementation")
    return TaskState(id=tid, repo=repo, deps=[], status=status, **kw)


def state_of(*tasks, **kw):
    st = ShipState(change="c", plan_path="p.md", **kw)
    for t in tasks:
        st.tasks[t.id] = t
    return st


def opened(st, kind):
    return [g for g in st.pending_gates if g["kind"] == kind]


class OwnerQuestionsQuoteEnough(unittest.TestCase):
    def test_retry_exhausted_question_quotes_the_failure_past_300_chars(self):
        t = task("T1", status="gating", dispatch_id="d1", attempts=CFG.max_retries)
        reason = "test [failed] exit=1 $ pytest\n" + "x" * 500 + "\nE   assert 7.0 == 6.3"
        st, _ = sd.decide(state_of(t), sd.GateVerdict("T1", 1, reason), CFG)
        self.assertIn("assert 7.0 == 6.3", opened(st, "retry_exhausted")[0]["question"])


class RetryKeepsItsContext(unittest.TestCase):
    """Driver hunt 4, 5 and 9: what the next worker of a failed task is told."""

    def fix_task(self):
        st = state_of(task("T1", repo="api", status="done"))
        st.worktree_paths = {"api": "/p/api"}
        st.phase = "auditing"
        st, _ = sd.decide(st, sd.AuditVerdict("CHANGES_REQUESTED", 1, ["api/src/x.py"],
                                              ["BLOCKING: src/x.py leaks the token in logs"]), CFG)
        return st, next(t for t in st.tasks.values() if t.id.startswith("AUDIT"))

    def test_a_fix_task_whose_gate_fails_keeps_its_findings(self):
        st, fix = self.fix_task()
        fix.status, fix.dispatch_id = "gating", "d1"
        st, _ = sd.decide(st, sd.GateVerdict(fix.id, 1, "test [failed] exit=1\nE assert 1 == 2"), CFG)
        spec = ship.build_task_spec(st.tasks[fix.id], {})
        self.assertIn("leaks the token", spec)
        self.assertIn("E assert 1 == 2", spec)

    def test_a_fix_task_whose_worker_fails_keeps_its_findings(self):
        st, fix = self.fix_task()
        fix.status, fix.dispatch_id = "running", "d1"
        st, _ = sd.decide(st, sd.WorkerDone("d1", "failed"), CFG)
        self.assertIn("leaks the token", ship.build_task_spec(st.tasks[fix.id], {}))

    def test_owner_retry_still_shows_the_last_failure(self):
        t = task("T1", status="gating", dispatch_id="d2", attempts=CFG.max_retries)
        st, _ = sd.decide(state_of(t), sd.GateVerdict("T1", 1, "DECISIVE: tests/test_a.py::test_b assert 7 == 6"), CFG)
        gate = dict(st.pending_gates.pop())
        st.gates["g1"] = gate
        st, _ = sd.decide(st, sd.GateResolved("g1", "retry"), CFG)
        t = st.tasks["T1"]
        self.assertEqual((t.status, t.attempts), ("pending", 0))
        spec = ship.build_task_spec(t, {})
        self.assertIn("DECISIVE", spec)
        self.assertNotIn("The audit found", spec)

    def test_a_finding_naming_no_file_reaches_the_repo_it_belongs_to(self):
        st = state_of(task("T1", repo="api", status="done"), task("T2", repo="admin", status="done"))
        st.worktree_paths = {"api": "/w/api", "admin": "/w/admin"}
        out = dict(sd._fix_assignments(st, ["api/src/auth.py", "admin/src/Login.tsx"],
                                       ["BLOCKING: src/auth.py logs the bearer token",
                                        "BLOCKING: the login screen never shows the 401 error message"]))
        self.assertIn("admin", out)
        self.assertIn("BLOCKING: the login screen never shows the 401 error message", out["admin"])
        self.assertIn("BLOCKING: src/auth.py logs the bearer token", out["api"])
        self.assertNotIn("BLOCKING: src/auth.py logs the bearer token", out["admin"])

    def test_a_finding_naming_a_repo_goes_only_there(self):
        st = state_of(task("T1", repo="api", status="done"), task("T2", repo="admin", status="done"))
        st.worktree_paths = {"api": "/w/api", "admin": "/w/admin"}
        out = dict(sd._fix_assignments(st, ["api/src/auth.py", "admin/src/Login.tsx"],
                                       ["BLOCKING: src/auth.py logs the bearer token",
                                        "BLOCKING: the admin login never shows the 401"]))
        self.assertEqual(out, {"api": ["BLOCKING: src/auth.py logs the bearer token"],
                               "admin": ["BLOCKING: the admin login never shows the 401"]})


class SkippedFixStillReaudits(unittest.TestCase):
    """A skipped audit-fix task must not end the run as done with no audit verdict."""

    def fixing(self, fix_status="gated"):
        st = state_of(task("T1", repo="a", status="done"),
                      task("AUDIT1-a", repo="a", status=fix_status, verification="",
                           audit_findings="BLOCKING: leak", last_failure="BLOCKING: leak"))
        st.phase, st.audit_rounds, st.previous_blocking = "fixing", 1, ["BLOCKING: leak"]
        st.gates["g1"] = {"task_id": "AUDIT1-a", "kind": "setup", "message_id": ""}
        return st

    def test_skipping_a_fix_task_runs_the_scoped_reaudit(self):
        st, actions = sd.decide(self.fixing(), sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual(st.tasks["AUDIT1-a"].status, "failed")
        self.assertEqual(st.phase, "auditing")
        self.assertIn(sd.ComputeChanges(), actions)
        self.assertNotIn(sd.Finish(), actions)

    def test_a_failed_original_task_ends_the_run_explicitly_unapproved(self):
        st = state_of(task("T1", repo="a", status="gated"))
        st.gates["g1"] = {"task_id": "T1", "kind": "setup", "message_id": ""}
        st, actions = sd.decide(st, sd.GateResolved("g1", "skip"), CFG)
        self.assertEqual((st.phase, st.audit_result), ("done", "unapproved"))
        self.assertIn(sd.Finish(), actions)


class FinishSaysWhatWasNotVerified(ShipCase):
    def test_a_skipped_reaudit_says_the_blocking_findings_were_not_verified(self):
        from ship_state import ShipState as S
        st = S(change="demo", plan_path="", phase="done", audit_result="skipped",
               previous_blocking=["BLOCKING: src/x.py leaks the token"])
        st.tasks["T1"] = task("T1", status="done")
        drv = ship._Driver.__new__(ship._Driver)
        drv.state, drv.change, drv.dry_run = st, "demo", True
        drv.client = ship.OrcaClient(executable="ORCA", runner=FakeOrca(self.root))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = drv.finish()
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("audit skipped", out)
        self.assertIn("not verified", out.lower())
        self.assertIn("leaks the token", out)


class SkippedFixRunEndToEnd(ShipCase):
    def test_a_skipped_fix_task_is_followed_by_the_reaudit(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake = FakeOrca(self.root)
        verdicts = [("CHANGES_REQUESTED", ["BLOCKING: src/proxima-api.py leaks the token"]), ("APPROVED", [])]

        def audit():
            verdict, findings = verdicts.pop(0)
            self.approve_audit(verdict, findings)()
        fake.on_audit = audit
        fake.auto_resolve = "skip"
        self.run_ship(fake, scripted_gate(0, 126))
        audits = [t for t in fake.tasks.values() if "audit round" in t["title"]]
        self.assertEqual(len(audits), 2, self.out)
        self.assertIn("leaks the token", audits[1]["spec"])  # the scoped re-audit
        with open(self.state_file, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(st["tasks"]["AUDIT1-proxima-api"]["status"], "failed")
        self.assertNotEqual(st["audit_result"], "")


class AuditBriefNeedsNoClaudeSkill(unittest.TestCase):
    def test_the_brief_names_the_skill_file_to_read(self):
        path = os.path.join(ship.PLUGIN, "skills", "rein-audit", "SKILL.md")
        self.assertTrue(os.path.isabs(path) and os.path.isfile(path))
        for prev in ((), ["BLOCKING: x"]):
            spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo", previous_blocking=prev)
            self.assertIn(path, spec)
            self.assertIn("read it and follow its steps", spec)
            self.assertIn(ship.INLINE_REVIEW, spec)

    def test_the_skill_reviews_inline_when_the_code_reviewer_is_missing_even_for_claude(self):
        with open(os.path.join(ship.PLUGIN, "skills", "rein-audit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        step3 = " ".join(text.split("3. **Judgement")[1].split("4. **Intent")[0].split())
        self.assertIn("not available", step3)
        self.assertIn("Claude without", step3)
        self.assertNotIn("Do not reimplement this inline", step3)


class AuditRespectsQuota(unittest.TestCase):
    hot = {"session": 95.0, "weekly": 97.0}
    cool = {"session": 10.0, "weekly": 10.0}
    unknown = {"session": None, "weekly": None}

    def test_opus_falls_back_when_claude_is_braked(self):
        limits = {"claude": self.hot, "codex": self.cool}
        self.assertEqual(sd.audit_model(["a/auth/x.py"], ["codex"], CFG, limits), sd.quota.CODEX)
        self.assertEqual(sd.audit_model(["f%d" % i for i in range(30)], ["codex"], CFG, limits), sd.quota.CODEX)

    def test_unknown_claude_usage_never_routes_the_audit_to_claude(self):
        limits = {"claude": self.unknown, "codex": self.unknown}
        self.assertEqual(sd.audit_model(["src/x.py"], ["codex"], CFG, limits), sd.quota.CODEX)
        self.assertEqual(sd.audit_model(["a/auth/x.py"], ["codex"], CFG, limits), sd.quota.CODEX)

    def test_every_reviewer_braked_asks_the_owner(self):
        st = state_of(task("T1", status="done"), phase="auditing", implementer_agents=["codex"])
        st, actions = sd.decide(st, sd.ChangesComputed(["a/x.py"], {"claude": self.hot, "codex": self.hot}), CFG)
        self.assertFalse([a for a in actions if isinstance(a, sd.StartAudit)])
        gate = opened(st, "setup")
        self.assertEqual(len(gate), 1)
        self.assertIsNone(gate[0]["task_id"])
        self.assertIn("quota", gate[0]["question"])
        st.gates["g"] = st.pending_gates.pop()
        retried, actions = sd.decide(st, sd.GateResolved("g", "retry"), CFG)
        self.assertEqual(actions, [sd.ComputeChanges()])
        skipped, actions = sd.decide(st, sd.GateResolved("g", "skip"), CFG)
        self.assertEqual((skipped.phase, skipped.audit_result), ("done", "skipped"))

    def test_antigravity_reviews_when_the_others_are_braked_and_it_did_not_implement(self):
        limits = {"claude": self.hot, "codex": self.hot, "antigravity": self.cool}
        self.assertEqual(sd.audit_model(["src/x.py"], ["codex"], CFG, limits), sd.quota.ANTIGRAVITY)
        self.assertEqual(sd.audit_model(["src/x.py"], ["antigravity"], CFG, limits), sd.quota.CODEX)

    def test_the_audit_avoids_the_provider_orca_actually_launched(self):
        # codex was asked for, Orca launched claude: claude implemented, so codex audits
        st = state_of(task("T1", status="done"), phase="auditing",
                      implementer_agents=["codex"], launched_agents=["claude"])
        _, actions = sd.decide(st, sd.ChangesComputed(["a/x.py"], {"claude": self.cool, "codex": self.cool}), CFG)
        self.assertEqual(actions, [sd.StartAudit(*sd.quota.CODEX)])


class DriverRecordsTheLaunchedAgent(ShipCase):
    def test_a_swapped_implementer_is_recorded_as_launched(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])

        class Swap(FakeOrca):
            def _answer(self, a):
                r = super()._answer(a)
                if a[:2] == ["orchestration", "worker-start"] and "audit" not in self.tasks[_flag(a, "--task")]["title"]:
                    r["launch"]["effective"] = {"agent": "claude", "model": "sonnet"}
                return r
        fake = Swap(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, scripted_gate())
        with open(self.state_file, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertIn("claude", st["launched_agents"])
        audit = [s for s in fake.starts() if "audit" in fake.tasks[_flag(s, "--task")]["title"]][0]
        self.assertNotEqual(_flag(audit, "--agent"), "claude", self.out)


class KillOnStart(FakeOrca):
    """worker-start succeeds in Orca, then the driver dies before it reads the answer."""
    kill_title = "T001"

    def _answer(self, a):
        r = super()._answer(a)
        if (a[:2] == ["orchestration", "worker-start"] and self.kill_title
                and self.tasks[_flag(a, "--task")]["title"].startswith(self.kill_title)):
            self.kill_title = ""
            raise KeyboardInterrupt("SIGTERM while worker-start was answering")
        return r


class LostStartAnswer(FakeOrca):
    """worker-start succeeds in Orca, but the CLI reports a lost connection."""
    lose = True

    def _answer(self, a):
        r = super()._answer(a)
        if a[:2] == ["orchestration", "worker-start"] and self.lose:
            self.lose = False
            raise FailEnvelope({"ok": False, "error": {"code": "runtime_unavailable", "message": "connection closed"}})
        return r


class NoDoubleDispatch(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN.split("- [ ] T002")[0])

    def worker_starts(self, fake):
        return [s for s in fake.starts() if "audit" not in fake.tasks[_flag(s, "--task")]["title"]]

    def test_a_kill_during_worker_start_is_adopted_not_restarted(self):
        fake = KillOnStart(self.root)
        with self.assertRaises(KeyboardInterrupt):
            self.run_ship(fake, scripted_gate())
        with open(self.state_file, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["tasks"]["T001"]["starting"])
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(len(self.worker_starts(fake)), 1, self.out)
        self.assertIn("adopted", self.out)

    def test_a_lost_answer_to_worker_start_adopts_the_worker(self):
        fake = LostStartAnswer(self.root)
        fake.auto_resolve = "retry"
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(len(self.worker_starts(fake)), 1, self.out)
        self.assertEqual(len({d["worktree"] for d in fake.dispatches.values()}), 1)

    def test_a_kill_during_the_audit_start_does_not_start_a_second_audit(self):
        fake = KillOnStart(self.root)
        fake.kill_title = "demo audit"
        fake.on_audit = self.approve_audit()
        with self.assertRaises(KeyboardInterrupt):
            self.run_ship(fake, scripted_gate())
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        audits = [s for s in fake.starts() if "audit" in fake.tasks[_flag(s, "--task")]["title"]]
        self.assertEqual(len(audits), 1, self.out)


class AuditStartFails(FakeOrca):
    def _answer(self, a):
        if (a[:2] == ["orchestration", "worker-start"] and _flag(a, "--agent") == "codex"
                and "audit" in self.tasks.get(_flag(a, "--task"), {}).get("title", "")):
            raise FailEnvelope({"ok": False, "error": {"code": "agent_unavailable", "message": "codex is not installed"}})
        return super()._answer(a)


class AuditStartErrorOpensAGate(ShipCase):
    def test_a_non_transient_audit_start_error_asks_the_owner_and_retry_picks_another_reviewer(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake = AuditStartFails(self.root)
        fake.auto_resolve = "retry"
        fake.on_audit = self.approve_audit()
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"ship": {"claudeWeeklyThreshold": 100}}, fh)  # Sonnet implements: codex is the fresh reviewer
        code = self.run_ship(fake, scripted_gate())
        self.assertEqual(code, 0, self.out)
        self.assertTrue(any("could not start the audit" in g["question"] for g in fake.gates), self.out)
        audits = [_flag(s, "--agent") for s in fake.starts() if "audit" in fake.tasks[_flag(s, "--task")]["title"]]
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0], "codex")
        self.assertNotIn(audits[1], ("codex", "claude"), "another reviewer, and still not the implementer")


class OrcaCallsAreBounded(unittest.TestCase):
    def capture(self, call):
        import orca_client
        seen = []

        class P:
            returncode, stdout, stderr = 0, json.dumps({"ok": True, "result": {}}), ""

        def run(argv, **kw):
            seen.append(kw.get("timeout"))
            return P()
        saved = orca_client.subprocess.run
        orca_client.subprocess.run = run
        try:
            call(orca_client.OrcaClient(executable="orca"))
        finally:
            orca_client.subprocess.run = saved
        return seen[0]

    def test_every_built_in_call_has_a_subprocess_timeout(self):
        self.assertTrue(self.capture(lambda c: c.worker_list("run_1")))
        self.assertTrue(self.capture(lambda c: c.gate_list()))
        wait = self.capture(lambda c: c.check(wait=True, timeout_ms=15000))
        self.assertGreater(wait, 15)
        self.assertGreaterEqual(self.capture(lambda c: c.worker_start("t", worktree="new-top-level",
                                                                      agent="codex", model="m")), 600)


class SpacedRestarts(FakeOrca):
    """The first five inbox checks fail like Orca auto-updates; status answers at once."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.checks = 0
        self.failures = 0

    def _answer(self, a):
        if a[:2] == ["orchestration", "check"] and "--ack" not in a:
            self.checks += 1
            if self.failures < 5:
                self.failures += 1
                raise FailEnvelope({"ok": False, "error": {"code": "runtime_unavailable", "message": "restart"}})
        return super()._answer(a)


class ResumeLimitIsPerIncident(ShipCase):
    def test_restarts_spread_over_a_long_run_each_resume(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"ship": {"taskTimeoutMinutes": 10 ** 6}}, fh)
        fake = SpacedRestarts(self.root)
        fake.on_audit = self.approve_audit()

        def slow_clock():  # an hour passes between calls: each restart is its own incident
            self.clock_value += 3600
            return self.clock_value
        self.clock = slow_clock
        code = self.run_ship(fake, scripted_gate(), sleep=lambda s: None)
        self.assertEqual(code, 0, self.out)
        self.assertGreater(self.out.count("Orca is back; resuming"), ship.RESUME_MAX)


class TaskVerificationRuns(ShipCase):
    """Owner decision: a task is done only when its own Verification AND the gate pass."""

    def test_a_failing_verification_retries_the_task_with_its_output(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        results = [(1, "E   assert total() == 6.3")]

        def verify(command, path, timeout):
            self.verifications.append((command, path))
            return results.pop(0) if results else (0, "")
        ship.run_task_verification = verify
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual([c for c, _ in self.verifications], ["pnpm test", "pnpm test"])
        self.assertEqual({p for _, p in self.verifications}, {self.wtpath("proxima-api")})
        t001 = [v for v in fake.tasks.values() if v["title"].startswith("T001")]
        self.assertEqual(len(t001), 2, "the failing verification cost a retry")
        self.assertIn("assert total() == 6.3", t001[1]["spec"])
        self.assertIn("task verification", t001[1]["spec"])

    def test_a_verification_type_task_is_not_run_by_the_driver(self):
        self.write_plan("""# Change: demo

- [ ] T001 Look at the page
  - Repo: proxima-api
  - Type: verification
  - Verification: `open the page in a browser`
""")
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, scripted_gate())
        self.assertEqual(self.verifications, [])

    def test_the_worker_is_told_it_is_judged_on_both(self):
        # Its Verification and the affected tests; the driver's gate (lint, typecheck, affected
        # tests) follows, and the full suite runs once per repo before the audit.
        self.assertIn("gate", ship.WORKER_PREAMBLE)
        self.assertIn("Verification command and the tests affected", ship.WORKER_PREAMBLE)
        self.assertIn("lint, typecheck and the affected tests", ship.WORKER_PREAMBLE)


class RealTaskVerification(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_pass_fail_and_timeout(self):
        run = ship.run_task_verification
        self.assertEqual(run("true", self.tmp.name, 30), (0, ""))
        code, text = run("echo noise; echo 'AssertionError: boom'; exit 1", self.tmp.name, 30)
        self.assertEqual(code, 1)
        self.assertIn("AssertionError: boom", text)
        code, text = run("sleep 5", self.tmp.name, 0.5)
        self.assertEqual(code, 1)
        self.assertIn("timed out", text)

    def test_it_runs_in_the_worktree(self):
        with open(os.path.join(self.tmp.name, "marker"), "w") as fh:
            fh.write("x")
        self.assertEqual(ship.run_task_verification("test -f marker", self.tmp.name, 30)[0], 0)


class VerificationAndGateBothFail(ShipCase):
    def test_both_failures_reach_the_worker(self):
        def verify(command, path, timeout):
            return 1, "E   assert own_check()"
        ship.run_task_verification = verify
        gate_out = json.dumps({"decision": {"decision": "red", "reason": "failed: lint"},
                               "report": {"results": {"lint": {"slot": "lint", "outcome": "failed", "exitCode": 1,
                                                               "command": "ruff", "errorLines": ["x.py:1: E501"]}}}})
        drv = ship._Driver.__new__(ship._Driver)
        drv.state = state_of(task("T1", repo="r", verification="`pytest -k own`"))
        drv.state.worktree_paths = {"r": "/wt"}
        drv.gate_runner = lambda path, unit_only, timeout=900.0: (1, gate_out)
        drv.cfg = CFG
        drv.say = lambda *a, **k: None
        v = drv.run_gate(sd.RunGate("T1", True))
        self.assertEqual(v.exit_code, 1)
        self.assertIn("assert own_check()", v.reason)
        self.assertIn("pytest -k own", v.reason)
        self.assertIn("x.py:1: E501", v.reason)


if __name__ == "__main__":
    unittest.main()
