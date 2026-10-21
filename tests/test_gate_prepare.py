"""I7: `gate.prepare` -- what must exist before a worktree's checks can run.

A storefront quick task failed its first `rein quick done`: typecheck needed the generated
`src/lib/proxima/generated`, which the worktree's setup never produced (`pnpm gen:api`).
`gate.prepare` is the project's own command: Rein runs it in the worktree before the first
slot of a gate that will actually run something (a gate answered from the cache needs
nothing generated). Its failure is a setup problem (126), never a red verdict on the code."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

from test_gate_run import GateRunCase  # noqa: E402
from test_quick_driver import CONFIG, DoneCase, _Gate  # noqa: E402

NEEDS_GEN = {"lint": "true", "typecheck": "test -f gen/api.ts", "test": "true"}


class Gate(GateRunCase):
    def test_typecheck_that_needs_generated_code_passes_once_prepare_generates_it(self):
        self.config(NEEDS_GEN, gate={"prepare": "mkdir -p gen && touch gen/api.ts"})
        code, payload = self.gate("--tier", "full")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["report"]["results"]["prepare"]["outcome"], "ok")

    def test_without_prepare_it_fails(self):
        self.config(NEEDS_GEN)
        code, _ = self.gate("--tier", "full")
        self.assertEqual(code, 1)

    def test_a_failing_prepare_is_setup_not_red(self):
        self.config(NEEDS_GEN, gate={"prepare": "echo cannot reach the schema >&2; exit 4"})
        code, payload = self.gate("--tier", "full")
        self.assertEqual(code, 126, payload)
        self.assertIn("prepare", json.dumps(payload["decision"]))
        self.assertEqual(payload["report"]["results"]["typecheck"]["outcome"], "not_invocable")

    def test_prepare_fills_the_worktree_placeholder(self):
        self.config({"lint": "test -f marker_proj"}, gate={"prepare": "touch marker_{worktree}"})
        code, payload = self.gate("--tier", "full")
        self.assertEqual(code, 0, payload)

    def test_a_gate_answered_from_the_cache_does_not_prepare(self):
        self.config({"lint": "true"}, gate={"prepare": "echo ran >> prepare.log"})
        with open(os.path.join(self.root, ".gitignore"), "w") as fh:
            fh.write("prepare.log\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                      "commit", "-q", "-m", "base"]):
            subprocess.run(["git", "-C", self.root] + args, check=True)
        self.gate("--tier", "full")
        self.gate("--tier", "full")
        with open(os.path.join(self.root, "prepare.log")) as fh:
            self.assertEqual(fh.read().split(), ["ran"])


class QuickDone(DoneCase):
    def test_quick_done_prepares_before_its_light_gate(self):
        self.make_repo(config=dict(CONFIG, gate={"prepare": "pnpm gen:api"}))
        self.write("src/foo.ts", "v2\n")
        run = _Gate()
        code, run, _ = self.done(run=run)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(run.slots()[0], "prepare")
        self.assertEqual(run.calls[0][1], "pnpm gen:api")


if __name__ == "__main__":
    unittest.main()


import gate_run  # noqa: E402


class PrepareAndCache(GateRunCase):
    """R2: a pass is stored under the tree state the slot really checked -- after prepare."""

    def git(self, *args):
        subprocess.run(["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def run_once(self):
        steps = [("typecheck", "grep -qx good generated.txt", "grep -qx good generated.txt")]
        return gate_run.run_steps(self.root, steps, lambda s, c: 60, cache=True,
                                  prepare="echo good > generated.txt")

    def test_a_pass_after_prepare_is_not_reused_for_the_unprepared_tree(self):
        os.environ["REIN_CACHE_DIR"] = self.cache
        self.addCleanup(os.environ.pop, "REIN_CACHE_DIR", None)
        with open(os.path.join(self.root, "generated.txt"), "w") as fh:
            fh.write("bad\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        first = self.run_once()
        self.assertEqual(first["results"]["typecheck"]["outcome"], "ok")
        with open(os.path.join(self.root, "generated.txt"), "w") as fh:
            fh.write("bad\n")  # back to the state before prepare
        second = self.run_once()
        self.assertIn("prepare", second["results"], "the unprepared tree never passed: prepare must run")
        with open(os.path.join(self.root, "generated.txt")) as fh:
            self.assertEqual(fh.read().strip(), "good", "the pass, cached or not, is for the prepared tree")

    def test_a_slot_cached_on_the_unprepared_tree_is_not_mixed_with_a_prepared_one(self):
        os.environ["REIN_CACHE_DIR"] = self.cache
        self.addCleanup(os.environ.pop, "REIN_CACHE_DIR", None)
        with open(os.path.join(self.root, "generated.txt"), "w") as fh:
            fh.write("bad\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        lint = ("lint", "true", "true")
        gate_run.run_steps(self.root, [lint], lambda s, c: 60, cache=True)  # lint cached on the bad tree
        steps = [lint, ("typecheck", "grep -qx good generated.txt", "grep -qx good generated.txt")]
        report = gate_run.run_steps(self.root, steps, lambda s, c: 60, cache=True, prepare="echo good > generated.txt")
        self.assertEqual(list(report["results"])[0], "prepare", "prepare runs before every slot of that gate")
        self.assertFalse(report["results"]["lint"].get("cached"), "lint's pass was for the unprepared tree")
