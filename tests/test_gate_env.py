"""Per-worktree environment for gates and workers (`gate.env` in flow.config.json).

Every worktree of a repo shared one test database, and every DB test module
dropped its schema: concurrent suites broke each other and produced false reds.
A repo (or the plan root) declares env vars with `{worktree}` / `{repo}`
placeholders; Rein expands them per worktree for every gate and verification it
runs there, and names them to the worker and auditor. Without config nothing
changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "rein", "lib"))
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")

import detect  # noqa: E402
import gate_env  # noqa: E402
import verify  # noqa: E402


def _write_config(root, cfg):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "flow.config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


class TestSlug(unittest.TestCase):
    def test_lowercase_and_only_safe_characters(self):
        self.assertEqual(gate_env.worktree_slug("/x/Data-Exports.Core"), "data_exports_core")

    def test_bounded_and_stable(self):
        long = "/wt/" + "sales-correlativo-recovery-with-a-very-long-name-indeed"
        slug = gate_env.worktree_slug(long)
        self.assertLessEqual(len(slug), 40)
        self.assertEqual(slug, gate_env.worktree_slug(long))
        self.assertRegex(slug, r"^[a-z0-9_]+$")

    def test_two_long_names_with_the_same_prefix_differ(self):
        a = gate_env.worktree_slug("/wt/" + "x" * 50 + "-one")
        b = gate_env.worktree_slug("/wt/" + "x" * 50 + "-two")
        self.assertNotEqual(a, b)

    def test_trailing_separator_and_empty_names(self):
        self.assertEqual(gate_env.worktree_slug("/wt/api/"), "api")
        self.assertEqual(gate_env.worktree_slug("/"), "wt")


class TestExpand(unittest.TestCase):
    def test_placeholders(self):
        env = gate_env.expand({"DB": "test_{worktree}", "R": "{repo}-x", "N": 5},
                              "/wt/Remove-Banners", "proxima-api")
        self.assertEqual(env, {"DB": "test_remove_banners", "R": "proxima-api-x", "N": "5"})

    def test_bad_shapes_are_ignored(self):
        self.assertEqual(gate_env.expand(None, "/wt/a", "r"), {})
        self.assertEqual(gate_env.expand(["X"], "/wt/a", "r"), {})
        self.assertEqual(gate_env.expand({"": "x", "BAD NAME": "y", "OK": None}, "/wt/a", "r"), {})


class TestFromConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_no_config_no_env(self):
        root = os.path.join(self.tmp.name, "repo")
        os.makedirs(root)
        self.assertEqual(gate_env.for_worktree(root, repo="repo"), {})

    def test_detect_exposes_the_gate_block(self):
        root = os.path.join(self.tmp.name, "repo")
        _write_config(root, {"gate": {"env": {"DB": "t_{worktree}"}}})
        self.assertEqual(detect.resolve(root)["gate"], {"env": {"DB": "t_{worktree}"}})

    def test_repo_config_wins_over_the_plan_root(self):
        plan = os.path.join(self.tmp.name, "plan")
        wt = os.path.join(self.tmp.name, "My-WT")
        _write_config(plan, {"gate": {"env": {"DB": "plan_{repo}", "ONLY_PLAN": "p"}}})
        _write_config(wt, {"gate": {"env": {"DB": "repo_{worktree}"}}})
        env = gate_env.for_worktree(wt, repo="api", env_root=plan)
        self.assertEqual(env, {"DB": "repo_my_wt", "ONLY_PLAN": "p"})

    def test_plan_root_alone_applies_to_every_repo(self):
        plan = os.path.join(self.tmp.name, "plan")
        wt = os.path.join(self.tmp.name, "wt-api")
        os.makedirs(wt)
        _write_config(plan, {"gate": {"env": {"DB": "t_{worktree}"}}})
        self.assertEqual(gate_env.for_worktree(wt, repo="api", env_root=plan), {"DB": "t_wt_api"})

    def test_export_lines_are_shell_quoted(self):
        lines = gate_env.export_lines({"B": "two words", "A": "x"})
        self.assertEqual(lines, ["export A=x", "export B='two words'"])


class TestRunOneEnv(unittest.TestCase):
    def test_the_command_sees_the_env_and_the_rest_of_the_environment(self):
        res = verify.run_one("t", 'printf "%s|%s" "$REIN_T_DB" "${PATH:+path}"', ".",
                             timeout=10, env={"REIN_T_DB": "db_x"})
        self.assertEqual(res.outcome, verify.OUTCOME_OK)
        self.assertEqual(res.output, "db_x|path")


class TestGateCliEnv(unittest.TestCase):
    def test_rein_gate_runs_the_slots_with_the_worktree_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "Feature-WT")
            _write_config(root, {"stack": "python", "gate": {"env": {"REIN_T_DB": "t_{worktree}_{repo}"}},
                                 "commands": {"test": 'test "$REIN_T_DB" = t_feature_wt_api'}})
            res = subprocess.run([sys.executable, REIN, "gate", root, "--repo", "api", "--json"],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["report"]["env"], ["REIN_T_DB"])

    def test_rein_env_prints_export_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "Feature-WT")
            _write_config(root, {"gate": {"env": {"REIN_T_DB": "t_{worktree}"}}})
            res = subprocess.run([sys.executable, REIN, "env", root, "--repo", "api"],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(res.stdout.strip(), "export REIN_T_DB=t_feature_wt")


if __name__ == "__main__":
    unittest.main()


class TestVerifyEnv(unittest.TestCase):
    def test_rein_verify_runs_with_the_worktree_env_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "Feature-WT")
            _write_config(root, {"stack": "python", "gate": {"env": {"REIN_T_DB": "t_{worktree}"}},
                                 "commands": {"test": 'test "$REIN_T_DB" = t_feature_wt'}})
            res = subprocess.run([sys.executable, REIN, "verify", root, "--json"], capture_output=True, text=True,
                                 env=dict(os.environ, HOME=tmp))
            self.assertEqual(json.loads(res.stdout)["results"]["test"]["outcome"], "ok", res.stdout)
