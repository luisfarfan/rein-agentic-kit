"""`rein quick close`: the record goes before `orca worktree rm`, and never from inside the worktree.

Orca refuses to remove a worktree holding an untracked file, and `.rein/quick.json` is one
wherever `.rein/` is not ignored. Only that file is deleted: `.rein/` itself may hold tracked
files (`.rein/reviews` is versioned in some repos)."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

from test_ship_done import FakeOrca, QuickCloseBase  # noqa: E402


class RefusesUntracked(FakeOrca):
    """Orca's real behaviour: a worktree with the record still in it is not removed."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.record_seen = []

    def __call__(self, argv):
        if argv[1:3] == ["worktree", "rm"]:
            path = argv[4][len("path:"):]
            present = os.path.isfile(os.path.join(path, ".rein", "quick.json"))
            self.record_seen.append(present)
            if present:
                self.calls.append(list(argv))
                return 1, json.dumps({"ok": False, "error": {"code": "dirty", "message": "untracked files"}})
        return super().__call__(argv)


class RecordBeforeRemove(QuickCloseBase):
    def test_the_record_is_deleted_before_orca_removes_the_worktree(self):
        path, _ = self.linked("fix")
        self.record(path)
        orca = RefusesUntracked()
        code, _ = self.close(arg=path, orca=orca)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(orca.record_seen, [False])
        self.assertIn("removed worktree %s" % path, self.text())

    def test_only_the_record_goes_never_the_rest_of_rein(self):
        path, _ = self.linked("fix")
        self.record(path)
        reviews = os.path.join(path, ".rein", "reviews")
        os.makedirs(reviews)
        with open(os.path.join(reviews, "episode.json"), "w") as fh:
            fh.write("{}")
        code, _ = self.close(arg=path, orca=FakeOrca(fail_rm=(path,)))
        self.assertEqual(code, 126)
        self.assertTrue(os.path.isfile(os.path.join(reviews, "episode.json")))

    def test_a_failed_remove_puts_the_record_back(self):
        path, _ = self.linked("fix")
        self.record(path)
        with open(os.path.join(path, ".rein", "quick.json")) as fh:
            before = fh.read()
        code, _ = self.close(arg=path, orca=FakeOrca(fail_rm=(path,)))
        self.assertEqual(code, 126)
        with open(os.path.join(path, ".rein", "quick.json")) as fh:
            self.assertEqual(fh.read(), before)
        self.assertIn("orca could not remove", self.text())


class FromInsideTheWorktree(QuickCloseBase):
    def test_run_inside_its_own_worktree_explains_and_removes_nothing(self):
        path, main = self.linked("fix")
        self.record(path)
        inside = os.path.join(path, "sub")
        os.makedirs(inside)
        code, orca = self.close(arg=path, cwd=inside)
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertTrue(os.path.isfile(os.path.join(path, ".rein", "quick.json")))
        self.assertIn("inside the worktree", self.text())
        self.assertIn("rein quick close", self.text())

    def test_without_argument_from_inside_also_explains(self):
        path, _ = self.linked("fix")
        self.record(path)
        code, orca = self.close(arg="", cwd=path, orca=FakeOrca(worktrees=[{"path": path}]))
        self.assertEqual(code, 1)
        self.assertEqual(orca.rm_paths(), [])
        self.assertIn("inside the worktree", self.text())


if __name__ == "__main__":
    unittest.main()
