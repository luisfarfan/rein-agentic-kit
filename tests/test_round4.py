"""Round 4 of the round-4 review (rereview4b.md), each case through the real consumer.

R1a quick done with no local diff still runs the outside guard; restoring only the local work is not
an --accept-outside. R1b a retry, owner's or automatic after a guard rejection, never launders the
external write of an earlier attempt: the base it is judged against is kept per task. R2 a flag
glued to {target} repeats per file inside `sh -c '...'` too. M2 the acceptance is in the gate log."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import affected  # noqa: E402
import gate_log  # noqa: E402
import guard  # noqa: E402
import ship_state  # noqa: E402
from test_guard import make_repo, run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_round3 import Script, Ship as Round3Ship, sweep  # noqa: E402
from test_ship import Drained, _flag  # noqa: E402


def read(path, rel="app.py"):
    with open(os.path.join(path, rel)) as fh:
        return fh.read()


class QuickNoLocalDiff(DoneCase):
    def setUp(self):
        super().setUp()
        self.qa = os.path.join(self.root, "proxima-qa")
        make_repo(self.qa, hook=False)
        self.make_repo(outside_before=guard.outside_snapshot([self.qa]))
        sweep(self.qa)

    def test_no_local_change_does_not_skip_the_outside_guard(self):
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("UNRESOLVED", self.text())
        self.assertEqual(read(self.qa), "x = 999\n")

    def test_accepting_it_explicitly_is_the_only_way_to_exit_zero(self):
        self.assertEqual(self.done()[0], 1)
        self.assertEqual(self.done()[0], 1, "asking again changes nothing")
        code, _, _ = self.done(accept_outside=True)
        self.assertEqual(code, 0, self.text())
        self.assertIn("nothing changed", self.text())

    def test_restoring_only_the_local_work_is_not_an_acceptance(self):
        self.write("src/foo.ts", "v2\n")
        self.assertEqual(self.done()[0], 1, self.text())
        self.write("src/foo.ts", "v1\n")  # the local diff is gone, the external write is not
        self.assertEqual(self.done()[0], 1, self.text())
        self.assertEqual(read(self.qa), "x = 999\n")

    def test_the_acceptance_is_written_to_the_gate_log(self):
        self.write("src/foo.ts", "v2\n")
        self.done(accept_outside=True)
        with open(gate_log.root_log_path(self.ws)) as fh:
            last = json.loads(fh.read().splitlines()[-1])
        self.assertEqual(last["exit"], 0)
        self.assertIn("proxima-qa", last["guardAccepted"])

    def test_the_acceptance_is_logged_without_a_local_diff_too(self):
        self.done(accept_outside=True)
        with open(gate_log.root_log_path(self.ws)) as fh:
            last = json.loads(fh.read().splitlines()[-1])
        self.assertIn("proxima-qa", last["guardAccepted"])

    def test_a_clean_quick_with_nothing_changed_is_still_zero(self):
        run_git(self.qa, "reset", "-q", "--hard", "HEAD~2")  # the external checkout back as it was
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())


class ShipRetry(Round3Ship):
    """An external write is judged against the task's retained base through every retry."""

    def drive(self, fake, gates, until, act=None):
        fake.autocomplete = False
        fake.pending = Script(fake, until, act)
        with self.assertRaises(Drained):
            self.run_ship(fake, gates)
        self.assertLess(fake.pending.polls, fake.pending.limit, "the script ran out before its end state")
        return ship_state.load(self.state_file)

    def test_an_owner_retry_without_restoring_does_not_close_the_task(self):
        fake = self.fake()
        answered = []

        def act(f):  # the owner answers `retry` to the first outside gate, and only to that one
            if f.gates and not answered and f.gates[0]["status"] == "pending":
                answered.append(1)
                f.resolve_gate(0, "retry")
        state = self.drive(fake, self.gates(), until=lambda f: len(f.gates) >= 2, act=act)
        self.assertNotEqual(state.tasks["T001"].status, "done")
        self.assertEqual(read(self.qa), "x = 999\n")
        self.assertEqual(len(answered), 1)
        self.assertEqual(len([s for s in fake.starts() if "T001" in fake.tasks[_flag(s, "--task")]["title"]]), 2)

    def test_an_automatic_retry_after_a_guard_rejection_does_not_clean_the_slate(self):
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(1)
                if len(calls) == 1:  # the first worker writes a file in the other checkout
                    with open(os.path.join(self.qa, "bad.py"), "w") as fh:
                        fh.write("bad = 1\n")
            return 0, "ok"
        fake = self.fake()
        # every attempt is rejected (the file stays): the task ends in the owner's retry_exhausted gate,
        # after the second identical rejection rather than maxRetries more workers (failure_triage)
        state = self.drive(fake, gates, until=lambda f: len(f.gates) >= 1)
        self.assertNotEqual(state.tasks["T001"].status, "done", self.out)
        self.assertEqual(len(calls), 2)
        self.assertIn("failed the same way twice", fake.gates[0]["question"])
        self.assertIn("rein accept-outside", fake.gates[0]["question"])
        self.assertEqual(read(self.qa, "bad.py"), "bad = 1\n")

    def test_restoring_the_external_checkout_lets_the_retry_close(self):
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(1)
                if len(calls) == 1:
                    with open(os.path.join(self.qa, "bad.py"), "w") as fh:
                        fh.write("bad = 1\n")
                elif len(calls) == 2:
                    os.remove(os.path.join(self.qa, "bad.py"))  # the owner (or worker) put it back
            return 0, "ok"
        fake = self.fake()
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, gates), 0, self.out)
        self.assertFalse(os.path.exists(os.path.join(self.qa, "bad.py")))


class ScriptFlag(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.probe = os.path.join(self.tmp, "probe.py")
        with open(self.probe, "w") as fh:
            fh.write("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")

    def argv(self, template, targets):
        cmd = affected.expand_target(template.replace("PROBE", "%s %s" % (sys.executable, self.probe)), targets)
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.tmp).stdout
        return json.loads(out.strip().splitlines()[-1])

    def test_the_flag_repeats_with_and_without_the_wrapper(self):
        targets = ["a.spec.ts", "b.spec.ts"]
        plain = self.argv("PROBE --include={target}", targets)
        self.assertEqual(plain, ["--include=a.spec.ts", "--include=b.spec.ts"])
        self.assertEqual(self.argv("sh -c 'PROBE --include={target}'", targets), plain)
        self.assertEqual(self.argv('sh -c "PROBE --include={target}"', targets), plain)

    def test_quoting_and_operators_inside_the_script(self):
        self.assertEqual(self.argv("sh -c 'PROBE --include={target}; echo >/dev/null'", ["a b.ts", "it's.ts"]),
                         ["--include=a b.ts", "--include=it's.ts"])

    def test_a_loose_placeholder_inside_the_script_still_joins(self):
        self.assertEqual(self.argv("sh -c 'PROBE {target}'", ["a b.ts", "c.ts"]), ["a b.ts", "c.ts"])


if __name__ == "__main__":
    unittest.main()
