"""`quick_driver.start_quick`: the I/O driver behind `rein quick "<sentence>"`.

No test shells out to `orca` or git: a fake runner replays the recorded
fixtures from tests/fixtures/orca and a fake `git` answers from a script.
The workspaces are temp dirs so the record can be really written.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import quick  # noqa: E402
import quick_driver  # noqa: E402
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
        brief = quick.build_brief("arregla el boton", "PPR-9", False)
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
        self.assertEqual(orca.launches(), [])

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
        brief = quick.build_brief(sentence, "", quick.looks_like_bug(sentence))
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


if __name__ == "__main__":
    unittest.main()
