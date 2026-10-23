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
from unittest import mock

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
    run_git(path, "config", "user.email", "t@t")  # the identity of the commits `commit` makes
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

    def test_a_task_that_declares_the_file_may_change_it(self):
        base = make_repo(self.repo)
        commit(self.repo, "flow.config.json", "{}", "fix: loosen the gate")
        self.assertEqual(guard.check(self.repo, base, {}, allowed=["flow.config.json"]), [])

    def test_mise_toml_is_protected_when_the_commands_come_from_mise(self):
        base = make_repo(self.repo)
        commit(self.repo, "mise.toml", "[tasks.test]\nrun = 'true'\n", "fix: tasks")
        self.assertEqual(guard.check(self.repo, base, {}), [])
        fed = {"commands": {"test": "mise run test"}, "commandSources": {"test": "mise"}}
        self.assertEqual(len(guard.check(self.repo, base, fed)), 1)

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

    def test_not_a_git_checkout_fails_closed(self):
        os.makedirs(self.repo)
        with self.assertRaises(guard.GuardError):  # never "no change" (N2)
            guard.check(self.repo, "origin/develop", {})


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


class HooksMustRun(unittest.TestCase):
    """Every worker asked whether the repo's commit-msg hook (which calls a sibling repo's script)
    was allowed: the briefs say the repo's own hooks must run; only invoking outside scripts
    yourself, or skipping hooks, is forbidden."""
    EN = ("git hooks must run", "even when they call scripts in other repositories")

    def test_worker_and_fix_briefs(self):
        for tid, kw in (("T1", {}), ("AUDIT1-api", {"audit_findings": "BLOCKING: x"}), ("GATE1-api", {})):
            t = ship.ss.TaskState(id=tid, repo="api", deps=[], human_review=False, verification="", type="", **kw)
            spec = ship.build_task_spec(t, {"tasks": []})
            for phrase in self.EN:
                self.assertIn(phrase, spec, tid)

    def test_audit_brief(self):
        spec = ship.build_audit_spec({}, {"/wt/a": ["x.py"]}, "R", "/wt/a", "demo")
        for phrase in self.EN:
            self.assertIn(phrase, spec)

    def test_quick_brief(self):
        brief = quick.build_brief("arregla x")
        self.assertIn("hooks del repo SÍ deben correr", brief)
        self.assertIn("aunque llamen scripts de otros repos", brief)


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


class Renames(unittest.TestCase):
    """I2: a renamed (or deleted) gate config is a change to it: both ends of a rename count."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)

    def test_renaming_flow_config_away_is_caught(self):
        run_git(self.repo, "mv", "flow.config.json", "old-config.json")
        run_git(self.repo, "commit", "-q", "-m", "chore: tidy")
        problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("flow.config.json", problems[0])

    def test_an_uncommitted_rename_is_caught(self):
        run_git(self.repo, "mv", "flow.config.json", "old-config.json")
        self.assertEqual(len(guard.check(self.repo, self.base, {})), 1)

    def test_renaming_something_onto_it_is_caught(self):
        run_git(self.repo, "rm", "-q", "flow.config.json")
        run_git(self.repo, "commit", "-q", "-m", "chore: drop")
        with open(os.path.join(self.repo, "cfg.json"), "w") as fh:
            fh.write('{"commands": {"test": "true"}}')
        run_git(self.repo, "add", "cfg.json")
        run_git(self.repo, "commit", "-q", "-m", "chore: add")
        base = run_git(self.repo, "rev-parse", "HEAD").strip()
        run_git(self.repo, "mv", "cfg.json", "flow.config.json")
        run_git(self.repo, "commit", "-q", "-m", "chore: move")
        self.assertEqual(len(guard.check(self.repo, base, {})), 1)


import detect  # noqa: E402

MISE_GATE = '[tasks.test]\nrun = "true"\n[tasks.lint]\nrun = "true"\n'


class RunnerProvenance(unittest.TestCase):
    """I3: a task runner's config is protected when it feeds a gate command -- at the base or now --
    whichever runner it is (mise, make, just, task), and not when no gate command comes from it."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")

    def repo_with(self, files, config=None):
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", "-b", "develop", self.repo], check=True)
        if config is not None:
            files = dict(files, **{"flow.config.json": json.dumps(config)})
        for name, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.repo, name)) or self.repo, exist_ok=True)
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "chore: base")
        return run_git(self.repo, "rev-parse", "HEAD").strip()

    def problems(self, base):
        return guard.check(self.repo, base, detect.resolve(self.repo))

    def test_deleting_the_runner_that_fed_the_gate_is_caught(self):
        base = self.repo_with({"mise.toml": MISE_GATE})
        run_git(self.repo, "rm", "-q", "mise.toml")
        problems = self.problems(base)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mise.toml", problems[0])

    def test_switching_runner_keeps_the_old_one_protected(self):
        base = self.repo_with({"Makefile": "test:\n\ttrue\nlint:\n\ttrue\n"})
        run_git(self.repo, "rm", "-q", "Makefile")
        with open(os.path.join(self.repo, "justfile"), "w") as fh:
            fh.write("test:\n    true\n")
        self.assertTrue(any("Makefile" in p for p in self.problems(base)))

    def test_a_runner_that_feeds_no_gate_command_may_change(self):
        base = self.repo_with({"mise.toml": '[tasks.deploy]\nrun = "echo"\n'},
                              config={"commands": {"test": "true", "lint": "true"}})
        with open(os.path.join(self.repo, "mise.toml"), "a") as fh:
            fh.write('[tasks.dev]\nrun = "echo"\n')
        self.assertEqual(self.problems(base), [])

    def test_a_configured_command_that_calls_the_runner_protects_it(self):
        base = self.repo_with({"mise.toml": MISE_GATE}, config={"commands": {"test": "mise run test"}})
        with open(os.path.join(self.repo, "mise.toml"), "w") as fh:
            fh.write('[tasks.test]\nrun = "echo skipped"\n')
        self.assertEqual(len(self.problems(base)), 1)

    def test_make_just_and_task_are_covered(self):
        for files, name in (({"Makefile": "test:\n\ttrue\n"}, "Makefile"),
                            ({"justfile": "test:\n    true\n"}, "justfile"),
                            ({"Taskfile.yml": "version: '3'\ntasks:\n  test:\n    cmds: [true]\n"}, "Taskfile.yml")):
            with self.subTest(runner=name):
                shutil.rmtree(self.repo, True)
                base = self.repo_with(files)
                with open(os.path.join(self.repo, name), "a") as fh:
                    fh.write("\n# loosened\n")
                problems = self.problems(base)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(name, problems[0])


import ship_state  # noqa: E402


class Authorization(unittest.TestCase):
    """I4: only an explicit, scoped declaration lets a task change gate config -- a `Gate config:`
    field on THIS task (or one already done in the same repo), or `rein quick --allow-config`.
    Mentioning the file, even to forbid touching it, grants nothing."""

    def test_a_mention_is_not_a_permission(self):
        self.assertEqual(guard.config_changes(["flow.config.json"], ["flow.config.json"], []), ["flow.config.json"])

    def test_a_declared_path_must_match_whole(self):
        self.assertEqual(guard.config_changes(["flow.config.json"], ["flow.config.json"], ["config.json"]),
                         ["flow.config.json"])
        self.assertEqual(guard.config_changes(["flow.config.json"], ["flow.config.json"], ["flow.config.json"]), [])

    PLAN = {"tasks": [
        {"id": "T001", "title": "Fix UI; do not modify flow.config.json", "repo": "api", "acceptance": []},
        {"id": "T002", "title": "Add gate.env", "repo": "api", "gateConfig": "flow.config.json, mise.toml"},
        {"id": "T003", "title": "Other repo", "repo": "web", "gateConfig": "flow.config.json"},
    ]}

    def state(self, done=()):
        st = ship_state.ShipState(change="c", plan_path="p")
        for t in self.PLAN["tasks"]:
            st.tasks[t["id"]] = ship_state.TaskState(id=t["id"], repo=t["repo"], deps=[], human_review=False,
                                                     verification="", type="implementation",
                                                     status="done" if t["id"] in done else "pending")
        return st

    def test_a_prohibition_in_the_task_text_grants_nothing(self):
        st = self.state()
        self.assertEqual(ship.gate_config_allowed(self.PLAN, st, st.tasks["T001"], "api"), [])

    def test_the_tasks_own_field_grants_its_paths(self):
        st = self.state()
        self.assertEqual(ship.gate_config_allowed(self.PLAN, st, st.tasks["T002"], "api"),
                         ["flow.config.json", "mise.toml"])

    def test_another_pending_tasks_field_grants_nothing_a_done_ones_does(self):
        st = self.state()
        self.assertEqual(ship.gate_config_allowed(self.PLAN, st, st.tasks["T001"], "api"), [])
        st = self.state(done=("T002", "T003"))
        self.assertEqual(ship.gate_config_allowed(self.PLAN, st, st.tasks["T001"], "api"),
                         ["flow.config.json", "mise.toml"])

    def test_the_plan_parser_reads_the_field(self):
        tasks = ship._plan.parse_tasks_md("- [ ] T001 Add env\n  - Gate config: flow.config.json\n")
        self.assertEqual(tasks[0]["gateConfig"], "flow.config.json")


class QuickAuthorization(DoneCase):
    def test_a_sentence_that_mentions_the_file_does_not_allow_it(self):
        self.make_repo(sentence="arregla el botón; no toques flow.config.json")
        self.write("src/foo.ts", "v2\n")
        self.write("flow.config.json", json.dumps({"commands": {"test": "true"}}))
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())

    def test_allow_config_in_the_record_allows_it(self):
        self.make_repo(allow_config=["flow.config.json"])
        self.write("src/foo.ts", "v2\n")
        self.write("flow.config.json", json.dumps({"commands": {"test": "true"}}))
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())


class HookExitCodes(unittest.TestCase):
    """I5: once the hook ran, any non-zero exit is a rejection -- 129 and "is not a git command" in
    its output included. Whether this git can run hooks at all is asked separately."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)

    def hook(self, body):
        path = os.path.join(self.repo, ".git", "hooks", "commit-msg")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, 0o755)
        commit(self.repo, "app.py", "x = 9\n", "bad message")

    def test_a_hook_that_exits_129_rejects(self):
        self.hook("echo hook-rejected; exit 129")
        problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("exit 129", problems[0])

    def test_a_rejection_that_says_not_a_git_command_still_rejects(self):
        self.hook("echo \"git: 'lint' is not a git command\"; exit 1")
        self.assertEqual(len(guard.check(self.repo, self.base, {})), 1)

    def test_a_git_without_hook_support_and_a_hook_installed_is_reported(self):
        self.hook("exit 0")
        with mock.patch.object(guard, "_git_runs_hooks", return_value=False):
            problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("git hook", problems[0])

    def test_a_git_without_hook_support_and_no_hook_is_fine(self):
        commit(self.repo, "app.py", "x = 9\n", "bad message")
        with mock.patch.object(guard, "_git_runs_hooks", return_value=False):
            self.assertEqual(guard.check(self.repo, self.base, {}), [])


class ManyCommits(unittest.TestCase):
    """I6: the newest commits are always checked, and going past the cap is said, never a quiet green."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo)

    def commits(self, messages):
        for i, msg in enumerate(messages):
            commit(self.repo, "app.py", "x = %d\n" % i, msg)

    def test_the_newest_commit_past_the_cap_is_checked(self):
        self.commits(["fix: %d" % i for i in range(3)] + ["bad newest"])
        with mock.patch.object(guard, "MAX_COMMITS", 3):
            problems = guard.check(self.repo, self.base, {})
        self.assertTrue(any("bad newest" in p for p in problems), problems)

    def test_going_past_the_cap_is_explicit(self):
        self.commits(["fix: %d" % i for i in range(5)])
        with mock.patch.object(guard, "MAX_COMMITS", 3):
            problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("5 commits", problems[0])
        self.assertIn("2", problems[0])


class SubprojectRunner(unittest.TestCase):
    """R1: the base's runner is found inside a chosen sub-project too."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "app"))
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        files = {"flow.config.json": json.dumps({"subproject": "app"}), "app/package.json": "{}",
                 "app/mise.toml": '[tasks.test]\nrun = "true"\n'}
        for name, text in files.items():
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "chore: base")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()

    def test_the_base_resolution_sees_the_subprojects_runner(self):
        self.assertEqual(guard.resolve_at(self.repo, self.base)["commands"].get("test"),
                         detect.resolve(self.repo)["commands"].get("test"))

    def test_deleting_the_subprojects_runner_is_caught(self):
        run_git(self.repo, "rm", "-q", "app/mise.toml")
        problems = guard.check(self.repo, self.base, detect.resolve(self.repo))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("app/mise.toml", problems[0])



class CapWithoutHook(unittest.TestCase):
    """R6: no commit-msg hook, nothing to run: the commit cap says nothing."""

    def test_past_the_cap_without_a_hook_is_clean(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        base = make_repo(repo, hook=False)
        for i in range(4):
            commit(repo, "app.py", "x = %d\n" % i, "anything %d" % i)
        with mock.patch.object(guard, "MAX_COMMITS", 3):
            self.assertEqual(guard.check(repo, base, {}), [])
