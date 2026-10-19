"""`quick_driver.start_quick`: the I/O driver behind `rein quick "<sentence>"`.

No test shells out to `orca` or git: a fake runner replays the recorded
fixtures from tests/fixtures/orca and a fake `git` answers from a script.
The workspaces are temp dirs so the record can be really written.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import gate  # noqa: E402
import quick  # noqa: E402
import quick_driver  # noqa: E402
import ship  # noqa: E402
import verify  # noqa: E402
from orca_client import OrcaClient  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "orca")
SHA = "a" * 40


def _fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)["result"]


def _limits(claude_weekly, codex_weekly, claude_session=5):
    def block(provider, session, weekly):
        return {"provider": provider, "status": "ok",
                "session": {"usedPercent": session}, "weekly": {"usedPercent": weekly}}
    return {"rateLimits": {"claude": block("claude", claude_session, claude_weekly),
                           "codex": block("codex", 5, codex_weekly)}}


class _Orca:
    """Fake orca runner: answers by subcommand and records every argv."""

    def __init__(self, repos, limits, worktree_path="", fail_create=None):
        self.calls = []
        self.repos = repos
        self.limits = limits
        self.worktree_path = worktree_path
        self.fail_create = fail_create

    def __call__(self, argv):
        self.calls.append(list(argv))
        head = argv[1:3]
        if head == ["repo", "list"]:
            return 0, json.dumps({"ok": True, "result": {"repos": self.repos}})
        if head == ["account", "list"]:
            return 0, json.dumps({"ok": True, "result": self.limits})
        if head == ["worktree", "create"]:
            if self.fail_create:
                return 1, json.dumps(self.fail_create)
            result = _fixture("worktree_create")
            result["worktree"]["path"] = self.worktree_path
            return 0, json.dumps({"ok": True, "result": result})
        if head == ["agent", "launch"]:
            return 0, json.dumps({"ok": True, "result": _fixture("agent_launch")})
        return 1, json.dumps({"ok": False, "error": {"code": "unknown", "message": "no"}})

    def launches(self):
        return [c for c in self.calls if c[1:3] in (["worktree", "create"], ["agent", "launch"])]

    def called(self, *sub):
        return any(c[1:1 + len(sub)] == list(sub) for c in self.calls)


class _Git:
    """Fake `git(path, *args) -> (code, stdout)`."""

    def __init__(self, branches="", status="", ignored=True, sha=SHA, branch="develop"):
        self.calls = []
        self.branches = branches
        self.status = status
        self.ignored = ignored
        self.sha = sha
        self.branch = branch
        self.exclude = None  # what `rev-parse --git-path info/exclude` prints; None = fails

    def __call__(self, path, *args):
        self.calls.append((path,) + args)
        if args[:2] == ("branch", "--list"):
            return 0, self.branches
        if args[:2] == ("status", "--porcelain"):
            return 0, self.status
        if args[:1] == ("check-ignore",):
            return (0 if self.ignored else 1), ""
        if args == ("rev-parse", "HEAD"):
            return 0, self.sha + "\n"
        if args == ("rev-parse", "--git-path", "info/exclude"):
            return (1, "") if self.exclude is None else (0, self.exclude + "\n")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return 0, self.branch + "\n"
        return 1, ""


class DriverCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo_path = os.path.join(self.root, "proxima-api")
        self.worktree = os.path.join(self.root, "wt", "quick-x")
        os.makedirs(self.repo_path)
        os.makedirs(self.worktree)
        self.repos = [
            {"id": "r1", "path": self.repo_path, "displayName": "proxima-api"},
            {"id": "r2", "path": os.path.join(self.root, "proxima-hub"), "displayName": "proxima-hub"},
        ]
        self.out = []

    def run_quick(self, sentence="arregla el boton", *, orca=None, git=None, limits=None, **kw):
        orca = orca or _Orca(self.repos, limits or _limits(10, 10), self.worktree)
        git = git or _Git()
        kw.setdefault("repo", "proxima-api")
        code = quick_driver.start_quick(
            sentence, client=OrcaClient(executable="ORCA", runner=orca),
            git=git, now=lambda: 1234.5, say=self.out.append, **kw)
        return code, orca, git

    def record(self, workspace):
        with open(os.path.join(workspace, ".rein", "quick.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def text(self):
        return "\n".join(self.out)


class StartTests(DriverCase):
    def test_default_creates_a_worktree_from_develop(self):
        code, orca, git = self.run_quick("arregla el boton", linear="PPR-9")
        self.assertEqual(code, 0)
        [argv] = orca.launches()
        brief = quick.build_brief("arregla el boton", "PPR-9", False, ship.BIN)
        self.assertEqual(argv, [
            "ORCA", "worktree", "create", "--repo", "id:r1", "--name", "quick-arregla-el-boton",
            "--base-branch", "develop", "--agent", "claude", "--model", "sonnet",
            "--prompt", brief, "--setup", "run", "--activate", "--linear-issue", "PPR-9",
            "--json"])
        rec = self.record(self.worktree)
        self.assertEqual(rec, quick.new_record(
            "proxima-api", self.worktree, "develop", SHA, "claude", "sonnet",
            "arregla el boton", "PPR-9", 1234.5))
        # base_sha is HEAD of the NEW worktree, not a merge base
        self.assertIn((self.worktree, "rev-parse", "HEAD"), git.calls)
        text = self.text()
        for part in ("claude", "sonnet", self.worktree):
            self.assertIn(part, text)

    def test_base_is_passed_through(self):
        code, orca, _ = self.run_quick(base="main")
        self.assertEqual(code, 0)
        argv = orca.launches()[0]
        self.assertEqual(argv[argv.index("--base-branch") + 1], "main")
        self.assertEqual(self.record(self.worktree)["base"], "main")

    def test_here_launches_in_the_main_checkout(self):
        code, orca, git = self.run_quick(here=True, git=_Git(branch="feature/x"))
        self.assertEqual(code, 0)
        [argv] = orca.launches()
        self.assertEqual(argv[1:3], ["agent", "launch"])
        self.assertEqual(argv[argv.index("--worktree") + 1], "path:" + self.repo_path)
        self.assertFalse(orca.called("worktree", "create"))
        rec = self.record(self.repo_path)
        self.assertEqual((rec["base"], rec["base_sha"], rec["workspace"]),
                         ("feature/x", SHA, self.repo_path))

    def test_here_refuses_dirty_but_ignores_rein_state(self):
        code, orca, _ = self.run_quick(here=True, git=_Git(status=" M src/a.py\n"))
        self.assertEqual(code, 2)
        self.assertIn("uncommitted", self.text())
        self.assertEqual(orca.launches(), [])
        self.out.clear()
        code, orca, _ = self.run_quick(
            here=True, git=_Git(status="?? .rein/\n M .rein/quick.json\n"))
        self.assertEqual(code, 0)
        self.assertEqual(len(orca.launches()), 1)

    def test_dirty_ok_allows_it(self):
        code, orca, _ = self.run_quick(here=True, dirty_ok=True,
                                       git=_Git(status=" M src/a.py\n"))
        self.assertEqual(code, 0)
        self.assertEqual(len(orca.launches()), 1)

    def test_default_does_not_look_at_dirtiness(self):
        git = _Git(status=" M src/a.py\n")
        code, _, _ = self.run_quick(git=git)
        self.assertEqual(code, 0)
        self.assertFalse([c for c in git.calls if c[1:3] == ("status", "--porcelain")])

    def test_here_with_base_is_usage(self):
        code, orca, _ = self.run_quick(here=True, base="main")
        self.assertEqual(code, 2)
        self.assertIn("--here", self.text())
        self.assertEqual(orca.launches(), [])

    def test_unknown_repo_lists_names(self):
        code, orca, _ = self.run_quick(repo="nope")
        self.assertEqual(code, 2)
        self.assertIn("proxima-api", self.text())
        self.assertIn("proxima-hub", self.text())
        self.assertEqual(orca.launches(), [])

    def test_empty_repo_uses_the_one_containing_cwd(self):
        inner = os.path.join(self.repo_path, "src")
        os.makedirs(inner)
        code, orca, _ = self.run_quick(repo="", cwd=inner)
        self.assertEqual(code, 0)
        self.assertEqual(orca.launches()[0][orca.launches()[0].index("--repo") + 1], "id:r1")

    def test_empty_repo_outside_every_repo_is_usage(self):
        code, _, _ = self.run_quick(repo="", cwd=self.root)
        self.assertEqual(code, 2)
        self.assertIn("proxima-api", self.text())


class AgentTests(DriverCase):
    def test_every_provider_braked_launches_nothing(self):
        # Claude's weekly is under the threshold (a candidate) but its session is full
        code, orca, _ = self.run_quick(limits=_limits(10, 93, claude_session=95))
        self.assertEqual(code, 1)
        self.assertEqual(orca.launches(), [])
        braked = [l for l in self.out if "usage is at" in l]
        self.assertEqual(len(braked), 2)
        self.assertTrue(any(l.startswith("claude session") for l in braked))
        self.assertTrue(any(l.startswith("codex weekly") for l in braked))
        self.assertFalse(os.path.exists(os.path.join(self.worktree, ".rein")))

    def test_picks_codex_when_claude_is_past_the_threshold(self):
        code, orca, _ = self.run_quick(limits=_limits(77, 10))
        self.assertEqual(code, 0)
        argv = orca.launches()[0]
        self.assertEqual(argv[argv.index("--agent") + 1], "codex")

    def test_override_skips_quota(self):
        orca = _Orca(self.repos, _limits(99, 99), self.worktree)
        code, orca, _ = self.run_quick(orca=orca, agent="codex", model="gpt-x")
        self.assertEqual(code, 0)
        self.assertFalse(orca.called("account", "list"))
        argv = orca.launches()[0]
        self.assertEqual((argv[argv.index("--agent") + 1], argv[argv.index("--model") + 1]),
                         ("codex", "gpt-x"))
        rec = self.record(self.worktree)
        self.assertEqual((rec["agent"], rec["model"]), ("codex", "gpt-x"))

    def test_agent_alone_takes_its_default_model(self):
        code, orca, _ = self.run_quick(agent="claude")
        self.assertEqual(code, 0)
        argv = orca.launches()[0]
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")

    def test_model_without_agent_is_usage(self):
        code, orca, _ = self.run_quick(model="haiku")
        self.assertEqual(code, 2)
        self.assertEqual(orca.launches(), [])


class PlacementTests(DriverCase):
    def test_name_clash_gets_a_suffix(self):
        git = _Git(branches="  quick-arregla-el-boton\n+ quick-arregla-el-boton-2\n"
                            "* luisfarfan/quick-other\n")
        code, orca, _ = self.run_quick(git=git)
        self.assertEqual(code, 0)
        argv = orca.launches()[0]
        self.assertEqual(argv[argv.index("--name") + 1], "quick-arregla-el-boton-3")

    def test_existing_record_refuses(self):
        os.makedirs(os.path.join(self.repo_path, ".rein"))
        with open(os.path.join(self.repo_path, ".rein", "quick.json"), "w") as fh:
            fh.write("{}")
        code, orca, _ = self.run_quick(here=True)
        self.assertEqual(code, 2)
        self.assertIn("quick.json", self.text())
        self.assertIn("borra %s cuando la tarea anterior esté commiteada"
                      % os.path.join(self.repo_path, ".rein", "quick.json"), self.text())
        self.assertEqual(orca.launches(), [])
        self.assertTrue(os.path.exists(os.path.join(self.repo_path, ".rein", "quick.json")))

    def test_the_brief_names_this_checkouts_rein_as_the_fallback(self):
        _, orca, _ = self.run_quick()
        brief = orca.launches()[0][orca.launches()[0].index("--prompt") + 1]
        self.assertIn("python3 %s quick done" % ship.BIN, brief)
        self.assertTrue(os.path.isabs(ship.BIN))

    def test_orca_failure_is_126_and_writes_no_record(self):
        failure = {"ok": False,
                   "error": {"code": "setup_failed", "message": "hook blew up",
                             "residualResources": [{"kind": "worktree", "id": "wt-1"}]}}
        orca = _Orca(self.repos, _limits(10, 10), self.worktree, fail_create=failure)
        code, _, _ = self.run_quick(orca=orca)
        self.assertEqual(code, 126)
        self.assertIn("hook blew up", self.text())
        self.assertIn("worktree wt-1", self.text())
        self.assertFalse(os.path.exists(os.path.join(self.worktree, ".rein")))

    def test_orca_unreachable_is_126(self):
        def boom(argv):
            raise OSError("no orca")
        code = quick_driver.start_quick(
            "x", repo="proxima-api", client=OrcaClient(executable="ORCA", runner=boom),
            git=_Git(), now=lambda: 0.0, say=self.out.append)
        self.assertEqual(code, 126)
        self.assertIn("no orca", self.text())

    def test_unignored_rein_prints_the_exclude_line(self):
        code, _, _ = self.run_quick(git=_Git(ignored=False))
        self.assertEqual(code, 0)
        self.assertIn('add ".rein/" to %s/.git/info/exclude' % self.worktree, self.text())
        self.assertFalse(os.path.exists(os.path.join(self.worktree, ".git")))

    def test_linked_worktree_hint_names_the_common_dirs_exclude(self):
        git = _Git(ignored=False)
        git.exclude = "/repos/proxima-api/.git/info/exclude"
        code, _, _ = self.run_quick(git=git)
        self.assertEqual(code, 0)
        self.assertIn('add ".rein/" to /repos/proxima-api/.git/info/exclude', self.text())
        self.assertNotIn(self.worktree + "/.git/info/exclude", self.text())

    def test_relative_exclude_path_is_made_absolute(self):
        git = _Git(ignored=False)
        git.exclude = ".git/info/exclude"
        self.run_quick(git=git)
        self.assertIn('add ".rein/" to %s' % os.path.join(self.worktree, ".git/info/exclude"),
                      self.text())

    def test_ignored_rein_prints_no_exclude_line(self):
        self.run_quick(git=_Git(ignored=True))
        self.assertNotIn("info/exclude", self.text())

    def test_brief_is_one_argv_element(self):
        sentence = 'arregla "el boton" $(rm -rf ~) y `id`; \'x\''
        code, orca, _ = self.run_quick(sentence)
        self.assertEqual(code, 0)
        [argv] = orca.launches()
        brief = quick.build_brief(sentence, "", quick.looks_like_bug(sentence), ship.BIN)
        self.assertEqual(argv[argv.index("--prompt") + 1], brief)
        self.assertEqual(argv.count(brief), 1)

    def test_a_bug_sentence_asks_for_a_reproducing_test(self):
        _, orca, _ = self.run_quick("hay un bug en el carrito")
        argv = orca.launches()[0]
        self.assertIn("reproduzca", argv[argv.index("--prompt") + 1])


class RunGitTests(unittest.TestCase):
    def test_run_git_returns_code_and_stdout(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        code, out = quick_driver.run_git(root, "rev-parse", "--is-inside-work-tree")
        self.assertNotEqual(code, 0)
        self.assertIsInstance(out, str)


# --- rein quick done ---------------------------------------------------------

DISPATCH = "ctx_audit"
LINT, TYPES, SUITE, ONE = "make lint", "make types", "make test", "make one {target}"
CONFIG = {"commands": {"lint": LINT, "typecheck": TYPES, "test": SUITE, "testOne": ONE},
          "ship": {"riskyPaths": ["**/auth/**"], "largeChangeFiles": 3}}


def _result(slot, outcome="ok", code=0, output=""):
    return verify.CommandResult(slot, "cmd", outcome != "not_invocable", outcome, code, [], 7,
                                output=output)


class _Gate:
    """Fake `run`: every slot is green unless `results` says otherwise."""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def __call__(self, slot, command, cwd, timeout, configured=None):
        self.calls.append((slot, command, cwd, timeout, configured))
        return self.results.get(slot) or _result(slot)

    def slots(self):
        return [c[0] for c in self.calls]


class _AuditOrca:
    """Fake orca for the audit: `on_start` plays the reviewer when the worker starts."""

    def __init__(self, on_start=None, limits=None, done=True, show_state="running",
                 question=None, stops=True, on_check=None, fail_check=False):
        self.calls = []
        self.on_check = on_check  # called on every wait-for-messages check
        self.fail_check = fail_check  # orca itself fails while the owner waits
        self.on_start = on_start
        self.limits = limits or _limits(10, 10)
        self.done = done
        self.show_state = show_state
        self.question = question  # body of a question the audit worker asks instead of finishing
        self.stops = stops  # does worker-stop really end the worker?
        self.stopped = False
        self.started = False
        self.checks = 0

    def __call__(self, argv):
        self.calls.append(list(argv))
        head = argv[1:3]
        ok = lambda result: (0, json.dumps({"ok": True, "result": result}))  # noqa: E731
        if head == ["account", "list"]:
            return ok(self.limits)
        if head == ["orchestration", "run-create"]:
            return ok({"run": {"id": "run_1"}})
        if head == ["orchestration", "task-create"]:
            return ok({"task": {"id": "task_1"}})
        if head == ["orchestration", "worker-start"]:
            self.started = True
            if self.on_start:
                self.on_start()
            return ok({"dispatchId": DISPATCH, "state": "ready"})
        if head == ["orchestration", "check"]:
            self.checks += 1
            if self.on_check:
                self.on_check()
            if self.fail_check and self.started:
                return 1, json.dumps({"ok": False,
                                      "error": {"code": "runtime", "message": "orca went away"}})
            if "--ack" in argv or not self.started:
                return ok({"messages": [], "deliveryId": None})
            if self.question is not None:
                theirs = {"id": "q0", "type": "question", "body": "not ours",
                          "payload": json.dumps({"dispatchId": "someone_else"})}
                ours = {"id": "q1", "type": "question", "body": self.question,
                        "payload": json.dumps({"dispatchId": DISPATCH})}
                return ok({"messages": [theirs, ours], "deliveryId": "d2"})
            if not self.done:
                return ok({"messages": [], "deliveryId": None})
            payload = json.dumps({"dispatchId": DISPATCH, "outcome": "succeeded"})
            other = {"id": "m0", "type": "worker_done",
                     "payload": json.dumps({"dispatchId": "someone_else", "outcome": "failed"})}
            mine = {"id": "m1", "type": "worker_done", "payload": payload}
            return ok({"messages": [other, mine], "deliveryId": "d1"})
        if head == ["orchestration", "worker-stop"]:
            self.stopped = self.stops
            return ok({"dispatchId": DISPATCH, "state": "stopping"})
        if head == ["orchestration", "worker-show"]:
            state = "stopped" if self.stopped else self.show_state
            return ok({"dispatch": {"status": "completed" if state == "succeeded" else "running"},
                       "worker": {"state": state}})
        if head == ["orchestration", "worker-release"]:
            return ok({"dispatchId": DISPATCH, "state": "released"})
        return 1, json.dumps({"ok": False, "error": {"code": "unknown", "message": "no"}})

    def called(self, *sub):
        return [c for c in self.calls if c[1:1 + len(sub)] == list(sub)]


class _Clock:
    def __init__(self, step=0.0):
        self.now, self.step = 1000.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class DoneCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ws = os.path.join(self.root, "quick-fix")
        self.out = []

    def git(self, *args):
        subprocess.run(["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args],
                       check=True, capture_output=True, text=True)

    def write(self, rel, body="x\n"):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def make_repo(self, config=None, record=True, **record_fields):
        os.makedirs(self.ws)
        subprocess.run(["git", "init", "-q", "-b", "develop", self.ws], check=True)
        self.write("flow.config.json", json.dumps(CONFIG if config is None else config))
        for rel in ("src/foo.ts", "src/foo.test.ts", "src/bar.ts", "src/baz.ts",
                    "src/auth/login.ts"):
            self.write(rel, "v1\n")
        self.write(".gitignore", ".rein/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        code = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if record:
            rec = quick.new_record("proxima-api", self.ws, "develop", code, "claude", "sonnet",
                                   "arregla el login", "", 1.0)
            rec.update(record_fields)
            self.write(".rein/quick.json", json.dumps(rec))

    def audit_rounds(self):
        with open(os.path.join(self.ws, ".rein", "quick.json"), encoding="utf-8") as fh:
            return json.load(fh)["audit_rounds"]

    def reviewer(self, verdict, findings=(), files=("src/auth/login.ts",)):
        def play():
            gate.record_review(self.ws, "quick-quick-fix", verdict, list(files), list(findings),
                               "opus-reviewer")
        return play

    def done(self, *, run=None, orca=None, cwd=None, env=None, clock=None):
        run = run or _Gate()
        orca = orca or _AuditOrca()
        code = quick_driver.done_quick(
            cwd=cwd or self.ws, client=OrcaClient(executable="ORCA", runner=orca), run=run,
            clock=clock or _Clock(), env={} if env is None else env, say=self.out.append)
        return code, run, orca

    def text(self):
        return "\n".join(self.out)


TERMINAL = {"ORCA_TERMINAL_HANDLE": "term_1"}


class DoneGateTests(DoneCase):
    def test_no_record_is_usage(self):
        self.make_repo(record=False)
        code, run, _ = self.done()
        self.assertEqual(code, 2)
        self.assertIn("start one with `rein quick`", self.text())
        self.assertEqual(run.calls, [])

    def test_outside_a_git_repo_is_126(self):
        code, run, _ = self.done(cwd=self.root)
        self.assertEqual(code, 126)
        self.assertEqual(run.calls, [])

    def test_unreadable_record_is_usage(self):
        self.make_repo()
        self.write(".rein/quick.json", "{not json")
        code, _, _ = self.done()
        self.assertEqual(code, 2)
        self.assertIn(quick.RECORD, self.text())

    def test_finds_the_record_from_a_subdirectory(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        code, run, _ = self.done(cwd=os.path.join(self.ws, "src"))
        self.assertEqual(code, 0)
        self.assertEqual({c[2] for c in run.calls}, {self.ws})

    def test_nothing_changed_is_green(self):
        self.make_repo()
        code, run, _ = self.done()
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", self.text())
        self.assertEqual(run.calls, [])

    def test_unreachable_base_is_126(self):
        self.make_repo(base_sha="f" * 40)
        self.write("src/foo.ts", "v2\n")
        code, run, _ = self.done()
        self.assertEqual(code, 126)
        self.assertEqual(run.calls, [])

    def test_green_light_gate(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        code, run, orca = self.done()
        self.assertEqual(code, 0)
        self.assertEqual(run.slots(), ["lint", "typecheck", "testOne:src/foo.test.ts"])
        slot, command, cwd, timeout, configured = run.calls[2]
        self.assertEqual((command, configured), ("make one src/foo.test.ts", ONE))
        self.assertEqual((cwd, timeout), (self.ws, 900.0))
        self.assertIn("verdict: green", self.text())
        self.assertEqual(orca.calls, [])  # no audit, no Orca
        self.assertEqual(self.audit_rounds(), 0)

    def test_gate_timeout_comes_from_the_config(self):
        cfg = dict(CONFIG, ship=dict(CONFIG["ship"], gateTimeoutSeconds=42))
        self.make_repo(config=cfg)
        self.write("src/foo.ts", "v2\n")
        _, run, _ = self.done()
        self.assertEqual({c[3] for c in run.calls}, {42})

    def test_red_shows_error_lines_only(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        before = "\n".join("early noise %d" % i for i in range(30))
        after = "\n".join("late noise %d" % i for i in range(30))
        out = before + "\nAssertionError: expected 1 got 2\n" + after
        run = _Gate({"testOne:src/foo.test.ts": _result("testOne:src/foo.test.ts", "failed", 1, out)})
        code, _, _ = self.done(run=run)
        self.assertEqual(code, 1)
        text = self.text()
        self.assertIn("verdict: red", text)
        self.assertIn("AssertionError: expected 1 got 2", text)
        self.assertNotIn("early noise 0", text)
        self.assertNotIn("late noise 15", text)
        self.assertIn("early noise 29", text)  # two lines of context around the error

    def test_setup_is_126(self):
        self.make_repo()
        self.write("src/foo.ts", "v2\n")
        run = _Gate({"lint": _result("lint", "not_invocable", 127)})
        code, _, orca = self.done(run=run, env=TERMINAL)
        self.assertEqual(code, 126)
        self.assertIn("verdict: setup", self.text())
        self.assertEqual(orca.calls, [])

    def test_large_change_warns_without_audit(self):
        self.make_repo()
        for rel in ("src/foo.ts", "src/bar.ts", "src/baz.ts", "src/new.ts"):
            self.write(rel, "v2\n")
        code, run, orca = self.done(env=TERMINAL)
        self.assertEqual(code, 0)
        self.assertIn("4 archivos", self.text())
        self.assertIn("plan", self.text())
        self.assertEqual(orca.calls, [])

    def test_no_related_tests_is_said(self):
        self.make_repo()
        self.write("src/bar.ts", "v2\n")
        code, run, _ = self.done()
        self.assertEqual(code, 0)
        self.assertEqual(run.slots(), ["lint", "typecheck"])
        self.assertIn("sin tests relacionados", self.text())

    def test_without_testone_the_suite_runs_once_and_says_so(self):
        cfg = {"commands": {"test": SUITE}, "ship": CONFIG["ship"]}
        self.make_repo(config=cfg)
        self.write("src/foo.ts", "v2\n")
        code, run, _ = self.done()
        self.assertEqual(code, 0)
        self.assertEqual(run.slots(), ["test"])
        self.assertIn("suite", self.text())

    def test_related_tests_that_cannot_run_are_counted(self):
        cfg = {"commands": {"lint": LINT}, "ship": CONFIG["ship"]}
        self.make_repo(config=cfg)
        self.write("src/foo.ts", "v2\n")
        code, run, _ = self.done()
        self.assertEqual(code, 0)
        self.assertEqual(run.slots(), ["lint"])
        self.assertIn("1 tests relacionados sin ejecutar", self.text())
        self.assertIn("`test` ni `testOne`", self.text())


class DoneAuditTests(DoneCase):
    def risky(self, **kw):
        self.make_repo(**kw)
        self.write("src/auth/login.ts", "v2\n")

    def test_risky_without_terminal_is_126(self):
        self.risky()
        code, _, orca = self.done()
        self.assertEqual(code, 126)
        self.assertIn("needs an Orca terminal", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])

    def test_a_red_gate_skips_the_audit(self):
        self.risky()
        run = _Gate({"lint": _result("lint", "failed", 1, "error: x")})
        code, _, orca = self.done(run=run, env=TERMINAL)
        self.assertEqual(code, 1)
        self.assertEqual(orca.calls, [])

    def test_every_reviewer_braked_is_126(self):
        self.risky()
        orca = _AuditOrca(limits=_limits(10, 95, claude_session=95))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 126)
        self.assertIn("audit cannot run", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])

    def test_braked_claude_audits_with_codex(self):
        self.risky()
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"),
                          limits=_limits(10, 10, claude_session=95))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0)
        [start] = orca.called("orchestration", "worker-start")
        self.assertEqual(start[start.index("--agent") + 1], "codex")

    def test_risky_runs_an_audit_and_approves(self):
        self.risky()
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        code, run, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0)
        self.assertEqual(run.slots(), ["lint", "typecheck"])  # src/auth/login.ts has no tests
        [create] = orca.called("orchestration", "run-create")
        self.assertEqual(create[create.index("--objective") + 1], "rein quick quick-fix")
        [task] = orca.called("orchestration", "task-create")
        self.assertEqual(task[task.index("--task-title") + 1], "rein quick audit quick-fix round 1")
        spec = task[task.index("--spec") + 1]
        self.assertIn("arregla el login", spec)
        self.assertIn("Run the rein-audit skill on " + self.ws, spec)
        self.assertIn("--change quick-quick-fix", spec)
        self.assertIn("src/auth/login.ts", spec)
        [start] = orca.called("orchestration", "worker-start")
        for flag, value in (("--worktree", "path:" + self.ws), ("--agent", "claude"),
                            ("--model", "opus"), ("--task", "task_1")):
            self.assertEqual(start[start.index(flag) + 1], value)
        self.assertEqual(len(orca.called("orchestration", "worker-release")), 1)
        acks = [c for c in orca.called("orchestration", "check") if "--ack" in c]
        self.assertEqual([c[c.index("--ack") + 1] for c in acks], ["d1"])
        self.assertEqual(self.audit_rounds(), 0)  # an approval spends no round
        self.assertIn("APPROVED", self.text())
        self.assertTrue(os.path.exists(os.path.join(self.ws, quick.RECORD)))  # never deleted

    def test_changes_requested_is_red_and_counts_a_round(self):
        self.risky()
        orca = _AuditOrca(on_start=self.reviewer(
            "CHANGES_REQUESTED", ["BLOCKING: sql built by string concat", "IMPORTANT: rename x"]))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKING: sql built by string concat", self.text())
        self.assertNotIn("rename x", self.text())
        self.assertEqual(self.audit_rounds(), 1)

    def test_a_round_is_only_spent_by_a_verdict_of_changes(self):
        self.risky()
        seen = []

        def play():
            seen.append(self.audit_rounds())
            self.reviewer("APPROVED")()
        orca = _AuditOrca(on_start=play)
        self.done(orca=orca, env=TERMINAL)
        self.assertEqual(seen, [0])
        order = [c[2] for c in orca.calls if c[1] == "orchestration"]
        self.assertLess(order.index("worker-start"), order.index("check"))
        self.assertEqual(self.audit_rounds(), 0)

    def test_second_round_title_counts_up(self):
        self.risky(audit_rounds=1)
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0)
        [task] = orca.called("orchestration", "task-create")
        self.assertTrue(task[task.index("--task-title") + 1].endswith("round 2"))
        self.assertEqual(self.audit_rounds(), 1)  # round 2 was approved: nothing was spent

    def test_third_round_leaves_it_to_the_owner(self):
        self.risky(audit_rounds=2)
        self.reviewer("CHANGES_REQUESTED", ["BLOCKING: still unsafe"])()
        code, _, orca = self.done(env=TERMINAL)
        self.assertEqual(code, 1)
        self.assertIn("dos rondas de auditoría: decide tú", self.text())
        self.assertIn("BLOCKING: still unsafe", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])
        self.assertEqual(self.audit_rounds(), 2)

    def test_audit_without_a_new_episode_is_126(self):
        self.risky()
        orca = _AuditOrca()  # the worker ends but records nothing
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 126)
        self.assertIn("without recording a review episode", self.text())
        self.assertEqual(len(orca.called("orchestration", "worker-release")), 1)
        self.assertEqual(self.audit_rounds(), 0)

    def test_an_old_episode_is_not_a_new_verdict(self):
        self.risky()
        self.reviewer("APPROVED")()  # left over from an earlier round...
        self.write("src/auth/login.ts", "v3\n")  # ...and no longer matching the files
        code, _, _ = self.done(orca=_AuditOrca(), env=TERMINAL)
        self.assertEqual(code, 126)

    def test_a_silent_worker_times_out_and_is_stopped(self):
        self.risky()
        orca = _AuditOrca(done=False)
        code, _, _ = self.done(orca=orca, env=TERMINAL, clock=_Clock(step=700))
        self.assertEqual(code, 126)
        self.assertIn("20 minutes", self.text())
        self.assertIn("audit worker %s stopped" % DISPATCH, self.text())
        self.assertGreaterEqual(orca.checks, 2)
        self.assertEqual(len(orca.called("orchestration", "worker-stop")), 1)
        self.assertEqual(len(orca.called("orchestration", "worker-show")), 2)  # before and after the stop
        self.assertEqual(len(orca.called("orchestration", "worker-release")), 1)
        self.assertEqual(self.audit_rounds(), 0)

    def test_a_worker_that_will_not_stop_is_reported_and_kept(self):
        self.risky()
        orca = _AuditOrca(done=False, stops=False)
        code, _, _ = self.done(orca=orca, env=TERMINAL, clock=_Clock(step=700))
        self.assertEqual(code, 126)
        self.assertIn("20 minutes", self.text())
        self.assertIn("audit worker %s not confirmed stopped" % DISPATCH, self.text())
        self.assertEqual(len(orca.called("orchestration", "worker-stop")), 1)
        self.assertEqual(orca.called("orchestration", "worker-release"), [])

    def test_a_question_from_the_auditor_stops_it_and_goes_to_the_owner(self):
        self.risky()
        orca = _AuditOrca(question="¿puedo tocar el middleware?")
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 126)
        self.assertIn("el auditor preguntó: ¿puedo tocar el middleware?", self.text())
        self.assertNotIn("not ours", self.text())  # another dispatch's message is ignored
        self.assertEqual(len(orca.called("orchestration", "worker-stop")), 1)
        self.assertEqual(len(orca.called("orchestration", "worker-release")), 1)
        acks = [c for c in orca.called("orchestration", "check") if "--ack" in c]
        self.assertEqual([c[c.index("--ack") + 1] for c in acks], ["d2"])
        self.assertEqual(self.audit_rounds(), 0)

    def test_a_finished_worker_whose_message_was_missed_is_read(self):
        self.risky()
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"), done=False, show_state="succeeded")
        code, _, _ = self.done(orca=orca, env=TERMINAL, clock=_Clock(step=700))
        self.assertEqual(code, 0)
        self.assertEqual(len(orca.called("orchestration", "worker-release")), 1)


    # --- F2: which rounds count, and when an approval still stands ------------

    def test_a_still_valid_approval_launches_nothing(self):
        self.risky()
        self.reviewer("APPROVED")()
        code, run, orca = self.done()  # no Orca terminal needed either
        self.assertEqual(code, 0)
        self.assertIn("auditoría vigente: aprobada", self.text())
        self.assertEqual(orca.calls, [])
        self.assertEqual(self.audit_rounds(), 0)

    def test_approved_then_a_tweak_outside_the_reviewed_files_stays_approved(self):
        self.risky()
        self.reviewer("APPROVED")()
        self.write("src/bar.ts", "v2\n")  # not risky, not reviewed
        code, _, orca = self.done(env=TERMINAL)
        self.assertEqual(code, 0)
        self.assertIn("auditoría vigente: aprobada", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])

    def test_approved_then_a_tweak_to_a_reviewed_file_is_audited_again(self):
        self.risky()
        self.reviewer("APPROVED")()
        self.write("src/auth/login.ts", "v3\n")
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0)
        self.assertEqual(len(orca.called("orchestration", "worker-start")), 1)
        self.assertNotIn("auditoría vigente", self.text())

    def test_an_approval_stands_even_after_the_cap(self):
        self.risky(audit_rounds=2)
        self.reviewer("APPROVED")()
        code, _, _ = self.done()
        self.assertEqual(code, 0)
        self.assertIn("auditoría vigente: aprobada", self.text())
        self.assertNotIn("dos rondas", self.text())

    def test_two_timeouts_do_not_reach_the_cap(self):
        self.risky()
        for _ in range(2):
            orca = _AuditOrca(done=False)
            code, _, _ = self.done(orca=orca, env=TERMINAL, clock=_Clock(step=700))
            self.assertEqual(code, 126)
            self.assertEqual(self.audit_rounds(), 0)
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0)
        self.assertEqual(len(orca.called("orchestration", "worker-start")), 1)
        self.assertNotIn("dos rondas", self.text())

    def test_the_cap_is_reached_after_two_changes_requested(self):
        self.risky()
        for n, finding in enumerate(("BLOCKING: first problem", "BLOCKING: second problem"), 1):
            orca = _AuditOrca(on_start=self.reviewer("CHANGES_REQUESTED", [finding]))
            code, _, _ = self.done(orca=orca, env=TERMINAL)
            self.assertEqual(code, 1)
            self.assertEqual(self.audit_rounds(), n)
        orca = _AuditOrca()
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 1)
        self.assertIn("dos rondas de auditoría: decide tú", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])
        self.assertEqual(self.audit_rounds(), 2)

    def test_the_cap_message_shows_the_last_changes_requested_not_a_later_episode(self):
        self.risky(audit_rounds=1)
        orca = _AuditOrca(on_start=self.reviewer("CHANGES_REQUESTED", ["BLOCKING: the real problem"]))
        self.done(orca=orca, env=TERMINAL)  # round 2 of 2: CHANGES_REQUESTED
        self.reviewer("APPROVED")()  # a later episode of another verdict, now stale
        self.write("src/auth/login.ts", "v9\n")
        self.out.clear()
        code, _, orca = self.done(env=TERMINAL)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKING: the real problem", self.text())
        self.assertEqual(orca.called("orchestration", "worker-start"), [])

    # --- F3: a long done ------------------------------------------------------

    def test_the_summary_says_the_audit_is_still_to_come(self):
        self.risky()
        orca = _AuditOrca(on_start=self.reviewer("APPROVED"))
        self.done(orca=orca, env=TERMINAL)
        self.assertIn("gate: green \u2014 auditoría pendiente", self.text())
        self.assertNotIn("verdict: green", self.text())

    def test_no_pending_audit_when_nothing_risky_changed(self):
        self.make_repo()
        self.write("src/bar.ts", "v2\n")
        self.done(env=TERMINAL)
        self.assertIn("verdict: green", self.text())
        self.assertNotIn("pendiente", self.text())

    def test_no_pending_audit_when_an_approval_stands(self):
        self.risky()
        self.reviewer("APPROVED")()
        self.done()
        self.assertIn("verdict: green", self.text())

    def test_sigterm_during_the_wait_stops_the_worker_and_exits_126(self):
        self.risky()
        installed = {}

        def peek():
            installed["term"] = signal.getsignal(signal.SIGTERM)
            installed["int"] = signal.getsignal(signal.SIGINT)
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        orca = _AuditOrca(done=False, on_check=peek)
        self.done(orca=orca, env=TERMINAL, clock=_Clock(step=700))
        self.assertTrue(callable(installed["term"]))
        self.assertNotEqual(installed["term"], before[0])  # ours, during the wait
        self.assertNotEqual(installed["int"], before[1])
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)), before)

        # the handler itself: stop-and-confirm, then exit 126 -- called directly, no real signal
        fake = _AuditOrca(done=False)
        client = OrcaClient(executable="ORCA", runner=fake)
        out = []
        handler = quick_driver._stop_on_signal(client, DISPATCH, out.append)
        with self.assertRaises(SystemExit) as caught:
            handler(signal.SIGTERM, None)
        self.assertEqual(caught.exception.code, 126)
        self.assertEqual(len(fake.called("orchestration", "worker-stop")), 1)
        self.assertEqual(len(fake.called("orchestration", "worker-release")), 1)
        self.assertIn("audit worker %s stopped" % DISPATCH, "\n".join(out))

    def test_a_signal_that_arrives_twice_stops_the_worker_once(self):
        fake = _AuditOrca(done=False)
        client = OrcaClient(executable="ORCA", runner=fake)
        handler = quick_driver._stop_on_signal(client, DISPATCH, lambda _line: None)
        for _ in range(2):
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
        self.assertEqual(len(fake.called("orchestration", "worker-stop")), 1)

    # --- F8: orca failing while waiting -----------------------------------------

    def test_an_orca_failure_while_waiting_stops_the_auditor(self):
        self.risky()
        orca = _AuditOrca(done=False, fail_check=True)
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 126)
        self.assertIn("orca failed", self.text())
        self.assertEqual(len(orca.called("orchestration", "worker-stop")), 1)
        self.assertIn("audit worker %s stopped" % DISPATCH, self.text())
        self.assertEqual(self.audit_rounds(), 0)


if __name__ == "__main__":
    unittest.main()
