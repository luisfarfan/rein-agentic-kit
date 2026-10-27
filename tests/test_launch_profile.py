"""`ship.launchProfile: lean`: the settings a Claude implementer worker starts with.

The profile is a `.claude/settings.local.json` Rein puts in the worktree before a Claude implementer
starts there and puts back before the auditor starts (and when the run stops). These tests pin the
merge, the put-back, the refusals that keep a worker from committing the file, the measurement
behind `rein benchmark startup`, and the driver's timing against FakeOrca.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "rein", "lib"))

import launch_profile as lp  # noqa: E402
import ship  # noqa: E402
import ship_decide as sd  # noqa: E402
from test_ship import FakeOrca, ShipCase, _flag, scripted_gate  # noqa: E402

ALWAYS = lambda _path: True  # noqa: E731 -- a worktree git ignores the file in


def _git_repo(path, ignore=""):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True)
    # The developer's global excludes (Claude Code adds settings.local.json there) must not decide.
    subprocess.run(["git", "-C", path, "config", "core.excludesFile", os.devnull], check=True)
    if ignore:
        with open(os.path.join(path, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(ignore)
    return path


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestLeanSettings(unittest.TestCase):
    def test_turns_the_plugins_off_and_denies_the_unused_tools(self):
        out = lp.lean_settings({}, ["superpowers@dir", "aws@official"])
        self.assertEqual(out["enabledPlugins"], {"superpowers@dir": False, "aws@official": False})
        self.assertEqual(out["permissions"]["deny"], lp.LEAN_DENY)

    def test_never_denies_toolsearch(self):
        """Denying it with the user's MCP servers configured loads every MCP tool up front (533k)."""
        self.assertNotIn("ToolSearch", lp.LEAN_DENY)
        self.assertNotIn("ToolSearch", lp.lean_settings({}, [])["permissions"]["deny"])

    def test_every_denied_name_is_a_rule_claude_accepts(self):
        """`advisor` was "an invalid permission rule": an interactive Claude stopped on the warning
        before the brief, and Orca aborted the worker's start (seen in the first real pilot)."""
        for name in lp.LEAN_DENY:
            self.assertRegex(name, r"^[A-Z][A-Za-z]*$", name)

    def test_keeps_what_the_owner_already_had(self):
        owner = {"enabledMcpjsonServers": ["context7"], "skillOverrides": {"x": "off"},
                 "enabledPlugins": {"mine@m": True},
                 "permissions": {"allow": ["Bash(ls)"], "deny": ["WebFetch", "ToolSearch"]}}
        out = lp.lean_settings(owner, ["p@x"])
        self.assertEqual(out["enabledMcpjsonServers"], ["context7"])
        self.assertEqual(out["skillOverrides"], {"x": "off"})
        self.assertEqual(out["enabledPlugins"], {"mine@m": True, "p@x": False})
        self.assertEqual(out["permissions"]["allow"], ["Bash(ls)"])
        # The owner's own deny entries stay, ToolSearch included: it is theirs, not the profile's.
        self.assertEqual(out["permissions"]["deny"][:2], ["WebFetch", "ToolSearch"])
        self.assertEqual(owner["permissions"]["deny"], ["WebFetch", "ToolSearch"], "the input is not mutated")

    def test_enabled_user_plugins_reads_only_the_enabled_ones(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"enabledPlugins": {"a@x": True, "b@x": False, "c@x": "yes"}}, fh)
        self.addCleanup(os.remove, fh.name)
        self.assertEqual(lp.enabled_user_plugins(fh.name), ["a@x"])
        self.assertEqual(lp.enabled_user_plugins(fh.name + ".missing"), [])


class TestApplyAndRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wt = os.path.join(self.tmp.name, "wt")
        os.makedirs(self.wt)
        self.file = os.path.join(self.wt, lp.REL_PATH)

    def test_without_a_file_restore_removes_it_and_the_folder(self):
        rec = lp.apply(self.wt, plugins=["p@x"], ignored=ALWAYS)
        self.assertIsNone(rec["original"])
        self.assertEqual(json.loads(_read(self.file))["enabledPlugins"], {"p@x": False})
        self.assertEqual(lp.restore(self.wt, rec), "")
        self.assertFalse(os.path.exists(os.path.join(self.wt, ".claude")))

    def test_restore_puts_the_owners_file_back_byte_for_byte(self):
        os.makedirs(os.path.dirname(self.file))
        owner = '{\n  "skillOverrides": {"x": "off"}\n}\n'
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write(owner)
        rec = lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        self.assertEqual(rec["original"], owner)
        self.assertIn("Artifact", _read(self.file))
        self.assertEqual(lp.restore(self.wt, rec), "")
        self.assertEqual(_read(self.file), owner)

    def test_a_second_apply_keeps_the_owners_original(self):
        rec = lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        again = lp.apply(self.wt, rec, plugins=[], ignored=ALWAYS)
        self.assertEqual(again, rec)
        self.assertIsNone(again["original"], "Rein's own file is never taken for the owner's")

    def test_a_profile_written_by_an_older_rein_is_replaced_and_keeps_the_owners_original(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write('{"theirs": true}\n')
        old = lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        stale = json.loads(old["written"])
        stale["permissions"]["deny"].append("advisor")  # what the first version wrote
        old = dict(old, written=json.dumps(stale))
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write(old["written"])
        new = lp.apply(self.wt, old, plugins=[], ignored=ALWAYS)
        self.assertEqual(new["original"], '{"theirs": true}\n')
        self.assertNotIn("advisor", _read(self.file))
        lp.restore(self.wt, new)
        self.assertEqual(_read(self.file), '{"theirs": true}\n')

    def test_a_file_changed_after_the_write_is_left_alone(self):
        rec = lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write('{"theirs": true}\n')
        self.assertIn("changed since Rein wrote it", lp.restore(self.wt, rec))
        self.assertEqual(_read(self.file), '{"theirs": true}\n')

    def test_an_owner_edit_between_applies_becomes_the_file_to_come_back_to(self):
        rec = lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write('{"theirs": true}\n')
        rec2 = lp.apply(self.wt, rec, plugins=[], ignored=ALWAYS)
        self.assertEqual(rec2["original"], '{"theirs": true}\n')
        self.assertTrue(json.loads(_read(self.file))["theirs"])
        lp.restore(self.wt, rec2)
        self.assertEqual(_read(self.file), '{"theirs": true}\n')

    def test_prepare_writes_nothing(self):
        lp.prepare(self.wt, plugins=[], ignored=ALWAYS)
        self.assertFalse(os.path.exists(self.file))

    def test_invalid_json_is_refused_and_untouched(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(lp.ProfileError):
            lp.apply(self.wt, plugins=[], ignored=ALWAYS)
        self.assertEqual(_read(self.file), "{not json")

    def test_a_file_git_would_not_ignore_is_refused(self):
        repo = _git_repo(os.path.join(self.tmp.name, "repo"))
        with self.assertRaises(lp.ProfileError) as cm:
            lp.apply(repo, plugins=[])
        self.assertIn("not ignored by git", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(repo, lp.REL_PATH)))

    def test_a_gitignored_file_is_applied(self):
        repo = _git_repo(os.path.join(self.tmp.name, "repo"), ignore=".claude/settings.local.json\n")
        lp.apply(repo, plugins=[])
        self.assertTrue(os.path.exists(os.path.join(repo, lp.REL_PATH)))
        status = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True).stdout
        self.assertNotIn("settings.local.json", status)


class TestMeasure(unittest.TestCase):
    def runner(self, usage=None, out=None, code=0):
        seen = {}

        def run(argv, cwd, timeout):
            seen["argv"], seen["cwd"] = argv, cwd
            path = os.path.join(cwd, lp.REL_PATH)
            seen["settings"] = _read(path) if os.path.exists(path) else None
            seen["claude_md"] = os.path.exists(os.path.join(cwd, "CLAUDE.md"))
            body = out if out is not None else json.dumps({"is_error": False, "result": "OK", "usage": usage or {}})
            return code, body, ""
        run.seen = seen
        return run

    def test_counts_cached_and_uncached_input(self):
        run = self.runner({"input_tokens": 2, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 12222,
                           "output_tokens": 4})
        res = lp.measure("default", runner=run)
        self.assertEqual(res, {"profile": "default", "tokens": 21224, "error": ""})
        self.assertIsNone(run.seen["settings"])
        argv = run.seen["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertIn("--no-session-persistence", argv)

    def test_lean_runs_with_the_profile_in_place_and_the_projects_instructions(self):
        src = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(src, ignore_errors=True))
        with open(os.path.join(src, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# project rules\n")
        run = self.runner({"input_tokens": 1})
        saved = lp.enabled_user_plugins
        lp.enabled_user_plugins = lambda *a: ["p@x"]
        self.addCleanup(setattr, lp, "enabled_user_plugins", saved)
        lp.measure("lean", source=src, runner=run)
        self.assertIn("Artifact", run.seen["settings"])
        self.assertTrue(run.seen["claude_md"])
        self.assertFalse(os.path.exists(run.seen["cwd"]), "the scratch repo is removed")

    def test_errors_are_reported_not_raised(self):
        self.assertIn("without JSON", lp.measure("default", runner=self.runner(out="boom", code=1))["error"])
        self.assertEqual(lp.measure("default", runner=self.runner(
            out=json.dumps({"is_error": True, "result": "not logged in"})))["error"], "not logged in")
        with self.assertRaises(ValueError):
            lp.measure("huge")


class TestConfig(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(sd.ShipConfig.from_flow_config({}).launch_profile, "default")

    def test_lean_is_read_and_anything_else_keeps_the_default(self):
        self.assertEqual(sd.ShipConfig.from_flow_config({"ship": {"launchProfile": "lean"}}).launch_profile, "lean")
        for bad in ("LEAN", "minimal", 1, None, ""):
            self.assertEqual(sd.ShipConfig.from_flow_config({"ship": {"launchProfile": bad}}).launch_profile,
                             "default", bad)


class ProfileOrca(FakeOrca):
    """Records, at every worker start, whether the api worktree held Rein's profile."""

    def __init__(self, root, settings_path, **kw):
        super().__init__(root, **kw)
        self.settings_path = settings_path
        self.seen = []  # (kind, worktree selector, agent, api profile present, --repo of a fresh worktree)

    def _answer(self, a):
        if a[:2] == ["orchestration", "worker-start"]:
            spec = self.tasks.get(_flag(a, "--task"), {}).get("spec", "")
            kind = "audit" if "rein-audit" in spec else "implementer"
            present = os.path.exists(self.settings_path) and "Artifact" in _read(self.settings_path)
            self.seen.append((kind, _flag(a, "--worktree"), _flag(a, "--agent"), present, _flag(a, "--repo")))
        return super()._answer(a)


class TestDriver(ShipCase):
    def setUp(self):
        super().setUp()
        for repo in ("proxima-api", "proxima-admin"):
            os.makedirs(self.wtpath(repo), exist_ok=True)
        self.settings = os.path.join(self.wtpath("proxima-api"), lp.REL_PATH)
        saved = (lp.is_ignored, lp.enabled_user_plugins)
        lp.is_ignored, lp.enabled_user_plugins = ALWAYS, (lambda *a: ["p@x"])
        self.addCleanup(lambda: (setattr(lp, "is_ignored", saved[0]), setattr(lp, "enabled_user_plugins", saved[1])))

    def configure(self, profile):
        with open(os.path.join(self.root, "flow.config.json"), "w", encoding="utf-8") as fh:
            json.dump({"ship": {"launchProfile": profile}}, fh)

    def ship(self, **kw):
        kw.setdefault("override", ("claude", "sonnet"))  # the fixture's quota would pick Codex
        fake = ProfileOrca(self.root, self.settings)
        fake.on_audit = self.approve_audit()
        code = self.run_ship(fake, scripted_gate(1, 0), **kw)  # T001 red once: a retry reuses its worktree
        self.assertEqual(code, 0, self.out)
        return fake

    def api_seen(self, fake):
        api = "id:" + self.wtid("proxima-api")
        return [s for s in fake.seen if s[1] in ("new-top-level", api)]

    def test_lean_is_in_place_for_claude_implementers_in_an_existing_worktree_only(self):
        self.configure("lean")
        fake = self.ship()
        implementers = [s for s in fake.seen if s[0] == "implementer"]
        self.assertTrue(implementers and all(s[2] == "claude" for s in implementers), fake.seen)
        reused = [s for s in self.api_seen(fake) if s[0] == "implementer" and s[1] != "new-top-level"]
        self.assertTrue(reused, fake.seen)
        self.assertTrue(all(s[3] for s in reused), "every reused-worktree implementer starts lean: %r" % fake.seen)
        fresh = [s for s in fake.seen if s[1] == "new-top-level" and s[4] == "id:repo-proxima-api"]
        self.assertTrue(fresh and not any(s[3] for s in fresh), fake.seen)
        self.assertIn("starts with the next worker", self.out)

    def test_the_auditor_never_starts_on_the_lean_profile(self):
        self.configure("lean")
        fake = self.ship()
        audits = [s for s in fake.seen if s[0] == "audit"]
        self.assertTrue(audits, fake.seen)
        self.assertTrue(any(s[3] for s in fake.seen if s[0] == "implementer"), "lean was in place before: %r" % fake.seen)
        self.assertFalse(any(s[3] for s in audits), fake.seen)

    def test_the_owners_file_is_back_when_the_run_ends(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w", encoding="utf-8") as fh:
            fh.write('{"skillOverrides": {"x": "off"}}\n')
        self.configure("lean")
        self.ship()
        self.assertEqual(_read(self.settings), '{"skillOverrides": {"x": "off"}}\n')
        with open(self.state_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["launch_profiles"], {})

    def test_default_profile_writes_nothing(self):
        fake = self.ship()
        self.assertFalse(any(s[3] for s in fake.seen))
        self.assertFalse(os.path.exists(self.settings))

    def test_a_profile_that_cannot_be_applied_never_stops_the_run(self):
        self.configure("lean")
        lp.is_ignored = lambda _p: False
        fake = self.ship()
        self.assertFalse(any(s[3] for s in fake.seen))
        self.assertIn("launch profile not applied", self.out)
        self.assertEqual(self.out.count("launch profile not applied"), 1, "said once per worktree")

    def test_a_non_claude_implementer_gets_no_profile(self):
        self.configure("lean")
        fake = self.ship(override=("codex", "gpt-6.1-sol"))
        self.assertTrue(fake.seen)
        self.assertFalse(any(s[3] for s in fake.seen))
        self.assertFalse(os.path.exists(self.settings))


class TestBenchmarkCli(unittest.TestCase):
    """`rein benchmark startup` decides its exit code from the measurement; `claude` is never run."""

    @classmethod
    def setUpClass(cls):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(HERE), "plugins", "rein", "bin", "rein")
        loader = importlib.machinery.SourceFileLoader("rein_cli_bench", path)
        spec = importlib.util.spec_from_loader("rein_cli_bench", loader)
        cls.cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.cli)

    def run_cli(self, argv, tokens):
        saved = (self.cli._profile.measure, self.cli.shutil.which)
        self.cli._profile.measure = lambda p, **kw: {"profile": p, "tokens": tokens[p], "error": ""}
        self.cli.shutil.which = lambda name: "/bin/" + name
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                return self.cli.main(["benchmark", "startup"] + argv), buf.getvalue()
        finally:
            self.cli._profile.measure, self.cli.shutil.which = saved

    def test_both_profiles_and_the_saving(self):
        code, out = self.run_cli([], {"default": 59019, "lean": 21224})
        self.assertEqual(code, 0, out)
        self.assertIn("64%", out)

    def test_lean_over_the_limit_fails(self):
        """The trap the profile must never fall into: 533k when ToolSearch is denied."""
        code, out = self.run_cli(["--profile", "lean"], {"lean": 533541})
        self.assertEqual(code, 1, out)
        self.assertIn("OVER the limit of 30000", out)

    def test_the_default_profile_alone_is_bounded_only_when_asked(self):
        self.assertEqual(self.run_cli([], {"default": 59019, "lean": 21224})[0], 0)
        self.assertEqual(self.run_cli(["--profile", "default"], {"default": 59019})[0], 1)
        self.assertEqual(self.run_cli(["--profile", "default", "--max-tokens", "60000"], {"default": 59019})[0], 0)

    def test_json(self):
        code, out = self.run_cli(["--json", "--profile", "lean"], {"lean": 21224})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["results"][0]["tokens"], 21224)

    def test_usage(self):
        for argv in (["benchmark"], ["benchmark", "nope"], ["benchmark", "startup", "--profile", "x"],
                     ["benchmark", "startup", "--max-tokens", "lots"], ["benchmark", "startup", "--bogus"]):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.cli.main(argv), 2, argv)


if __name__ == "__main__":
    unittest.main()
