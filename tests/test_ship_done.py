"""`rein done` / `rein quick close`: real git repos with a bare origin, a fake Orca runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import ship_done  # noqa: E402
import ship_state  # noqa: E402
from orca_client import OrcaClient  # noqa: E402

CHANGE = "demo"


def git(path, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (args, proc.stderr))
    return proc.stdout


def commit_file(path, name, text="x\n", msg="change"):
    full = os.path.join(path, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as fh:
        fh.write(text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", msg)


class FakeOrca:
    def __init__(self, repos=None, worktrees=None, terminals=None, fail_rm=()):
        self.calls = []
        self.repos = repos or []
        self.worktrees = worktrees or []
        self.terminals = terminals or []
        self.fail_rm = fail_rm

    def __call__(self, argv):
        self.calls.append(list(argv))
        head = argv[1:3]
        if head == ["repo", "list"]:
            return 0, json.dumps({"ok": True, "result": {"repos": self.repos}})
        if head == ["worktree", "list"]:
            return 0, json.dumps({"ok": True, "result": {"worktrees": self.worktrees}})
        if head == ["terminal", "list"]:
            return 0, json.dumps({"ok": True, "result": {"terminals": self.terminals}})
        if head == ["worktree", "rm"]:
            if any(argv[4] == "path:" + p for p in self.fail_rm):
                return 1, json.dumps({"ok": False, "error": {"code": "boom", "message": "cannot remove"}})
            return 0, json.dumps({"ok": True, "result": {}})
        if head == ["terminal", "close"]:
            return 0, json.dumps({"ok": True, "result": {}})
        return 1, json.dumps({"ok": False, "error": {"code": "unknown", "message": "no"}})

    def rm_paths(self):
        return [c[4][len("path:"):] for c in self.calls if c[1:3] == ["worktree", "rm"]]

    def closed(self):
        return [c for c in self.calls if c[1:3] == ["terminal", "close"]]


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", self.origin], check=True)
        seed = os.path.join(self.tmp, "seed")
        self.clone(self.origin, seed)
        git(seed, "checkout", "-q", "-b", "develop")
        commit_file(seed, "README", "hi\n", "init")
        git(seed, "push", "-q", "origin", "develop")
        self.seed = seed
        self.out = []

    def clone(self, src, dst):
        subprocess.run(["git", "clone", "-q", src, dst], check=True, capture_output=True)

    def say(self, line):
        self.out.append(line)

    def text(self):
        return "\n".join(self.out)

    def worktree(self, name="wt"):
        """A LINKED worktree on branch `feature`, like Orca makes."""
        main = os.path.join(self.tmp, name + "-main")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, name)
        git(main, "worktree", "add", "-q", "-b", "feature", path, "origin/develop")
        return path

    def plan_repo(self, openspec=True):
        root = os.path.join(self.tmp, "plan")
        self.clone(self.origin, root)
        git(root, "checkout", "-q", "-B", "develop", "origin/develop")
        if openspec:
            commit_file(root, "openspec/changes/%s/proposal.md" % CHANGE, "p\n", "plan")
        os.makedirs(os.path.join(root, ".rein"), exist_ok=True)
        with open(os.path.join(root, ".gitignore"), "w") as fh:
            fh.write(".rein/\n")
        git(root, "add", ".gitignore")
        git(root, "commit", "-q", "-m", "ignore")
        self.root = root
        return root

    def state(self, root, paths, phase="done"):
        data = {"change": CHANGE, "plan_path": root, "phase": phase,
                "worktrees": {r: "id::" + p for r, p in paths.items()}, "worktree_paths": paths}
        sp = ship_state.state_path(root, CHANGE)
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w") as fh:
            json.dump(data, fh)
        os.makedirs(ship_state.answers_dir(root, CHANGE), exist_ok=True)
        return sp

    def run_done(self, root, orca=None, dry=False, alive=False, yes=False, env=None):
        orca = orca or FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}])
        client = OrcaClient(executable="ORCA", runner=orca)
        code = ship_done.run_done(root, CHANGE, client, dry_run=dry, yes=yes, driver_alive=lambda c: alive,
                                  today="2026-10-20", say=self.say, env=env or {})
        return code, orca


class DoneTests(Env):
    def test_phase_not_done_refuses(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.state(root, {"api": wt}, phase="running")
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("phase", self.text())

    def test_driver_alive_refuses(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root, alive=True)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])

    def test_driver_check_is_scoped_to_the_plan_root(self):
        root = self.plan_repo()
        self.state(root, {"api": self.worktree()})
        seen = []
        real = ship_done.sw.driver_alive
        ship_done.sw.driver_alive = lambda change, **kw: seen.append((change, kw)) or True
        self.addCleanup(setattr, ship_done.sw, "driver_alive", real)
        client = OrcaClient(executable="ORCA", runner=FakeOrca())
        code = ship_done.run_done(root, CHANGE, client, today="2026-10-20", say=self.say, env={})
        self.assertEqual(code, 1)
        self.assertEqual(seen, [(CHANGE, {"root": root})])

    def test_missing_state_refuses(self):
        root = self.plan_repo()
        code, _ = self.run_done(root)
        self.assertEqual(code, 1)

    def test_dirty_worktree_blocks_everything(self):
        root = self.plan_repo()
        a, b = self.worktree("a"), self.worktree("b")
        with open(os.path.join(b, "scratch.txt"), "w") as fh:
            fh.write("wip")
        self.state(root, {"api": a, "web": b})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn(b, self.text())
        self.assertTrue(os.path.isfile(ship_state.state_path(root, CHANGE)))

    def test_rein_dir_is_not_dirty(self):
        root = self.plan_repo()
        wt = self.worktree()
        os.makedirs(os.path.join(wt, ".rein"))
        with open(os.path.join(wt, ".rein", "quick.json"), "w") as fh:
            fh.write("{}")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())

    def test_unmerged_commit_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "new.txt")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("not integrated", self.text())

    def test_rebased_commits_count_as_integrated(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "new.txt", "n\n", "feat")
        # the same patch lands on develop through another clone (a rebase/squash-free equivalent)
        commit_file(self.seed, "other.txt", "o\n", "other")
        patch = git(wt, "format-patch", "-1", "--stdout")
        proc = subprocess.run(["git", "-C", self.seed, "am", "-q"], input=patch, text=True, capture_output=True,
                              env=dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        git(self.seed, "push", "-q", "origin", "develop")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])

    def test_all_clear_cleans_up(self):
        root = self.plan_repo()
        wt = self.worktree()
        gone = os.path.join(self.tmp, "vanished")
        self.state(root, {"api": wt, "web": gone})
        orca = FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}],
                        terminals=[{"handle": "t1", "title": "rein ship demo", "worktreePath": root},
                                   {"handle": "t2", "title": "other", "worktreePath": root}])
        code, orca = self.run_done(root, orca)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])
        self.assertEqual([c[4] for c in orca.closed()], ["t1"])
        self.assertIn("already gone", self.text())
        archive = os.path.join(root, "openspec", "changes", "archive", "2026-10-20-" + CHANGE)
        self.assertTrue(os.path.isfile(os.path.join(archive, "proposal.md")))
        self.assertFalse(os.path.exists(os.path.join(root, "openspec", "changes", CHANGE)))
        msg = git(root, "log", "-1", "--format=%B").strip()
        self.assertEqual(msg, "docs(openspec): archive %s" % CHANGE)
        self.assertNotIn("Co-Authored", msg)
        self.assertEqual(git(root, "status", "--porcelain").strip(), "")
        self.assertIn("push", self.text())
        self.assertFalse(os.path.exists(ship_state.state_path(root, CHANGE)))
        self.assertTrue(os.path.isfile(os.path.join(root, ".rein", "ship", "archive", CHANGE + ".json")))
        self.assertFalse(os.path.exists(ship_state.answers_dir(root, CHANGE)))

    def test_archive_commit_touches_only_the_move(self):
        root = self.plan_repo()
        wt = self.worktree()
        with open(os.path.join(root, "unrelated.txt"), "w") as fh:
            fh.write("u")
        git(root, "add", "unrelated.txt")
        self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        files = git(root, "show", "--name-only", "--format=", "HEAD").split()
        self.assertTrue(all("openspec/changes" in f for f in files), files)
        self.assertIn("A  unrelated.txt", git(root, "status", "--porcelain"))

    def test_dry_run_removes_nothing(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root, dry=True)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertEqual(orca.closed(), [])
        self.assertTrue(os.path.isfile(ship_state.state_path(root, CHANGE)))
        self.assertTrue(os.path.isdir(os.path.join(root, "openspec", "changes", CHANGE)))
        self.assertIn(wt, self.text())

    def test_dry_run_reports_blockers(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "new.txt")
        self.state(root, {"api": wt})
        code, _ = self.run_done(root, dry=True)
        self.assertEqual(code, 1)

    def test_existing_archive_folder_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        os.makedirs(os.path.join(root, "openspec", "changes", "archive", "2026-10-20-" + CHANGE))
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("archive", self.text())

    def test_orca_error_continues_and_exits_126(self):
        root = self.plan_repo()
        a, b = self.worktree("a"), self.worktree("b")
        self.state(root, {"api": a, "web": b})
        orca = FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}], fail_rm=(a,))
        code, orca = self.run_done(root, orca)
        self.assertEqual(code, 126)
        self.assertEqual(orca.rm_paths(), [a, b])
        self.assertIn("cannot remove", self.text())

    def test_no_openspec_skips_archive(self):
        root = self.plan_repo(openspec=False)
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertFalse(os.path.exists(os.path.join(root, "openspec", "changes", "archive")))

    def test_falls_back_to_main_when_no_develop(self):
        root = self.plan_repo(openspec=False)
        subprocess.run(["git", "-C", self.origin, "branch", "-m", "develop", "main"], check=True)
        path = os.path.join(self.tmp, "wtm")
        main = os.path.join(self.tmp, "wtm-main")
        self.clone(self.origin, main)
        git(main, "worktree", "add", "-q", "-b", "feature", path, "origin/main")
        self.state(root, {"api": path})
        orca = FakeOrca(repos=[])  # no worktreeBaseRef known
        code, _ = self.run_done(root, orca)
        self.assertEqual(code, 0, self.text())


class QuickCloseBase(Env):
    def record(self, workspace, base="develop", at_base=True):
        sha = git(workspace, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(workspace, ".rein"), exist_ok=True)
        with open(os.path.join(workspace, ".rein", "quick.json"), "w") as fh:
            json.dump({"repo": "api", "workspace": workspace, "base": base, "base_sha": sha}, fh)

    def quick_wt(self, name="quick-fix"):
        path = self.worktree(name)
        # a linked worktree has a `.git` FILE; emulate that so "not --here" is recognised
        return path

    def close(self, arg="", cwd=None, orca=None, dry=False, yes=False):
        orca = orca or FakeOrca()
        client = OrcaClient(executable="ORCA", runner=orca)
        code = ship_done.run_quick_close(arg, client, cwd=cwd or self.tmp, dry_run=dry, yes=yes, say=self.say)
        return code, orca

    def linked(self, name):
        """A real linked worktree of a clone, like Orca makes."""
        main = os.path.join(self.tmp, name + "-main")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, name)
        git(main, "worktree", "add", "-q", "-b", "quick-" + name, path, "origin/develop")
        return path, main


class QuickCloseTests(QuickCloseBase):
    def test_clean_integrated_removed_and_record_deleted(self):
        path, _ = self.linked("fix")
        self.record(path)
        code, orca = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [path])

    def test_dirty_blocks(self):
        path, _ = self.linked("fix")
        self.record(path)
        with open(os.path.join(path, "wip"), "w") as fh:
            fh.write("x")
        code, orca = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertTrue(os.path.isfile(os.path.join(path, ".rein", "quick.json")))

    def test_unmerged_blocks(self):
        path, _ = self.linked("fix")
        self.record(path)
        commit_file(path, "new.txt")
        code, orca = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("not integrated", self.text())

    def test_no_commits_beyond_base_sha_is_nothing_to_keep(self):
        path, _ = self.linked("fix")
        self.record(path)
        code, orca = self.close(arg=path)
        self.assertEqual(code, 0, self.text())

    def test_here_record_only_deletes_the_record(self):
        main = os.path.join(self.tmp, "main")
        self.clone(self.origin, main)
        git(main, "checkout", "-q", "develop")
        self.record(main)
        code, orca = self.close(arg=main)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertFalse(os.path.exists(os.path.join(main, ".rein", "quick.json")))
        self.assertTrue(os.path.isdir(main))

    def test_here_record_unmerged_is_kept(self):
        main = os.path.join(self.tmp, "main")
        self.clone(self.origin, main)
        git(main, "checkout", "-q", "develop")
        self.record(main)
        commit_file(main, "new.txt")
        code, _ = self.close(arg=main)
        self.assertEqual(code, 1)
        self.assertTrue(os.path.isfile(os.path.join(main, ".rein", "quick.json")))

    def test_no_argument_scans_orca_worktrees_and_cwd(self):
        a, _ = self.linked("a")
        b, _ = self.linked("b")
        c, _ = self.linked("c")
        for p in (a, b):
            self.record(p)
        commit_file(b, "new.txt")  # b is blocked
        here = os.path.join(self.tmp, "here")
        self.clone(self.origin, here)
        git(here, "checkout", "-q", "develop")
        self.record(here)
        orca = FakeOrca(worktrees=[{"path": a}, {"path": b}, {"path": c}])
        code, orca = self.close(cwd=here, orca=orca)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [a])
        self.assertFalse(os.path.exists(os.path.join(here, ".rein", "quick.json")))
        self.assertTrue(os.path.isfile(os.path.join(b, ".rein", "quick.json")))
        self.assertIn(b, self.text())

    def test_argument_by_name(self):
        a, _ = self.linked("a")
        self.record(a)
        orca = FakeOrca(worktrees=[{"path": a}])
        code, orca = self.close(arg="a", orca=orca)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [a])

    def test_dry_run_changes_nothing(self):
        a, _ = self.linked("a")
        self.record(a)
        code, orca = self.close(arg=a, dry=True)
        self.assertEqual(code, 0)
        self.assertEqual(orca.rm_paths(), [])
        self.assertTrue(os.path.isfile(os.path.join(a, ".rein", "quick.json")))

    def test_orca_error_is_126(self):
        a, _ = self.linked("a")
        self.record(a)
        code, _ = self.close(arg=a, orca=FakeOrca(fail_rm=(a,)))
        self.assertEqual(code, 126)
        self.assertTrue(os.path.isfile(os.path.join(a, ".rein", "quick.json")))

    def test_unknown_argument(self):
        code, _ = self.close(arg="nope")
        self.assertEqual(code, 1)


class DoneSafetyTests(Env):
    def rows(self, **by_path):
        return [dict(path=p, **extra) for p, extra in by_path.items()]

    def orca_for(self, *rows):
        return FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}],
                        worktrees=list(rows))

    def test_detached_head_without_known_branch_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        git(wt, "checkout", "-q", "--detach")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("branch", self.text())

    def test_branch_orca_will_delete_is_checked_too(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "new.txt")
        git(wt, "checkout", "-q", "--detach", "origin/develop")  # HEAD looks integrated
        self.state(root, {"api": wt})
        code, orca = self.run_done(root, self.orca_for({"path": wt, "branch": "refs/heads/feature"}))
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("not integrated", self.text())

    def test_main_worktree_flag_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root, self.orca_for({"path": wt, "branch": "feature", "isMainWorktree": True}))
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("main", self.text())

    def test_main_checkout_detected_by_git_when_orca_is_silent(self):
        root = self.plan_repo()
        main = os.path.join(self.tmp, "plainclone")
        self.clone(self.origin, main)
        self.state(root, {"api": main})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])

    def test_untracked_files_block_even_if_status_hides_them(self):
        root = self.plan_repo()
        wt = self.worktree()
        git(wt, "config", "status.showUntrackedFiles", "no")
        os.makedirs(os.path.join(wt, "sub"))
        with open(os.path.join(wt, "sub", "mine.txt"), "w") as fh:
            fh.write("x")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("uncommitted", self.text())

    def ignored(self, wt, name=".env"):
        excl = git(wt, "rev-parse", "--git-path", "info/exclude").strip()
        excl = excl if os.path.isabs(excl) else os.path.join(wt, excl)
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        with open(excl, "a") as fh:
            fh.write(".env\nnode_modules/\n")
        with open(os.path.join(wt, name), "w") as fh:
            fh.write("SECRET=1")

    def test_ignored_file_needs_yes(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.ignored(wt)
        os.makedirs(os.path.join(wt, "node_modules"))
        with open(os.path.join(wt, "node_modules", "x.js"), "w") as fh:
            fh.write("1")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("WARNING", self.text())
        self.assertIn(".env", self.text())
        self.assertNotIn("node_modules", self.text())
        self.assertIn("--yes", self.text())
        self.out.clear()
        code, orca = self.run_done(root, yes=True)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])

    def test_node_modules_alone_needs_no_yes(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.ignored(wt, name="keep.txt")  # also ignores .env, which does not exist here
        os.makedirs(os.path.join(wt, "node_modules"))
        with open(os.path.join(wt, "node_modules", "x.js"), "w") as fh:
            fh.write("1")
        os.unlink(os.path.join(wt, "keep.txt"))
        self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())

    def test_failed_rm_keeps_state_and_mailbox(self):
        root = self.plan_repo()
        a = self.worktree("a")
        sp = self.state(root, {"api": a})
        orca = FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}], fail_rm=(a,))
        code, orca = self.run_done(root, orca)
        self.assertEqual(code, 126)
        self.assertTrue(os.path.isfile(sp))
        self.assertTrue(os.path.isdir(ship_state.answers_dir(root, CHANGE)))
        self.assertTrue(os.path.isdir(os.path.join(root, "openspec", "changes", CHANGE)))
        self.assertEqual(orca.closed(), [])

    def test_retry_after_everything_is_gone_is_idempotent(self):
        root = self.plan_repo()
        a = self.worktree("a")
        self.state(root, {"api": a})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        # a second run: worktree gone, plan already archived, state restored by a retry
        shutil.rmtree(a)
        self.state(root, {"api": a})
        self.out.clear()
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("already", self.text())

    def test_plan_repo_detached_blocks_before_removing(self):
        root = self.plan_repo()
        wt = self.worktree()
        git(root, "checkout", "-q", "--detach")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("branch", self.text())

    def test_untracked_plan_blocks_before_removing(self):
        root = self.plan_repo(openspec=False)
        os.makedirs(os.path.join(root, "openspec", "changes", CHANGE))
        with open(os.path.join(root, "openspec", "changes", CHANGE, "proposal.md"), "w") as fh:
            fh.write("p")
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("tracked", self.text())

    def test_rejected_commit_restores_the_move(self):
        root = self.plan_repo()
        wt = self.worktree()
        hook = os.path.join(root, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho rejected >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        sp = self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 126)
        self.assertTrue(os.path.isfile(os.path.join(root, "openspec", "changes", CHANGE, "proposal.md")))
        self.assertFalse(os.path.exists(os.path.join(root, "openspec", "changes", "archive")))
        self.assertEqual(git(root, "status", "--porcelain").strip(), "")
        self.assertTrue(os.path.isfile(sp))


class NoiseTests(Env):
    def ignore_env(self, wt):
        excl = git(wt, "rev-parse", "--git-path", "info/exclude").strip()
        excl = excl if os.path.isabs(excl) else os.path.join(wt, excl)
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        with open(excl, "a") as fh:
            fh.write(".env\n.env.local\n.claude/\n.pytest_cache/\n.mcp.json\n.codegraph/\n.next/\n")

    def write(self, base, rel, text):
        full = os.path.join(base, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)

    def test_tool_caches_and_mcp_json_are_not_warned(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.ignore_env(wt)
        for rel in (".claude/settings.local.json", ".pytest_cache/v/x", ".mcp.json", ".codegraph/db",
                    ".next/cache/a"):
            self.write(wt, rel, "x")
        self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())

    def test_identical_copy_in_main_checkout_is_not_lost(self):
        root = self.plan_repo()
        wt = self.worktree()
        main = wt + "-main"
        self.ignore_env(wt)
        self.write(wt, ".env", "SECRET=1")
        self.write(main, ".env", "SECRET=1")
        self.write(wt, ".env.local", "A=2")
        self.write(main, ".env.local", "A=DIFFERENT")
        self.write(wt, "only-here.txt", "z")
        excl = git(wt, "rev-parse", "--git-path", "info/exclude").strip()
        excl = excl if os.path.isabs(excl) else os.path.join(wt, excl)
        with open(excl, "a") as fh:
            fh.write("only-here.txt\n")
        self.state(root, {"api": wt})
        code, _ = self.run_done(root)
        self.assertEqual(code, 1)
        text = self.text()
        self.assertIn(".env.local", text)
        self.assertIn("only-here.txt", text)
        self.assertNotIn("SECRET", text)
        lines = [l for l in self.out if l.strip().endswith(": .env")]
        self.assertEqual(lines, [])

    def test_quick_close_checks_develop_when_base_is_not_on_the_remote(self):
        main = os.path.join(self.tmp, "qm")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, "quick-x")
        git(main, "worktree", "add", "-q", "-b", "quick-x", path, "origin/develop")
        sha = git(path, "rev-parse", "HEAD").strip()
        self.write(path, ".rein/quick.json", json.dumps({"base": "ship/some-change", "base_sha": "0" * 40}))
        client = OrcaClient(executable="ORCA", runner=FakeOrca())
        code = ship_done.run_quick_close(path, client, cwd=self.tmp, say=self.say)
        self.assertEqual(code, 0, self.text())
        self.assertIn("origin/develop", self.text())
        # an unmerged commit still blocks under the fallback ref
        self.out.clear()
        self.write(path, ".rein/quick.json", json.dumps({"base": "ship/some-change", "base_sha": "0" * 40}))
        commit_file(path, "new.txt")
        code = ship_done.run_quick_close(path, client, cwd=self.tmp, say=self.say)
        self.assertEqual(code, 1)
        self.assertIn("not integrated", self.text())
        self.assertEqual(sha, git(main, "rev-parse", "origin/develop").strip())


class MergeIntegrationTests(Env):
    """Integration is judged by content: merging the tip into origin/<base> must change nothing."""

    def advance_develop(self, name="other.txt"):
        commit_file(self.seed, name, "o\n", "dev advances")
        git(self.seed, "push", "-q", "origin", "develop")

    def test_content_only_in_a_merge_commit_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.advance_develop()
        git(wt, "fetch", "-q", "origin")
        git(wt, "merge", "-q", "--no-ff", "--no-commit", "origin/develop")
        with open(os.path.join(wt, "precious.txt"), "w") as fh:
            fh.write("work only in the merge commit\n")
        git(wt, "add", "precious.txt")
        git(wt, "commit", "-q", "-m", "merge develop")
        self.assertEqual(git(wt, "cherry", "origin/develop", "HEAD").strip(), "")  # invisible to cherry
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("not integrated", self.text())

    def test_a_pure_sync_merge_is_integrated(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.advance_develop()
        git(wt, "fetch", "-q", "origin")
        git(wt, "merge", "-q", "--no-ff", "-m", "sync", "origin/develop")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])

    def test_base_merged_in_then_patch_landed_is_integrated(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "new.txt", "n\n", "feat")
        patch = git(wt, "format-patch", "-1", "--stdout")
        self.advance_develop()
        proc = subprocess.run(["git", "-C", self.seed, "am", "-q"], input=patch, text=True, capture_output=True,
                              env=dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        git(self.seed, "push", "-q", "origin", "develop")
        git(wt, "fetch", "-q", "origin")  # the base, already holding the patch, is merged in afterwards
        git(wt, "merge", "-q", "--no-ff", "-m", "sync", "origin/develop")
        self.assertIn("+", git(wt, "cherry", "origin/develop", "HEAD"))  # cherry alone would block
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])

    def test_a_squash_merge_is_integrated(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "a.txt", "a\n", "a")
        commit_file(wt, "b.txt", "b\n", "b")
        git(wt, "push", "-q", "origin", "feature")
        self.advance_develop()
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "merge", "-q", "--squash", "origin/feature")
        git(self.seed, "commit", "-q", "-m", "squash feature")
        git(self.seed, "push", "-q", "origin", "develop")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])

    def test_squash_then_more_work_blocks(self):
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "a.txt", "a\n", "a")
        git(wt, "push", "-q", "origin", "feature")
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "merge", "-q", "--squash", "origin/feature")
        git(self.seed, "commit", "-q", "-m", "squash feature")
        git(self.seed, "push", "-q", "origin", "develop")
        commit_file(wt, "a.txt", "later\n", "more")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])

    def test_quick_close_keeps_a_merge_only_branch(self):
        main = os.path.join(self.tmp, "qm")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, "quick-m")
        git(main, "worktree", "add", "-q", "-b", "quick-m", path, "origin/develop")
        sha = git(path, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(path, ".rein"))
        with open(os.path.join(path, ".rein", "quick.json"), "w") as fh:
            json.dump({"base": "develop", "base_sha": sha}, fh)
        self.advance_develop()
        git(path, "fetch", "-q", "origin")
        git(path, "merge", "-q", "--no-ff", "--no-commit", "origin/develop")
        with open(os.path.join(path, "precious.txt"), "w") as fh:
            fh.write("p\n")
        git(path, "add", "precious.txt")
        git(path, "commit", "-q", "-m", "merge develop")
        client = OrcaClient(executable="ORCA", runner=FakeOrca())
        code = ship_done.run_quick_close(path, client, cwd=self.tmp, say=self.say)
        self.assertEqual(code, 1, self.text())
        self.assertTrue(os.path.isfile(os.path.join(path, ".rein", "quick.json")))

    def no_merge_tree(self, path, *args):
        if args[:1] == ("merge-tree",):
            return 129, ""
        return ship_done.qd.run_git(path, *args)

    def test_without_merge_tree_merges_block_and_plain_commits_use_cherry(self):
        wt = self.worktree()
        commit_file(wt, "new.txt", "n\n", "feat")
        patch = git(wt, "format-patch", "-1", "--stdout")
        proc = subprocess.run(["git", "-C", self.seed, "am", "-q"], input=patch, text=True, capture_output=True,
                              env=dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        git(self.seed, "push", "-q", "origin", "develop")
        why = ship_done.check_worktree(wt, "develop", self.no_merge_tree, ship_done.qd.run_fetch, branch="feature")
        self.assertEqual(why, "")
        self.advance_develop("more.txt")
        git(wt, "fetch", "-q", "origin")
        git(wt, "merge", "-q", "--no-ff", "-m", "sync", "origin/develop")
        why = ship_done.check_worktree(wt, "develop", self.no_merge_tree, ship_done.qd.run_fetch, branch="feature")
        self.assertIn("merge", why)


    def write(self, path, name, text):
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def indent_twin(self):
        """Review I2: upstream holds the same patch as the branch except its indentation."""
        wt = self.worktree()
        self.write(self.seed, "p.py", "def f():\n    return 1\n")
        git(self.seed, "add", "p.py")
        git(self.seed, "commit", "-q", "-m", "p")
        git(self.seed, "push", "-q", "origin", "develop")
        git(wt, "fetch", "-q", "origin")
        git(wt, "merge", "-q", "--ff-only", "origin/develop")
        self.write(wt, "p.py", "def f():\n    if x:\n        return 2\n    return 1\n")
        git(wt, "commit", "-q", "-am", "good")
        self.write(self.seed, "p.py", "def f():\n    if x:\n    return 2\n    return 1\n")
        git(self.seed, "commit", "-q", "-am", "bad indent")
        git(self.seed, "push", "-q", "origin", "develop")
        git(wt, "fetch", "-q", "origin")
        self.assertTrue(git(wt, "cherry", "origin/develop", "HEAD").startswith("-"))  # cherry says "in there"
        return wt

    def test_merge_tree_differs_is_never_overridden_by_cherry(self):
        wt = self.indent_twin()
        why = ship_done.check_worktree(wt, "develop", ship_done.qd.run_git, ship_done.qd.run_fetch, branch="feature")
        self.assertIn("content differs", why)

    def test_without_merge_tree_cherry_equivalence_must_be_verbatim(self):
        wt = self.indent_twin()
        why = ship_done.check_worktree(wt, "develop", self.no_merge_tree, ship_done.qd.run_fetch, branch="feature")
        self.assertIn("not integrated", why)

    def test_squash_then_upstream_edit_reports_content_differs_with_diffstat(self):
        # Review M2: safe to block, but say why and what differs so the owner can decide.
        root = self.plan_repo()
        wt = self.worktree()
        commit_file(wt, "c.txt", "c1\n", "c1")
        commit_file(wt, "c.txt", "c2\n", "c2")
        git(wt, "push", "-q", "origin", "feature")
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "merge", "-q", "--squash", "origin/feature")
        git(self.seed, "commit", "-q", "-m", "squash feature")
        self.write(self.seed, "c.txt", "c1-edited\nc2\n")
        git(self.seed, "commit", "-q", "-am", "edit")
        git(self.seed, "push", "-q", "origin", "develop")
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("content differs from origin/develop", self.text())
        self.assertIn("c.txt", self.text())
        self.assertIn("verify by hand", self.text())


class WarningScopeTests(QuickCloseBase):
    def exclude(self, path, *patterns):
        excl = git(path, "rev-parse", "--git-path", "info/exclude").strip()
        excl = excl if os.path.isabs(excl) else os.path.join(path, excl)
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        with open(excl, "a") as fh:
            fh.write("".join(p + "\n" for p in patterns))

    def put(self, path, name, text="S=1"):
        with open(os.path.join(path, name), "w") as fh:
            fh.write(text)

    def test_here_record_with_ignored_env_needs_no_yes(self):
        main = os.path.join(self.tmp, "main")
        self.clone(self.origin, main)
        git(main, "checkout", "-q", "develop")
        self.record(main)
        self.exclude(main, ".env", ".rein/")
        self.put(main, ".env")
        code, orca = self.close(arg=main)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())
        self.assertFalse(os.path.exists(os.path.join(main, ".rein", "quick.json")))
        self.assertTrue(os.path.isfile(os.path.join(main, ".env")))

    def test_warnings_of_a_kept_target_do_not_block_others(self):
        a, _ = self.linked("a")
        c, _ = self.linked("c")
        for p in (a, c):
            self.record(p)
        commit_file(a, "new.txt")  # a is not integrated
        self.exclude(a, ".env")
        self.put(a, ".env")
        orca = FakeOrca(worktrees=[{"path": a}, {"path": c}])
        code, orca = self.close(orca=orca)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [c])
        self.assertNotIn("WARNING", self.text())

    def test_a_target_with_ignored_files_is_kept_without_yes_others_close(self):
        a, _ = self.linked("a")
        c, _ = self.linked("c")
        for p in (a, c):
            self.record(p)
        self.exclude(a, ".env")
        self.put(a, ".env")
        orca = FakeOrca(worktrees=[{"path": a}, {"path": c}])
        code, orca = self.close(orca=orca)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [c])
        self.assertIn("WARNING", self.text())
        self.assertIn("--yes", self.text())
        self.assertTrue(os.path.isfile(os.path.join(a, ".rein", "quick.json")))

    def test_os_junk_is_not_warned(self):
        a, _ = self.linked("a")
        self.record(a)
        self.exclude(a, ".DS_Store", "Thumbs.db", "desktop.ini")
        for name in (".DS_Store", "Thumbs.db", "desktop.ini"):
            self.put(a, name, "junk")
        code, orca = self.close(arg=a)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())

    def test_non_ascii_copy_in_main_checkout_is_not_warned(self):
        a, main = self.linked("a")
        self.record(a)
        self.exclude(a, "*.env")
        self.put(a, "caf\u00e9.env")
        self.put(main, "caf\u00e9.env")
        code, orca = self.close(arg=a)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())


class ArchiveGuardTests(Env):
    def test_own_tab_is_left_open(self):
        root = self.plan_repo()
        wt = self.worktree()
        self.state(root, {"api": wt})
        orca = FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}],
                        terminals=[{"handle": "t1", "title": "rein ship demo", "worktreePath": root}])
        code, orca = self.run_done(root, orca, env={"ORCA_TERMINAL_HANDLE": "t1"})
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.closed(), [])
        self.assertIn("left the tab", self.text())
        self.assertFalse(os.path.exists(ship_state.state_path(root, CHANGE)))

    def test_plan_root_on_any_branch_archives_there_and_names_it(self):
        # Owner decision (review I1): plan roots live on develop or on feature worktrees.
        root = self.plan_repo()
        before = git(root, "rev-parse", "HEAD").strip()
        git(root, "checkout", "-q", "-b", "topic")
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [wt])
        self.assertIn("committed on topic", self.text())
        self.assertEqual(git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(), "topic")
        self.assertEqual(git(root, "log", "-1", "--format=%s").strip(), "docs(openspec): archive %s" % CHANGE)
        self.assertNotEqual(git(root, "rev-parse", "HEAD").strip(), before)
        self.assertFalse(os.path.isdir(os.path.join(root, "openspec", "changes", CHANGE)))

    def test_untracked_file_in_the_plan_folder_blocks(self):
        root = self.plan_repo()
        with open(os.path.join(root, "openspec", "changes", CHANGE, "notes.md"), "w") as fh:
            fh.write("draft\n")
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("uncommitted", self.text())

    def test_uncommitted_plan_edits_block(self):
        root = self.plan_repo()
        with open(os.path.join(root, "openspec", "changes", CHANGE, "proposal.md"), "a") as fh:
            fh.write("- [x] done\n")
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("uncommitted", self.text())
        self.assertEqual(git(root, "log", "-1", "--format=%s").strip(), "ignore")

    def test_plan_root_mid_merge_blocks(self):
        root = self.plan_repo()
        head = git(root, "rev-parse", "HEAD").strip()
        with open(os.path.join(root, ".git", "MERGE_HEAD"), "w") as fh:
            fh.write(head + "\n")
        wt = self.worktree()
        self.state(root, {"api": wt})
        code, orca = self.run_done(root)
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("merge", self.text())


class QuickCloseSafetyTests(QuickCloseBase):
    def test_non_quick_linked_worktree_only_loses_its_record(self):
        main = os.path.join(self.tmp, "m")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, "other")
        git(main, "worktree", "add", "-q", "-b", "feature-x", path, "origin/develop")
        self.record(path)
        code, orca = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [])
        self.assertFalse(os.path.exists(os.path.join(path, ".rein", "quick.json")))
        self.assertTrue(os.path.isdir(path))

    def test_orca_main_flag_never_removed(self):
        path, _ = self.linked("fix")
        self.record(path)
        orca = FakeOrca(worktrees=[{"path": path, "branch": "quick-fix", "isMainWorktree": True}])
        code, orca = self.close(arg=path, orca=orca)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.rm_paths(), [])

    def test_branch_orca_deletes_is_checked(self):
        path, _ = self.linked("fix")
        self.record(path)
        commit_file(path, "new.txt")
        git(path, "checkout", "-q", "--detach", "origin/develop")
        orca = FakeOrca(worktrees=[{"path": path, "branch": "quick-fix"}])
        code, orca = self.close(arg=path, orca=orca)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])

    def test_detached_without_branch_blocks(self):
        path, _ = self.linked("fix")
        self.record(path)
        git(path, "checkout", "-q", "--detach")
        code, orca = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])

    def test_ignored_env_needs_yes(self):
        path, _ = self.linked("fix")
        self.record(path)
        excl = git(path, "rev-parse", "--git-path", "info/exclude").strip()
        excl = excl if os.path.isabs(excl) else os.path.join(path, excl)
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        with open(excl, "a") as fh:
            fh.write(".env\n")
        with open(os.path.join(path, ".env"), "w") as fh:
            fh.write("S=1")
        code, orca = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("--yes", self.text())
        code, orca = self.close(arg=path, yes=True)
        self.assertEqual(code, 0, self.text())

    def test_close_word_in_a_sentence_is_not_the_subcommand(self):
        rein = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
        proc = subprocess.run([sys.executable, rein, "quick", "close", "the", "modal"],
                              capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('rein quick "<sentence>"', proc.stdout)


if __name__ == "__main__":
    unittest.main()


class DonePushTests(Env):
    """`rein done --push` (opt-in) pushes the plan-archive commit to the current branch; without it,
    nothing is pushed."""

    def run_push(self, root, push=True, dry=False):
        orca = FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}])
        client = OrcaClient(executable="ORCA", runner=orca)
        return ship_done.run_done(root, CHANGE, client, dry_run=dry, push=push, driver_alive=lambda c: False,
                                  today="2026-10-20", say=self.say, env={})

    def remote_head(self):
        return git(self.origin, "rev-parse", "develop").strip()

    def test_push_sends_the_archive_commit_to_the_current_branch(self):
        root = self.plan_repo()
        self.state(root, {"api": self.worktree()})
        self.assertEqual(self.run_push(root), 0, self.text())
        self.assertEqual(self.remote_head(), git(root, "rev-parse", "HEAD").strip())
        self.assertIn("pushed", self.text())

    def test_without_push_nothing_is_pushed(self):
        root = self.plan_repo()
        before = self.remote_head()
        self.state(root, {"api": self.worktree()})
        self.assertEqual(self.run_push(root, push=False), 0, self.text())
        self.assertEqual(self.remote_head(), before)

    def test_dry_run_pushes_nothing(self):
        root = self.plan_repo()
        before = self.remote_head()
        self.state(root, {"api": self.worktree()})
        self.assertEqual(self.run_push(root, dry=True), 0, self.text())
        self.assertEqual(self.remote_head(), before)
        self.assertIn("would push", self.text())

    def test_a_rejected_push_keeps_the_local_commit_and_says_so(self):
        root = self.plan_repo()
        commit_file(self.seed, "other", "x\n", "someone else pushed")
        git(self.seed, "push", "-q", "origin", "develop")  # the plan repo is now behind: non-fast-forward
        self.state(root, {"api": self.worktree()})
        self.assertEqual(self.run_push(root), 126, self.text())
        self.assertEqual(git(root, "log", "-1", "--format=%s").strip(), "docs(openspec): archive %s" % CHANGE)
        self.assertIn("could not push", self.text())
        self.assertIn("done: %s closed" % CHANGE, self.text())

    def test_the_cli_takes_push(self):
        import importlib.machinery
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "plugins", "rein", "bin", "rein")
        spec = importlib.util.spec_from_loader("rein_cli_push", importlib.machinery.SourceFileLoader("rein_cli_push", path))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        seen = {}
        saved = cli._done.run_done
        self.addCleanup(setattr, cli._done, "run_done", saved)
        cli._done.run_done = lambda root, change, client, **kw: seen.update(kw) or 0
        self.assertEqual(cli.cmd_done([CHANGE, "--push"]), 0)
        self.assertTrue(seen.get("push"))
        self.assertEqual(cli.cmd_done([CHANGE]), 0)
        self.assertFalse(seen.get("push"))
