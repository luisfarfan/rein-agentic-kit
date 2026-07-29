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
            gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["BLOCKING: fix it"], "rev")
            r = gate.check_review(root, "demo")
        self.assertFalse(r["ok"])
        self.assertIn("CHANGES_REQUESTED", r["reason"])
        self.assertEqual(r["findings"], [{"severity": "BLOCKING", "text": "fix it"}])

    def test_the_latest_episode_wins(self):
        """A re-review after a fix must supersede the older verdict."""
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["BLOCKING: x"], "rev")
            # Timestamped directory names are second-resolution; force ordering.
            base = os.path.join(root, gate.EPISODE_DIR)
            os.rename(os.path.join(base, os.listdir(base)[0]), os.path.join(base, "20200101T000000Z-demo"))
            gate.record_review(root, "demo", "APPROVED", ["a.py"], [], "rev")
            self.assertTrue(gate.check_review(root, "demo")["ok"])

    def test_episodes_of_another_change_are_not_consulted(self):
        with Tree(self.FILES) as root:
            gate.record_review(root, "other", "APPROVED", ["a.py"], [], "rev")
            self.assertFalse(gate.check_review(root, "demo")["ok"])


class TestSeverityFindings(unittest.TestCase):
    FILES = {"a.py": "print(1)\n"}

    def test_prefixes_are_parsed_case_insensitively(self):
        with Tree(self.FILES) as root:
            ep = gate.record_review(
                root, "demo", "CHANGES_REQUESTED", ["a.py"],
                ["blocking: fix the thing", "Important: nice to know", "SUGGESTION: polish"],
                "rev",
            )
        self.assertEqual(ep["findings"], [
            {"severity": "BLOCKING", "text": "fix the thing"},
            {"severity": "IMPORTANT", "text": "nice to know"},
            {"severity": "SUGGESTION", "text": "polish"},
        ])

    def test_leading_whitespace_does_not_defeat_the_prefix(self):
        """Round-4 BLOCKING: the CLI splits --findings on '|', so the natural
        spelling "IMPORTANT: a | BLOCKING: b" hands every finding after the
        first a leading space. Matching the raw string read ' BLOCKING: x' as
        IMPORTANT — and D2's APPROVED-with-blocker refusal failed OPEN on
        exactly the documented input shape."""
        parsed = gate._parse_finding(" BLOCKING: x")
        self.assertEqual(parsed["severity"], "BLOCKING")
        self.assertEqual(parsed["text"], "x")
        # And the whole CLI shape, end to end through record_review:
        pieces = [f for f in "IMPORTANT: a | BLOCKING: b".split("|") if f.strip()]
        with Tree({"a.py": "x=1\n"}) as root:
            with self.assertRaises(ValueError) as ctx:
                gate.record_review(root, "demo", "APPROVED", ["a.py"], pieces, "rev")
            self.assertIn("BLOCKING", str(ctx.exception))

    def test_dict_finding_with_non_string_text_does_not_crash_mid_record(self):
        """A machine-written episode with numeric text must fail (or pass) as
        validation, never as an AttributeError halfway through recording."""
        parsed = gate._parse_finding({"severity": "SUGGESTION", "text": 42})
        self.assertEqual(parsed["text"], "42")

    def test_untagged_finding_defaults_to_important_not_the_mildest(self):
        """D1: an untagged finding must never silently become SUGGESTION."""
        with Tree(self.FILES) as root:
            ep = gate.record_review(
                root, "demo", "CHANGES_REQUESTED", ["a.py"],
                ["BLOCKING: x", "no prefix here"], "rev",
            )
        self.assertIn({"severity": "IMPORTANT", "text": "no prefix here"}, ep["findings"])

    def test_colon_inside_finding_text_does_not_break_the_split(self):
        """The prefix match must be anchored to the known severity words, not
        a naive split on the first colon in the string."""
        with Tree(self.FILES) as root:
            ep = gate.record_review(
                root, "demo", "CHANGES_REQUESTED", ["a.py"],
                ["BLOCKING: SQL query: no bound params in login handler"], "rev",
            )
        self.assertEqual(ep["findings"], [
            {"severity": "BLOCKING", "text": "SQL query: no bound params in login handler"},
        ])

    def test_changes_requested_without_a_blocking_finding_is_refused(self):
        with Tree(self.FILES) as root:
            with self.assertRaises(ValueError) as ctx:
                gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["IMPORTANT: meh"], "rev")
        self.assertIn("BLOCKING", str(ctx.exception))

    def test_approved_with_a_blocking_finding_is_refused(self):
        with Tree(self.FILES) as root:
            with self.assertRaises(ValueError) as ctx:
                gate.record_review(root, "demo", "APPROVED", ["a.py"], ["BLOCKING: nope"], "rev")
        self.assertIn("BLOCKING", str(ctx.exception))

    def test_blocking_finding_with_empty_text_is_refused(self):
        # An empty-text BLOCKING finding would satisfy D2's "at least one
        # BLOCKING finding" at write time while telling the fix agent nothing.
        with Tree(self.FILES) as root:
            with self.assertRaises(ValueError) as ctx:
                gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["BLOCKING:   "], "rev")
        self.assertIn("text", str(ctx.exception))

    def test_old_plain_string_episode_still_loads_and_checks(self):
        """Episodes written before this change stored findings as bare strings."""
        with Tree(self.FILES) as root:
            gate.record_review(root, "demo", "CHANGES_REQUESTED", ["a.py"], ["BLOCKING: x"], "rev")
            episode_path = gate.latest_episode(root, "demo")["path"]
            with open(episode_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw["findings"] = ["fix it please"]
            with open(episode_path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)

            r = gate.check_review(root, "demo")
        self.assertFalse(r["ok"])
        self.assertEqual(r["findings"], [{"severity": "IMPORTANT", "text": "fix it please"}])


if __name__ == "__main__":
    unittest.main()
