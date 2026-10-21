"""`gate.regenerable`: gitignored files a setup regenerates are not a loss.

A worktree's setup script writes files such as `public/config.json` that differ from the
main checkout's copy; `rein done` and `rein quick close` listed them as "would be lost"
and wanted `--yes`. Patterns declared in `gate.regenerable` (flow.config.json, the repo's
own or the plan root's) do not count."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

from test_ship_done import FakeOrca, QuickCloseBase, commit_file, git  # noqa: E402


class Regenerable(QuickCloseBase):
    def configure(self, gate):
        commit_file(self.seed, "flow.config.json", json.dumps({"gate": gate}), "config")
        commit_file(self.seed, ".gitignore", "public/config.json\n.env\ngen/\n", "ignore")
        git(self.seed, "push", "-q", "origin", "develop")

    def generated(self, path):
        main = os.path.join(self.tmp, os.path.basename(path) + "-main")
        for where, text in ((main, "main"), (path, "worktree")):
            os.makedirs(os.path.join(where, "public"), exist_ok=True)
            with open(os.path.join(where, "public", "config.json"), "w") as fh:
                fh.write(text)

    def test_a_declared_regenerable_file_needs_no_yes(self):
        self.configure({"regenerable": ["public/config.json"]})
        path, _ = self.linked("fix")
        self.record(path)
        self.generated(path)
        code, orca = self.close(arg=path)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())
        self.assertEqual(orca.rm_paths(), [path])

    def test_a_directory_pattern_covers_its_files(self):
        self.configure({"regenerable": ["gen/"]})
        path, _ = self.linked("fix")
        self.record(path)
        os.makedirs(os.path.join(path, "gen", "api"))
        with open(os.path.join(path, "gen", "api", "client.ts"), "w") as fh:
            fh.write("x")
        code, _ = self.close(arg=path)
        self.assertEqual(code, 0, self.text())

    def test_an_undeclared_ignored_file_still_warns(self):
        self.configure({"regenerable": ["public/config.json"]})
        path, _ = self.linked("fix")
        self.record(path)
        self.generated(path)
        with open(os.path.join(path, ".env"), "w") as fh:
            fh.write("S=1")
        code, orca = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertIn(".env", self.text())
        self.assertNotIn("public/config.json", self.text())
        self.assertEqual(orca.rm_paths(), [])

    def test_without_config_the_generated_file_still_warns(self):
        self.configure({})
        path, _ = self.linked("fix")
        self.record(path)
        self.generated(path)
        code, _ = self.close(arg=path)
        self.assertEqual(code, 1)
        self.assertIn("public/config.json", self.text())

    def test_rein_done_reads_the_plan_roots_declaration_too(self):
        self.configure({})
        path, _ = self.linked("fix")
        self.generated(path)
        root = self.plan_repo()
        with open(os.path.join(root, "flow.config.json"), "w") as fh:
            json.dump({"plan": {"source": "openspec"}, "gate": {"regenerable": ["public/config.json"]}}, fh)
        git(root, "add", "flow.config.json")
        git(root, "commit", "-q", "-m", "cfg")
        self.state(root, {"api": path})
        code, _ = self.run_done(root)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("WARNING", self.text())


if __name__ == "__main__":
    unittest.main()
