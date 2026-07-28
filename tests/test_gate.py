"""Tests for the deterministic gates.

These exist because of one line in the origin project's run-auto skill:

    Stop conditions are read from a verifiable signal -- never from the model's
    impression that "this is probably enough".

So the assertions here are about what the gate refuses, not only what it allows:
a gate that cannot say no is not a gate.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import gate  # noqa: E402

PLAN = """\
- [x] T001 Done already
- [ ] T002 Needs T001
  - Depends on: T001
  - Verification: `pytest tests/test_two.py`
- [ ] T003 Needs T002
  - Depends on: T002
- [ ] T004 Supervised
  - Human review: true
"""


class Tree:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        for rel, body in self.files.items():
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        return self.tmp.name

    def __exit__(self, *exc):
        self.tmp.cleanup()


class TestNextTask(unittest.TestCase):
    def test_returns_the_first_unblocked_task(self):
        with Tree({"tasks.md": PLAN}) as root:
            r = gate.next_task(root)
        self.assertTrue(r["ready"])
        self.assertEqual(r["taskId"], "T002")
        self.assertEqual(r["verification"], "pytest tests/test_two.py")
        self.assertEqual(r["remaining"], 3)

    def test_a_blocked_task_is_never_offered(self):
        """T003 depends on T002, which is still open -- offering it would be wrong."""
        with Tree({"tasks.md": PLAN}) as root:
            self.assertNotEqual(gate.next_task(root)["taskId"], "T003")

    def test_human_review_is_surfaced_not_hidden(self):
        plan = "- [ ] T001 Supervised\n  - Human review: true\n"
        with Tree({"tasks.md": plan}) as root:
            r = gate.next_task(root)
        # Still "ready" -- the gate reports the fact; stopping is the caller's
        # contract. Hiding it here would make the signal un-inspectable.
        self.assertTrue(r["ready"])
        self.assertTrue(r["humanReview"])

    def test_not_ready_always_names_a_reason(self):
        for files, expect in (
            ({"README.md": "x"}, "no plan"),
            ({"tasks.md": "- [x] T001 done\n"}, "no pending"),
        ):
            with Tree(files) as root:
                r = gate.next_task(root)
            self.assertFalse(r["ready"])
            self.assertIn(expect, r["reason"])

    def test_dependency_cycle_is_reported_and_does_not_hang(self):
        plan = "- [ ] T001 a\n  - Depends on: T002\n- [ ] T002 b\n  - Depends on: T001\n"
        with Tree({"tasks.md": plan}) as root:
            r = gate.next_task(root)
        self.assertFalse(r["ready"])
        self.assertEqual(set(r["unresolvableDeps"]), {"T001", "T002"})

    def test_dependency_on_a_nonexistent_id_does_not_deadlock(self):
        """A typo'd dependency must not park the plan forever."""
        with Tree({"tasks.md": "- [ ] T001 a\n  - Depends on: T999\n"}) as root:
            r = gate.next_task(root)
        self.assertTrue(r["ready"])
        self.assertEqual(r["taskId"], "T001")


class TestRecordReview(unittest.TestCase):
    FILES = {"a.py": "print(1)\n", "b.py": "print(2)\n"}

    def test_records_hashes_and_a_state_hash(self):
        with Tree(self.FILES) as root:
            ep = gate.record_review(root, "demo", "APPROVED", ["a.py", "b.py"], [], "rev")
        self.assertEqual(set(ep["reviewed_files"]), {"a.py", "b.py"})
        self.assertEqual(ep["reviewed_state_hash"], gate.state_hash(ep["reviewed_files"]))

    def test_state_hash_is_order_independent(self):
        """Same reviewed set must hash the same however it was built."""
        a = {"a.py": "1", "b.py": "2"}
        b = {"b.py": "2", "a.py": "1"}
        self.assertEqual(gate.state_hash(a), gate.state_hash(b))

    def test_refuses_the_things_that_would_make_it_a_lie(self):
        with Tree(self.FILES) as root:
            for kwargs, expect in (
                (dict(verdict="LGTM"), "verdict"),
                (dict(reviewer=""), "reviewer"),
                (dict(reviewer="implementer"), "own implementation"),
                (dict(files=[]), "empty"),
                (dict(files=["ghost.py"]), "do not exist"),
            ):
                call = dict(verdict="APPROVED", files=["a.py"], findings=[], reviewer="rev")
                call.update(kwargs)
                with self.assertRaises(ValueError) as ctx:
                    gate.record_review(root, "demo", **call)
                self.assertIn(expect, str(ctx.exception))


class TestCheckReview(unittest.TestCase):
    FILES = {"a.py": "print(1)\n", "b.py": "print(2)\n"}

    def test_no_episode_is_not_silently_ok(self):
        with Tree(self.FILES) as root:
            r = gate.check_review(root)
        self.assertFalse(r["ok"])
        self.assertIn("no review episode", r["reason"])

    def test_approved_and_untouched_passes(self):
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "APPROVED", ["a.py", "b.py"], [], "rev")
            self.assertTrue(gate.check_review(root, "demo")["ok"])

    def test_editing_a_reviewed_file_makes_the_approval_stale(self):
        """The point of the whole mechanism: approval is bound to a state."""
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "APPROVED", ["a.py", "b.py"], [], "rev")
            with open(os.path.join(root, "a.py"), "a", encoding="utf-8") as fh:
                fh.write("print(999)\n")
            r = gate.check_review(root, "demo")
        self.assertFalse(r["ok"])
        self.assertTrue(r["stale"])
        self.assertIn("a.py", r["changed"])

    def test_deleting_a_reviewed_file_is_also_stale(self):
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "APPROVED", ["a.py", "b.py"], [], "rev")
            os.remove(os.path.join(root, "b.py"))
            r = gate.check_review(root, "demo")
        self.assertFalse(r["ok"])
        self.assertTrue(any("b.py" in c for c in r["changed"]))

    def test_changes_requested_never_satisfies_the_gate(self):
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["fix it"], "rev")
            r = gate.check_review(root, "demo")
        self.assertFalse(r["ok"])
        self.assertIn("CHANGES_REQUESTED", r["reason"])
        self.assertEqual(r["findings"], ["fix it"])

    def test_the_latest_episode_wins(self):
        """A re-review after a fix must supersede the older verdict."""
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["x"], "rev")
            # Timestamped directory names are second-resolution; force ordering.
            base = os.path.join(root, gate.EPISODE_DIR)
            os.rename(os.path.join(base, os.listdir(base)[0]), os.path.join(base, "20200101T000000Z-demo"))
            gate.record_review(root, "demo", "APPROVED", ["a.py"], [], "rev")
            self.assertTrue(gate.check_review(root, "demo")["ok"])

    def test_episodes_of_another_change_are_not_consulted(self):
        with Tree(self.FILES) as root:
            gate.record_review(root, "other", "APPROVED", ["a.py"], [], "rev")
            self.assertFalse(gate.check_review(root, "demo")["ok"])


if __name__ == "__main__":
    unittest.main()
