"""Regressions from the ship-driver bug hunt: failure context, audit closure, resume, quota.

Decide-level cases build a ShipState by hand; driver-level cases reuse test_ship's FakeOrca.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from ship_state import ShipState, TaskState  # noqa: E402
from test_ship import PLAN, FakeOrca, FailEnvelope, ShipCase, _flag, scripted_gate  # noqa: E402,F401

CFG = sd.ShipConfig()
OK = {"claude": {"session": 10.0, "weekly": 10.0}, "codex": {"session": 5.0, "weekly": 5.0}}


def task(tid, repo="a", status="pending", **kw):
    kw.setdefault("human_review", False)
    kw.setdefault("verification", "make test")
    kw.setdefault("type", "implementation")
    return TaskState(id=tid, repo=repo, deps=[], status=status, **kw)


def state_of(*tasks, **kw):
    st = ShipState(change="c", plan_path="p.md", **kw)
    for t in tasks:
        st.tasks[t.id] = t
    return st


def opened(st, kind):
    return [g for g in st.pending_gates if g["kind"] == kind]


class OwnerQuestionsQuoteEnough(unittest.TestCase):
    def test_retry_exhausted_question_quotes_the_failure_past_300_chars(self):
        t = task("T1", status="gating", dispatch_id="d1", attempts=CFG.max_retries)
        reason = "test [failed] exit=1 $ pytest\n" + "x" * 500 + "\nE   assert 7.0 == 6.3"
        st, _ = sd.decide(state_of(t), sd.GateVerdict("T1", 1, reason), CFG)
        self.assertIn("assert 7.0 == 6.3", opened(st, "retry_exhausted")[0]["question"])


if __name__ == "__main__":
    unittest.main()
