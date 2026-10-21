"""Rein's guard on a finished task: gate config left alone, every commit passes the repo's commit-msg.

A worker rewrote the flow.config.json it was gated with; another committed with
`LEFTHOOK_EXCLUDE=conventional-commits`. The briefs forbid both, and Rein now checks both
when a task ends (ship's task gate, `rein quick done`) and treats a breach as a failure
that names its reason."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import guard  # noqa: E402
import quick  # noqa: E402
import ship  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402

HOOK = """#!/bin/sh
if [ -n "$SKIP$LEFTHOOK_EXCLUDE" ]; then echo "a bypass variable reached the hook"; exit 2; fi
head -1 "$1" | grep -Eq '^(feat|fix|chore|docs|test|refactor)(\\(.+\\))?: ' && exit 0
echo "subject must be conventional"
exit 1
"""


def run_git(path, *args):
    return subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


def make_repo(path, config=None, hook=True):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", path], check=True)
    with open(os.path.join(path, "flow.config.json"), "w") as fh:
        json.dump(config or {"commands": {"test": "true"}}, fh)
    with open(os.path.join(path, "app.py"), "w") as fh:
        fh.write("x = 1\n")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-q", "-m", "chore: base")
    if hook:
        hook_path = os.path.join(path, ".git", "hooks", "commit-msg")
        with open(hook_path, "w") as fh:
            fh.write(HOOK)
        os.chmod(hook_path, os.stat(hook_path).st_mode | stat.S_IEXEC)
    return run_git(path, "rev-parse", "HEAD").strip()


def commit(path, rel, text, msg):
    with open(os.path.join(path, rel), "w") as fh:
        fh.write(text)
    run_git(path, "add", rel)
    run_git(path, "commit", "-q", "--no-verify", "-m", msg)


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")

    def test_a_change_to_flow_config_is_a_problem(self):
        base = make_repo(self.repo)
        commit(self.repo, "flow.config.json", "{}", "fix: loosen the gate")
        problems = guard.check(self.repo, base, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("flow.config.json", problems[0])

    def test_a_task_that_names_the_file_may_change_it(self):
        base = make_repo(self.repo)
        commit(self.repo, "flow.config.json", "{}", "fix: loosen the gate")
        self.assertEqual(guard.check(self.repo, base, {}, allowed_text="Add gate.env to flow.config.json"), [])

    def test_mise_toml_is_protected_when_the_commands_come_from_mise(self):
        base = make_repo(self.repo)
        commit(self.repo, "mise.toml", "[tasks.test]\nrun = 'true'\n", "fix: tasks")
        self.assertEqual(guard.check(self.repo, base, {}), [])
        self.assertEqual(len(guard.check(self.repo, base, {"taskRunner": "mise"})), 1)

    def test_gate_protected_adds_globs(self):
        base = make_repo(self.repo)
        os.makedirs(os.path.join(self.repo, "ci"))
        commit(self.repo, "ci/gate.sh", "true\n", "fix: gate")
        self.assertEqual(len(guard.check(self.repo, base, {"gate": {"protected": ["ci/*"]}})), 1)

    def test_an_uncommitted_change_counts_too(self):
        base = make_repo(self.repo)
        with open(os.path.join(self.repo, "flow.config.json"), "w") as fh:
            fh.write("{}")
        self.assertEqual(len(guard.check(self.repo, base, {})), 1)

    def test_a_commit_the_hook_rejects_is_a_problem(self):
        base = make_repo(self.repo)
        commit(self.repo, "app.py", "x = 2\n", "fix: ok")
        commit(self.repo, "app.py", "x = 3\n", "wip stuff")
        problems = guard.check(self.repo, base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("wip stuff", problems[0])
        self.assertIn("commit-msg", problems[0])

    def test_the_bypass_variables_never_reach_the_hook(self):
        base = make_repo(self.repo)
        commit(self.repo, "app.py", "x = 2\n", "fix: ok")
        env = dict(os.environ, SKIP="commitlint", LEFTHOOK_EXCLUDE="conventional-commits")
        self.assertEqual(guard.check(self.repo, base, {}, env=env), [])

    def test_no_hook_no_problem(self):
        base = make_repo(self.repo, hook=False)
        commit(self.repo, "app.py", "x = 2\n", "anything")
        self.assertEqual(guard.check(self.repo, base, {}), [])

    def test_not_a_git_checkout_yields_nothing(self):
        os.makedirs(self.repo)
        self.assertEqual(guard.check(self.repo, "origin/develop", {}), [])


class Briefs(unittest.TestCase):
    RULES = ("outside", "--no-verify", "LEFTHOOK_EXCLUDE", "flow.config.json")

    def test_the_worker_brief_forbids_them(self):
        t = ship.ss.TaskState(id="T1", repo="api", deps=[], human_review=False, verification="", type="")
        spec = ship.build_task_spec(t, {"tasks": []})
        for word in self.RULES + ("mise.toml",):
            self.assertIn(word, spec)

    def test_the_audit_brief_forbids_them(self):
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo")
        for word in self.RULES:
            self.assertIn(word, spec)

    def test_the_quick_brief_forbids_them(self):
        brief = quick.build_brief("arregla x")
        for word in ("fuera",) + self.RULES[1:]:
            self.assertIn(word, brief)


class QuickDone(DoneCase):
    def test_a_quick_change_to_the_gate_config_is_red_with_its_reason(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        self.write("flow.config.json", json.dumps({"commands": {"test": "true"}}))
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("flow.config.json", self.text())
        self.assertIn("guard", self.text())

    def test_a_clean_quick_change_is_still_green(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())


class ShipTask(ShipCase):
    def test_a_task_that_rewrites_the_gate_config_is_retried_with_the_reason(self):
        self.write_plan("""# Change: demo

- [ ] T001 Add the api endpoint
  - Repo: proxima-api
  - Verification: `pnpm test`
""")
        path = self.wtpath("proxima-api")
        base = make_repo(path, hook=False)
        run_git(path, "update-ref", "refs/remotes/origin/develop", base)  # what the worktree branched from
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:  # the first worker loosened the gate
                    commit(path, "flow.config.json", "{}", "fix: endpoint")
                else:  # the retry put it back
                    commit(path, "flow.config.json", json.dumps({"commands": {"test": "true"}}), "fix: revert")
            return 0, "ok"
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("flow.config.json, the configuration Rein gates it with", specs[1])


if __name__ == "__main__":
    unittest.main()
