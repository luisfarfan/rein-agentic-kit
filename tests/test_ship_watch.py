"""`rein watch`: block until the run needs the owner, print it, exit."""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship_state  # noqa: E402
import ship_watch  # noqa: E402
from test_ship_ux import FakeOrca, UxCase, _gate  # noqa: E402


class WatchCase(UxCase):
    def watch(self, fake, change="demo", once=False, alive=True, max_polls=5):
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            if len(sleeps) >= max_polls:
                raise AssertionError("watch never exited")

        self.sleeps = sleeps
        return self.capture(ship_watch.run_watch, self.root, change, self.client(fake), once=once,
                            sleep=sleep, driver_alive=lambda c: alive)

    def set_phase(self, change, phase, **kw):
        path = ship_state.state_path(self.root, change)
        state = ship_state.load(path)
        state.phase = phase
        for k, v in kw.items():
            setattr(state, k, v)
        ship_state.save(state, path)

    def seen(self, change="demo"):
        with open(os.path.join(self.root, ".rein", "ship", change + ".seen"), encoding="utf-8") as fh:
            return fh.read().split()


class TestWatchGates(WatchCase):
    def test_a_new_gate_is_printed_once_and_remembered(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1", question="Which db? " + "z" * 3000, options=["a", "b"])])
        code, out = self.watch(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("QUESTION g1 (question) task T001", out)
        self.assertIn("question: Which db? " + "z" * 3000, out)
        self.assertIn("options: a, b", out)
        self.assertEqual(self.seen(), ["g1"])
        code, out = self.watch(fake, once=True)
        self.assertEqual(code, 1, out)
        self.assertNotIn("QUESTION", out)

    def test_waits_until_a_gate_appears(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[])
        polls = []

        def sleep(s):
            polls.append(s)
            if len(polls) == 2:
                fake.gates.append(_gate("g1"))

        code, out = self.capture(ship_watch.run_watch, self.root, "demo", self.client(fake),
                                 sleep=sleep, driver_alive=lambda c: True)
        self.assertEqual(code, 0, out)
        self.assertEqual(polls, [ship_watch.WATCH_POLL_SECONDS] * 2)
        self.assertIn("QUESTION g1", out)

    def test_a_gate_with_a_queued_answer_is_ignored(self):
        self.write_state("demo", {"g1": "question"})
        ship_state.write_answer(self.root, "demo", "g1", "REST", 1.0)
        code, out = self.watch(FakeOrca(gates=[_gate("g1")]), once=True)
        self.assertEqual(code, 1, out)
        self.assertNotIn("QUESTION", out)

    def test_once_with_nothing_pending_exits_one(self):
        self.write_state("demo", {"g1": "question"})
        code, out = self.watch(FakeOrca(gates=[_gate("g1", status="resolved")]), once=True)
        self.assertEqual(code, 1)


class TestWatchEnd(WatchCase):
    def test_finished(self):
        self.write_state("demo", {})
        self.set_phase("demo", "done", audit_result="approved")
        path = ship_state.state_path(self.root, "demo")
        state = ship_state.load(path)
        state.tasks["T001"] = ship_state.TaskState(id="T001", repo="r", deps=[], human_review=False,
                                                   verification="", type="", status="done")
        ship_state.save(state, path)
        code, out = self.watch(FakeOrca(gates=[_gate("g1")]))
        self.assertEqual(code, 0, out)
        self.assertIn("FINISHED demo: audit approved", out)
        self.assertIn("T001 done", out)
        self.assertNotIn("QUESTION", out)

    def test_stopped_after_two_polls_without_a_driver(self):
        self.write_state("demo", {})
        code, out = self.watch(FakeOrca(), alive=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.sleeps), 1)
        self.assertIn("STOPPED demo: the ship driver is not running (phase running); resume it in "
                      'the Orca tab "rein ship demo"', out)

    def test_a_live_driver_is_never_stopped(self):
        self.write_state("demo", {})
        with self.assertRaises(AssertionError):
            self.watch(FakeOrca(), alive=True, max_polls=4)

    def test_once_never_reports_stopped(self):
        self.write_state("demo", {})
        code, out = self.watch(FakeOrca(), once=True, alive=False)
        self.assertEqual(code, 1)
        self.assertNotIn("STOPPED", out)


class TestDriverDetection(unittest.TestCase):
    def test_matches_a_ship_driver_for_that_change(self):
        ps = lambda: ["100 python3 /x/bin/rein ship demo --root /w", "5 vim"]
        self.assertTrue(ship_watch.driver_alive("demo", ps=ps, own_pid=999))

    def test_other_change_does_not_match(self):
        ps = lambda: ["100 python3 /x/bin/rein ship other --root /w"]
        self.assertFalse(ship_watch.driver_alive("demo", ps=ps, own_pid=999))

    def test_never_matches_the_watcher_itself(self):
        ps = lambda: ["200 python3 /x/bin/rein watch demo --root /w",
                      "201 sh -c rein watch demo ship",
                      "999 python3 /x/bin/rein ship demo"]
        self.assertFalse(ship_watch.driver_alive("demo", ps=ps, own_pid=999))


class TestWatchCli(unittest.TestCase):
    def setUp(self):
        loader = SourceFileLoader("rein_cli_watch", BIN)
        spec = importlib.util.spec_from_file_location("rein_cli_watch", BIN, loader=loader)
        self.cli = importlib.util.module_from_spec(spec)
        loader.exec_module(self.cli)
        self.seen = []
        self._real = self.cli._watch.run_watch
        self.cli._watch.run_watch = lambda *a, **k: self.seen.append((a[:2], k)) or 0
        self.addCleanup(lambda: setattr(self.cli._watch, "run_watch", self._real))

    def test_shapes(self):
        self.assertEqual(self.cli.main(["watch", "demo", "--root", "/r", "--once"]), 0)
        (root, change), kw = self.seen[0]
        self.assertEqual((root, change, kw["once"]), ("/r", "demo", True))

    def test_usage_is_two(self):
        for argv in ([], ["a", "b"], ["demo", "--nope"], ["demo", "--root"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(self.cli.main(["watch", *argv]), 2, argv)
            self.assertIn("usage: rein watch", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
