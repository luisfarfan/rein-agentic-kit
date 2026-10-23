"""Round 2 of the round-4 review (review4.md): R1 guard, R2 shell-aware {target}, R3 report vs
quick close, R4 report delivered once, M1 automatic probes are not owner decisions."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import affected  # noqa: E402
import guard  # noqa: E402
import quick_inbox  # noqa: E402
from orca_client import OrcaClient  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_guard_attribution import other_session_commit  # noqa: E402
from test_guard_outside import write  # noqa: E402
from test_quick_close_record import RefusesUntracked  # noqa: E402
from test_quick_inbox import Case, Orca  # noqa: E402
from test_ship_done import QuickCloseBase  # noqa: E402


class R1(unittest.TestCase):
    """Whatever cannot be attributed is a visible warning, never silence; what is attributable blocks."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "qa")
        make_repo(self.qa, hook=False)

    def review(self, before):
        warnings = []
        return guard.outside_changes(before, warnings=warnings), warnings

    def test_a_workers_pending_edit_swept_into_another_sessions_commit_is_not_green(self):
        before = guard.outside_snapshot([self.qa])
        write_over = os.path.join(self.qa, "app.py")
        with open(write_over, "w") as fh:
            fh.write("x = 999\n")  # the worker, outside its worktree
        other_session_commit(self.qa, "rel.py", "r = 1\n", "chore: release")  # `git add rel.py` only...
        run_git(self.qa, "add", "-A")
        env = dict(os.environ, GIT_AUTHOR_NAME="o", GIT_AUTHOR_EMAIL="other@o", GIT_COMMITTER_NAME="o",
                   GIT_COMMITTER_EMAIL="other@o")
        subprocess.run(["git", "-C", self.qa, "commit", "-q", "--no-verify", "-m", "chore: sweep"], check=True,
                       env=env, capture_output=True)
        problems, warnings = self.review(before)
        self.assertTrue(problems or warnings, "a swept-in edit must not come out green")

    def test_amending_in_the_external_checkout_is_the_workers(self):
        before = guard.outside_snapshot([self.qa])
        with open(os.path.join(self.qa, "app.py"), "w") as fh:
            fh.write("x = 999\n")
        run_git(self.qa, "add", "app.py")
        run_git(self.qa, "commit", "-q", "--amend", "--no-verify", "-m", "chore: base amended")
        problems, warnings = self.review(before)
        self.assertEqual(len(problems), 1, (problems, warnings))

    def test_a_foreign_commit_made_in_the_checkout_is_a_warning_not_silence(self):
        before = guard.outside_snapshot([self.qa])
        other_session_commit(self.qa, "rel.py", "r = 1\n", "chore: release")
        problems, warnings = self.review(before)
        self.assertEqual(problems, [])
        self.assertEqual(len(warnings), 1, warnings)

    def test_a_pre_existing_branch_checkout_stays_a_warning(self):
        run_git(self.qa, "checkout", "-q", "-b", "other")
        commit(self.qa, "o.py", "o = 1\n", "feat: other")
        run_git(self.qa, "checkout", "-q", "develop")
        run_git(self.qa, "reset", "-q", "--hard", "HEAD")
        before = guard.outside_snapshot([self.qa])
        run_git(self.qa, "checkout", "-q", "other")
        problems, warnings = self.review(before)
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])  # a fast-forward-able checkout of existing work: nothing created


class R2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.probe = os.path.join(self.tmp, "probe.py")
        with open(self.probe, "w") as fh:
            fh.write("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")

    def run_cmd(self, template, targets):
        cmd = affected.expand_target(template.replace("PROBE", "%s %s" % (sys.executable, self.probe)), targets)
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.tmp).stdout
        return [json.loads(line) for line in out.splitlines() if line.startswith("[")], out

    def test_a_shell_operator_after_the_placeholder_is_left_alone(self):
        runs, out = self.run_cmd("PROBE {target}; echo AFTER", ["a", "b"])
        self.assertEqual(runs, [["a", "b"]])
        self.assertIn("AFTER", out)

    def test_operators_glued_to_the_placeholder(self):
        runs, out = self.run_cmd("PROBE {target}&& echo AFTER", ["a", "b"])
        self.assertEqual(runs, [["a", "b"]])
        self.assertIn("AFTER", out)

    def test_a_glued_flag_repeats_and_keeps_spaces(self):
        runs, _ = self.run_cmd("PROBE --include={target}", ["a", "my dir/b"])
        self.assertEqual(runs, [["--include=a", "--include=my dir/b"]])

    def test_a_double_quoted_placeholder_keeps_names_intact(self):
        runs, _ = self.run_cmd('PROBE "{target}"', ["a", "my dir/b"])
        self.assertEqual(runs, [["a", "my dir/b"]])

    def test_a_single_quoted_flag_with_a_quote_in_the_name(self):
        runs, _ = self.run_cmd("PROBE --include='{target}'", ["a", "it's"])
        self.assertEqual(runs, [["--include=a", "--include=it's"]])

    def test_a_dollar_in_a_double_quoted_name_is_not_expanded(self):
        runs, _ = self.run_cmd('PROBE --include="{target}"', ["a$HOME"])
        self.assertEqual(runs, [["--include=a$HOME"]])

    def test_a_single_target_with_a_trailing_operator(self):
        runs, out = self.run_cmd("PROBE {target}; echo AFTER", ["a"])
        self.assertEqual(runs, [["a"]])
        self.assertIn("AFTER", out)


class R3(QuickCloseBase):
    def test_report_then_close_removes_the_worktree(self):
        path, _ = self.linked("fix")
        self.record(path)
        self.assertEqual(quick_inbox.report(path, "Final report; tests pass", say=lambda *_: None), 0)

        class Strict(RefusesUntracked):
            def __call__(self, argv):
                if argv[1:3] == ["worktree", "rm"]:
                    wt = argv[4][len("path:"):]
                    others = subprocess.run(["git", "-C", wt, "ls-files", "--others", "--exclude-standard"],
                                            capture_output=True, text=True).stdout.split()
                    if others:
                        self.calls.append(list(argv))
                        return 1, json.dumps({"ok": False, "error": {"code": "dirty",
                                                                      "message": "untracked files: %s" % others}})
                return super().__call__(argv)
        code, _ = self.close(arg=path, orca=Strict())
        self.assertEqual(code, 0, self.text())
        self.assertIn("removed worktree %s" % path, self.text())

    def test_a_failed_remove_puts_the_report_back(self):
        from test_ship_done import FakeOrca
        path, _ = self.linked("fix")
        self.record(path)
        quick_inbox.report(path, "Final report", say=lambda *_: None)
        code, _ = self.close(arg=path, orca=FakeOrca(fail_rm=(path,)))
        self.assertEqual(code, 126)
        with open(os.path.join(path, ".rein", "quick-report.md")) as fh:
            self.assertIn("Final report", fh.read())


class R4(Case):
    def test_two_watch_runs_deliver_a_report_once(self):
        quick_inbox.report(self.ws, "Informe final", say=self.out.append)
        client = OrcaClient(executable="ORCA", runner=Orca(worktrees=[self.ws]))
        results = []
        for _ in range(3):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = quick_inbox.watch(self.ws, client, cwd=self.ws, once=True, clock=lambda: 0)
            results.append((code, buf.getvalue()))
        self.assertEqual(results[0][0], 0)
        self.assertIn("REPORT", results[0][1])
        self.assertEqual([r[0] for r in results[1:]], [1, 1])
        self.assertEqual([r[1] for r in results[1:]], ["", ""])

    def test_a_later_report_is_still_delivered(self):
        quick_inbox.report(self.ws, "uno", say=self.out.append)
        client = OrcaClient(executable="ORCA", runner=Orca(worktrees=[self.ws]))
        with contextlib.redirect_stdout(io.StringIO()):
            quick_inbox.watch(self.ws, client, cwd=self.ws, once=True, clock=lambda: 0)
        quick_inbox.report(self.ws, "dos", say=self.out.append)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = quick_inbox.watch(self.ws, client, cwd=self.ws, once=True, clock=lambda: 0)
        self.assertEqual(code, 0)
        self.assertIn("dos", buf.getvalue())
        self.assertNotIn("uno", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
