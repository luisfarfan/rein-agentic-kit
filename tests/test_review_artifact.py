"""The human_review gate keeps its review artifact with the plan, not in a temp directory.

The owner's sign-off list (what to check by hand, task by task) used to be written wherever the
orchestrating session had a scratchpad -- /private/tmp, wiped on reboot. When a task needs human
review, Rein writes `review-<task>.md` into the plan's own change folder
(`openspec/changes/<change>/`; `.rein/review/<change>/` for a flat tasks.md plan; or
`ship.reviewDir`) and the owner's question gives that path."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

from test_ship import FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402

PLAN = ("# Change: demo\n\n- [ ] T001 Revisar los textos del arbol\n  - Repo: proxima-api\n"
        "  - Human review: true\n  - Verification: `pnpm test`\n  - Acceptance:\n"
        "    - Cada nodo dice lo que muestra la pantalla\n")


class ReviewArtifact(ShipCase):
    def ship(self):
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.auto_resolve = "approve"
        fake.on_audit = self.approve_audit()
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        return [_flag(c, "--question") for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]

    def test_an_openspec_plan_keeps_it_in_its_change_folder(self):
        folder = os.path.join(self.root, "openspec", "changes", "demo")
        os.makedirs(folder)
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            json.dump({"plan": {"source": "openspec"}}, fh)
        with open(os.path.join(folder, "tasks.md"), "w") as fh:
            fh.write(PLAN)
        [question] = self.ship()
        path = os.path.join(folder, "review-T001.md")
        self.assertIn(path, question)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("T001", body)
        self.assertIn("Revisar los textos del arbol", body)
        self.assertIn("Cada nodo dice lo que muestra la pantalla", body)
        self.assertIn("pnpm test", body)

    def test_a_flat_plan_keeps_it_under_rein_review(self):
        self.write_plan(PLAN)
        [question] = self.ship()
        path = os.path.join(self.root, ".rein", "review", "demo", "review-T001.md")
        self.assertIn(path, question)
        self.assertTrue(os.path.isfile(path))

    def test_the_directory_is_configurable(self):
        self.write_plan(PLAN)
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            json.dump({"ship": {"reviewDir": "docs/reviews"}}, fh)
        [question] = self.ship()
        self.assertIn(os.path.join(self.root, "docs", "reviews", "review-T001.md"), question)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "docs", "reviews", "review-T001.md")))

    def test_a_review_dir_outside_the_root_is_refused(self):
        self.write_plan(PLAN)
        with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
            json.dump({"ship": {"reviewDir": "../elsewhere"}}, fh)
        [question] = self.ship()
        self.assertIn(os.path.join(self.root, ".rein", "review", "demo", "review-T001.md"), question)


if __name__ == "__main__":
    unittest.main()
