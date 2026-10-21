"""I8: Rein notices a worker that wrote outside its worktree.

A Codex worker ran a sibling repo's update script, which added a rule import to
proxima-qa/CLAUDE.md -- a repo only its owner may change. The briefs forbid it; now Rein
also checks it: when a worker (or quick agent) is launched it notes the git state of
every other checkout Orca knows, and when its task ends any new change in one of them
fails the task with the files named. (Prevention -- a sandbox the agent cannot write out
of -- is the agent's or Orca's to provide; this is detection.)"""

from __future__ import annotations

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

import guard  # noqa: E402
from test_guard import commit, make_repo, run_git  # noqa: E402
from test_quick_driver import DoneCase  # noqa: E402
from test_ship import FakeOrca, ShipCase  # noqa: E402


def write(path, rel, text):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a") as fh:
        fh.write(text)


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qa = os.path.join(self.tmp, "proxima-qa")
        make_repo(self.qa, hook=False)

    def test_an_untouched_sibling_is_clean(self):
        before = guard.outside_snapshot([self.qa])
        self.assertEqual(guard.outside_changes(before), [])

    def test_an_edit_in_a_sibling_is_named(self):
        before = guard.outside_snapshot([self.qa])
        write(self.qa, "CLAUDE.md", "@rules/new.md\n")
        problems = guard.outside_changes(before)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("proxima-qa", problems[0])
        self.assertIn("CLAUDE.md", problems[0])

    def test_a_commit_in_a_sibling_is_caught(self):
        before = guard.outside_snapshot([self.qa])
        commit(self.qa, "app.py", "x = 5\n", "chore: sneaky")
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_a_file_already_dirty_and_edited_further_is_caught(self):
        write(self.qa, "app.py", "y = 1\n")
        before = guard.outside_snapshot([self.qa])
        write(self.qa, "app.py", "y = 2\n")
        self.assertEqual(len(guard.outside_changes(before)), 1)

    def test_rein_state_and_ignored_paths_do_not_count(self):
        before = guard.outside_snapshot([self.qa])
        write(self.qa, ".rein/ship/demo.json", "{}")
        write(self.qa, "tasks.md", "- [x] T001\n")
        self.assertEqual(guard.outside_changes(before, ignore=[os.path.join(self.qa, "tasks.md")]), [])

    def test_a_vanished_checkout_is_not_a_crash(self):
        before = guard.outside_snapshot([self.qa])
        shutil.rmtree(self.qa)
        self.assertEqual(guard.outside_changes(before), [])


class Quick(DoneCase):
    def test_quick_done_fails_when_the_agent_wrote_in_another_repo(self):
        qa = os.path.join(self.root, "proxima-qa")
        make_repo(qa, hook=False)
        self.make_repo(outside_before=guard.outside_snapshot([qa]))
        self.write("src/foo.ts", "v2\n")
        write(qa, "CLAUDE.md", "@rules/new.md\n")
        code, _, _ = self.done()
        self.assertEqual(code, 1, self.text())
        self.assertIn("CLAUDE.md", self.text())


class Ship(ShipCase):
    def test_a_worker_that_writes_in_a_sibling_repo_is_retried_with_the_reason(self):
        self.write_plan("# Change: demo\n\n- [ ] T001 Add the api endpoint\n  - Repo: proxima-api\n"
                        "  - Verification: `pnpm test`\n")
        qa = os.path.join(self.tmp.name, "proxima-qa")
        make_repo(qa, hook=False)
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
        calls = []

        def gates(p, unit_only, timeout=None, **kw):
            if kw.get("tier") == "task":
                calls.append(p)
                if len(calls) == 1:  # the first worker ran the sibling's update script
                    write(qa, "CLAUDE.md", "@rules/new.md\n")
                else:  # the owner put it back before the retry finished
                    os.remove(os.path.join(qa, "CLAUDE.md"))
            return 0, "ok"
        fake.on_audit = self.approve_audit()
        self.run_ship(fake, gates)
        specs = [t["spec"] for t in fake.tasks.values() if t["title"].startswith("T001")]
        self.assertEqual(len(specs), 2, self.out)
        self.assertIn("CLAUDE.md", specs[1])
        self.assertIn("outside", specs[1])


if __name__ == "__main__":
    unittest.main()
