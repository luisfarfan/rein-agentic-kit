"""Round 5 (retest 2026-10-22), through the real consumers.

A  the outside guard watches the checkouts of the task (a quick's repo, a plan's repos, and the
   siblings `gate.outsideWatch` declares), never every repo Orca knows; and the owner has a named,
   verified, logged exit for a real change that is another session's work."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import gate_log  # noqa: E402
import guard  # noqa: E402
import ship_state  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_guard_outside import write  # noqa: E402
from test_quick_cli import rein_cli  # noqa: E402
from test_quick_driver import DoneCase, DriverCase, _Git, _Orca, _limits  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


def last_log(ws):
    with open(gate_log.root_log_path(ws)) as fh:
        return json.loads(fh.read().splitlines()[-1])


class QuickScope(DriverCase):
    def other_repo(self, name):
        path = os.path.join(self.root, name)
        make_repo(path, hook=False)
        self.repos.append({"id": name, "displayName": name, "path": path})
        return os.path.realpath(path)

    def start(self):
        make_repo(self.repo_path, hook=False)
        orca = _Orca(self.repos, _limits(10, 10), self.worktree)
        code, _, _ = self.run_quick(orca=orca)
        self.assertEqual(code, 0, self.text())
        return self.record(self.worktree)["outside_before"]

    def test_a_quick_watches_its_own_repo_and_not_the_products_of_other_sessions(self):
        admin = self.other_repo("proxima-admin")
        before = self.start()
        self.assertEqual(sorted(before), [os.path.realpath(self.repo_path)])
        self.assertNotIn(admin, before)

    def test_a_declared_sibling_is_watched_too(self):
        qa = self.other_repo("proxima-qa")
        self.other_repo("proxima-admin")
        make_repo(self.repo_path, hook=False)
        with open(os.path.join(self.repo_path, "flow.config.json"), "w") as fh:
            json.dump({"gate": {"outsideWatch": ["proxima-qa"]}}, fh)
        code, _, _ = self.run_quick(orca=_Orca(self.repos, _limits(10, 10), self.worktree))
        self.assertEqual(code, 0, self.text())
        self.assertEqual(sorted(self.record(self.worktree)["outside_before"]),
                         sorted([os.path.realpath(self.repo_path), qa]))


class QuickDone(DoneCase):
    """The deadlock of the retest: another session integrated a change in a repo and regenerated
    AGENTS.md there; the quick could neither pass nor accept it."""

    def setUp(self):
        super().setUp()
        self.admin = os.path.join(self.root, "proxima-admin")
        make_repo(self.admin, hook=False)
        write(self.admin, "AGENTS.md", "v1\n")
        self.before = guard.outside_snapshot([self.admin])

    def integrate(self):  # the other session: a pending regeneration and a commit under the checkout's identity
        write(self.admin, "AGENTS.md", "regenerated\n")
        commit(self.admin, "feature.py", "x = 1\n", "feat: integrate")
        self.sha = run_git(self.admin, "rev-parse", "HEAD").strip()

    def test_a_repo_the_quick_does_not_watch_cannot_block_it(self):
        self.make_repo()  # no outside_before entry for proxima-admin
        self.integrate()
        self.write("src/foo.ts", "v2\n")
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())

    def test_a_real_change_in_a_watched_repo_is_red_and_names_the_exit(self):
        self.make_repo(outside_before=self.before)
        write(self.admin, "AGENTS.md", "regenerated\n")
        self.write("src/foo.ts", "v2\n")
        self.assertEqual(self.done(accept_outside=True)[0], 1, "--accept-outside is not for a certain change")
        self.assertIn("--accept-path", self.text())

    def test_accepting_the_exact_path_is_green_and_logged_with_its_fingerprint(self):
        self.make_repo(outside_before=self.before)
        write(self.admin, "AGENTS.md", "regenerated\n")
        self.write("src/foo.ts", "v2\n")
        agents = os.path.join(self.admin, "AGENTS.md")
        code, _, _ = self.done(accept_paths=[agents])
        self.assertEqual(code, 0, self.text())
        accepted = last_log(self.ws)["guardAccepted"]
        self.assertIn(os.path.realpath(agents), accepted)
        self.assertIn(guard._fingerprint(self.admin, "AGENTS.md"), accepted)

    def test_accepting_one_path_does_not_accept_the_rest(self):
        self.make_repo(outside_before=self.before)
        write(self.admin, "AGENTS.md", "regenerated\n")
        write(self.admin, "other.md", "new\n")
        self.write("src/foo.ts", "v2\n")
        code, _, _ = self.done(accept_paths=[os.path.join(self.admin, "AGENTS.md")])
        self.assertEqual(code, 1, self.text())
        self.assertIn("other.md", self.text())

    def test_a_path_that_did_not_change_is_a_red_not_a_quiet_pass(self):
        self.make_repo(outside_before=self.before)
        self.write("src/foo.ts", "v2\n")
        code, _, _ = self.done(accept_paths=[os.path.join(self.admin, "AGENTS.md")])
        self.assertEqual(code, 1, self.text())
        self.assertIn("is not a path that changed", self.text())

    def test_accepting_by_commit_and_with_no_local_diff(self):
        self.make_repo(outside_before=self.before)
        self.integrate()
        self.assertEqual(self.done()[0], 1, self.text())
        code, _, _ = self.done(accept_commits=[self.sha[:10]],
                               accept_paths=[os.path.join(self.admin, "AGENTS.md")])
        self.assertEqual(code, 0, self.text())
        self.assertIn(self.sha[:12], last_log(self.ws)["guardAccepted"])

    def test_a_commit_prefix_that_is_too_short_or_foreign_accepts_nothing(self):
        self.make_repo(outside_before=self.before)
        self.integrate()
        write(self.admin, "AGENTS.md", "regenerated\n")
        for named in (self.sha[:3], "deadbeef"):
            with self.subTest(named=named):
                code, _, _ = self.done(accept_commits=[named],
                                       accept_paths=[os.path.join(self.admin, "AGENTS.md")])
                self.assertEqual(code, 1, self.text())


class ShipScope(ShipCase):
    PLAN = "# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"

    def fake(self, repo="proxima-qa"):
        self.write_plan(self.PLAN)
        self.sibling = os.path.join(self.tmp.name, repo)
        make_repo(self.sibling, hook=False)
        fake = FakeOrca(self.root, repos=("proxima-api", repo))
        original = fake._answer

        def answer(a):
            out = original(a)
            if a[:2] == ["repo", "list"]:
                for r in out["repos"]:
                    if r["displayName"] == repo:
                        r["path"] = self.sibling
            return out
        fake._answer = answer
        fake.on_audit = self.approve_audit()
        return fake

    def gates(self):
        done = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task" and not done:
                done.append(1)
                write(self.sibling, "AGENTS.md", "regenerated by another session\n")
            return 0, "ok"
        return gates

    def specs(self, fake):
        return [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]

    def test_a_checkout_the_plan_does_not_name_is_not_watched(self):
        fake = self.fake()
        self.run_ship(fake, self.gates())
        self.assertEqual(len(self.specs(fake)), 1, self.out)
        self.assertEqual(ship_state.load(self.state_file).tasks["T001"].status, "done")

    def test_the_owner_names_the_change_and_the_task_closes_with_it_logged(self):
        self.watch("proxima-qa")
        fake = self.fake()
        target = os.path.join(self.sibling, "AGENTS.md")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = rein_cli.cmd_accept_outside(["--root", self.root, "--path", target])
        self.assertEqual(rc, 0)
        self.run_ship(fake, self.gates())
        self.assertEqual(len(self.specs(fake)), 1, self.out)
        self.assertEqual(ship_state.load(self.state_file).tasks["T001"].status, "done")
        self.assertIn("accepted by the owner", self.out)


class AcceptCli(unittest.TestCase):
    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rein_cli.cmd_accept_outside(argv)
        return code, out.getvalue()

    def test_records_paths_and_commits_and_refuses_what_it_cannot_verify_later(self):
        root = tempfile.mkdtemp()
        code, out = self.run_cli(["--root", root, "--path", "/a/b/AGENTS.md", "--commit", "abcdef1234"])
        self.assertEqual(code, 0, out)
        self.assertEqual(guard.load_accepts(root), {"paths": [os.path.realpath("/a/b/AGENTS.md")],
                                                    "commits": ["abcdef1234"]})
        for bad in ([], ["--path", "relative.md"], ["--commit", "abc"], ["--root"], ["--path"]):
            with self.subTest(bad=bad):
                self.assertEqual(self.run_cli(["--root", root] + bad if bad[:1] != ["--root"] else bad)[0], 2)

    def test_quick_done_routes_the_repeatable_flags(self):
        from unittest import mock
        with mock.patch.object(rein_cli._quick, "OrcaClient"), \
                mock.patch.object(rein_cli._quick, "done_quick", return_value=0) as done:
            rein_cli.cmd_quick(["done", "--accept-path", "/x/AGENTS.md", "--accept-path=/y/b",
                                "--accept-commit", "abcdef1"])
        kw = done.call_args[1]
        self.assertEqual((kw["accept_paths"], kw["accept_commits"]), (["/x/AGENTS.md", "/y/b"], ["abcdef1"]))

    def test_the_accept_flags_belong_to_done_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rein_cli.cmd_quick(["fix it", "--accept-path", "/x"]), 2)
            self.assertEqual(rein_cli.cmd_quick(["done", "--accept-path"]), 2)


if __name__ == "__main__":
    unittest.main()


class DecisionsInTheBrief(ShipCase):
    """B: the decisions a task cites reach its brief whatever DECISIONS_LIMIT says, and a cut is told
    with the absolute path of the tasks.md that holds the rest (ai-usage-platform: D6/D7 dropped silently)."""

    def plan_with(self, cites):
        decisions = "\n".join("- D%d: Decision number %d — %s" % (i, i, "because " * 30) for i in range(1, 8))
        self.write_plan("# Change: demo\n\n## Why\nShip it.\n\n## Decisions\n%s\n\n"
                        "- [ ] T001 Add the api endpoint%s\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"
                        % (decisions, cites))

    def spec(self):
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, lambda p, unit_only, timeout=None, **kw: (0, "ok"))
        [spec] = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        return spec

    def test_the_decisions_the_task_cites_are_in_even_past_the_limit(self):
        self.plan_with(" (follow D6 and D7)")
        spec = self.spec()
        self.assertIn("- D6: Decision number 6", spec)
        self.assertIn("- D7: Decision number 7", spec)

    def test_a_cut_is_told_with_the_absolute_path_of_the_tasks_md(self):
        self.plan_with(" (follow D7)")
        spec = self.spec()
        self.assertIn("decisions not shown here", spec)
        self.assertIn(os.path.join(os.path.abspath(self.root), "tasks.md"), spec)
        self.assertIn("tasks.md", spec.split("decisions not shown here")[1])

    def test_nothing_is_said_when_everything_fits(self):
        self.write_plan("# Change: demo\n\n## Decisions\n- D1: Use Postgres — the team runs it\n\n"
                        "- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n")
        self.assertNotIn("not shown", self.spec())


from test_quick_inbox import Case as InboxCase, Orca as InboxOrca  # noqa: E402
import quick as _quick  # noqa: E402
import quick_inbox  # noqa: E402
from orca_client import OrcaClient  # noqa: E402


class WatchAndOldQuestions(InboxCase):
    """C: a question the owner answered in the agent's terminal (no `rein quick answer`) is not
    announced again by every watch, never buries a real event, and watch leaves the record alone."""

    def bytes_of(self):
        with open(os.path.join(self.ws, _quick.RECORD), "rb") as fh:
            return fh.read()

    def watch(self):
        out = io.StringIO()
        client = OrcaClient(executable="ORCA", runner=InboxOrca(worktrees=[self.ws]))
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch("", client, cwd=self.ws, once=True, clock=lambda: 1e10)
        return code, out.getvalue()

    def commit_now(self):
        run_git(self.ws, "commit", "-q", "--allow-empty", "-m", "fix: the agent went on")

    def test_a_question_followed_by_a_commit_is_superseded_not_announced(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 1.0, say=self.out.append)
        self.commit_now()  # the owner answered in the terminal and the agent committed
        code, out = self.watch()
        self.assertEqual((code, out), (1, ""))

    def test_the_record_of_a_quick_that_is_fine_is_not_rewritten_by_a_watch(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 1.0, say=self.out.append)
        self.commit_now()
        write(self.ws, "src.py", "x = 1\n")  # edits in progress: a sign of work
        before = self.bytes_of()
        mtime = os.stat(os.path.join(self.ws, _quick.RECORD)).st_mtime_ns
        for _ in range(3):
            self.watch()
        self.assertEqual(self.bytes_of(), before)
        self.assertEqual(os.stat(os.path.join(self.ws, _quick.RECORD)).st_mtime_ns, mtime)

    def test_a_question_the_agent_still_waits_on_is_announced_once(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 9e9, say=self.out.append)  # after the last commit
        code, out = self.watch()
        self.assertEqual(code, 0)
        self.assertIn("QUESTION", out)
        code, out = self.watch()
        self.assertEqual(code, 1)
        self.assertNotIn("QUESTION", out)
        self.assertIn("STILL OPEN", out)  # listed, not announced as news

    def test_an_old_question_does_not_bury_a_real_report_of_another_quick(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 1.0, say=self.out.append)
        self.commit_now()
        other = os.path.join(self.tmp, "quick-other")
        os.makedirs(other)
        run_git(other, "init", "-q")
        run_git(other, "commit", "-q", "--allow-empty", "-m", "base")
        sha = run_git(other, "rev-parse", "HEAD").strip()
        record = _quick.new_record("api", other, "develop", sha, "codex", "gpt", "otra", "", 1.0)
        os.makedirs(os.path.join(other, ".rein"))
        with open(os.path.join(other, _quick.RECORD), "w") as fh:
            json.dump(record, fh)
        quick_inbox.report(other, "listo: cambié x", now=lambda: 5.0, say=self.out.append)
        client = OrcaClient(executable="ORCA", runner=InboxOrca(worktrees=[self.ws, other]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch("", client, cwd=self.ws, once=True, clock=lambda: 1e10)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("QUESTION"), 0)
        self.assertIn("REPORT %s r1" % other, out.getvalue())

    def test_a_newer_question_supersedes_the_older_one(self):
        quick_inbox.ask(self.ws, "primera", now=lambda: 9e9, say=self.out.append)
        quick_inbox.ask(self.ws, "segunda", now=lambda: 9e9 + 5, say=self.out.append)
        code, out = self.watch()
        self.assertEqual(code, 0)
        self.assertIn("segunda", out)
        self.assertNotIn("primera", out)


import ship  # noqa: E402


class FixTaskBrief(ShipCase):
    """D: the brief of a generated AUDIT-fix task whose Verification is the whole suite does not also
    forbid running it (the worker had to ask the owner which one to obey)."""

    def test_a_whole_suite_verification_is_not_forbidden_by_the_same_brief(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm exec ng test --watch=false`\n")
        fake = FakeOrca(self.root)
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, lambda p, unit_only, timeout=None, **kw: (0, "ok"))
        [spec] = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertIn("pnpm exec ng test --watch=false", spec)
        self.assertIn("Verification command exactly as given", spec)
        self.assertIn("when that is itself the whole suite", spec)
        # no sentence forbids the suite without the exception that covers the Verification
        self.assertNotRegex(spec, r"Do not run the project's full test suite\.")

    def test_an_audit_fix_task_that_falls_back_to_the_full_suite_says_so_once(self):
        task = ship.ss.TaskState(id="AUDIT1-proxima-admin", repo="proxima-admin", deps=[], human_review=False,
                                 verification="`pnpm exec ng test --watch=false` (the repo's full test suite)",
                                 type="audit-fix")
        task.audit_findings = "BLOCKING: x"
        spec = ship.build_task_spec(task, {"tasks": []})
        self.assertIn("exactly as given", spec)
        self.assertIn("Verification command: `pnpm exec ng test --watch=false` (the repo's full test suite)", spec)
        self.assertIn("when that is itself the whole suite", spec)
