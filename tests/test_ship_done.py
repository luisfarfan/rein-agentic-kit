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
        path = os.path.join(self.tmp, name)
        self.clone(self.origin, path)
        git(path, "checkout", "-q", "-b", "feature", "origin/develop")
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

    def run_done(self, root, orca=None, dry=False, alive=False):
        orca = orca or FakeOrca(repos=[{"displayName": "api", "worktreeBaseRef": "origin/develop"}])
        client = OrcaClient(executable="ORCA", runner=orca)
        code = ship_done.run_done(root, CHANGE, client, dry_run=dry, driver_alive=lambda c: alive,
                                  today="2026-10-20", say=self.say)
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
        self.clone(self.origin, path)
        self.state(root, {"api": path})
        orca = FakeOrca(repos=[])  # no worktreeBaseRef known
        code, _ = self.run_done(root, orca)
        self.assertEqual(code, 0, self.text())


class QuickCloseTests(Env):
    def record(self, workspace, base="develop", at_base=True):
        sha = git(workspace, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(workspace, ".rein"), exist_ok=True)
        with open(os.path.join(workspace, ".rein", "quick.json"), "w") as fh:
            json.dump({"repo": "api", "workspace": workspace, "base": base, "base_sha": sha}, fh)

    def quick_wt(self, name="quick-fix"):
        path = self.worktree(name)
        # a linked worktree has a `.git` FILE; emulate that so "not --here" is recognised
        return path

    def close(self, arg="", cwd=None, orca=None, dry=False):
        orca = orca or FakeOrca()
        client = OrcaClient(executable="ORCA", runner=orca)
        code = ship_done.run_quick_close(arg, client, cwd=cwd or self.tmp, dry_run=dry, say=self.say)
        return code, orca

    def linked(self, name):
        """A real linked worktree of a clone, like Orca makes."""
        main = os.path.join(self.tmp, name + "-main")
        self.clone(self.origin, main)
        path = os.path.join(self.tmp, name)
        git(main, "worktree", "add", "-q", "-b", "quick-" + name, path, "origin/develop")
        return path, main

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
        self.record(path, base="nonexistent-but-unused")
        # base cannot be fetched, but HEAD == base_sha: nothing to keep... still must fetch to be sure
        code, _ = self.close(arg=path)
        self.assertEqual(code, 1)  # an unverifiable base blocks even here
        self.out.clear()
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


if __name__ == "__main__":
    unittest.main()
