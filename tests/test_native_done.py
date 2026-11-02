"""Native closure uses only throwaway git repositories and a simulated Orca runtime."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from test_ship_done import Env, FakeOrca, CHANGE, commit_file, git
import ship_done as done
from orca_client import OrcaClient


class RemovingOrca(FakeOrca):
    """Unlike the old test double, actually removes git worktrees in the temporary fixture."""
    truncated = False
    agents = []
    unverifiable = 0
    after_remove = None

    def __call__(self, argv):
        if argv[1:3] == ["worktree", "ps"]:
            self.calls.append(list(argv))
            rows = [dict(r, hostId="local", agents=self.agents, liveTerminalCount=0,
                         hasAttachedPty=False, unverifiableTerminalCount=self.unverifiable)
                    for r in self.worktrees if os.path.isdir(r["path"])]
            return 0, json.dumps({"ok": True, "result": {"worktrees": rows,
                "hostScope": {"hostIds": ["local"]}, "truncated": self.truncated}})
        result = super().__call__(argv)
        if argv[1:3] == ["worktree", "rm"] and result[0] == 0:
            path = argv[4][5:]
            git(path, "worktree", "remove", path)
            if self.after_remove:
                self.after_remove(path)
        return result


class NativeDone(Env):
    def setup_native(self, count=1, openspec=False):
        self.plan = self.plan_repo(openspec)
        targets, rows, repos = [], [], []
        for i in range(count):
            wt = self.worktree("wt%s" % i)
            main = wt + "-main"
            repo = "repo-id-%s" % i
            head = git(wt, "rev-parse", "HEAD").strip()
            evidence = os.path.join(self.tmp, "gate-%s.txt" % i)
            with open(evidence, "w") as fh:
                fh.write("existing gate + functional QA passed for " + head)
            review_root = os.path.join(self.tmp, "reviews", repo)
            done._gate.record_review(review_root, CHANGE, "APPROVED", [os.path.join(wt, "README")], [], "independent-reviewer")
            targets.append(dict(repo=repo, repo_path=main, path=wt, branch="feature",
                                integration="origin/develop", head=head, review_root=review_root,
                                validation=[dict(path=evidence, sha256=done._digest(evidence),
                                                 head=head, result="passed")]))
            repos.append(dict(id=repo, path=main, displayName="similar-%s" % i))
            rows.append(dict(repoId=repo, path=wt, branch="refs/heads/feature", isMainWorktree=False))
        self.manifest = os.path.join(self.tmp, "native.json")
        self.data = dict(schema=1, change=CHANGE, execution="finished", worktrees=targets)
        self.write_manifest()
        self.orca = RemovingOrca(repos=repos, worktrees=rows)
        self.journal = os.path.join(self.plan, ".rein", "done", CHANGE + ".json")
        return targets

    def write_manifest(self):
        with open(self.manifest, "w") as fh:
            json.dump(self.data, fh)

    def close(self, **kw):
        self.write_manifest()
        return done.run_done(self.plan, CHANGE, OrcaClient(executable="ORCA", runner=self.orca),
                             native=self.manifest, say=self.say, today="2026-11-01", **kw)

    def blocked(self, needle, **kw):
        self.assertEqual(self.close(**kw), 1, self.text())
        self.assertEqual(self.orca.rm_paths(), [])
        self.assertIn(needle, self.text())
        self.assertFalse(os.path.exists(self.journal))

    def test_single_repo_closes_without_ship_or_quick_state_and_is_idempotent(self):
        t, = self.setup_native()
        before = git(self.origin, "rev-parse", "develop")
        with patch.object(done._gate_env, "run_cleanup") as cleanup:
            self.assertEqual(self.close(), 0, self.text())
            self.assertEqual(self.close(), 0, self.text())
            cleanup.assert_not_called()
        self.assertFalse(os.path.exists(t["path"]))
        self.assertEqual(self.orca.rm_paths(), [t["path"]])
        self.assertEqual(git(self.origin, "rev-parse", "develop"), before)
        self.assertFalse(os.path.exists(done.ss.state_path(self.plan, CHANGE)))
        with open(self.journal) as fh:
            journal = json.load(fh)
        self.assertTrue(journal["complete"])
        self.assertTrue(journal["targets"][t["path"]]["cleaned"])

    def test_multirepo_integrated_commits_with_explicit_different_base_and_plan_archive(self):
        a, b = self.setup_native(2, openspec=True)
        # A second origin and a different integration branch: repo membership is exact, not a name.
        second = os.path.join(self.tmp, "second.git")
        import subprocess
        subprocess.run(["git", "clone", "-q", "--bare", self.origin, second], check=True)
        git(b["path"], "remote", "set-url", "origin", second)
        for t, base in ((a, "develop"), (b, "release/next")):
            wt = t["path"]
            commit_file(wt, "feature.txt", t["repo"])
            # Integration is setup owned by the test, never done by rein done.
            git(wt, "push", "-q", "origin", "HEAD:refs/heads/" + base)
            t["head"] = git(wt, "rev-parse", "HEAD").strip()
            t["integration"] = "origin/" + base
            t["validation"][0]["head"] = t["head"]
            done._gate.record_review(t["review_root"], CHANGE, "APPROVED",
                                     [os.path.join(wt, p) for p in ("README", "feature.txt")], [], "reviewer")
        extra = self.worktree("wt0-similar-name")
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(self.orca.rm_paths(), [a["path"], b["path"]])
        self.assertTrue(os.path.isdir(extra))
        self.assertTrue(os.path.isdir(os.path.join(self.plan, "openspec", "changes", "archive", "2026-11-01-demo")))

    def test_dry_run_reports_all_actions_without_writes_or_qa(self):
        t, = self.setup_native(openspec=True)
        head = git(self.plan, "rev-parse", "HEAD")
        with patch.object(done._gate_env, "run_cleanup", side_effect=AssertionError("ran cleanup")):
            self.assertEqual(self.close(dry_run=True), 0, self.text())
        self.assertTrue(os.path.isdir(t["path"]))
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual(self.orca.rm_paths(), [])
        self.assertEqual(git(self.plan, "rev-parse", "HEAD"), head)
        self.assertIn("would archive", self.text())
        self.assertIn("evidence reused", self.text())

    def test_dirty_one_blocks_all(self):
        a, b = self.setup_native(2)
        with open(os.path.join(b["path"], "scratch"), "w") as fh:
            fh.write("uncommitted")
        self.blocked("uncommitted")

    def test_unintegrated_work_is_kept_even_with_yes(self):
        t, = self.setup_native()
        commit_file(t["path"], "new")
        t["head"] = git(t["path"], "rev-parse", "HEAD").strip()
        t["validation"][0]["head"] = t["head"]
        self.blocked("not integrated", yes=True)

    def test_stale_validation_head(self):
        t, = self.setup_native()
        commit_file(t["path"], "new")
        self.blocked("HEAD differs")

    def test_changed_validation_artifact(self):
        t, = self.setup_native()
        with open(t["validation"][0]["path"], "a") as fh:
            fh.write("edited")
        self.blocked("validation evidence changed")

    def test_missing_validation(self):
        t, = self.setup_native()
        t["validation"] = []
        self.blocked("validation")

    def test_failed_validation_is_not_a_pass(self):
        t, = self.setup_native()
        t["validation"][0]["result"] = "failed"
        self.blocked("result=passed")

    def test_evidence_for_another_head_is_refused(self):
        t, = self.setup_native()
        t["validation"][0]["head"] = "f" * 40
        self.blocked("matching head")

    def test_unapproved_review(self):
        t, = self.setup_native()
        done._gate.record_review(t["review_root"], CHANGE, "CHANGES_REQUESTED", [os.path.join(t["path"], "README")],
                                ["BLOCKING: README:1 expected: x observed: y evidence: z breaks: closure"], "reviewer")
        self.blocked("not current/approved")

    def test_stale_review(self):
        t, = self.setup_native()
        commit_file(t["path"], "README", "edit")
        t["head"] = git(t["path"], "rev-parse", "HEAD").strip()
        t["validation"][0]["head"] = t["head"]
        self.blocked("reviewed state has changed")

    def test_wrong_repo_id_is_not_inferred_from_similar_name(self):
        t, = self.setup_native()
        t["repo"] = "similar-0"
        self.blocked("do not match Orca")

    def test_wrong_main_repo_path(self):
        t, = self.setup_native()
        t["repo_path"] = self.seed
        self.blocked("do not match Orca")

    def test_wrong_branch(self):
        t, = self.setup_native()
        t["branch"] = "feature-alike"
        self.blocked("branch does not match")

    def test_main_checkout_refused(self):
        t, = self.setup_native()
        t["path"] = t["repo_path"]
        self.orca.worktrees[0]["path"] = t["path"]
        self.blocked("main checkout")

    def test_missing_worktree_without_journal_is_not_completion(self):
        t, = self.setup_native()
        git(t["path"], "worktree", "remove", t["path"])
        self.blocked("missing without a recorded removal")

    def test_active_terminal_even_with_yes(self):
        self.setup_native()
        self.orca.terminals = [{"handle": "active"}]
        self.blocked("terminal sessions", yes=True)

    def test_structured_agent_without_terminal(self):
        self.setup_native()
        self.orca.agents = [{"state": "working", "hasLivePty": False}]
        self.blocked("agent sessions")

    def test_incomplete_session_inventory(self):
        self.setup_native()
        self.orca.truncated = True
        self.blocked("inventory is incomplete")

    def test_unverifiable_process(self):
        self.setup_native()
        self.orca.unverifiable = 1
        self.blocked("unverifiable processes")

    def test_missing_remote_base_has_no_fallback(self):
        t, = self.setup_native()
        t["integration"] = "origin/not-a-branch"
        self.blocked("cannot fetch origin/not-a-branch")

    def test_execution_must_be_finished(self):
        self.setup_native()
        self.data["execution"] = "running"
        self.blocked("execution is not finished")

    def test_ship_unapproved_is_not_approval(self):
        t, = self.setup_native()
        state_path = self.state(self.plan, {"api": t["path"]})
        state = done._load_state(state_path)
        state["audit_result"] = "unapproved"
        done._cache._write(state_path, state)
        self.assertEqual(done.run_done(self.plan, CHANGE, OrcaClient(runner=self.orca), say=self.say), 1)
        self.assertIn("not approved", self.text())
        self.assertEqual(self.orca.rm_paths(), [])
        self.blocked("Ship state exists")

    def test_local_ignored_files_need_yes_and_dry_run_lists_them(self):
        t, = self.setup_native()
        exclude = git(t["path"], "rev-parse", "--git-path", "info/exclude").strip()
        with open(exclude, "a") as fh:
            fh.write(".env\n")
        with open(os.path.join(t["path"], ".env"), "w") as fh:
            fh.write("local data")
        self.assertEqual(self.close(dry_run=True), 0, self.text())
        self.assertIn(".env", self.text())
        self.blocked("--yes")
        self.assertEqual(self.close(yes=True), 0, self.text())

    def test_failed_remove_retries_remaining_targets(self):
        a, b = self.setup_native(2)
        self.orca.fail_rm = (b["path"],)
        self.assertEqual(self.close(), 126, self.text())
        self.assertFalse(os.path.exists(a["path"]))
        self.assertTrue(os.path.exists(b["path"]))
        self.orca.fail_rm = ()
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(self.orca.rm_paths().count(a["path"]), 1)

    def test_cleanup_failure_is_pending_and_resumable_without_removed_worktree(self):
        t, = self.setup_native()
        with patch.object(done, "_cleanup_of", return_value=("cleanup", t["repo_path"], {})), \
             patch.object(done._gate_env, "run_cleanup", side_effect=[(3, "failed"), (0, "ok")]) as cleanup:
            self.assertEqual(self.close(), 126, self.text())
            self.assertFalse(os.path.exists(t["path"]))
            self.assertFalse(done._load_state(self.journal)["complete"])
            self.assertEqual(self.close(), 0, self.text())
            self.assertEqual(self.close(), 0, self.text())
            self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(len(self.orca.rm_paths()), 1)

    def test_manifest_change_during_retry_is_refused(self):
        t, = self.setup_native()
        self.orca.fail_rm = (t["path"],)
        self.assertEqual(self.close(), 126, self.text())
        t["branch"] = "other"
        self.orca.calls.clear()
        self.assertEqual(self.close(), 1, self.text())
        self.assertIn("manifest changed", self.text())
        self.assertEqual(self.orca.rm_paths(), [])

    def test_crash_after_remove_before_ack_is_recovered(self):
        t, = self.setup_native()
        original = done._cache._write
        def write(path, data):
            if path == self.journal and data["targets"][t["path"]].get("removed"):
                raise OSError("crash after rm")
            original(path, data)
        with patch.object(done._cache, "_write", side_effect=write):
            self.assertEqual(self.close(), 126, self.text())
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(len(self.orca.rm_paths()), 1)

    def test_archive_failure_retries_after_worktrees_removed(self):
        self.setup_native(openspec=True)
        with patch.object(done, "_archive_plan", return_value=False):
            self.assertEqual(self.close(), 1, self.text())
        self.assertEqual(self.close(), 0, self.text())

    def test_crash_after_archive_commit_is_recovered(self):
        self.setup_native(openspec=True)
        original = done._cache._write
        def write(path, data):
            if path == self.journal and data.get("archived"):
                raise OSError("crash after commit")
            original(path, data)
        with patch.object(done._cache, "_write", side_effect=write):
            self.assertEqual(self.close(), 126, self.text())
        head = git(self.plan, "rev-parse", "HEAD")
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(git(self.plan, "rev-parse", "HEAD"), head)

    def test_recreated_removed_path_is_never_deleted(self):
        a, b = self.setup_native(2)
        self.orca.fail_rm = (b["path"],)
        self.assertEqual(self.close(), 126, self.text())
        os.mkdir(a["path"])
        self.orca.calls.clear()
        self.assertEqual(self.close(), 1, self.text())
        self.assertIn("recreated", self.text())
        self.assertEqual(self.orca.rm_paths(), [])

    def test_changed_review_on_remaining_target_blocks_recovery(self):
        a, b = self.setup_native(2)
        self.orca.fail_rm = (b["path"],)
        self.assertEqual(self.close(), 126, self.text())
        episode = done._gate.latest_episode(b["review_root"], CHANGE)
        with open(episode["path"], "a") as fh:
            fh.write(" ")
        self.orca.calls.clear()
        self.assertEqual(self.close(), 1, self.text())
        self.assertIn("episode changed", self.text())
        self.assertEqual(self.orca.rm_paths(), [])

    def test_inside_target_root_refused(self):
        t, = self.setup_native()
        self.plan = t["path"]
        self.blocked("outside the target")

    def test_existing_native_lock_blocks_concurrent_close(self):
        t, = self.setup_native()
        with done._native_locks(self.plan, [t]):
            self.blocked("another native closure")

    def test_cleanup_command_and_env_really_run_after_removal(self):
        t, = self.setup_native()
        marker = os.path.join(self.tmp, "cleaned")
        command = "test ! -d '%s' && printf '%%s' \"$TASK_REPO\" > '%s'" % (t["path"], marker)
        with patch.object(done, "_cleanup_of", return_value=(command, t["repo_path"], {"TASK_REPO": "api"})):
            self.assertEqual(self.close(), 0, self.text())
        with open(marker) as fh:
            self.assertEqual(fh.read(), "api")

    def test_removed_ack_failure_does_not_overwrite_captured_cleanup(self):
        t, = self.setup_native()
        self.orca.fail_rm = (t["path"],)
        with patch.object(done, "_cleanup_of", return_value=("original", t["repo_path"], {})):
            self.assertEqual(self.close(), 126, self.text())
        self.orca.fail_rm = ()
        with patch.object(done, "_cleanup_of", side_effect=AssertionError("must reuse cleanup")), \
             patch.object(done._gate_env, "run_cleanup", return_value=(0, "")) as cleanup:
            self.assertEqual(self.close(), 0, self.text())
            self.assertEqual(cleanup.call_args.args[0], "original")

    def test_wrong_git_repository_despite_matching_orca_inventory(self):
        t, = self.setup_native()
        t["repo_path"] = self.seed
        self.orca.repos[0]["path"] = self.seed
        self.blocked("different git repository")

    def test_review_of_unrelated_files_is_not_approval_for_this_repo(self):
        t, = self.setup_native()
        done._gate.record_review(t["review_root"], CHANGE, "APPROVED",
                                 [os.path.join(self.seed, "README")], [], "independent-reviewer")
        self.blocked("does not cover this worktree")

    def test_duplicate_targets_refused(self):
        t, = self.setup_native()
        self.data["worktrees"].append(t)
        self.blocked("duplicate worktree")

    def test_missing_orca_inventory_fails_closed(self):
        self.setup_native()
        self.orca.worktrees = []
        self.blocked("do not match Orca")

    def test_malformed_journal_is_preserved(self):
        self.setup_native()
        os.makedirs(os.path.dirname(self.journal))
        with open(self.journal, "w") as fh:
            fh.write("broken json")
        self.assertEqual(self.close(), 1, self.text())
        self.assertIn("journal is unreadable", self.text())
        with open(self.journal) as fh:
            self.assertEqual(fh.read(), "broken json")

    def test_new_rejection_after_first_removal_keeps_second_worktree(self):
        a, b = self.setup_native(2)
        def reject(path):
            if path == a["path"]:
                done._gate.record_review(b["review_root"], CHANGE, "CHANGES_REQUESTED",
                    [os.path.join(b["path"], "README")],
                    ["BLOCKING: README:1 expected: x observed: y evidence: test breaks: closure"], "reviewer")
        self.orca.after_remove = reject
        self.assertEqual(self.close(), 1, self.text())
        self.assertEqual(self.orca.rm_paths(), [a["path"]])
        self.assertTrue(os.path.isdir(b["path"]))
        self.assertFalse(done._load_state(self.journal)["complete"])

    def test_evidence_changed_after_first_removal_keeps_second_worktree(self):
        a, b = self.setup_native(2)
        def change(path):
            if path == a["path"]:
                with open(b["validation"][0]["path"], "a") as fh:
                    fh.write("edited")
        self.orca.after_remove = change
        self.assertEqual(self.close(), 1, self.text())
        self.assertEqual(self.orca.rm_paths(), [a["path"]])
        self.assertTrue(os.path.isdir(b["path"]))

    def test_shared_multirepo_review_survives_partial_removal(self):
        a, b = self.setup_native(2)
        for t in (a, b):
            t["review_root"] = self.plan
        done._gate.record_review(self.plan, CHANGE, "APPROVED",
            [os.path.join(t["path"], "README") for t in (a, b)], [], "reviewer")
        self.orca.fail_rm = (b["path"],)
        self.assertEqual(self.close(), 126, self.text())
        self.orca.fail_rm = ()
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(self.orca.rm_paths().count(a["path"]), 1)

    def test_cleanup_uses_native_gate_repo_name_not_orca_id(self):
        t, = self.setup_native()
        self.assert_cleanup_resource(t, "")

    def test_cleanup_reuses_explicit_native_gate_repo_name(self):
        t, = self.setup_native()
        t["gate_repo"] = "api-gate-name"
        self.assert_cleanup_resource(t, t["gate_repo"])

    def assert_cleanup_resource(self, t, gate_repo):
        config = {"gate": {"env": {"RESOURCE": self.tmp + "/resource_{repo}_{worktree}"},
                           "cleanup": 'rm -f "$RESOURCE"'}}
        with open(os.path.join(self.plan, "flow.config.json"), "w") as fh:
            json.dump(config, fh)
        env = done._gate_env.for_worktree(t["path"], repo=gate_repo, env_root=self.plan)
        resource = env["RESOURCE"]
        with open(resource, "w") as fh:
            fh.write("created by gate")
        self.assertNotIn(t["repo"], resource)
        self.assertEqual(self.close(), 0, self.text())
        self.assertFalse(os.path.exists(resource), "cleanup must remove the resource the native gate created")

    def test_interrupted_staged_archive_move_recovers_without_touching_other_staged_files(self):
        self.setup_native(openspec=True)
        commit_file(self.plan, "unrelated", "old")
        with open(os.path.join(self.plan, "unrelated"), "a") as fh:
            fh.write("staged change")
        git(self.plan, "add", "unrelated")
        def interrupted(root, change, src, dst, run_git):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            git(root, "mv", src, dst)
            raise OSError("interrupted between mv and commit")
        with patch.object(done, "_archive_plan", side_effect=interrupted):
            self.assertEqual(self.close(), 126, self.text())
        before = git(self.plan, "status", "--porcelain")
        self.assertEqual(self.close(dry_run=True), 0, self.text())
        self.assertEqual(git(self.plan, "status", "--porcelain"), before)
        self.assertIn("recover the exact interrupted archive move", self.text())
        self.assertEqual(self.close(), 0, self.text())
        self.assertEqual(git(self.plan, "diff", "--cached", "--name-only").strip(), "unrelated")
        self.assertEqual(git(self.plan, "show", "HEAD:unrelated"), "old")

    def test_changed_interrupted_archive_move_is_preserved(self):
        self.setup_native(openspec=True)
        def interrupted(root, change, src, dst, run_git):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            git(root, "mv", src, dst)
            with open(os.path.join(dst, "proposal.md"), "a") as fh:
                fh.write("new local change")
            raise OSError("crash")
        with patch.object(done, "_archive_plan", side_effect=interrupted):
            self.assertEqual(self.close(), 126, self.text())
        before = git(self.plan, "status", "--porcelain")
        self.assertEqual(self.close(), 1, self.text())
        self.assertEqual(git(self.plan, "status", "--porcelain"), before)
        self.assertIn("preserve journal", self.text())

    def test_journal_fsync_failure_prevents_removal(self):
        self.setup_native()
        with patch.object(done.os, "fsync", side_effect=OSError("fsync failed")):
            self.assertEqual(self.close(), 126, self.text())
        self.assertEqual(self.orca.rm_paths(), [])
        self.assertIn("fsync failed", self.text())


class NativeCLI(unittest.TestCase):
    def test_native_flag_and_invalid_combinations(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins", "rein", "bin", "rein")
        spec = importlib.util.spec_from_loader("native_cli", importlib.machinery.SourceFileLoader("native_cli", path))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli._done, "run_done", return_value=0) as run:
            self.assertEqual(cli.cmd_done(["demo", "--native", "/x/native.json", "--dry-run"]), 0)
            self.assertEqual(run.call_args.kwargs["native"], "/x/native.json")
            self.assertTrue(run.call_args.kwargs["dry_run"])
            for args in (["demo", "--native"], ["demo", "--native=x", "--native=y"],
                         ["demo", "--native=x", "--push"]):
                self.assertEqual(cli.cmd_done(args), 2)


if __name__ == "__main__":
    unittest.main()
