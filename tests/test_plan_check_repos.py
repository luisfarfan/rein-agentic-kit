"""`rein plan-check` on a cross-repo plan: a task's Verification is looked up in ITS repo.

The plan lives in one repo and a task with `Repo: proxima-api` names a test module of
proxima-api. The unbacked-verification check looked for that module in the plan's repo and
reported it BLOCKING. A task's repo is the plan repo when it names no other, else
`plan.repos` in the plan root's flow.config.json (name -> path, relative to the plan root),
else the plan root's sibling directory of that name. A repo that cannot be found is
reported, never checked against the wrong tree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import plan_check  # noqa: E402

PLAN = """# Change: demo

- [ ] T001 Cap the discount
  - Repo: proxima-api
  - Verification: `pytest tests/test_discount_cap.py`
  - Acceptance:
    - the cap holds
- [ ] T002 Show it
  - Verification: `python3 -m unittest tests.test_planner_side`
  - Acceptance:
    - it shows
"""


class Layout(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plan_root = os.path.join(self.tmp, "proxima-engineering")
        self.api = os.path.join(self.tmp, "proxima-api")
        for d in (self.plan_root, self.api):
            os.makedirs(os.path.join(d, "tests"))
        self.touch(self.plan_root, "tests/test_planner_side.py")
        self.plan = os.path.join(self.plan_root, "openspec", "changes", "demo", "tasks.md")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            fh.write(PLAN)
        subprocess.run(["git", "init", "-q", self.plan_root], check=True)

    def touch(self, root, rel):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

    def blocking(self, findings):
        return [f for f in findings if f["severity"] == "BLOCKING"]

    def check(self, **kw):
        with open(self.plan) as fh:
            return plan_check.unbacked_findings(fh.read(), self.plan_root, **kw)


class SiblingRepo(Layout):
    def test_a_module_that_exists_in_the_tasks_repo_is_not_blocking(self):
        self.touch(self.api, "tests/test_discount_cap.py")
        self.assertEqual(self.blocking(self.check()), [])

    def test_a_module_missing_from_the_tasks_repo_still_blocks(self):
        found = self.blocking(self.check())
        self.assertEqual([f["taskId"] for f in found], ["T001"])
        self.assertIn("proxima-api", found[0]["text"])

    def test_the_plan_repos_own_tasks_use_the_plan_root(self):
        os.remove(os.path.join(self.plan_root, "tests", "test_planner_side.py"))
        self.touch(self.api, "tests/test_discount_cap.py")
        self.assertEqual([f["taskId"] for f in self.blocking(self.check())], ["T002"])

    def test_the_cli_resolves_the_plan_root_from_a_nested_plan_file(self):
        self.touch(self.api, "tests/test_discount_cap.py")
        out = subprocess.run([sys.executable, REIN, "plan-check", self.plan, "--gate"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout)


class ConfiguredRepo(Layout):
    def test_plan_repos_in_the_config_wins_over_the_sibling(self):
        elsewhere = os.path.join(self.tmp, "checkouts", "api")
        self.touch(elsewhere, "tests/test_discount_cap.py")
        with open(os.path.join(self.plan_root, "flow.config.json"), "w") as fh:
            json.dump({"plan": {"repos": {"proxima-api": "../checkouts/api"}}}, fh)
        self.assertEqual(self.blocking(self.check()), [])

    def test_a_known_path_from_the_caller_is_used(self):
        elsewhere = os.path.join(self.tmp, "orca", "proxima-api")
        self.touch(elsewhere, "tests/test_discount_cap.py")
        self.assertEqual(self.blocking(self.check(repo_paths={"proxima-api": elsewhere})), [])


class UnknownRepo(Layout):
    def test_a_repo_that_cannot_be_found_is_reported_not_checked_against_the_plan_repo(self):
        shutil.rmtree(self.api)
        self.touch(self.plan_root, "tests/test_discount_cap.py")  # must not be mistaken for the api's
        found = self.check()
        self.assertEqual(self.blocking(found), [])
        notes = [f for f in found if f["taskId"] == "T001"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["severity"], "IMPORTANT")
        self.assertIn("plan.repos", notes[0]["text"])


if __name__ == "__main__":
    unittest.main()


FAKE_ORCA = """#!/bin/sh
echo "$@" >> "%(log)s"
case "$1 $2" in
  "repo list") printf '%%s' '%(json)s' ;;
  *) printf '%%s' '{"ok": false, "error": {"code": "nope", "message": "unsupported"}}'; exit 1 ;;
esac
"""


class OrcaRegisteredRepo(Layout):
    """The plan lives in an Orca worktree (~/orca/workspaces/<repo>/<name>): no sibling folder is the
    task's repo, but Orca's registered repos say where it is."""

    def fake_orca(self, repos):
        log = os.path.join(self.tmp, "orca.log")
        body = json.dumps({"ok": True, "result": {"repos": repos}})
        path = os.path.join(self.tmp, "fake-orca")
        with open(path, "w") as fh:
            fh.write(FAKE_ORCA % {"log": log, "json": body})
        os.chmod(path, 0o755)
        return path, log

    def run_cli(self, orca):
        env = dict(os.environ, ORCA_CLI_COMMAND=orca)
        out = subprocess.run([sys.executable, REIN, "plan-check", self.plan], capture_output=True, text=True,
                             env=env)
        return json.loads(out.stdout)["findings"]

    def test_the_cli_finds_the_repo_through_orca_repo_list(self):
        shutil.rmtree(self.api)
        elsewhere = os.path.join(self.tmp, "Developer", "proxima-api")
        self.touch(elsewhere, "tests/test_discount_cap.py")
        orca, _ = self.fake_orca([{"displayName": "proxima-api", "path": elsewhere}])
        findings = self.run_cli(orca)
        self.assertEqual([f for f in findings if f["taskId"] == "T001"], [], findings)

    def test_orca_is_only_asked_when_the_repo_is_not_found_otherwise(self):
        self.touch(self.api, "tests/test_discount_cap.py")
        orca, log = self.fake_orca([])
        self.assertEqual(self.blocking(self.run_cli(orca)), [])
        self.assertFalse(os.path.exists(log), "a sibling repo needs no Orca call")

    def test_orca_unreachable_still_reports_the_repo_as_not_found(self):
        shutil.rmtree(self.api)
        findings = self.run_cli(os.path.join(self.tmp, "no-such-orca"))
        notes = [f for f in findings if f["taskId"] == "T001"]
        self.assertEqual([f["severity"] for f in notes], ["IMPORTANT"])
        self.assertIn("Orca", notes[0]["text"])

    def test_a_lazy_lookup_is_consulted_once_and_last(self):
        calls = []
        elsewhere = os.path.join(self.tmp, "orca", "proxima-api")
        self.touch(elsewhere, "tests/test_discount_cap.py")
        shutil.rmtree(self.api)

        def lookup():
            calls.append(1)
            return {"proxima-api": elsewhere}
        self.assertEqual(self.blocking(self.check(repo_paths=lookup)), [])
        self.assertEqual(calls, [1])
