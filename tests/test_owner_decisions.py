"""The owner's answers to a run's gates reach the auditor.

An auditor could not find in the ship state that the owner had chosen (b) for a worker's
question, so it asked the same thing again. Every gate the owner resolves is kept in the
state (`owner_decisions`: what was asked and what they answered), and the audit and
re-audit briefs list them as binding."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
import ship_state  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
from test_ship import PLAN, FakeOrca, ShipCase, scripted_gate  # noqa: E402


def _state():
    state = ShipState(change="c", plan_path="p.md")
    state.tasks["T001"] = TaskState(id="T001", repo="a", deps=[], human_review=False, verification="",
                                    type="implementation", status="gated", dispatch_id="d1")
    state.gates["g1"] = {"task_id": "T001", "kind": "question", "message_id": "m1",
                         "question": "Formatter: (a) inline or (b) core/money?"}
    return state


class Decide(unittest.TestCase):
    def test_a_resolved_gate_is_kept_as_an_owner_decision(self):
        new, _ = sd.decide(_state(), sd.GateResolved("g1", "(b) with the core/money formatter"), sd.ShipConfig())
        self.assertEqual(new.owner_decisions, [{
            "gate": "g1", "kind": "question", "task_id": "T001",
            "question": "Formatter: (a) inline or (b) core/money?",
            "answer": "(b) with the core/money formatter"}])

    def test_a_refused_resolution_is_not_a_decision(self):
        state = _state()
        state.gates["g1"]["kind"] = "timeout"
        new, _ = sd.decide(state, sd.GateResolved("g1", "maybe"), sd.ShipConfig())
        self.assertEqual(new.owner_decisions, [])

    def test_an_old_state_without_the_field_loads(self):
        self.assertEqual(ShipState(change="c", plan_path="p").owner_decisions, [])


class Brief(unittest.TestCase):
    DECISIONS = [{"gate": "g1", "kind": "question", "task_id": "T002",
                  "question": "Formatter?", "answer": "(b) core/money"}]

    def test_the_audit_brief_lists_the_decisions(self):
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo",
                                     owner_decisions=self.DECISIONS)
        self.assertIn("Owner decisions", spec)
        self.assertIn("T002", spec)
        self.assertIn("Formatter?", spec)
        self.assertIn("(b) core/money", spec)

    def test_the_reaudit_brief_lists_them_too(self):
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo",
                                     previous_blocking=["BLOCKING: x"], owner_decisions=self.DECISIONS)
        self.assertIn("(b) core/money", spec)

    def test_no_decisions_no_section(self):
        self.assertNotIn("Owner decisions", ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo"))


class Driver(ShipCase):
    def test_the_answer_to_a_workers_question_reaches_the_auditor(self):
        self.write_plan(PLAN.split("- [ ] T002")[0])
        fake = FakeOrca(self.root)
        did = []

        def ask(f):
            did.append(next(iter(f.dispatches)))
            return [{"id": "msg_q", "type": "question", "from_handle": "term_" + did[0], "subject": "?",
                     "body": "Use REST or GraphQL?", "payload": '{"dispatchId": "%s"}' % did[0]}]
        fake.pending = [ask, lambda f: f.resolve_gate(0, "REST, as the api does") or []]
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, scripted_gate())
        audit = [t["spec"] for t in fake.tasks.values() if "rein-audit" in t["spec"]]
        self.assertEqual(len(audit), 1, self.out)
        self.assertIn("Use REST or GraphQL?", audit[0])
        self.assertIn("REST, as the api does", audit[0])
        self.assertEqual(len(ship_state.load(self.state_file).owner_decisions), 1)


if __name__ == "__main__":
    unittest.main()
