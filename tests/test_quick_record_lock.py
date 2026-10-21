"""I1: every writer of `.rein/quick.json` reads, changes and saves it under one lock.

`os.replace` made each save atomic but not the read-modify-write: the watch read a record
with no question, `rein quick ask` saved q1, and the watch then saved its old copy back --
the question was gone and the agent waited for an answer nobody could give. The audit
round did the same over minutes: it kept the record it read when `done` started."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import quick_driver  # noqa: E402
import quick_inbox  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_quick_driver import TERMINAL, DoneCase, _AuditOrca  # noqa: E402
from test_quick_inbox import Case  # noqa: E402


class WatchVersusAsk(Case):
    def test_a_question_asked_while_the_watch_saves_its_alert_survives(self):
        asker = []

        def config_mid_transaction(_resolved):
            # The watch has read the record and decided to alert; the agent asks right now.
            t = threading.Thread(target=quick_inbox.ask, args=(self.ws, "¿payload?"),
                                 kwargs={"say": lambda _l: None})
            t.start()
            asker.append(t)
            time.sleep(0.3)
            return sd.ShipConfig()
        with mock.patch.object(quick_inbox._sd.ShipConfig, "from_flow_config", side_effect=config_mid_transaction):
            alert = quick_inbox.stall(self.ws, self.record(), 1.0 + 11 * 60)
        asker[0].join(5)
        self.assertIn("STALLED", alert)
        record = self.record()
        self.assertEqual([q["text"] for q in record["questions"]], ["¿payload?"])
        self.assertEqual(record["stall_alerted_at"], 1.0 + 11 * 60)


class TwoAsks(Case):
    def test_concurrent_asks_both_land(self):
        threads = [threading.Thread(target=quick_inbox.ask, args=(self.ws, "q%d" % i),
                                    kwargs={"say": lambda _l: None}) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        texts = sorted(q["text"] for q in self.record()["questions"])
        self.assertEqual(texts, sorted("q%d" % i for i in range(8)))
        self.assertEqual(sorted(q["id"] for q in self.record()["questions"]), sorted("q%d" % i for i in range(1, 9)))


class TheLockLivesOutsideTheTree(Case):
    def test_no_lock_file_is_left_in_the_worktree(self):
        quick_inbox.ask(self.ws, "¿x?", say=lambda _l: None)
        self.assertEqual(sorted(os.listdir(os.path.join(self.ws, ".rein"))), ["quick.json"])


class AuditRound(DoneCase):
    def test_a_question_asked_during_the_audit_survives_its_verdict(self):
        self.make_repo()
        self.write("src/auth/login.ts", "v2\n")
        play = self.reviewer("CHANGES_REQUESTED", ["BLOCKING: fix it"])

        def on_start():
            quick_inbox.ask(self.ws, "¿cuál formato?", say=lambda _l: None)
            play()
        code, _, _ = self.done(orca=_AuditOrca(on_start=on_start), env=TERMINAL)
        self.assertEqual(code, 1, self.text())
        record, _ = quick_driver._load_record(self.ws)
        self.assertEqual(record["audit_rounds"], 1)
        self.assertEqual([q["text"] for q in record["questions"]], ["¿cuál formato?"])


if __name__ == "__main__":
    unittest.main()
