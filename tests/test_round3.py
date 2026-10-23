"""Round 3 of the round-4 review (rereview4.md): R1 an unattributable external write is not green
for quick done nor ship; R2 a placeholder inside a shell script string is substituted, not repeated."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import affected  # noqa: E402
import guard  # noqa: E402
import ship_state  # noqa: E402
from test_guard import make_repo, run_git  # noqa: E402
from test_guard_attribution import other_session_commit  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import Drained, FakeOrca, ShipCase, scripted_gate  # noqa: E402


def sweep(path):
    """The worker edits app.py outside its worktree; another identity then commits everything."""
    with open(os.path.join(path, "app.py"), "w") as fh:
        fh.write("x = 999\n")
    other_session_commit(path, "rel.py", "r = 1\n", "chore: release")
    run_git(path, "add", "-A")
    env = dict(os.environ, GIT_AUTHOR_NAME="o", GIT_AUTHOR_EMAIL="other@o", GIT_COMMITTER_NAME="o",
               GIT_COMMITTER_EMAIL="other@o")
    subprocess.run(["git", "-C", path, "commit", "-q", "--no-verify", "-m", "chore: sweep"], check=True,
                   env=env, capture_output=True)


class Quick(DoneCase):
    def setUp(self):
        super().setUp()
        self.qa = os.path.join(self.root, "proxima-qa")
        make_repo(self.qa, hook=False)
        self.make_repo(outside_before=guard.outside_snapshot([self.qa]))
        self.write("src/foo.ts", "v2\n")
        sweep(self.qa)

    def test_a_swept_external_edit_leaves_quick_done_pending_not_green(self):
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("not green", self.text())
        self.assertIn("--accept-outside", self.text())
        self.assertIn("proxima-qa", self.text())

    def test_the_owner_resolves_it_explicitly(self):
        code, _, _ = self.done(accept_outside=True)
        self.assertEqual(code, 0, self.text())
        self.assertIn("accepted", self.text())


class QuickBenign(DoneCase):
    def test_a_fast_forward_to_work_that_existed_stays_green(self):
        qa = os.path.join(self.root, "proxima-qa")
        make_repo(qa, hook=False)
        run_git(qa, "branch", "other")
        run_git(qa, "checkout", "-q", "other")
        with open(os.path.join(qa, "o.py"), "w") as fh:
            fh.write("o = 1\n")
        run_git(qa, "add", "o.py")
        run_git(qa, "commit", "-q", "--no-verify", "-m", "feat: o")
        run_git(qa, "checkout", "-q", "develop")
        self.make_repo(outside_before=guard.outside_snapshot([qa]))
        self.write("src/foo.ts", "v2\n")
        run_git(qa, "merge", "-q", "--ff-only", "other")
        code, _, _ = self.done()
        self.assertEqual(code, 0, self.text())


class Ship(ShipCase):
    PLAN = "# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n  - Verification: `pnpm test`\n"

    def fake(self):
        self.write_plan(self.PLAN)
        qa = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(qa, hook=False)
        self.qa = qa
        fake = FakeOrca(self.root, repos=("proxima-api", "proxima-qa"))
        original = fake._answer

        def answer(a):
            out = original(a)
            if a[:2] == ["repo", "list"]:
                for r in out["repos"]:
                    if r["displayName"] == "proxima-qa":
                        r["path"] = qa
            return out
        fake._answer = answer
        fake.on_audit = self.approve_audit()
        return fake

    def gates(self):
        done = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task" and not done:
                done.append(1)
                sweep(self.qa)
            return 0, "ok"
        return gates

    def test_a_swept_external_edit_holds_the_task_for_the_owner(self):
        fake = self.fake()
        fake.autocomplete = False
        fake.pending = [lambda f: [f.done_message(d) for d in f.dispatches]] + [lambda f: []] * 4
        with self.assertRaises(Drained):
            self.run_ship(fake, self.gates())
        state = ship_state.load(self.state_file)
        self.assertEqual(state.tasks["T001"].status, "gated")
        questions = [c for c in fake.calls if c[:2] == ["orchestration", "gate-create"]]
        self.assertEqual(len(questions), 1)
        self.assertIn("proxima-qa", " ".join(questions[0]))

    def test_accepting_it_lets_the_task_finish(self):
        fake = self.fake()
        fake.auto_resolve = "accept"
        self.assertEqual(self.run_ship(fake, self.gates()), 0, self.out)

    def test_skipping_it_fails_the_run(self):
        fake = self.fake()
        fake.auto_resolve = "skip"
        self.assertEqual(self.run_ship(fake, self.gates()), 1, self.out)


class ScriptContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, body in (("test_a.py", "import unittest\nclass A(unittest.TestCase):\n    def test_a(self):\n        pass\n"),
                           ("test_b.py", "import unittest\nclass B(unittest.TestCase):\n    def test_b(self):\n        self.fail('b')\n")):
            with open(os.path.join(self.tmp, name), "w") as fh:
                fh.write(body)

    def run_template(self, template):
        cmd = affected.expand_target(template.replace("PY", sys.executable), ["test_a.py", "test_b.py"])
        return subprocess.run(cmd, shell=True, cwd=self.tmp, capture_output=True, text=True)

    def test_sh_c_single_quotes_run_every_target_and_the_failing_one_fails(self):
        proc = self.run_template("sh -c 'PY -m unittest {target}'")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Ran 2 tests", proc.stderr)

    def test_sh_c_double_quotes_too(self):
        proc = self.run_template('sh -c "PY -m unittest {target}"')
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Ran 2 tests", proc.stderr)

    def test_a_path_with_spaces_inside_the_script_stays_one_argument(self):
        cmd = affected.expand_target("sh -c 'printf \"%s|\" {target}'", ["a b.py", "c.py"])
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
        self.assertEqual(out, "a b.py|c.py|")

    def test_the_plain_forms_are_unchanged(self):
        self.assertEqual(affected.expand_target("pytest -q {target}", ["a", "b"]), "pytest -q a b")
        self.assertEqual(affected.expand_target("ng test --include={target}", ["a", "b"]),
                         "ng test --include=a --include=b")
        self.assertEqual(affected.expand_target('ng test --include="{target}"', ["a", "b"]),
                         'ng test --include="a" --include="b"')


if __name__ == "__main__":
    unittest.main()
