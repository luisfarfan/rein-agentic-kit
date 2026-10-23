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

    def retried_after_a_write(self, fake, target_repo_path, rel="OWNER_ONLY.md"):
        """The worker writes `rel` in `target_repo_path` during its first gate; its retry restores it."""
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:
                    write(target_repo_path, rel, "not yours\n")
                elif len(calls) == 2:
                    os.remove(os.path.join(target_repo_path, rel))
            return 0, "ok"
        self.run_ship(fake, gates)
        return self.specs(fake)

    def fake_with(self, paths):
        """FakeOrca whose repo list gives the repos in `paths` ({name: checkout})."""
        self.write_plan(self.PLAN)
        fake = FakeOrca(self.root, repos=tuple(["proxima-api"] + [n for n in paths if n != "proxima-api"]))
        original = fake._answer

        def answer(a):
            out = original(a)
            if a[:2] == ["repo", "list"]:
                for r in out["repos"]:
                    if r["displayName"] in paths:
                        r["path"] = paths[r["displayName"]]
            return out
        fake._answer = answer
        fake.on_audit = self.approve_audit()
        return fake

    def test_a_sibling_declared_in_the_task_repo_is_watched(self):  # review5 A1: was exit 0 with the write kept
        api = os.path.join(self.tmp.name, "proxima-api")
        qa = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(api, config={"commands": {"test": "true"}, "gate": {"outsideWatch": ["proxima-qa"]}}, hook=False)
        make_repo(qa, hook=False)
        fake = self.fake_with({"proxima-api": api, "proxima-qa": qa})
        specs = self.retried_after_a_write(fake, qa)
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("OWNER_ONLY.md", specs[1])

    def test_the_plan_root_is_watched_by_its_git_checkout_not_by_its_folder_name(self):  # review5 A1
        make_repo(self.root, hook=False)  # the root is a real checkout registered as `engineering`
        fake = self.fake_with({"proxima-api": os.path.join(self.tmp.name, "proxima-api"), "engineering": self.root})
        make_repo(os.path.join(self.tmp.name, "proxima-api"), hook=False)
        self.assertNotEqual(os.path.basename(self.root), "engineering")
        specs = self.retried_after_a_write(fake, self.root)
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("OWNER_ONLY.md", specs[1])

    def test_an_unrelated_repo_is_still_not_watched(self):
        api = os.path.join(self.tmp.name, "proxima-api")
        other = os.path.join(self.tmp.name, "proxima-admin")
        make_repo(api, hook=False)
        make_repo(other, hook=False)
        fake = self.fake_with({"proxima-api": api, "proxima-admin": other})
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task" and not calls:
                calls.append(1)
                write(other, "AGENTS.md", "regenerated\n")
            return 0, "ok"
        self.run_ship(fake, gates)
        self.assertEqual(len(self.specs(fake)), 1, self.out)

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


class SymlinkIdentity(DoneCase):
    """review5 A2: a symlink is its own git entry; naming A never accepts B although both point at one target."""

    def setUp(self):
        super().setUp()
        self.admin = os.path.join(self.root, "proxima-admin")
        make_repo(self.admin, hook=False)
        write(self.admin, "target.txt", "t\n")
        os.symlink("old-a", os.path.join(self.admin, "A"))
        os.symlink("old-b", os.path.join(self.admin, "B"))
        run_git(self.admin, "add", "-A")
        run_git(self.admin, "commit", "-q", "-m", "chore: links")
        self.make_repo(outside_before=guard.outside_snapshot([self.admin]))
        for name in ("A", "B"):  # two independent git changes, same destination
            os.remove(os.path.join(self.admin, name))
            os.symlink("target.txt", os.path.join(self.admin, name))
        self.write("src/foo.ts", "v2\n")

    def test_naming_one_link_does_not_accept_the_other(self):
        code, _, _ = self.done(accept_paths=[os.path.join(self.admin, "A")])
        self.assertEqual(code, 1, self.text())
        self.assertIn("changed: B", self.text())

    def test_each_link_is_logged_by_its_own_name(self):
        code, _, _ = self.done(accept_paths=[os.path.join(self.admin, "A"), os.path.join(self.admin, "B")])
        self.assertEqual(code, 0, self.text())
        accepted = last_log(self.ws)["guardAccepted"]
        self.assertIn(os.path.join(self.admin, "A"), accepted)
        self.assertIn(os.path.join(self.admin, "B"), accepted)
        self.assertNotIn("target.txt", accepted.replace("link:target.txt", ""))

    def test_naming_the_target_file_does_not_accept_a_link_to_it(self):
        code, _, _ = self.done(accept_paths=[os.path.join(self.admin, "target.txt")])
        self.assertEqual(code, 1, self.text())


class WatchAndOldQuestions(InboxCase):
    """C: noise control without hiding a wait. A question is told once; later activity (a commit by
    anyone, a report, another question) never closes it -- only an answer, `rein quick resolve` or
    `rein quick ask --replaces` does; watch leaves the records of quicks that are fine alone."""

    def bytes_of(self):
        with open(os.path.join(self.ws, _quick.RECORD), "rb") as fh:
            return fh.read()

    def watch(self, workspaces=None):
        out = io.StringIO()
        client = OrcaClient(executable="ORCA", runner=InboxOrca(worktrees=workspaces or [self.ws]))
        with contextlib.redirect_stdout(out):
            code = quick_inbox.watch("", client, cwd=self.ws, once=True, clock=lambda: 1e10)
        return code, out.getvalue()

    def commit_now(self, identity=None):
        args = ["-c", "user.email=%s" % identity] if identity else []
        run_git(self.ws, *args, "commit", "-q", "--allow-empty", "-m", "chore: somebody else's edit")

    def test_the_record_of_a_quick_that_is_fine_is_not_rewritten_by_a_watch(self):
        write(self.ws, "src.py", "x = 1\n")  # edits in progress: a sign of work
        before = self.bytes_of()
        mtime = os.stat(os.path.join(self.ws, _quick.RECORD)).st_mtime_ns
        for _ in range(3):
            self.watch()
        self.assertEqual(self.bytes_of(), before)
        self.assertEqual(os.stat(os.path.join(self.ws, _quick.RECORD)).st_mtime_ns, mtime)

    def test_a_question_is_announced_once_then_listed_as_still_open(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 9e9, say=self.out.append)
        code, out = self.watch()
        self.assertEqual(code, 0)
        self.assertIn("QUESTION", out)
        code, out = self.watch()
        self.assertEqual(code, 1)
        self.assertNotIn("QUESTION", out)
        self.assertIn("STILL OPEN", out)

    def test_a_commit_by_another_identity_does_not_answer_a_question(self):
        quick_inbox.ask(self.ws, "¿Autoriza el cambio? Sigo esperando", now=lambda: 1.0, say=self.out.append)
        self.watch()  # told
        self.commit_now("other@o")  # another session, nobody answered
        code, out = self.watch()
        self.assertEqual(code, 1)
        self.assertIn("STILL OPEN %s q1" % self.ws, out)
        self.assertIn("a commit", out)  # the uncertainty is shown
        self.assertIn("rein quick resolve", out)

    def test_two_independent_questions_are_both_announced(self):
        quick_inbox.ask(self.ws, "¿Autoriza A?", now=lambda: 9e9, say=self.out.append)
        quick_inbox.ask(self.ws, "¿Qué dato uso para B?", now=lambda: 9e9 + 5, say=self.out.append)
        code, out = self.watch()
        self.assertEqual(code, 0)
        self.assertIn("Autoriza A", out)
        self.assertIn("dato uso para B", out)

    def test_resolve_closes_exactly_the_named_question_and_tells_the_agent_nothing(self):
        quick_inbox.ask(self.ws, "uno", now=lambda: 9e9, say=self.out.append)
        quick_inbox.ask(self.ws, "dos", now=lambda: 9e9 + 1, say=self.out.append)
        orca = InboxOrca(worktrees=[self.ws])
        client = OrcaClient(executable="ORCA", runner=orca)
        self.assertEqual(quick_inbox.resolve(self.ws, ["q1"], client, say=self.out.append), 0)
        self.assertEqual(orca.sent(), [])
        code, out = self.watch()
        self.assertIn("dos", out)
        self.assertNotIn("uno", out)
        self.assertEqual(quick_inbox.resolve(self.ws, ["q1"], client, say=self.out.append), 1, "already closed")

    def test_the_agent_can_replace_a_question_explicitly(self):
        quick_inbox.ask(self.ws, "vieja", now=lambda: 9e9, say=self.out.append)
        quick_inbox.ask(self.ws, "nueva", now=lambda: 9e9 + 1, say=self.out.append, replaces=["q1"])
        code, out = self.watch()
        self.assertIn("nueva", out)
        self.assertNotIn("vieja", out)

    def test_the_cli_routes_resolve_and_replaces(self):
        from unittest import mock
        with mock.patch.object(rein_cli._inbox, "resolve", return_value=0) as res, \
                mock.patch.object(rein_cli._quick, "OrcaClient"):
            rein_cli.cmd_quick(["resolve", "--id", "q1", "--id=q2"])
        self.assertEqual(res.call_args[0][:2], ("", ["q1", "q2"]))
        with mock.patch.object(rein_cli._inbox, "ask", return_value=0) as ask:
            rein_cli.cmd_quick(["ask", "nueva", "--replaces", "q1"])
        self.assertEqual((ask.call_args[0][1], ask.call_args[1]["replaces"]), ("nueva", ["q1"]))

    def test_an_old_question_does_not_bury_a_real_report_of_another_quick(self):
        quick_inbox.ask(self.ws, "¿payload?", now=lambda: 1.0, say=self.out.append)
        self.watch()  # told once
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
        code, out = self.watch([self.ws, other])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("QUESTION"), 0)
        self.assertIn("REPORT %s r1" % other, out)


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


class SyntheticTaskDecisions(unittest.TestCase):
    """review5 B: the decisions a generated task cites (in its findings, failure or Verification) are in its brief."""

    PLAN = {"path": "/abs/proxima-engineering/tasks.md", "why": "w", "tasks": [],
            "decisions": [{"id": "D%d" % i, "title": "Decision %d" % i, "rationale": "because " * 40}
                          for i in range(1, 8)]}

    def spec(self, **kw):
        task = ship.ss.TaskState(id="AUDIT1-proxima-api", repo="proxima-api", deps=[], human_review=False,
                                 verification=kw.pop("verification", ""), type="audit-fix")
        for k, v in kw.items():
            setattr(task, k, v)
        return ship.build_task_spec(task, self.PLAN)

    def test_a_citation_in_the_audit_findings(self):
        spec = self.spec(audit_findings="BLOCKING: src/x.py violates D7; follow D7 when fixing")
        self.assertIn("- D7: Decision 7", spec)

    def test_a_citation_in_the_last_failure(self):
        self.assertIn("- D6: Decision 6", self.spec(last_failure="the gate failed: D6 says no cache"))

    def test_a_citation_in_the_verification(self):
        self.assertIn("- D5: Decision 5", self.spec(verification="`pnpm test` (the check D5 asks for)"))

    def test_what_is_cited_nowhere_is_still_cut_with_the_path(self):
        spec = self.spec(audit_findings="BLOCKING: D7")
        self.assertIn("/abs/proxima-engineering/tasks.md", spec)
