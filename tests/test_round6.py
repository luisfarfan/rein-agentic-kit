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
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import gate_log  # noqa: E402
import guard  # noqa: E402
import ship_state  # noqa: E402
from test_guard import commit as _commit, make_repo, run_git  # noqa: E402
from test_guard_outside import write  # noqa: E402
from test_quick_driver import DoneCase, TERMINAL, _AuditOrca  # noqa: E402
from test_ship import Drained, FakeOrca, ShipCase, scripted_gate  # noqa: E402

def _clone_works():
    src = tempfile.mkdtemp()
    dst = os.path.join(tempfile.mkdtemp(), "c")
    try:
        os.makedirs(os.path.join(src, "d"))
        return subprocess.run(guard._clone_command(os.path.join(src, "d"), dst), capture_output=True).returncode == 0
    finally:
        shutil.rmtree(src, True)


CLONE = unittest.skipUnless(_clone_works(), "this filesystem cannot clone copy-on-write")
REIN = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")

PRE_COMMIT = """#!/bin/sh
if git diff --cached | grep -q '^+.*FORBIDDEN'; then echo "a forbidden token is staged"; exit 1; fi
exit 0
"""


def commit(path, rel, text, msg):
    os.makedirs(os.path.dirname(os.path.join(path, rel)), exist_ok=True)
    _commit(path, rel, text, msg)


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
        self.assertIn("past the newest 1 commits", problems[0])
        self.assertIn("unchecked", problems[0])

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


# --- 6b -----------------------------------------------------------------------------------------

ESLINT = """#!/bin/sh
if grep -q FORBIDDEN "$@"; then echo "src/a.ts:1:1  error  forbidden token  no-forbidden"; exit 1; fi
exit 0
"""
SH_HOOK = """#!/bin/sh
./node_modules/.bin/eslint $(git diff --cached --name-only)
"""
LEFTHOOK_YML = """pre-commit:
  jobs:
    - name: eslint
      glob: "*.ts"
      run: pnpm exec eslint {staged_files}
"""


def add_deps(path):
    """A gitignored node_modules with a local `eslint`, as the worker's worktree has after its setup."""
    bin_dir = os.path.join(path, "node_modules", ".bin")
    os.makedirs(bin_dir, exist_ok=True)
    tool = os.path.join(bin_dir, "eslint")
    with open(tool, "w") as fh:
        fh.write(ESLINT)
    os.chmod(tool, 0o755)


def js_repo(path, hook_body=SH_HOOK):
    base = make_repo(path, hook=False)
    put(path, ".gitignore", "node_modules/\n.rein/\n")
    run_git(path, "add", ".gitignore")
    run_git(path, "commit", "-q", "--no-verify", "-m", "chore: ignore")
    base = run_git(path, "rev-parse", "HEAD").strip()
    add_deps(path)
    install_hook(path, "pre-commit", hook_body)
    return base


class DependenciesAndUncertainty(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = js_repo(self.repo)

    @CLONE
    def test_a_hook_that_needs_the_workers_dependencies_runs_with_them_and_passes(self):
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")  # --no-verify
        self.assertEqual(guard.check(self.repo, self.base, {}), [])

    @CLONE
    def test_a_hook_that_really_rejects_the_tree_is_red_with_its_error_lines_and_a_log(self):
        commit(self.repo, "src/a.ts", "FORBIDDEN\n", "feat: bad")
        [problem] = guard.check(self.repo, self.base, {})
        self.assertIn("does not pass the repo's pre-commit hook", problem)
        self.assertIn("forbidden token", problem)
        log = problem.rsplit("full output: ", 1)[1]
        self.assertIn("forbidden token", open(log).read())
        os.remove(log)

    @CLONE
    def test_the_workers_dependency_folder_is_not_modified(self):
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        before = sorted(os.listdir(os.path.join(self.repo, "node_modules", ".bin")))
        guard.check(self.repo, self.base, {})
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, "node_modules", ".bin"))), before)
        self.assertEqual(run_git(self.repo, "status", "--porcelain=v1"), "")

    def test_a_missing_dependency_is_unchecked_not_red(self):
        shutil.rmtree(os.path.join(self.repo, "node_modules"))
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        sha = run_git(self.repo, "rev-parse", "HEAD").strip()
        unchecked = []
        problems = guard.check(self.repo, self.base, {}, unchecked=unchecked)
        self.assertEqual(problems, [])
        [item] = unchecked
        self.assertEqual(item["sha"], sha)
        self.assertIn("could not run", item["text"])
        # Without somewhere to put it, it fails closed rather than passing.
        self.assertEqual(len(guard.check(self.repo, self.base, {})), 1)

    def test_the_owner_accepts_an_unchecked_commit_by_sha_and_it_is_recorded(self):
        shutil.rmtree(os.path.join(self.repo, "node_modules"))
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        sha = run_git(self.repo, "rev-parse", "HEAD").strip()
        unchecked, accepted = [], []
        self.assertEqual(guard.check(self.repo, self.base, {}, accept_hooks=[sha[:9]], unchecked=unchecked,
                                     hook_accepted=accepted), [])
        self.assertEqual(unchecked, [])
        self.assertEqual([a["id"] for a in accepted], [sha])
        self.assertEqual(guard.unmatched_hook_accepts(["deadbeefdead"], accepted),
                         ["--accept-hook deadbeefdead is not a commit whose pre-commit hook was unchecked"])

    def test_an_accept_never_turns_a_red_green(self):
        commit(self.repo, "src/a.ts", "FORBIDDEN\n", "feat: bad")
        sha = run_git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(len(guard.check(self.repo, self.base, {}, accept_hooks=[sha], unchecked=[])), 1)


@CLONE
@unittest.skipUnless(shutil.which("lefthook") and shutil.which("pnpm"), "lefthook and pnpm are not installed")
class LefthookWithPnpm(unittest.TestCase):
    """The repos Rein is used on: lefthook running `pnpm exec eslint {staged_files}`."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        make_repo(self.repo, hook=False)
        put(self.repo, ".gitignore", "node_modules/\n.rein/\n")
        put(self.repo, "package.json", '{"name": "x", "private": true}\n')
        put(self.repo, "lefthook.yml", LEFTHOOK_YML)
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "--no-verify", "-m", "chore: tooling")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()
        subprocess.run(["lefthook", "install"], cwd=self.repo, check=True, capture_output=True)
        add_deps(self.repo)

    def test_a_valid_commit_with_dependencies_in_the_worktree_is_green(self):
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        unchecked = []
        self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=unchecked), [])
        self.assertEqual(unchecked, [])

    def test_a_commit_the_linter_rejects_is_red(self):
        commit(self.repo, "src/a.ts", "FORBIDDEN\n", "feat: bad")
        problems = guard.check(self.repo, self.base, {}, unchecked=[])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("forbidden token", problems[0])

    def test_without_the_dependencies_it_is_unchecked_and_names_the_cause(self):
        shutil.rmtree(os.path.join(self.repo, "node_modules"))
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        unchecked = []
        self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=unchecked), [])
        [item] = unchecked
        self.assertIn("eslint", item["text"])  # the cause survives, not just lefthook's banner


class AutofixHook(unittest.TestCase):
    FIX = """#!/bin/sh
for f in $(git diff --cached --name-only); do
  sed 's/BAD/GOOD/' "$f" > "$f.tmp" && mv "$f.tmp" "$f" && git add "$f"
done
exit 0
"""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)
        install_hook(self.repo, "pre-commit", self.FIX)

    def test_a_commit_the_hook_would_have_rewritten_is_red_with_the_fix(self):
        commit(self.repo, "src.txt", "BAD\n", "feat: skipped the autofix")  # --no-verify
        [problem] = guard.check(self.repo, self.base, {})
        self.assertIn("only after the hook rewrites src.txt", problem)
        self.assertIn("+GOOD", problem)
        self.assertEqual(run_git(self.repo, "show", "HEAD:src.txt"), "BAD\n")  # nothing was changed for it
        self.assertEqual(run_git(self.repo, "status", "--porcelain=v1"), "")

    def test_a_commit_made_through_the_hook_is_already_fixed_and_green(self):
        put(self.repo, "src.txt", "BAD\n")
        run_git(self.repo, "add", "src.txt")
        run_git(self.repo, "commit", "-q", "-m", "feat: through the hook")
        self.assertEqual(run_git(self.repo, "show", "HEAD:src.txt"), "GOOD\n")
        self.assertEqual(guard.check(self.repo, self.base, {}), [])


class QuickUncheckedHook(DoneCase):
    def setUp(self):
        super().setUp()
        self.make_repo()
        put(self.ws, ".gitignore", ".rein/\nnode_modules/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "chore: ignore deps")
        base = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        rec = json.load(open(os.path.join(self.ws, ".rein", "quick.json")))
        rec["base_sha"] = base
        put(self.ws, ".rein/quick.json", json.dumps(rec))
        install_hook(self.ws, "pre-commit", SH_HOOK)  # needs ./node_modules/.bin/eslint, which is missing
        self.write("src/bar.ts", "export const b = 2\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "fix: bar")
        self.sha = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()

    def test_it_is_not_green_and_not_red_and_names_the_exit(self):
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("UNCHECKED", self.text())
        self.assertNotIn("does not pass the repo's pre-commit hook", self.text())
        self.assertIn("--accept-hook", self.text())
        self.assertIn("could not run in the re-check", last_log(self.ws)["guard"])

    def test_it_never_retries_by_itself_and_the_same_answer_comes_back(self):
        self.assertEqual(self.done()[0], 1)
        self.assertEqual(self.done()[0], 1)

    def test_accept_hook_makes_it_green_and_is_logged_by_commit(self):
        code, _, _ = self.done(accept_hooks=[self.sha[:10]])
        self.assertEqual(code, 0, self.text())
        self.assertIn(self.sha[:12], last_log(self.ws)["guardAccepted"])

    def test_naming_a_commit_that_was_not_unchecked_is_an_error(self):
        code, _, _ = self.done(accept_hooks=["deadbeefdead"])
        self.assertEqual(code, 1, self.text())
        self.assertIn("--accept-hook deadbeefdead", self.text())

    @CLONE
    def test_with_the_dependencies_present_it_is_green(self):
        add_deps(self.ws)
        self.assertEqual(self.done()[0], 0, self.text())


class ShipUncheckedHook(ShipCase):
    PLAN = ("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
            "  - Verification: `pnpm test`\n")

    def test_the_task_is_held_for_the_owner_and_no_retry_is_spent(self):
        self.write_plan(self.PLAN)
        path = self.wtpath("proxima-api")
        base = js_repo(path)
        shutil.rmtree(os.path.join(path, "node_modules"))
        run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:
                    commit(path, "src/a.ts", "export const a = 1\n", "feat: endpoint")
            return 0, "ok"
        fake = FakeOrca(self.root, repos=("proxima-api",))
        fake.on_audit = self.approve_audit()
        fake.auto_resolve = "accept"
        self.run_ship(fake, gates)
        [gate] = [g for g in fake.gates if "pre-commit hook" in g["question"]]
        self.assertEqual(gate["options"], '["accept", "retry", "skip"]')
        self.assertEqual(len(calls), 1, self.out)  # one worker, one gate: nothing was retried
        self.assertEqual(ship_state.load(self.state_file).tasks["T001"].status, "done")

    def test_accept_outside_hook_remembers_the_owners_answer_for_the_run(self):
        self.write_plan(self.PLAN)
        path = self.wtpath("proxima-api")
        base = js_repo(path)
        shutil.rmtree(os.path.join(path, "node_modules"))
        run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        commit(path, "src/a.ts", "export const a = 1\n", "feat: endpoint")
        sha = run_git(path, "rev-parse", "HEAD").strip()
        guard.record_accepts(self.root, hooks=[sha[:10]])
        unchecked, accepted = [], []
        problems = guard.check(path, base, {}, accept_hooks=guard.load_accepts(self.root)["hooks"],
                               unchecked=unchecked, hook_accepted=accepted)
        self.assertEqual((problems, unchecked, [a["id"] for a in accepted]), ([], [], [sha]))


class AcceptOutsideHookCli(unittest.TestCase):
    def test_records_a_hook_acceptance(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        proc = subprocess.run([sys.executable, REIN, "accept-outside", "--root", root, "--hook", "abcdef1234"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(guard.load_accepts(root)["hooks"], ["abcdef1234"])
        short = subprocess.run([sys.executable, REIN, "accept-outside", "--root", root, "--hook", "abc"],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(short.returncode, 2)


class QuickAuditRound6b(DoneCase):
    def setUp(self):
        super().setUp()
        self.admin = os.path.join(self.root, "proxima-admin")
        self.other = os.path.join(self.root, "proxima-api")

    def auditor(self, *effects):
        approve = self.reviewer("APPROVED")

        def play():
            for effect in effects:
                effect()
            approve()
        return play

    def test_r3_one_repos_ignore_is_not_another_repos(self):
        make_repo(self.admin, config={"gate": {"outsideIgnore": ["generated/"]}}, hook=False)
        put(self.admin, "generated/file.txt", "1\n") if os.makedirs(os.path.join(self.admin, "generated")) is None else None
        make_repo(self.other, hook=False)
        os.makedirs(os.path.join(self.other, "generated"))
        put(self.other, "generated/file.txt", "1\n")
        run_git(self.other, "add", "-A")
        run_git(self.other, "commit", "-q", "--no-verify", "-m", "chore: generated")
        before = guard.outside_snapshot([self.admin, self.other])
        self.assertEqual(before[self.admin]["ignore"], ["generated/"])
        self.assertNotIn("ignore", before[self.other])
        self.make_repo(outside_before=before)
        self.write("src/auth/login.ts", "v2\n")
        orca = _AuditOrca(on_start=self.auditor(lambda: put(self.other, "generated/file.txt", "2\n")))
        code, _, _ = self.done(orca=orca, env=TERMINAL)
        self.assertEqual(code, 1, self.text())
        self.assertIn("generated/file.txt", self.text())

    def test_r3_the_repos_own_ignore_still_applies_to_it(self):
        make_repo(self.admin, config={"gate": {"outsideIgnore": ["generated/"]}}, hook=False)
        os.makedirs(os.path.join(self.admin, "generated"))
        put(self.admin, "generated/file.txt", "1\n")
        before = guard.outside_snapshot([self.admin])
        self.make_repo(outside_before=before)
        self.write("src/auth/login.ts", "v2\n")
        orca = _AuditOrca(on_start=self.auditor(lambda: put(self.admin, "generated/file.txt", "2\n")))
        self.assertEqual(self.done(orca=orca, env=TERMINAL)[0], 0, self.text())

    def test_r4_a_workers_allow_config_does_not_let_the_auditor_edit_it(self):
        self.make_repo(allow_config=["flow.config.json"])
        self.write("src/auth/login.ts", "v2\n")
        cfg = json.loads(open(os.path.join(self.ws, "flow.config.json")).read())
        cfg["gate"] = {"affectedMax": 3}
        orca = _AuditOrca(on_start=self.auditor(lambda: put(self.ws, "flow.config.json", json.dumps(cfg))))
        self.assertEqual(self.done(orca=orca, env=TERMINAL)[0], 1, self.text())
        self.out.clear()
        code, _, orca2 = self.done(orca=_AuditOrca(), env=TERMINAL)  # not reverted, not accepted
        self.assertEqual(code, 1, self.text())
        self.assertIn("(audit)", self.text())
        self.assertNotIn("auditoría vigente", self.text())
        self.assertEqual(orca2.called("orchestration", "worker-start"), [])
        put(self.ws, "flow.config.json", json.dumps(dict(cfg, gate={})))  # back to what it was for the gates
        cfg.pop("gate")
        put(self.ws, "flow.config.json", json.dumps(cfg))
        self.out.clear()
        self.assertEqual(self.done(orca=_AuditOrca(), env=TERMINAL)[0], 0, self.text())
        self.assertNotIn("audit_breach", json.load(open(os.path.join(self.ws, ".rein", "quick.json"))))


class ShipAuditOtherWorktrees(ShipCase):
    PLAN = ("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
            "  - Verification: `pnpm test`\n\n- [ ] T002 Add the admin screen\n  - Repo: proxima-admin\n"
            "  - Verification: `pnpm test`\n")

    def test_r5_the_auditor_writing_into_another_worktree_of_the_run_is_held(self):
        self.write_plan(self.PLAN)
        for repo in ("proxima-api", "proxima-admin"):
            path = self.wtpath(repo)
            base = make_repo(path, hook=False)
            run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-admin"))
        approve = self.approve_audit()

        def on_audit():
            put(self.wtpath("proxima-admin"), "app.py", "x = 'audited'\n")
            approve()
            fake.auto_resolve = "reject"
        fake.on_audit = on_audit
        self.run_ship(fake, scripted_gate())
        [gate] = [g for g in fake.gates if "guard found the auditor" in g["question"]]
        self.assertIn("app.py", gate["question"])
        self.assertEqual(ship_state.load(self.state_file).audit_result, "rejected")

    def test_r5_the_auditors_own_worktree_is_not_watched_as_outside(self):
        self.write_plan(self.PLAN)
        for repo in ("proxima-api", "proxima-admin"):
            path = self.wtpath(repo)
            base = make_repo(path, hook=False)
            run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-admin"))
        fake.on_audit = self.approve_audit()  # writes reviewed.txt in the auditor's own worktree
        self.assertEqual(self.run_ship(fake, scripted_gate()), 0, self.out)
        self.assertEqual(ship_state.load(self.state_file).audit_result, "approved")


# --- 6c -----------------------------------------------------------------------------------------

ENOENT_HOOK = """#!/bin/sh
printf 'ENOENT.ts\\n  1:1  error  Unexpected console statement  no-console\\n'
exit 1
"""


class RejectionIsAlwaysRed(unittest.TestCase):
    """B1: a hook that ran and rejected the tree is red, whatever its output happens to contain."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)

    def hook(self, body):
        install_hook(self.repo, "pre-commit", "#!/bin/sh\n" + body)
        commit(self.repo, "src/ENOENT.ts", "console.log(1)\n", "feat: log")
        self.sha = run_git(self.repo, "rev-parse", "HEAD").strip()

    def verdict(self, accept=True):
        unchecked, accepted = [], []
        problems = guard.check(self.repo, self.base, {}, accept_hooks=[self.sha] if accept else [], unchecked=unchecked,
                               hook_accepted=accepted)
        return problems, unchecked, accepted

    def test_a_file_called_enoent_with_a_lint_error_is_red_and_not_acceptable(self):
        self.hook("printf 'ENOENT.ts\\n  1:1  error  Unexpected console statement  no-console\\n'\nexit 1\n")
        problems, unchecked, accepted = self.verdict()
        self.assertEqual((len(problems), unchecked, accepted), (1, [], []), problems)
        self.assertIn("no-console", problems[0])

    def test_a_free_substring_is_not_a_missing_tool(self):
        self.hook("echo 'ENOENT: no such file or directory, open x'; echo 'No such file or directory'; exit 1\n")
        problems, unchecked, _ = self.verdict()
        self.assertEqual((len(problems), unchecked), (1, []), problems)

    def test_rejection_signals_win_even_over_a_127(self):
        self.hook("echo 'src/a.ts:3:1: error unexpected'; echo 'sh: eslint: command not found'; exit 127\n")
        problems, unchecked, _ = self.verdict()
        self.assertEqual((len(problems), unchecked), (1, []), problems)

    def test_the_exact_missing_tool_forms_are_unchecked(self):
        for body in ("echo 'sh: 1: eslint: not found'; exit 1\n", "echo 'sh: eslint: command not found'; exit 1\n",
                     "echo 'ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL  Command \"eslint\" not found'; exit 1\n",
                     "exit 127\n"):
            with self.subTest(body=body):
                run_git(self.repo, "reset", "-q", "--hard", self.base)
                self.hook(body)
                problems, unchecked, _ = self.verdict(accept=False)
                self.assertEqual(problems, [])
                self.assertEqual(len(unchecked), 1)

    def test_the_enoent_file_through_quick_and_ship_stays_red_with_both_accepts(self):
        self.hook("printf 'ENOENT.ts\\n  1:1  error  Unexpected console statement  no-console\\n'\nexit 1\n")
        guard.record_accepts(self.tmp, hooks=[self.sha])
        problems = guard.check(self.repo, self.base, {}, accept_hooks=guard.load_accepts(self.tmp)["hooks"],
                               unchecked=[])
        self.assertEqual(len(problems), 1)


class QuickEnoent(DoneCase):
    def test_done_is_red_and_accept_hook_does_not_save_it(self):
        self.make_repo()
        install_hook(self.ws, "pre-commit", ENOENT_HOOK)
        self.write("src/ENOENT.ts", "console.log(1)\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "feat: log")
        sha = subprocess.run(["git", "-C", self.ws, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.done()[0], 1, self.text())
        self.assertIn("does not pass the repo's pre-commit hook", self.text())
        self.out.clear()
        self.assertEqual(self.done(accept_hooks=[sha])[0], 1, self.text())


@CLONE
class CopyOnWriteDependencies(unittest.TestCase):
    """B2: the re-check gets clones of the dependency folders; nothing it writes reaches the worker."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = js_repo(self.repo)

    def test_a_hook_writing_tracked_config_through_the_dependency_folder_is_red_and_the_worker_is_untouched(self):
        install_hook(self.repo, "pre-commit",
                     "#!/bin/sh\ncd node_modules\nprintf '{\"commands\":{\"test\":\"false\"}}\\n' > ../flow.config.json\nexit 0\n")
        original = open(os.path.join(self.repo, "flow.config.json")).read()
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        problems = guard.check(self.repo, self.base, {}, unchecked=[])
        self.assertTrue(any("only after the hook rewrites" in p for p in problems), problems)
        with open(os.path.join(self.repo, "flow.config.json")) as fh:
            self.assertEqual(fh.read(), original)
        self.assertEqual(run_git(self.repo, "status", "--porcelain=v1"), "")
        # and the next look blames nothing the re-run did
        self.assertFalse(any("it changes flow.config.json" in p for p in guard.check(self.repo, self.base, {}, unchecked=[])))

    def test_a_cache_the_hook_writes_inside_the_dependencies_stays_in_the_clone(self):
        install_hook(self.repo, "pre-commit", "#!/bin/sh\nmkdir -p node_modules/.cache && echo x > node_modules/.cache/y\nexit 0\n")
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=[]), [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "node_modules", ".cache")))

    def test_without_copy_on_write_it_is_unchecked_and_says_so(self):
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        unchecked = []
        with mock.patch.object(guard, "_clone_command", lambda s, t: ["false"]):
            self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=unchecked), [])
        [item] = unchecked
        self.assertIn("copy-on-write", item["text"])
        self.assertIn("does not copy them for real", item["text"])


class QuickBreachSurvivesAnEmptyDiff(DoneCase):
    """B3: undoing the local work does not settle what the auditor did elsewhere."""

    def test_the_kept_rejection_is_checked_when_nothing_changed_locally(self):
        admin = os.path.join(self.root, "proxima-admin")
        make_repo(admin, hook=False)
        put(admin, "app.py", "x = 1\n")
        before = guard.outside_snapshot([admin])
        self.make_repo(outside_before=before)
        self.write("src/auth/login.ts", "v2\n")
        put(admin, "app.py", "x = 2\n")  # a legitimate edit by the owner, accepted by name below
        approve = self.reviewer("APPROVED")

        def auditor():
            put(admin, "app.py", "x = 1\n")  # undoes it
            approve()
        code, _, _ = self.done(orca=_AuditOrca(on_start=auditor), env=TERMINAL,
                               accept_paths=[os.path.join(admin, "app.py")])
        self.assertEqual(code, 1, self.text())
        self.git("checkout", "--", "src/auth/login.ts")  # only the worker's local change goes back
        self.out.clear()
        code, _, _ = self.done(orca=_AuditOrca(), env=TERMINAL)
        self.assertEqual(code, 1, self.text())
        self.assertNotIn("nothing changed since the task started", self.text())
        self.assertIn("(audit)", self.text())
        put(admin, "app.py", "x = 2\n")  # the owner's edit restored: the rejection clears
        self.out.clear()
        self.assertEqual(self.done(orca=_AuditOrca(), env=TERMINAL, accept_paths=[os.path.join(admin, "app.py")])[0],
                         0, self.text())


class ShipAuditFrozenIgnoresOfOtherWorktrees(ShipCase):
    """B4: a worker's later `outsideIgnore` never narrows what the auditor is watched for."""

    PLAN = ("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
            "  - Verification: `pnpm test`\n\n- [ ] T002 Add the admin screen\n  - Repo: proxima-admin\n"
            "  - Gate config: flow.config.json\n  - Verification: `pnpm test`\n")

    def test_an_ignore_the_worker_added_does_not_hide_the_auditors_write(self):
        self.write_plan(self.PLAN)
        for repo in ("proxima-api", "proxima-admin"):
            path = self.wtpath(repo)
            base = make_repo(path, hook=False)
            put(path, "app.py", "x = 0\n")
            run_git(path, "add", "-A")
            run_git(path, "commit", "-q", "--no-verify", "-m", "chore: app")
            base = run_git(path, "rev-parse", "HEAD").strip()
            run_git(path, "update-ref", "refs/remotes/origin/develop", base)
        admin = self.wtpath("proxima-admin")
        done = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task" and p == admin and not done:
                done.append(1)
                put(admin, "flow.config.json", json.dumps({"commands": {"test": "true"},
                                                          "gate": {"outsideIgnore": ["app.py"]}}))
                run_git(admin, "add", "-A")
                run_git(admin, "commit", "-q", "--no-verify", "-m", "chore: gate config")
            return 0, "ok"
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-admin"))
        approve = self.approve_audit()

        def on_audit():
            put(admin, "app.py", "x = 777\n")
            approve()
            fake.auto_resolve = "reject"
        fake.on_audit = on_audit
        self.run_ship(fake, gates)
        [gate] = [g for g in fake.gates if "guard found the auditor" in g["question"]]
        self.assertIn("app.py", gate["question"])
        self.assertEqual(ship_state.load(self.state_file).audit_result, "rejected")


# --- 6d -----------------------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RealNodeMissingRequire(unittest.TestCase):
    """C1: a bad import in the code the hook checks is the code's error, not a missing tool."""

    def test_a_require_of_a_missing_source_is_red_and_no_accept_saves_it(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        base = make_repo(repo, hook=False)
        install_hook(repo, "pre-commit", "#!/bin/sh\nnode src/bad.cjs\n")
        commit(repo, "src/bad.cjs", "require('./missing-source.cjs')\n", "feat: bad import")
        sha = run_git(repo, "rev-parse", "HEAD").strip()
        proc = subprocess.run(["node", "src/bad.cjs"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)  # the hook, run for real, rejects the program
        self.assertIn("Cannot find module", proc.stderr)
        unchecked, accepted = [], []
        problems = guard.check(repo, base, {}, accept_hooks=[sha], unchecked=unchecked, hook_accepted=accepted)
        self.assertEqual((len(problems), unchecked, accepted), (1, [], []), problems)
        self.assertIn("does not pass the repo's pre-commit hook", problems[0])

    def test_the_other_ambiguous_forms_are_red_too(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        base = make_repo(repo, hook=False)
        for body in ("echo \"ModuleNotFoundError: No module named 'x'\"; exit 1\n",
                     "echo 'Error: Cannot find module ./x imported from src/a.ts'; exit 1\n",
                     "echo '.git/hooks/pre-commit: line 2: ./tool: No such file or directory'; exit 1\n"):
            with self.subTest(body=body):
                run_git(repo, "reset", "-q", "--hard", base)
                install_hook(repo, "pre-commit", "#!/bin/sh\n" + body)
                commit(repo, "src/a.ts", "x\n", "feat: a")
                unchecked = []
                self.assertEqual(len(guard.check(repo, base, {}, unchecked=unchecked)), 1)
                self.assertEqual(unchecked, [])


@CLONE
class LinksIntoTheWorker(unittest.TestCase):
    """C2: a symlink inside the cloned dependencies that points into the worker is moved to the re-check."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = js_repo(self.repo)
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        commit(self.repo, "src/bar.ts", "original\n", "chore: bar")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()
        os.symlink(os.path.join(self.repo, "src"), os.path.join(self.repo, "node_modules", "linked-source"))

    def bar(self):
        with open(os.path.join(self.repo, "src", "bar.ts")) as fh:
            return fh.read()

    def test_an_absolute_link_to_the_workers_src_is_relocated_and_the_worker_is_untouched(self):
        install_hook(self.repo, "pre-commit", "#!/bin/sh\nprintf changed-by-hook > node_modules/linked-source/bar.ts\nexit 0\n")
        commit(self.repo, "src/bar.ts", "committed\n", "feat: bar")
        problems = guard.check(self.repo, self.base, {}, unchecked=[])
        self.assertTrue(any("only after the hook rewrites src/bar.ts" in p for p in problems), problems)  # via the re-check's own copy
        self.assertEqual(self.bar(), "committed\n")
        self.assertEqual(run_git(self.repo, "status", "--porcelain=v1"), "")

    def test_a_legitimate_link_between_dependencies_still_works(self):
        os.makedirs(os.path.join(self.repo, "node_modules", "real"))
        tool = os.path.join(self.repo, "node_modules", "real", "eslint")
        shutil.copy(os.path.join(self.repo, "node_modules", ".bin", "eslint"), tool)
        os.remove(os.path.join(self.repo, "node_modules", ".bin", "eslint"))
        os.symlink(tool, os.path.join(self.repo, "node_modules", ".bin", "eslint"))  # absolute, inside the folder
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=[]), [])

    def test_a_link_that_cannot_be_moved_is_unchecked(self):
        commit(self.repo, "src/a.ts", "export const a = 1\n", "feat: a")
        unchecked = []
        with mock.patch.object(guard, "_relocate_links", side_effect=guard.Unrelocatable("x -> /y: denied")):
            self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=unchecked), [])
        self.assertIn("could not be moved", unchecked[0]["text"])

    def test_if_a_write_still_reaches_the_worker_the_result_is_void_and_rein_touches_nothing(self):
        install_hook(self.repo, "pre-commit", "#!/bin/sh\nprintf changed-by-hook > node_modules/linked-source/bar.ts\nexit 0\n")
        commit(self.repo, "src/bar.ts", "committed\n", "feat: bar")
        write(self.repo, "wip.txt", "the workers own uncommitted work\n")
        with mock.patch.object(guard, "_relocate_links", return_value=0):  # the first defence fails
            problems = guard.check(self.repo, self.base, {}, unchecked=[])
        self.assertTrue(any("el worktree del worker cambi\u00f3 durante la comprobaci\u00f3n del hook" in p
                            and "no toc\u00f3 nada" in p and "src/bar.ts" in p for p in problems), problems)
        self.assertEqual(self.bar(), "changed-by-hook")  # whatever happened there, Rein did not undo it
        with open(os.path.join(self.repo, "wip.txt")) as fh:
            self.assertEqual(fh.read(), "the workers own uncommitted work\n")

    def test_a_relative_link_inside_the_worktree_stays_inside_the_recheck(self):
        os.symlink("../src", os.path.join(self.repo, "node_modules", "rel-source"))
        install_hook(self.repo, "pre-commit", "#!/bin/sh\nprintf changed-by-hook > node_modules/rel-source/bar.ts\nexit 0\n")
        commit(self.repo, "src/bar.ts", "committed\n", "feat: bar")
        problems = guard.check(self.repo, self.base, {}, unchecked=[])
        self.assertTrue(any("rewrites" in p for p in problems), problems)
        self.assertEqual(self.bar(), "committed\n")


# --- 6e -----------------------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("pnpm") and shutil.which("node"), "pnpm and node are not installed")
class RealPnpmRecursiveExec(unittest.TestCase):
    """D2: pnpm prints ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL when the tool ran and failed too."""

    def test_a_real_rejection_through_pnpm_r_exec_is_red_and_no_accept_saves_it(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        base = make_repo(repo, hook=False)
        put(repo, "package.json", '{"name": "x", "private": true}\n')
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "--no-verify", "-m", "chore: pkg")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        install_hook(repo, "pre-commit", "#!/bin/sh\npnpm -r exec node bad.cjs\n")
        commit(repo, "bad.cjs", "require('./missing-source.cjs')\n", "feat: bad import")
        sha = run_git(repo, "rev-parse", "HEAD").strip()
        proc = subprocess.run(["pnpm", "-r", "exec", "node", "bad.cjs"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL", proc.stdout + proc.stderr)
        unchecked, accepted = [], []
        problems = guard.check(repo, base, {}, accept_hooks=[sha], unchecked=unchecked, hook_accepted=accepted)
        self.assertEqual((len(problems), unchecked, accepted), (1, [], []), problems)

    def test_the_bare_marker_is_not_a_missing_tool(self):
        self.assertFalse(guard._env_failure(1, "ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL Command failed with exit code 1: node x"))
        self.assertTrue(guard._env_failure(1, 'ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL  Command "eslint" not found'))


class WorkerChangedDuringTheCheck(unittest.TestCase):
    """D1: if the worker's worktree changes while the hook runs -- the owner, the worker, another session --
    Rein cannot tell who did it, and it never restores, overwrites or deletes anything there."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = make_repo(self.repo, hook=False)
        self.signal, self.release = os.path.join(self.tmp, "signal"), os.path.join(self.tmp, "release")
        install_hook(self.repo, "pre-commit", (
            "#!/bin/sh\n: > '%s'\ni=0\nwhile [ ! -e '%s' ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done\nexit 0\n"
            % (self.signal, self.release)))
        put(self.repo, "wip.py", "tracked\n")
        run_git(self.repo, "add", "wip.py")
        run_git(self.repo, "commit", "-q", "--no-verify", "-m", "chore: wip file")
        self.base = run_git(self.repo, "rev-parse", "HEAD").strip()
        commit(self.repo, "src/a.py", "x = 1\n", "feat: a")

    def during(self, work):
        """Run the guard; once the hook has started (the worker snapshot is taken), `work()` happens in the
        worker, then the hook is released."""
        import threading
        result = {}
        thread = threading.Thread(target=lambda: result.update(problems=guard.check(self.repo, self.base, {}, unchecked=[])))
        thread.start()
        for _ in range(200):
            if os.path.exists(self.signal):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.signal), "the hook never started")
        work()
        open(self.release, "w").close()
        thread.join(30)
        self.assertFalse(thread.is_alive())
        problems = result["problems"]
        self.assertTrue(any("cambi\u00f3 durante la comprobaci\u00f3n del hook" in p and "no toc\u00f3 nada" in p
                            for p in problems), problems)
        return problems

    def snapshot(self):
        return (run_git(self.repo, "status", "--porcelain=v1", "--untracked-files=all"),
                run_git(self.repo, "diff", "--cached"), run_git(self.repo, "diff"))

    def test_an_edit_of_tracked_work_in_progress_stays_byte_for_byte(self):
        put(self.repo, "wip.py", "work in progress\n")  # dirty before the check
        state = {}

        def work():
            put(self.repo, "wip.py", "newer edit, made meanwhile\n")
            state["after"] = self.snapshot()
        self.during(work)
        self.assertEqual(open(os.path.join(self.repo, "wip.py"), "rb").read(), b"newer edit, made meanwhile\n")
        self.assertEqual(self.snapshot(), state["after"])

    def test_a_new_untracked_file_is_kept(self):
        def work():
            put(self.repo, "note.txt", "a new note\n")
        self.during(work)
        self.assertEqual(open(os.path.join(self.repo, "note.txt"), "rb").read(), b"a new note\n")

    def test_a_large_untracked_file_is_kept(self):
        payload = os.urandom(6 * 1024 * 1024 + 1000)

        def work():
            with open(os.path.join(self.repo, "big.bin"), "wb") as fh:
                fh.write(payload)
        self.during(work)
        self.assertEqual(open(os.path.join(self.repo, "big.bin"), "rb").read(), payload)

    def test_a_staged_edit_keeps_its_content_and_its_index_entry(self):
        state = {}

        def work():
            put(self.repo, "wip.py", "staged meanwhile\n")
            run_git(self.repo, "add", "wip.py")
            state["after"] = self.snapshot()
        self.during(work)
        self.assertEqual(self.snapshot(), state["after"])
        self.assertIn("staged meanwhile", state["after"][1])
        self.assertEqual(open(os.path.join(self.repo, "wip.py"), "rb").read(), b"staged meanwhile\n")

    def test_without_a_change_the_same_hook_is_green(self):
        open(self.release, "w").close()
        self.assertEqual(guard.check(self.repo, self.base, {}, unchecked=[]), [])


if __name__ == "__main__":
    unittest.main()
