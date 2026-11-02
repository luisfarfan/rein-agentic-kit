"""`rein watch`: block until the run needs the owner, print it, exit."""

from __future__ import annotations

import io
import os
import sys
import unittest
from functools import partial
from unittest.mock import patch
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

    def run_with(self, alive_seq, on_sleep=None, max_polls=40):
        """`alive_seq[i]` is what driver_alive answers on poll i (the last value repeats)."""
        polls = []

        def alive(change):
            return alive_seq[min(len(polls), len(alive_seq) - 1)]

        def sleep(s):
            polls.append(s)
            if on_sleep:
                on_sleep(len(polls))
            if len(polls) >= max_polls:
                raise AssertionError("watch never exited")

        code, out = self.capture(ship_watch.run_watch, self.root, "demo", self.client(FakeOrca()),
                                 sleep=sleep, driver_alive=alive)
        return code, out, polls

    def test_stopped_after_the_startup_grace_when_never_seen(self):
        self.write_state("demo", {})
        code, out, polls = self.run_with([False])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(polls), ship_watch.STARTUP_GRACE_POLLS - 1)
        self.assertIn("STOPPED demo: the ship driver is not running (phase running); resume it in "
                      'the Orca tab "rein ship demo"', out)

    def test_a_slow_starting_driver_is_not_stopped(self):
        self.write_state("demo", {})
        with self.assertRaises(AssertionError):
            self.run_with([False, False, False, False, True], max_polls=12)

    def test_stopped_two_polls_after_a_seen_driver_dies(self):
        self.write_state("demo", {})
        code, out, polls = self.run_with([True, True, False])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(polls), 3)
        self.assertIn("STOPPED demo", out)

    def test_a_state_file_being_written_keeps_the_watch_alive(self):
        self.write_state("demo", {})
        path = ship_state.state_path(self.root, "demo")

        def touch(n):
            os.utime(path, (1000 + n, 1000 + n))

        with self.assertRaises(AssertionError):
            self.run_with([True, False], on_sleep=touch, max_polls=10)

    def test_a_live_driver_is_never_stopped(self):
        self.write_state("demo", {})
        with self.assertRaises(AssertionError):
            self.watch(FakeOrca(), alive=True, max_polls=4)

    def test_once_orca_error_is_126_not_nothing_pending(self):
        self.write_state("demo", {"g1": "question"})

        def broken(argv):
            return 1, '{"ok": false, "error": {"code": "down", "message": "orca is down"}}'

        code, out = self.capture(ship_watch.run_watch, self.root, "demo", self.client(broken), once=True,
                                 sleep=lambda s: None, driver_alive=lambda c: True)
        self.assertEqual(code, 126, out)
        self.assertIn("orca error", out)

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

    def test_only_a_rein_process_running_ship_matches(self):
        for line in ("300 grep rein ship demo",
                     "301 tail -f /tmp/rein ship demo",
                     "302 claude -p please run rein ship demo now",
                     "303 vim /x/bin/rein ship demo",
                     "304 orca terminal send --text rein ship demo",
                     "305 python3 /x/bin/rein answer demo ship",
                     "306 python3 /x/bin/rein ship demo extra words",
                     "307 python3 -c import x; rein ship demo"):
            self.assertFalse(ship_watch.driver_alive("demo", ps=lambda: [line], own_pid=999), line)
        for line in ("400 /usr/bin/python3 /x/bin/rein ship demo",
                     "401 /opt/py/bin/python3.12 -u /x/bin/rein ship demo --root /w --agent codex --model m",
                     "402 /Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python "
                     "/v/bin/rein ship demo --root=/w",
                     "403 /v/bin/rein ship demo"):
            self.assertTrue(ship_watch.driver_alive("demo", ps=lambda: [line], own_pid=999), line)

    def test_root_is_respected(self):
        ps = lambda: ["100 python3 /x/bin/rein ship demo --root /other/plan"]
        self.assertFalse(ship_watch.driver_alive("demo", ps=ps, own_pid=999, root="/w/plan"))
        self.assertTrue(ship_watch.driver_alive("demo", ps=ps, own_pid=999, root="/other/plan"))
        spaced = lambda: ["100 python3 /x/bin/rein ship demo --root /w/my plan --agent codex --model m"]
        self.assertTrue(ship_watch.driver_alive("demo", ps=spaced, own_pid=999, root="/w/my plan"))
        self.assertFalse(ship_watch.driver_alive("demo", ps=spaced, own_pid=999, root="/w/my"))
        # no --root (or a relative one): its plan repo cannot be told, so it counts (never a false "dead")
        bare = lambda: ["100 python3 /x/bin/rein ship demo"]
        self.assertTrue(ship_watch.driver_alive("demo", ps=bare, own_pid=999, root="/w/plan"))

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

    def test_timeout_is_parsed_and_existing_once_contract_is_preserved(self):
        self.assertEqual(self.cli.cmd_watch(["demo", "--timeout", "60"]), 0)
        self.assertEqual(self.seen[-1][1]["timeout"], 60)
        self.assertEqual(self.cli.cmd_watch(["demo", "--timeout=0.5", "--root=/r"]), 0)
        self.assertEqual(self.seen[-1][1]["timeout"], 0.5)
        self.assertEqual(self.cli.cmd_watch(["demo", "--once"]), 0)
        self.assertNotIn("timeout", self.seen[-1][1])

    def test_invalid_timeout_never_calls_the_watcher(self):
        for value in ("0", "-1", "301", "nan", "inf", "bad", ""):
            with self.subTest(value=value):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(self.cli.cmd_watch(["demo", "--timeout=" + value]), 2)
        for args in (["demo", "--timeout"], ["demo", "--timeout=2", "--once"],
                     ["demo", "--timeout=1", "--timeout=2"]):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.cli.cmd_watch(args), 2)
        self.assertEqual(self.seen, [])


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()


class TestWatchWindows(WatchCase):
    """Real watcher/state/CLI with virtual time and Orca simulated; no live runs are inspected."""

    def setUp(self):
        super().setUp()
        self.write_state("demo", {"g1": "question", "g2": "question"})
        self.clock = Clock()
        self.fake = FakeOrca(gates=[])

    def window(self, timeout=60, alive=True, **kw):
        return self.capture(ship_watch.run_watch, self.root, "demo", self.client(self.fake),
                            sleep=self.clock.sleep, clock=self.clock, timeout=timeout,
                            driver_alive=alive if callable(alive) else lambda c: alive, **kw)

    def test_empty_window_returns_waiting_and_does_not_claim_a_wake_subscription(self):
        state = ship_state.state_path(self.root, "demo")
        with open(state, "rb") as fh:
            before = fh.read()
        code, out = self.window()
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("WAITING demo:"), out)
        self.assertIn("continue waiting in this agent turn", out)
        self.assertNotIn("FINISHED", out)
        self.assertEqual(self.clock.now, 60)
        self.assertEqual(self.clock.sleeps, [15, 15, 15, 15])
        with open(state, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(os.listdir(os.path.dirname(state)), ["demo.json"])

    def test_poll_sleep_does_not_overshoot_a_short_window(self):
        code, out = self.window(16)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.clock.sleeps, [15, 1])
        self.assertEqual(self.clock.now, 16)

    def test_dead_driver_grace_is_not_reset_forever_by_short_windows(self):
        for _ in range(4):
            code, out = self.window(alive=False)
            self.assertEqual(code, 0, out)
            if out.startswith("STOPPED"):
                break
        else:
            self.fail("renewing windows hid a dead driver indefinitely")
        self.assertEqual(self.clock.now, (ship_watch.STARTUP_GRACE_POLLS - 1) * 15)
        self.assertNotIn("WAITING", out)
        self.assertTrue(all(seconds == 15 for seconds in self.clock.sleeps))

    def test_driver_dies_between_windows_and_is_still_reported_stopped(self):
        code, first = self.window()
        self.assertEqual(code, 0, first)
        self.assertTrue(first.startswith("WAITING"))
        code, second = self.window(alive=False)
        self.assertEqual(code, 0, second)
        self.assertTrue(second.startswith("STOPPED"), second)
        self.assertEqual(self.clock.now, 60 + (ship_watch.STARTUP_GRACE_POLLS - 1) * 15)

    def test_a_seen_driver_dying_at_deadline_gets_its_two_miss_confirmation(self):
        code, out = self.window(alive=lambda c: self.clock.now < 60)
        self.assertEqual(code, 0, out)
        self.assertIn("STOPPED", out)
        self.assertNotIn("WAITING", out)
        self.assertEqual(self.clock.now, 75)

    def test_slow_start_after_window_but_before_grace_does_not_falsely_stop(self):
        code, out = self.window(alive=lambda c: self.clock.now >= 75)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("WAITING"))
        self.assertNotIn("STOPPED", out)
        self.assertEqual(self.clock.now, 75)

    def test_transient_driver_miss_at_deadline_then_recovery(self):
        code, out = self.window(alive=lambda c: self.clock.now != 60)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("WAITING"))
        self.assertNotIn("STOPPED", out)
        self.assertEqual(self.clock.now, 75)

    def test_question_at_deadline_wins_over_waiting(self):
        self.clock.on_sleep = lambda: self.fake.gates.append(_gate("g1")) if self.clock.now == 60 else None
        code, out = self.window()
        self.assertEqual(code, 0, out)
        self.assertIn("QUESTION g1", out)
        self.assertNotIn("WAITING", out)

    def test_completion_at_deadline_wins_and_preserves_unapproved_audit(self):
        def finish():
            if self.clock.now == 30:
                self.set_phase("demo", "done", audit_result="unapproved")
        self.clock.on_sleep = finish
        code, out = self.window(30)
        self.assertEqual(code, 0, out)
        self.assertIn("FINISHED demo: audit unapproved", out)
        self.assertNotIn("WAITING", out)

    def test_next_window_collects_question_without_any_user_status_prompt(self):
        # Regression: returning from the first shell call must not be treated as ending supervision.
        self.clock.on_sleep = lambda: self.fake.gates.append(_gate("g1")) if self.clock.now == 75 else None
        first_code, first = self.window()
        second_code, second = self.window()
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertTrue(first.startswith("WAITING"))
        self.assertIn("QUESTION g1", second)
        self.assertEqual(self.clock.now, 75)
        # Watch is observational: never consume/ack the driver's inbox or answer on behalf of the owner.
        self.assertTrue(all(call[1:3] == ["orchestration", "gate-list"] for call in self.fake.calls))

    def test_restart_replays_the_unanswered_question(self):
        self.fake.gates = [_gate("g1")]
        for _ in range(2):
            code, out = self.window()
            self.assertEqual(code, 0, out)
            self.assertIn("PENDING g1", out)

    def test_queued_and_skipped_questions_do_not_hide_a_new_question(self):
        self.fake.gates = [_gate("g1"), _gate("g2")]
        ship_state.write_answer(self.root, "demo", "g1", "owner answer", 1)
        code, out = self.window(skip=frozenset(["g2"]))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("WAITING"))
        ship_state.drop_answer(self.root, "demo", "g1")
        code, out = self.window(skip=frozenset(["g2"]))
        self.assertEqual(code, 0, out)
        self.assertIn("PENDING g1", out)
        self.assertNotIn("PENDING g2", out)

    def test_orca_error_is_reported_as_unknown_not_a_clean_run(self):
        def unavailable(argv):
            return 1, '{"ok": false, "error": {"code": "down", "message": "unavailable"}}'
        self.fake = unavailable
        code, out = self.window()
        self.assertEqual(code, 0, out)
        self.assertIn("phase unknown", out)
        self.assertNotIn("FINISHED", out)

    def test_recovery_after_orca_error_still_collects_pending_question(self):
        self.fake.gates = [_gate("g1")]
        original = self.fake
        calls = []
        def recovering(argv):
            calls.append(argv)
            if len(calls) == 1:
                return 1, '{"ok": false, "error": {"code": "down", "message": "unavailable"}}'
            return original(argv)
        self.fake = recovering
        code, out = self.window()
        self.assertEqual(code, 0, out)
        self.assertIn("PENDING g1", out)
        self.assertEqual(self.clock.now, 15)

    def test_invalid_library_windows_are_usage_errors(self):
        for value in (0, -1, 301, True, "60", float("nan"), float("inf")):
            code, out = self.window(value)
            self.assertEqual(code, 2, out)
        self.assertEqual(self.fake.calls, [])

    def test_real_cli_windows_collect_multiple_gates_then_finish_with_no_user_nudge(self):
        loader = SourceFileLoader("rein_cli_watch_windows", BIN)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        runner = partial(ship_watch.run_watch, sleep=self.clock.sleep, clock=self.clock,
                         driver_alive=lambda c: True)
        with patch.object(cli._watch, "run_watch", runner), \
             patch.object(cli._ship, "OrcaClient", return_value=self.client(self.fake)):
            code, out = self.capture(cli.cmd_watch, ["demo", "--root", self.root, "--timeout=60"])
            self.assertEqual(code, 0, out)
            self.assertTrue(out.startswith("WAITING"))
            self.fake.gates = [_gate("g1"), _gate("g2")]
            code, out = self.capture(cli.cmd_watch, ["demo", "--root", self.root, "--timeout=60"])
            self.assertEqual(code, 0, out)
            self.assertIn("PENDING g1", out)
            self.assertIn("PENDING g2", out)
            # The owner/driver resolves both gates; the watcher never does this mutation.
            for gate in self.fake.gates:
                gate["status"] = "resolved"
            self.set_phase("demo", "done", audit_result="approved")
            code, out = self.capture(cli.cmd_watch, ["demo", "--root", self.root, "--timeout=60"])
            self.assertEqual(code, 0, out)
            self.assertIn("FINISHED demo", out)


if __name__ == "__main__":
    unittest.main()
