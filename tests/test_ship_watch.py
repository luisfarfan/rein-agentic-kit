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
    def watch(self, fake, change="demo", once=False, alive=True, max_polls=5, skip=()):
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            if len(sleeps) >= max_polls:
                raise AssertionError("watch never exited")

        self.sleeps = sleeps
        return self.capture(ship_watch.run_watch, self.root, change, self.client(fake), once=once,
                            sleep=sleep, driver_alive=lambda c: alive, skip=frozenset(skip))

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
    def test_a_new_gate_is_printed_with_its_full_question(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1", question="Which db? " + "z" * 3000, options=["a", "b"])])
        code, out = self.watch(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("PENDING g1 (question) task T001", out)
        self.assertIn("question: Which db? " + "z" * 3000, out)
        self.assertIn("options: a, b", out)

    def test_two_gates_in_one_wake_are_both_printed(self):
        self.write_state("demo", {"g1": "question", "g2": "timeout"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2", options=["retry", "skip"])])
        code, out = self.watch(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("g1 (question)", out)
        self.assertIn("g2 (timeout)", out)

    def test_a_restarted_watcher_reprints_an_unanswered_gate_as_pending(self):
        self.write_state("demo", {"g1": "question"})
        fake = FakeOrca(gates=[_gate("g1")])
        for _ in range(2):
            code, out = self.watch(fake)
            self.assertEqual(code, 0, out)
            self.assertIn("PENDING g1 (question)", out)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".rein", "ship", "demo.seen")))

    def test_an_ignored_queued_answer_reappears_once_its_file_is_gone(self):
        self.write_state("demo", {"g1": "question"})
        ship_state.write_answer(self.root, "demo", "g1", "REST", 1.0)
        fake = FakeOrca(gates=[_gate("g1")])
        code, out = self.watch(fake, once=True)
        self.assertEqual(code, 1, out)
        ship_state.drop_answer(self.root, "demo", "g1")  # the driver dropped it without resolving
        code, out = self.watch(fake, once=True)
        self.assertEqual(code, 0, out)
        self.assertIn("PENDING g1", out)

    def test_waits_for_a_gate_that_opens_later_and_prints_it_as_question(self):
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
        self.assertNotIn("PENDING", out)

    def test_skip_suppresses_only_the_listed_ids(self):
        self.write_state("demo", {"g1": "question", "g2": "question"})
        fake = FakeOrca(gates=[_gate("g1"), _gate("g2")])
        code, out = self.watch(fake, skip=["g1"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("g1", out)
        self.assertIn("PENDING g2", out)

    def test_skipping_everything_pending_waits_and_a_new_gate_still_wakes(self):
        self.write_state("demo", {"g1": "question", "g2": "question"})
        fake = FakeOrca(gates=[_gate("g1")])
        polls = []

        def sleep(s):
            polls.append(s)
            if len(polls) == 2:
                fake.gates.append(_gate("g2"))

        code, out = self.capture(ship_watch.run_watch, self.root, "demo", self.client(fake),
                                 sleep=sleep, driver_alive=lambda c: True, skip=frozenset(["g1"]))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(polls), 2)
        self.assertIn("QUESTION g2", out)
        self.assertNotIn("g1", out)

    def test_a_gate_with_a_queued_answer_is_ignored(self):
        self.write_state("demo", {"g1": "question"})
        ship_state.write_answer(self.root, "demo", "g1", "REST", 1.0)
        code, out = self.watch(FakeOrca(gates=[_gate("g1")]), once=True)
        self.assertEqual(code, 1, out)
        self.assertNotIn("QUESTION", out)
        self.assertNotIn("PENDING", out)

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

    def test_a_change_or_path_containing_watch_is_not_the_watcher(self):
        ps = lambda: ["100 python3 /x/bin/rein ship watch-tower --root /w/watch"]
        self.assertTrue(ship_watch.driver_alive("watch-tower", ps=ps, own_pid=999))

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

    def test_skip_is_parsed(self):
        self.assertEqual(self.cli.main(["watch", "demo", "--skip", "g1,g2"]), 0)
        self.assertEqual(self.seen[0][1]["skip"], frozenset(["g1", "g2"]))
        self.assertEqual(self.cli.main(["watch", "demo", "--skip=g3"]), 0)
        self.assertEqual(self.seen[1][1]["skip"], frozenset(["g3"]))

    def test_usage_is_two(self):
        for argv in ([], ["a", "b"], ["demo", "--nope"], ["demo", "--root"], ["demo", "--skip"],
                     ["demo", "--skip", ""], ["demo", "--skip", ","]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(self.cli.main(["watch", *argv]), 2, argv)
            self.assertIn("usage: rein watch", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
