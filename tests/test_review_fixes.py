"""Regressions from the independent review of the driver and done hunts.

Every driver case here runs the REAL task-verification runner (ShipCase stubs it by default).
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
from test_ship import FakeOrca, ShipCase, scripted_gate  # noqa: E402


def one_task_plan(verification, type_=""):
    return """# Change: demo

- [ ] T001 Add the api endpoint
  - Repo: proxima-api
%s  - Verification: %s
""" % (("  - Type: %s\n" % type_) if type_ else "", verification)


class B1PlanVerificationField(unittest.TestCase):
    """Only a field that BEGINS with one backticked command is runnable, and only that command."""

    def cmd(self, field):
        return ship.task_verification_command(field)

    def test_a_leading_backticked_command_is_extracted_without_the_prose(self):
        self.assertEqual(self.cmd("`true` devuelve 0"), ("true", ""))
        self.assertEqual(self.cmd("`alembic heads` devuelve un solo head; `uv run pytest -q`")[0], "alembic heads")
        self.assertEqual(self.cmd("  `pnpm test --filter api`  ")[0], "pnpm test --filter api")

    def test_prose_is_not_run(self):
        for field in ("Chrome a 390: el carrito no desborda",
                      "Chrome: precio > 0 en /pagar",
                      "Open the login page and check the error",
                      "Playwright a 390 px — `document.scrollWidth === clientWidth`",
                      "``double`` ticks", "`unterminated", "``", ""):
            command, why = self.cmd(field)
            self.assertEqual(command, "", field)
            self.assertTrue(why, field)

    def test_dangerous_or_outward_commands_are_refused(self):
        for field in ("`aws iam get-account-summary --profile proxima-prod` devuelve 0",
                      "`pnpm deploy`", "`git push origin main`", "`rm -rf build`", "`sudo make`",
                      "`curl https://x`", "`wget x`", "`cdk diff`", "`terraform plan`", "`kubectl get pods`",
                      "`docker push img`", "`npm publish`", "`pnpm publish`", "`echo x > /etc/hosts`",
                      "`echo x >../outside`", "`echo x >> ~/notes`", "`echo x | tee /tmp/x`"):
            command, why = self.cmd(field)
            self.assertEqual(command, "", field)
            self.assertIn("refused", why, field)

    def test_a_local_redirect_is_allowed(self):
        self.assertEqual(self.cmd("`pytest -q 2>&1 >out.txt`")[0], "pytest -q 2>&1 >out.txt")
        self.assertEqual(self.cmd("`make check 2>/dev/null`")[0], "make check 2>/dev/null")


class B1RealVerificationInTheDriver(ShipCase):
    def setUp(self):
        super().setUp()
        ship.run_task_verification = self._saved[3]  # the real runner, not ShipCase's stub
        for repo in ("proxima-api", "proxima-admin"):
            os.makedirs(self.wtpath(repo), exist_ok=True)

    def ship_one(self, verification, type_=""):
        self.write_plan(one_task_plan(verification, type_))
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        fake.auto_resolve = "skip"  # a gate the fix should not open must not hang the test
        code = self.run_ship(fake, scripted_gate(), sleep=lambda s: None)
        workers = [v for v in fake.tasks.values() if v["title"].startswith("T001")]
        return code, fake, workers

    def test_a_backticked_true_followed_by_prose_runs_and_passes(self):
        code, _, workers = self.ship_one("`true` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertIn("T001 verification exit 0", self.out)

    def test_a_prose_field_is_not_run_and_the_gate_decides(self):
        code, fake, workers = self.ship_one("Chrome a 390: el carrito no desborda")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertNotIn("verification exit", self.out)
        self.assertEqual(fake.gates, [])

    def test_prose_with_a_redirect_creates_no_file(self):
        code, _, _ = self.ship_one("Chrome: precio > 0 en /pagar")
        self.assertEqual(code, 0, self.out)
        self.assertNotIn("0", os.listdir(self.wtpath("proxima-api")))

    def test_a_dangerous_command_is_refused_and_logged(self):
        code, _, workers = self.ship_one("`touch ran && echo deploy` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1)
        self.assertFalse(os.path.exists(os.path.join(self.wtpath("proxima-api"), "ran")))
        self.assertIn("refused", self.out)

    def test_a_real_failing_command_reaches_the_worker_then_passes(self):
        check = "`test -f .seen || { touch .seen; echo 'AssertionError: own-check'; exit 1; }` pasa"
        code, _, workers = self.ship_one(check)
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 2, "the failing verification cost one retry")
        self.assertIn("AssertionError: own-check", workers[1]["spec"])
        self.assertIn("task verification", workers[1]["spec"])

    def test_command_not_found_burns_no_retry(self):
        code, fake, workers = self.ship_one("`rein-no-such-tool-xyz --check` devuelve 0")
        self.assertEqual(code, 0, self.out)
        self.assertEqual(len(workers), 1, self.out)
        self.assertIn("could not run", self.out)
        self.assertEqual(fake.gates, [])


if __name__ == "__main__":
    unittest.main()
