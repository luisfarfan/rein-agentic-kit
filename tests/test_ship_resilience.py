"""rein ship: the bell for the owner, and resuming after an Orca restart."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_ship import ENV, PLAN, FakeOrca, FailEnvelope, ShipCase, scripted_gate  # noqa: E402  (sets sys.path)
import ship  # noqa: E402
from orca_client import OrcaClient, OrcaError  # noqa: E402

RESTART = {"ok": False, "error": {"code": "runtime_unavailable", "message": "Orca runtime closed the connection"}}


class Resilient(ShipCase):
    def setUp(self):
        super().setUp()
        self.sleeps = []
        self.write_plan(PLAN.split("- [ ] T002")[0])

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock_value += seconds

    def ship(self, fake, gates=None, **kw):
        return self.run_ship(fake, gates or scripted_gate(), sleep=self.sleep, **kw)


class FlakyOrca(FakeOrca):
    """Fails `check` with `error` for the first `fail_checks` calls; `status` answers after `status_down` failures."""

    def __init__(self, *a, fail_checks=1, status_down=1, error=None, **kw):
        super().__init__(*a, **kw)
        self.fail_checks, self.status_down = fail_checks, status_down
        self.error = error or RESTART
        self.restarting = False
        self.status_calls_while_down = 0

    def _answer(self, a):
        if a[0] == "status" and self.restarting:
            self.status_calls_while_down += 1
            if self.status_down is not None and self.status_calls_while_down >= self.status_down:
                self.restarting = False
            else:
                raise FailEnvelope(self.error)
        if a[:2] == ["orchestration", "check"] and "--ack" not in a and self.fail_checks:
            self.fail_checks -= 1
            self.restarting = True
            self.status_calls_while_down = 0
            raise FailEnvelope(self.error)
        return super()._answer(a)


class TestAutoResume(Resilient):
    def test_restart_resumes_and_finishes(self):
        fake = FlakyOrca(self.root, status_down=2)
        fake.on_audit = self.approve_audit()
        code = self.ship(fake)
        self.assertEqual(code, 0, self.out)
        self.assertIn("ship: Orca is restarting (runtime_unavailable); waiting to resume…", self.out)
        self.assertIn("ship: Orca is back; resuming", self.out)
        self.assertEqual(self.sleeps, [ship.RESUME_POLL_SECONDS])

    def test_never_answering_gives_up_after_the_wait(self):
        fake = FlakyOrca(self.root, status_down=None)
        code = self.ship(fake)
        self.assertEqual(code, 126, self.out)
        self.assertIn("state saved, re-run `rein ship demo` to resume", self.out)
        self.assertGreaterEqual(self.clock_value - 1000.0, ship.RESUME_WAIT_SECONDS)
        self.assertLess(self.clock_value - 1000.0, ship.RESUME_WAIT_SECONDS + 2 * ship.RESUME_POLL_SECONDS)
        self.assertTrue(all(s == ship.RESUME_POLL_SECONDS for s in self.sleeps))

    def test_other_errors_keep_the_old_behaviour(self):
        fake = FlakyOrca(self.root, error={"ok": False, "error": {"code": "bad_request", "message": "no"}})
        code = self.ship(fake)
        self.assertEqual(code, 126)
        self.assertIn("state saved, re-run", self.out)
        self.assertNotIn("restarting", self.out)
        self.assertEqual(self.sleeps, [])

    def test_exit_code_with_connection_text_is_transient(self):
        fake = FlakyOrca(self.root, status_down=1, error={"ok": False})
        fake.error = None
        orig = fake._answer
        # a bare non-JSON failure: exit_1 with "Connection refused" on stderr
        calls = {"n": 0}

        def runner(argv):
            if argv[1] == "orchestration" and argv[2] == "check" and "--ack" not in argv and calls["n"] == 0:
                calls["n"] += 1
                return 1, "", "Error: Connection refused"
            return FakeOrca.__call__(fake, argv)
        fake.fail_checks = 0
        fake.on_audit = self.approve_audit()
        client = OrcaClient(executable="ORCA", runner=runner)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ship.run_ship(self.root, "demo", client, scripted_gate(), env=ENV, clock=self.clock,
                                 state_path=self.state_file, sleep=self.sleep)
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("restarting (exit_1)", buf.getvalue())

    def test_gives_up_after_three_automatic_resumes(self):
        fake = FlakyOrca(self.root, fail_checks=99, status_down=1)
        code = self.ship(fake)
        self.assertEqual(code, 126, self.out)
        self.assertEqual(self.out.count("Orca is back; resuming"), ship.RESUME_MAX)
        self.assertIn("state saved, re-run `rein ship demo` to resume", self.out)

    def test_transient_classification(self):
        self.assertTrue(ship.is_transient(OrcaError("unavailable", "cannot run orca")))
        self.assertFalse(ship.is_transient(OrcaError("exit_1", "boom")))
        self.assertTrue(ship.is_transient(OrcaError("exit_1", "Connection reset")))


class TestBell(Resilient):
    def question(self, f):
        did = next(iter(f.dispatches))
        return [{"id": "msg_q", "type": "question", "from_handle": "term_" + did, "subject": "?",
                 "body": "Use REST or GraphQL?", "payload": json.dumps({"dispatchId": did})}]

    def test_gate_rings_once_and_finish_rings_once(self):
        fake = FakeOrca(self.root, autocomplete=True)
        fake.on_audit = self.approve_audit()
        pend = [self.question]
        fake.pending = pend + [lambda f: f.resolve_gate(0, "REST") or [], lambda f: [], lambda f: []]
        code = self.ship(fake)
        self.assertEqual(code, 0, self.out)
        waiting = [l for l in self.out.splitlines() if "waiting for the owner" in l]
        self.assertEqual(len(waiting), 1, self.out)
        self.assertTrue(waiting[0].endswith("\a"), repr(waiting[0]))
        self.assertEqual(self.out.count("\a"), 2, repr(self.out))
        last = [l for l in self.out.splitlines() if "tasks done, audit" in l]
        self.assertTrue(last[0].endswith("\a"))

    def test_ring_writes_bell_with_the_line(self):
        buf = io.StringIO()
        d = ship._Driver.__new__(ship._Driver)
        d.dry_run = False
        with redirect_stdout(buf):
            d.ring("hello")
        self.assertEqual(buf.getvalue(), "ship: hello\a\n")

    def test_dry_run_output_has_no_bell(self):
        runner = ship.DryRunRunner(["proxima-api", "proxima-admin"], lambda line: None)
        client = OrcaClient(executable="ORCA", runner=runner)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ship.run_ship(self.root, "demo", client, scripted_gate(), env=ENV, clock=self.clock,
                                 dry_run=True, state_path=self.state_file, sleep=self.sleep)
        self.assertEqual(code, 0, buf.getvalue())
        self.assertNotIn("\a", buf.getvalue())

    def test_dry_run_driver_does_not_ring(self):
        d = ship._Driver.__new__(ship._Driver)
        d.dry_run = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            d.ring("hello")
        self.assertNotIn("\a", buf.getvalue())
        self.assertIn("ship: hello", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
