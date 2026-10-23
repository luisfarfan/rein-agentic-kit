"""Round 6 (README review 2026-10-23), through the real consumers.

1  the guard also runs after the auditor (quick `_audit`, ship `read_verdict`): a verdict recorded
   after the auditor wrote outside its worktree or edited protected config is never green;
2  the hook check re-runs `pre-commit` too, on what each commit staged, so `--no-verify` does not
   skip it; a hook that cannot be re-run reliably is not green;
3  the plan root's `gate.protected` applies to every repo of the change;
4  `rein verify --plan --change <name>` selects the change (an unknown one is an explicit error);
5  `rein ledger --json` with an empty ledger prints `{"runs": []}`."""

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

import gate_log  # noqa: E402
import guard  # noqa: E402
import ship_state  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_guard_outside import write  # noqa: E402
from test_quick_driver import DoneCase, TERMINAL, _AuditOrca  # noqa: E402
from test_ship import Drained, FakeOrca, ShipCase, scripted_gate  # noqa: E402

REIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")

PRE_COMMIT = """#!/bin/sh
if git diff --cached | grep -q '^+.*FORBIDDEN'; then echo "a forbidden token is staged"; exit 1; fi
exit 0
"""


def install_hook(path, name, body):
    hook = os.path.join(path, ".git", "hooks", name)
    with open(hook, "w") as fh:
        fh.write(body)
    os.chmod(hook, os.stat(hook).st_mode | stat.S_IEXEC)


def put(path, rel, text):
    """Write `rel` whole (test_guard_outside.write appends)."""
    with open(os.path.join(path, rel), "w") as fh:
        fh.write(text)


def last_log(ws):
    with open(gate_log.root_log_path(ws)) as fh:
        return json.loads(fh.read().splitlines()[-1])


# --- 2 (F3): pre-commit ------------------------------------------------------------------------

class PreCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)
        install_hook(self.repo, "pre-commit", PRE_COMMIT)

    def test_a_commit_that_skipped_the_hook_with_no_verify_is_a_problem(self):
        commit(self.repo, "a.py", "x = 'FORBIDDEN'\n", "feat: skip the hook")  # --no-verify
        problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("pre-commit hook", problems[0])
        self.assertIn("a forbidden token is staged", problems[0])

    def test_only_the_offending_commit_is_named(self):
        commit(self.repo, "ok.py", "x = 1\n", "feat: fine")
        commit(self.repo, "a.py", "x = 'FORBIDDEN'\n", "feat: bad")
        bad = run_git(self.repo, "rev-parse", "HEAD").strip()[:8]
        commit(self.repo, "b.py", "y = 2\n", "feat: fine too")
        problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(bad, problems[0])

    def test_commits_that_pass_the_hook_are_clean(self):
        commit(self.repo, "a.py", "x = 2\n", "feat: ok")
        self.assertEqual(guard.check(self.repo, self.base, {}), [])

    def test_the_workers_worktree_is_not_touched(self):
        commit(self.repo, "a.py", "x = 'FORBIDDEN'\n", "feat: bad")
        write(self.repo, "wip.txt", "uncommitted\n")
        before = run_git(self.repo, "status", "--porcelain=v1")
        head = run_git(self.repo, "rev-parse", "HEAD")
        guard.check(self.repo, self.base, {})
        self.assertEqual(run_git(self.repo, "status", "--porcelain=v1"), before)
        self.assertEqual(run_git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(len(run_git(self.repo, "worktree", "list").strip().splitlines()), 1)  # none left behind
        with open(os.path.join(self.repo, "wip.txt")) as fh:
            self.assertEqual(fh.read(), "uncommitted\n")

    def test_a_hook_that_cannot_finish_is_not_green(self):
        install_hook(self.repo, "pre-commit", "#!/bin/sh\nsleep 5\n")
        commit(self.repo, "a.py", "x = 3\n", "feat: slow")
        with mock.patch.object(guard, "HOOK_TIMEOUT", 1):
            problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unchecked", problems[0])
        self.assertEqual(len(run_git(self.repo, "worktree", "list").strip().splitlines()), 1)

    def test_more_commits_than_it_will_rerun_are_reported_not_passed(self):
        commit(self.repo, "a.py", "x = 1\n", "feat: one")
        commit(self.repo, "b.py", "x = 2\n", "feat: two")
        with mock.patch.object(guard, "MAX_PRE_COMMIT", 1):
            problems = guard.check(self.repo, self.base, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("1 were not checked", problems[0])

    def test_a_git_that_cannot_run_hooks_is_reported_when_the_hook_exists(self):
        commit(self.repo, "a.py", "x = 1\n", "feat: one")
        with mock.patch.object(guard, "_git_runs_hooks", return_value=False):
            problems = guard.check(self.repo, self.base, {})
        self.assertTrue(any("pre-commit hook" in p and "git >= 2.36" in p for p in problems), problems)

    def test_a_repo_without_a_pre_commit_hook_has_nothing_to_check(self):
        os.remove(os.path.join(self.repo, ".git", "hooks", "pre-commit"))
        commit(self.repo, "a.py", "x = 'FORBIDDEN'\n", "feat: no hook")
        self.assertEqual(guard.check(self.repo, self.base, {}), [])

    def test_the_bypass_variables_never_reach_the_hook(self):
        install_hook(self.repo, "pre-commit", '#!/bin/sh\n[ -z "$SKIP$LEFTHOOK_EXCLUDE" ] || { echo bypass; exit 2; }\n')
        commit(self.repo, "a.py", "x = 1\n", "feat: one")
        with mock.patch.dict(os.environ, {"LEFTHOOK_EXCLUDE": "pre-commit"}):
            self.assertEqual(guard.check(self.repo, self.base, {}), [])


class PreCommitInQuick(DoneCase):
    def test_done_is_red_for_a_commit_that_skipped_pre_commit(self):
        self.make_repo()
        install_hook(self.ws, "pre-commit", PRE_COMMIT)
        self.write("src/bar.ts", "FORBIDDEN\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "fix: the thing")
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("pre-commit hook", self.text())


class PreCommitInShip(ShipCase):
    def test_a_task_that_skipped_pre_commit_is_retried_with_the_reason(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")
        path = self.wtpath("proxima-api")
        base = make_repo(path, hook=False)
        install_hook(path, "pre-commit", PRE_COMMIT)
        run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:
                    commit(path, "api.py", "x = 'FORBIDDEN'\n", "feat: endpoint")
                else:  # the retry redoes it without the offending commit
                    run_git(path, "reset", "-q", "--hard", base)
                    commit(path, "api.py", "x = 1\n", "feat: endpoint, clean")
            return 0, "ok"
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("pre-commit hook", specs[1])


# --- 3 (F1): gate.protected from the plan root ------------------------------------------------

class InheritedProtected(ShipCase):
    PLAN = ("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
            "  - Verification: `pnpm test`\n")

    def run_with(self, root_config):
        self.write_plan(self.PLAN)
        if root_config is not None:
            with open(os.path.join(self.root, "flow.config.json"), "w") as fh:
                json.dump(root_config, fh)
        path = self.wtpath("proxima-api")
        base = make_repo(path, hook=False)
        run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:
                    os.makedirs(os.path.join(path, "policy"), exist_ok=True)
                    commit(path, "policy/rules.md", "loosened\n", "docs: rules")
                else:
                    run_git(path, "rm", "-q", "policy/rules.md")
                    run_git(path, "commit", "-q", "--no-verify", "-m", "docs: put it back")
            return 0, "ok"
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        return [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]

    def test_the_plan_roots_protected_globs_apply_to_the_repos_of_the_change(self):
        specs = self.run_with({"gate": {"protected": ["policy/*"]}})
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("policy/rules.md", specs[1])

    def test_without_the_declaration_the_same_change_passes(self):
        self.assertEqual(len(self.run_with(None)), 1, self.out)

    def test_the_repos_own_globs_still_apply_with_the_roots_added(self):
        base = guard.protected_patterns({"gate": {"protected": ["own/*"]}}, inherited=["policy/*"])
        self.assertIn("own/*", base)
        self.assertIn("policy/*", base)
        self.assertIn("flow.config.json", base)


# --- 1 (F2): the guard after the auditor -------------------------------------------------------

class QuickAudit(DoneCase):
    def setUp(self):
        super().setUp()
        self.admin = os.path.join(self.root, "proxima-admin")
        make_repo(self.admin, hook=False)
        write(self.admin, "AGENTS.md", "v1\n")
        self.before = guard.outside_snapshot([self.admin])

    def risky(self):
        self.make_repo(outside_before=self.before)
        self.write("src/auth/login.ts", "v2\n")

    def auditor(self, *effects):
        approve = self.reviewer("APPROVED")

        def play():
            for effect in effects:
                effect()
            approve()
        return play

    def test_a_clean_audit_still_approves(self):
        self.risky()
        orca = _AuditOrca(on_start=self.auditor())
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 0, self.text())

    def test_an_auditor_that_writes_outside_its_worktree_never_gives_green(self):
        self.risky()
        orca = _AuditOrca(on_start=self.auditor(lambda: write(self.admin, "AGENTS.md", "audited!\n")))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 1, self.text())
        self.assertNotIn("audit: APPROVED", self.text())
        self.assertIn("the auditor broke a rule", self.text())
        self.assertIn("AGENTS.md", self.text())
        self.assertIn("--accept-path", self.text())  # the explicit exit is named
        self.assertIn("audit guard", last_log(self.ws)["note"])
        self.assertEqual(self.audit_rounds(), 0)  # a breach is not a round of changes

    def test_an_auditor_that_edits_the_gate_config_never_gives_green(self):
        self.risky()
        orca = _AuditOrca(on_start=self.auditor(lambda: self.write("flow.config.json", '{"commands": {}}\n')))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 1, self.text())
        self.assertIn("the auditor changed flow.config.json", self.text())
        self.assertNotIn("audit: APPROVED", self.text())

    def test_the_breach_stays_red_on_the_next_done_until_reverted(self):
        self.risky()
        orca = _AuditOrca(on_start=self.auditor(lambda: write(self.admin, "AGENTS.md", "audited!\n")))
        self.assertEqual(self.done(orca=orca, env=TERMINAL)[0], 1)
        self.out.clear()
        code, _, orca2 = self.done(orca=_AuditOrca(), env=TERMINAL)  # the approval stands: no new audit
        self.assertEqual(code, 1, self.text())
        self.assertEqual(orca2.called("orchestration", "worker-start"), [])
        put(self.admin, "AGENTS.md", "v1\n")  # the owner reverts what the auditor wrote
        self.out.clear()
        self.assertEqual(self.done(orca=_AuditOrca(), env=TERMINAL)[0], 0, self.text())

    def test_naming_the_path_accepts_it_and_logs_it(self):
        self.risky()
        orca = _AuditOrca(on_start=self.auditor(lambda: write(self.admin, "AGENTS.md", "audited!\n")))
        self.assertEqual(self.done(orca=orca, env=TERMINAL)[0], 1)
        self.out.clear()
        agents = os.path.join(self.admin, "AGENTS.md")
        code, _, _ = self.done(orca=_AuditOrca(), env=TERMINAL, accept_paths=[agents])
        self.assertEqual(code, 0, self.text())
        self.assertIn(os.path.realpath(agents), last_log(self.ws)["guardAccepted"])


class ShipAudit(ShipCase):
    PLAN = "# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"

    def setUp(self):
        super().setUp()
        self.write_plan(self.PLAN)
        self.sibling = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(self.sibling, hook=False)
        write(self.sibling, "AGENTS.md", "v1\n")
        run_git(self.sibling, "add", "-A")
        run_git(self.sibling, "commit", "-q", "-m", "chore: agents")
        self.watch("proxima-qa")
        self.api = self.wtpath("proxima-api")
        base = make_repo(self.api, hook=False)
        run_git(self.api, "update-ref", "refs/remotes/origin/develop", base)

    def fake(self, rounds):
        """`rounds`: for each audit, what the auditor does besides recording APPROVED, and the
        answer the owner gives to the gate it opens."""
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-qa"))
        original = fake._answer

        def answer(a):
            out = original(a)
            if a[:2] == ["repo", "list"]:
                for r in out["repos"]:
                    r["path"] = {"proxima-qa": self.sibling}.get(r["displayName"], r["path"])
            return out
        fake._answer = answer
        approve = self.approve_audit()
        queue = list(rounds)

        def on_audit():
            effect, answer_to = queue.pop(0)
            effect()
            approve()
            fake.auto_resolve = answer_to
        fake.on_audit = on_audit
        return fake

    def guard_gates(self, fake):
        return [g for g in fake.gates if "guard found the auditor" in g["question"]]

    def test_an_auditor_that_writes_outside_its_worktree_is_not_approved(self):
        fake = self.fake([(lambda: write(self.sibling, "AGENTS.md", "audited!\n"), "reject")])
        self.run_ship(fake, scripted_gate())
        [gate] = self.guard_gates(fake)
        self.assertEqual(gate["options"], '["retry-audit", "accept", "reject"]')
        self.assertIn("AGENTS.md", gate["question"])
        self.assertEqual(ship_state.load(self.state_file).audit_result, "rejected")

    def test_an_auditor_that_edits_the_gate_config_is_not_approved(self):
        fake = self.fake([(lambda: put(self.api, "flow.config.json", '{"commands": {}}\n'), "reject")])
        self.run_ship(fake, scripted_gate())
        [gate] = self.guard_gates(fake)
        self.assertIn("the auditor changed flow.config.json", gate["question"])
        self.assertEqual(ship_state.load(self.state_file).audit_result, "rejected")

    def test_a_clean_audit_is_approved_as_before(self):
        fake = self.fake([(lambda: None, "reject")])
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(self.guard_gates(fake), [])
        self.assertEqual(ship_state.load(self.state_file).audit_result, "approved")

    def test_accept_keeps_the_auditors_verdict_when_the_owner_says_it_is_not_its_write(self):
        fake = self.fake([(lambda: write(self.sibling, "AGENTS.md", "audited!\n"), "accept")])
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(len(self.guard_gates(fake)), 1)
        self.assertEqual(ship_state.load(self.state_file).audit_result, "approved")

    def test_a_re_audit_does_not_absorb_what_was_not_reverted(self):
        fake = self.fake([(lambda: write(self.sibling, "AGENTS.md", "audited!\n"), "retry-audit"),
                          (lambda: None, "reject")])
        self.run_ship(fake, scripted_gate())
        self.assertEqual(len(self.guard_gates(fake)), 2, self.out)  # still red the second time
        self.assertEqual(ship_state.load(self.state_file).audit_result, "rejected")

    def test_a_re_audit_after_reverting_it_is_approved(self):
        fake = self.fake([(lambda: write(self.sibling, "AGENTS.md", "audited!\n"), "retry-audit"),
                          (lambda: put(self.sibling, "AGENTS.md", "v1\n"), "reject")])
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(len(self.guard_gates(fake)), 1)
        self.assertEqual(ship_state.load(self.state_file).audit_result, "approved")

    def test_the_breach_is_in_the_gate_log(self):
        fake = self.fake([(lambda: write(self.sibling, "AGENTS.md", "audited!\n"), "reject")])
        self.run_ship(fake, scripted_gate())
        with open(gate_log.ship_log_path(self.state_file)) as fh:
            rows = [json.loads(line) for line in fh.read().splitlines()]
        [row] = [r for r in rows if r.get("kind") == "audit"]
        self.assertIn("AGENTS.md", row["guard"])


# --- 4 (R3) and 5 (F5): the CLI ---------------------------------------------------------------

def openspec_tree(root):
    for change, tid in (("alpha", "T001"), ("beta", "T002")):
        write(root, "openspec/changes/%s/tasks.md" % change,
              "# Change: %s\n## Why\nx\n---\n- [ ] %s probe\n  - Type: implementation\n  - Depends on: none\n"
              "  - Verification: `python3 -c \"pass\"`\n  - Acceptance:\n    - something\n\n" % (change, tid))


class VerifyPlanChange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        openspec_tree(self.tmp)

    def cli(self, *extra):
        return subprocess.run([sys.executable, REIN, "verify", self.tmp, "--plan", "--json", *extra],
                              capture_output=True, text=True, timeout=120)

    def test_change_selects_that_changes_tasks(self):
        for change, expected in (("alpha", ["T001"]), ("beta", ["T002"])):
            proc = self.cli("--change", change)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(sorted(json.loads(proc.stdout)["results"]), expected)

    def test_an_unknown_change_is_an_explicit_error_never_an_empty_green(self):
        proc = self.cli("--change", "nope")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        report = json.loads(proc.stdout)
        self.assertIn("no plan at", report["error"])
        self.assertIn("nope", report["error"])
        self.assertEqual(report["results"], {})

    def test_the_text_output_says_so_too(self):
        proc = subprocess.run([sys.executable, REIN, "verify", self.tmp, "--plan", "--change", "nope"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no plan at", proc.stdout)

    def test_no_change_named_lists_the_ones_that_exist(self):
        proc = self.cli()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("alpha, beta", json.loads(proc.stdout)["error"])

    def test_a_flat_plan_still_verifies_without_a_change(self):
        flat = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, flat, True)
        write(flat, "tasks.md", "# Change: probe\n## Why\nx\n---\n- [ ] T009 probe\n  - Type: implementation\n"
                                "  - Depends on: none\n  - Verification: `python3 -c \"pass\"`\n"
                                "  - Acceptance:\n    - something\n\n")
        proc = subprocess.run([sys.executable, REIN, "verify", flat, "--plan", "--json"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(list(json.loads(proc.stdout)["results"]), ["T009"])


class LedgerJson(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)

    def cli(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run([sys.executable, REIN, "ledger", *args], capture_output=True, text=True,
                              timeout=60, env=env)

    def test_an_empty_ledger_with_json_is_an_empty_runs_list(self):
        proc = self.cli("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"runs": []})

    def test_an_empty_ledger_without_json_is_still_text(self):
        proc = self.cli()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ledger is empty", proc.stdout)


if __name__ == "__main__":
    unittest.main()
