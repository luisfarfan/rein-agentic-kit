"""Regressions from the independent review of the driver and done hunts.

Every driver case here runs the REAL task-verification runner (ShipCase stubs it by default).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
import ship_state  # noqa: E402
import ship_watch as sw  # noqa: E402
import verify  # noqa: E402
from test_ship import PLAN, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402


def one_task_plan(verification, type_=""):
    return """# Change: demo

- [ ] T001 Add the api endpoint
  - Repo: proxima-api
%s  - Verification: %s
""" % (("  - Type: %s\n" % type_) if type_ else "", verification)


class B1PlanVerificationField(unittest.TestCase):
    """Only a field that BEGINS with one backticked command is runnable, and only that command."""

    def cmd(self, field):
        return ship.task_verification_command(field)

    def test_a_leading_backticked_command_is_extracted_without_the_prose(self):
        self.assertEqual(self.cmd("`true` devuelve 0"), ("true", ""))
        self.assertEqual(self.cmd("`alembic heads` devuelve un solo head; `uv run pytest -q`")[0], "alembic heads")
        self.assertEqual(self.cmd("  `pnpm test --filter api`  ")[0], "pnpm test --filter api")

    def test_prose_is_not_run(self):
        for field in ("Chrome a 390: el carrito no desborda",
                      "Chrome: precio > 0 en /pagar",
                      "Open the login page and check the error",
                      "Playwright a 390 px — `document.scrollWidth === clientWidth`",
                      "``double`` ticks", "`unterminated", "``", ""):
            command, why = self.cmd(field)
            self.assertEqual(command, "", field)
            self.assertTrue(why, field)

    def test_dangerous_or_outward_commands_are_refused(self):
        for field in ("`aws iam get-account-summary --profile proxima-prod` devuelve 0",
                      "`pnpm deploy`", "`git push origin main`", "`rm -rf build`", "`sudo make`",
                      "`curl https://x`", "`wget x`", "`cdk diff`", "`terraform plan`", "`kubectl get pods`",
                      "`docker push img`", "`npm publish`", "`pnpm publish`", "`echo x > /etc/hosts`",
                      "`echo x >../outside`", "`echo x >> ~/notes`", "`echo x | tee /tmp/x`"):
            command, why = self.cmd(field)
            self.assertEqual(command, "", field)
            self.assertIn("refused", why, field)

    def test_a_local_redirect_is_allowed(self):
        self.assertEqual(self.cmd("`pytest -q 2>&1 >out.txt`")[0], "pytest -q 2>&1 >out.txt")
        self.assertEqual(self.cmd("`make check 2>/dev/null`")[0], "make check 2>/dev/null")


class B1RealVerificationInTheDriver(ShipCase):
    def setUp(self):
        super().setUp()
        ship.run_task_verification = self._saved[3]  # the real runner, not ShipCase's stub
        for repo in ("proxima-api", "proxima-admin"):
            os.makedirs(self.wtpath(repo), exist_ok=True)

    def ship_one(self, verification, type_=""):
        self.write_plan(one_task_plan(verification, type_))
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        fake.auto_resolve = "skip"  # a gate the fix should not open must not hang the test
        code = self.run_ship(fake, scripted_gate(), sleep=lambda s: None)
        workers = [v for v in fake.tasks.values() if v["title"].startswith("T001")]
        return code, fake, workers

    def test_a_backticked_true_followed_by_prose_runs_and_passes(self):
        code, _, workers = self.ship_one("`true` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertIn("T001 verification exit 0", self.out)

    def test_a_prose_field_is_not_run_and_the_gate_decides(self):
        code, fake, workers = self.ship_one("Chrome a 390: el carrito no desborda")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertNotIn("verification exit", self.out)
        self.assertEqual(fake.gates, [])

    def test_prose_with_a_redirect_creates_no_file(self):
        code, _, _ = self.ship_one("Chrome: precio > 0 en /pagar")
        self.assertEqual(code, 0, self.out)
        self.assertNotIn("0", os.listdir(self.wtpath("proxima-api")))

    def test_a_dangerous_command_is_refused_and_logged(self):
        code, _, workers = self.ship_one("`touch ran && echo deploy` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1)
        self.assertFalse(os.path.exists(os.path.join(self.wtpath("proxima-api"), "ran")))
        self.assertIn("refused", self.out)

    def test_a_real_failing_command_reaches_the_worker_then_passes(self):
        check = "`test -f .seen || { touch .seen; echo 'AssertionError: own-check'; exit 1; }` pasa"
        code, _, workers = self.ship_one(check)
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 2, "the failing verification cost one retry")
        self.assertIn("AssertionError: own-check", workers[1]["spec"])
        self.assertIn("task verification", workers[1]["spec"])

    def test_command_not_found_burns_no_retry(self):
        code, fake, workers = self.ship_one("`rein-no-such-tool-xyz --check` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertIn("could not run", self.out)
        self.assertEqual(fake.gates, [])


class LateStartAnswer(FakeOrca):
    """worker-start fails with a lost answer, yet Orca does start the worker -- and lists it only
    from the SECOND worker-list on (Orca answered late)."""

    def __init__(self, root, title="T001", **kw):
        super().__init__(root, **kw)
        self.late_title, self.hidden, self.lists = title, None, 0

    def _answer(self, a):
        if a[:2] == ["orchestration", "worker-list"]:
            self.lists += 1
            out = super()._answer(a)
            if self.hidden and self.lists <= self.hidden[1]:
                out = {"workers": [r for r in out["workers"] if r["dispatchId"] != self.hidden[0]]}
            return out
        r = super()._answer(a)
        if (a[:2] == ["orchestration", "worker-start"] and self.late_title
                and self.tasks[_flag(a, "--task")]["title"].startswith(self.late_title)):
            self.late_title = ""
            self.hidden = (r["dispatchId"], self.lists + 1)  # the very next list misses it
            raise FailEnvelope({"ok": False, "error": {"code": "runtime_unavailable", "message": "connection closed"}})
        return r


class M1LateWorkerStartAnswer(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN.split("- [ ] T002")[0])

    def starts(self, fake, audit):
        return [s for s in fake.starts() if ("audit" in fake.tasks[_flag(s, "--task")]["title"]) == audit]

    def test_a_worker_listed_late_is_adopted_on_the_retry_not_started_twice(self):
        fake = LateStartAnswer(self.root)
        fake.auto_resolve = "retry"
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate(), sleep=lambda s: None), 0, self.out)
        self.assertEqual(len(self.starts(fake, audit=False)), 1, self.out)
        self.assertIn("adopted", self.out)

    def test_an_auditor_listed_late_is_adopted_on_resume_not_started_twice(self):
        fake = LateStartAnswer(self.root, title="demo audit")
        fake.auto_resolve = "retry"
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate(), sleep=lambda s: None), 0, self.out)
        self.assertEqual(len(self.starts(fake, audit=True)), 1, self.out)


class M3DriverAliveArgvShapes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def alive(self, line, root="/Users/l/plan"):
        return sw.driver_alive("demo", ps=lambda: [line], own_pid=1, root=root)

    def test_a_rein_path_with_spaces_is_a_driver(self):
        folder = os.path.join(self.tmp.name, "Application Support", "rein", "bin")
        os.makedirs(folder)
        rein = os.path.join(folder, "rein")
        open(rein, "w").close()
        self.assertTrue(self.alive("123 /opt/homebrew/bin/python3.13 %s ship demo --root /Users/l/plan" % rein))
        self.assertTrue(self.alive("123 %s ship demo --root /Users/l/plan" % rein))

    def test_a_spaced_path_that_is_not_a_file_is_not_a_driver(self):
        self.assertFalse(self.alive("123 /usr/bin/grep rein ship demo"))
        self.assertFalse(self.alive("123 /usr/bin/vim notes about rein ship demo"))

    def test_interpreter_options_with_a_value(self):
        self.assertTrue(self.alive("123 python3 -X utf8 /x/bin/rein ship demo --root /Users/l/plan"))
        self.assertTrue(self.alive("123 python3 -W ignore -I /x/bin/rein ship demo --root /Users/l/plan"))
        self.assertFalse(self.alive("123 python3 -c /x/bin/rein ship demo"))
        self.assertFalse(self.alive("123 python3 -m /x/bin/rein ship demo"))

    def test_a_future_ship_flag_still_matches(self):
        self.assertTrue(self.alive("123 python3 /x/bin/rein ship demo --root /Users/l/plan --effort high"))
        self.assertTrue(self.alive("123 python3 /x/bin/rein ship demo --verbose"))
        self.assertFalse(self.alive("123 python3 /x/bin/rein ship demo and more words"))


class DriverPidLiveness(ShipCase):
    def test_the_driver_records_its_pid_in_the_state(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, scripted_gate())
        with open(self.state_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh).get("driver_pid"), os.getpid())

    def put_pid(self, pid):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as fh:
            json.dump({"change": "demo", "plan_path": self.root, "driver_pid": pid}, fh)

    def spawn(self, *argv):
        proc = subprocess.Popen(list(argv))
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        time.sleep(0.2)
        return proc

    def test_a_recorded_live_driver_pid_is_alive_whatever_its_argv_looks_like(self):
        # an argv the heuristics cannot place (ps shows nothing at all here)
        proc = self.spawn(sys.executable, "-c", "import time; time.sleep(30)", "rein", "ship", "demo")
        self.put_pid(proc.pid)
        self.assertTrue(sw.driver_alive("demo", ps=lambda: [], own_pid=1, root=self.root))
        proc.kill()
        proc.wait()
        self.assertFalse(sw.driver_alive("demo", ps=lambda: [], own_pid=1, root=self.root))

    def test_a_reused_pid_running_something_else_is_not_the_driver(self):
        proc = self.spawn("sleep", "30")
        self.put_pid(proc.pid)
        self.assertFalse(sw.driver_alive("demo", ps=lambda: [], own_pid=1, root=self.root))


PASSING = "\n".join("tests/test_m%d.py::test_case_%d PASSED" % (i, i) for i in range(300))


class M4FailureLinesEdges(unittest.TestCase):
    def test_a_failure_ending_in_ok_is_kept(self):
        out = verify.failure_lines(PASSING + "\nAssertionError: response was not ok\n" + PASSING)
        self.assertIn("AssertionError: response was not ok", out)

    def test_a_failure_mentioning_passed_is_kept(self):
        out = verify.failure_lines(PASSING + "\nE   AssertionError: expected PASSED, got FAILED\n" + PASSING)
        self.assertIn("E   AssertionError: expected PASSED, got FAILED", out)

    def test_real_passing_lines_are_still_dropped(self):
        noise = "\n".join(["test_a (t.T) ... ok", "\u2713 renders (3 ms)", "PASS src/a.test.js",
                            "tests/t.py::test_x PASSED [ 10%]"] * 80)
        out = verify.failure_lines(noise + "\nFAILED tests/t.py::test_y - boom")
        self.assertIn("FAILED tests/t.py::test_y - boom", out)
        self.assertFalse(any("... ok" in x or "PASSED [" in x or x.startswith("PASS ") for x in out), out)

    def test_eslint_errors_keep_their_file_path(self):
        text = ""
        for f in range(10):
            text += "\n/repo/src/file%d.js\n" % f + "\n".join(
                "  %d:5  error  'x%d' is not defined  no-undef" % (n, n) for n in range(1, 12)) + "\n"
        text += "\n\u2716 110 problems (110 errors, 0 warnings)\n"
        out = verify.failure_lines(text)
        self.assertIn("/repo/src/file9.js", out)
        last_error = max(i for i, x in enumerate(out) if " error " in x)
        headers = [i for i, x in enumerate(out) if x.startswith("/repo/")]
        for i, line in enumerate(out):
            if " error " in line:
                self.assertTrue(any(h < i for h in headers), "error line without its file: %r" % line)
        self.assertLess(max(headers), last_error)

    def test_an_oversized_last_strong_line_does_not_starve_the_rest(self):
        text = PASSING + "\nAssertionError: short one\n" + "FAILED " + "x" * 400
        out = verify.failure_lines(text, cap=10 ** 6, max_chars=120)
        self.assertIn("AssertionError: short one", out)


def _task(tid, repo="a", status="pending", **kw):
    kw.setdefault("human_review", False)
    kw.setdefault("verification", "make test")
    kw.setdefault("type", "implementation")
    return TaskState(id=tid, repo=repo, deps=[], status=status, **kw)


class M5SkippedFixTasks(unittest.TestCase):
    def reauditing_after_a_skip(self):
        st = ShipState(change="c", plan_path="p.md", phase="auditing", audit_rounds=1,
                       previous_blocking=["BLOCKING: leak"])
        st.tasks["T1"] = _task("T1", status="done")
        st.tasks["AUDIT1-a"] = _task("AUDIT1-a", status="failed", verification="",
                                     audit_findings="BLOCKING: leak", last_failure="BLOCKING: leak")
        return st

    def test_more_audit_rounds_never_reopen_a_skipped_fix_on_their_own(self):
        cfg = sd.ShipConfig(max_audit_rounds=4)
        st, actions = sd.decide(self.reauditing_after_a_skip(),
                                sd.AuditVerdict("CHANGES_REQUESTED", 1, ["src/a.py"], ["BLOCKING: leak"]), cfg)
        self.assertNotIn("AUDIT2-a", st.tasks, "a new fix round reopened what the owner skipped")
        self.assertEqual(st.phase, "auditing")
        self.assertEqual([g["kind"] for g in st.pending_gates], ["audit_exhausted"])
        self.assertIn("skipped", st.pending_gates[0]["question"])

    def test_without_a_skip_more_rounds_still_fix_automatically(self):
        cfg = sd.ShipConfig(max_audit_rounds=4)
        st = self.reauditing_after_a_skip()
        st.tasks["AUDIT1-a"].status = "done"
        st, _ = sd.decide(st, sd.AuditVerdict("CHANGES_REQUESTED", 1, ["src/a.py"], ["BLOCKING: leak"]), cfg)
        self.assertIn("AUDIT2-a", st.tasks)


class M5SkippedFixThenApproved(ShipCase):
    def test_an_approving_reaudit_after_a_skipped_fix_reports_success(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake = FakeOrca(self.root)
        verdicts = [("CHANGES_REQUESTED", ["BLOCKING: src/proxima-api.py leaks the token"]), ("APPROVED", [])]

        def audit():
            verdict, findings = verdicts.pop(0)
            self.approve_audit(verdict, findings)()
        fake.on_audit = audit
        fake.auto_resolve = "skip"
        code = self.run_ship(fake, scripted_gate(0, 126))
        self.assertEqual(code, 0, self.out)
        self.assertIn("audit approved", self.out)
        self.assertIn("1 audit-fix task skipped by the owner", self.out)


class M6EveryReviewerRefused(unittest.TestCase):
    OK = {"claude": {"session": 10.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}

    def auditing(self, excluded):
        st = ShipState(change="c", plan_path="p.md", phase="auditing", audit_excluded=list(excluded),
                       launched_agents=["claude"])
        st.tasks["T1"] = _task("T1", status="done")
        return st

    def test_a_refused_reviewer_is_not_picked_again(self):
        st, actions = sd.decide(self.auditing(["claude", "codex"]),
                                sd.ChangesComputed(["src/a.py"], self.OK), sd.ShipConfig())
        self.assertFalse([a for a in actions if isinstance(a, sd.StartAudit)], actions)
        gates = [g for g in st.pending_gates if g["kind"] == "setup"]
        self.assertEqual(len(gates), 1)
        self.assertIn("no other reviewer is available", gates[0]["question"])
        self.assertEqual(st.audit_excluded, [], "the owner's retry tries every reviewer again")

    def test_one_refused_reviewer_leaves_the_other(self):
        _, actions = sd.decide(self.auditing(["codex"]), sd.ChangesComputed(["src/a.py"], self.OK), sd.ShipConfig())
        starts = [a for a in actions if isinstance(a, sd.StartAudit)]
        self.assertEqual(len(starts), 1)
        self.assertNotEqual(starts[0].agent, "codex")


class VerifyPlanProseTest(unittest.TestCase):
    def test_verify_plan_never_runs_prose(self):
        import verify
        with tempfile.TemporaryDirectory() as root:
            report = verify.verify_plan(root, [
                {"id": "T1", "verification": "Chrome: precio > 0 en /pagar",
                 "verificationRaw": "Chrome: precio > 0 en /pagar"},
                {"id": "T2", "verification": "true", "verificationRaw": "`true` devuelve 0"},
            ])
            self.assertEqual(os.listdir(root), [])  # prose created no file
            text = json.dumps(report)
            self.assertIn("not run", text)
            self.assertIn('"command": "true"', text)


if __name__ == "__main__":
    unittest.main()


class ProdWordTest(unittest.TestCase):
    def test_prod_is_refused_only_as_its_own_word(self):
        for cmd in ("uv run pytest tests/unit/catalog/test_products.py", "pnpm vitest run src/production-ready.spec.ts"):
            self.assertEqual(ship.verification_refusal(cmd), "", cmd)
        for cmd in ("aws s3 ls --profile proxima-prod", "pnpm run seed --env prod", "cat config/prod.yml"):
            self.assertTrue(ship.verification_refusal(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
