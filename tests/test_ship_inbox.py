"""The driver applies the answers `rein answer` queued in the mailbox."""

from __future__ import annotations

import json
import os
import unittest

from test_ship import PLAN, Drained, FailEnvelope, FakeOrca, ShipCase, _flag, scripted_gate  # sets sys.path
import orca_client  # noqa: E402
import ship  # noqa: E402
import ship_state  # noqa: E402


class InboxCase(ShipCase):
    def setUp(self):
        super().setUp()
        self.write_plan(PLAN.split("- [ ] T002")[0])

    @staticmethod
    def question(f):
        did = next(iter(f.dispatches))
        return [{"id": "msg_q", "type": "question", "from_handle": "term_" + did, "subject": "?",
                 "body": "REST or GraphQL?", "payload": json.dumps({"dispatchId": did})}]

    def queue(self, text="REST", gate_index=0):
        def drop(f):
            ship_state.write_answer(self.root, "demo", f.gates[gate_index]["id"], text, 1)
            return []
        return drop

    def resolves(self, fake):
        return [c for c in fake.calls if c[:2] == ["orchestration", "gate-resolve"]]


class TestDriverAppliesAnswers(InboxCase):
    def test_a_queued_answer_resolves_as_the_coordinator_and_is_fed(self):
        fake = FakeOrca(self.root, autocomplete=False)
        fake.pending = [self.question, self.queue(), lambda f: []]
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        calls = self.resolves(fake)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--from", calls[0])
        self.assertEqual(_flag(calls[0], "--resolution"), "REST")
        self.assertEqual(ship_state.read_answers(self.root, "demo"), [])
        self.assertEqual(fake.replies, [("msg_q", "REST")])

    def test_wait_is_short_enough_to_notice_an_answer(self):
        self.assertEqual(ship.WAIT_MS, 15000)

    def test_a_stale_answer_is_deleted_with_a_note(self):
        ship_state.write_answer(self.root, "demo", "gate_gone", "x", 1)
        fake = FakeOrca(self.root, autocomplete=False)
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        self.assertEqual(ship_state.read_answers(self.root, "demo"), [])
        self.assertEqual(self.resolves(fake), [])

    def test_a_failing_resolve_keeps_the_file_and_logs_once(self):
        fake = FakeOrca(self.root, autocomplete=False)
        fake.resolve_error = orca_client.OrcaError("nope", "refused")
        fake.pending = [self.question, self.queue(), lambda f: [], lambda f: []]
        out = []
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        self.assertEqual([a["gate"] for a in ship_state.read_answers(self.root, "demo")], [fake.gates[0]["id"]])
        self.assertGreaterEqual(len(self.resolves(fake)), 2, "it keeps trying")
        self.assertEqual(self.out.count("could not apply the queued answer"), 1)

    def test_a_malformed_mailbox_file_is_ignored(self):
        d = ship_state.answers_dir(self.root, "demo")
        os.makedirs(d)
        with open(os.path.join(d, "junk.json"), "w") as fh:
            fh.write("{nope")
        fake = FakeOrca(self.root, autocomplete=False)
        with self.assertRaises(Drained):
            self.run_ship(fake, scripted_gate())
        self.assertTrue(os.path.exists(os.path.join(d, "junk.json")))
        self.assertEqual(self.resolves(fake), [])


if __name__ == "__main__":
    unittest.main()
