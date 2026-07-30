"""Tests for the retrieval-tool provisioner.

The reason this module exists is a measured failure: across seven runs of this
repo, `graphify` was invoked ZERO times, because the repo has no index — so the
kit's graph-first retrieval prompts were inert while the README advertised them.
So the assertions here are mostly about what the probe REFUSES to claim:
"installed" and "usable" are different facts, and conflating them is how a
recommendation becomes decoration.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import setup  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")


def _active_languages(yml_path: str) -> list[str]:
    """The `languages:` block of a generated project.yml, without a yaml dep."""
    with open(yml_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = lines.index("languages:") + 1
    langs = []
    for line in lines[start:]:
        if line.startswith("- "):
            langs.append(line[2:].strip())
        else:
            break
    return langs


class Tree:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        for rel, body in self.files.items():
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        return self.tmp.name

    def __exit__(self, *exc):
        self.tmp.cleanup()


class TestProbeIsReadOnly(unittest.TestCase):
    def test_probe_never_writes_anything(self):
        with Tree({"README.md": "x"}) as root:
            before = sorted(os.listdir(root))
            setup.probe(root)
            self.assertEqual(sorted(os.listdir(root)), before)

    def test_every_tool_states_why_it_is_recommended(self):
        """A tool with no stated benefit cannot be judged, and this kit has
        already shipped two mechanisms it could not justify."""
        state = setup.probe(".")
        for name, entry in state["tools"].items():
            self.assertTrue(entry["why"].strip(), f"{name} has no rationale")

    def test_shape_is_stable_for_every_tool(self):
        state = setup.probe(".")
        for name, entry in state["tools"].items():
            for key in ("present", "path", "why", "installable"):
                self.assertIn(key, entry, f"{name} missing {key}")


class TestInstalledIsNotUsable(unittest.TestCase):
    """The distinction the whole module turns on."""

    def test_graphify_without_an_index_is_reported_inert(self):
        with Tree({"README.md": "x"}) as root:
            entry = setup.probe(root)["tools"]["graphify"]
        if not entry["present"]:
            self.skipTest("graphify not installed on this machine")
        self.assertFalse(entry["indexed"])
        self.assertIn("inert", entry)
        self.assertIn("no index", entry["inert"])

    def test_graphify_with_an_index_is_not_inert(self):
        with Tree({"graphify-out/graph.json": "{}"}) as root:
            entry = setup.probe(root)["tools"]["graphify"]
        if not entry["present"]:
            self.skipTest("graphify not installed on this machine")
        self.assertTrue(entry["indexed"])
        self.assertNotIn("inert", entry)

    def test_codegraph_without_an_index_is_reported_inert(self):
        with Tree({"README.md": "x"}) as root:
            entry = setup.probe(root)["tools"]["codegraph"]
        if not entry["present"]:
            self.skipTest("codegraph not installed on this machine")
        self.assertFalse(entry["indexed"])
        self.assertIn("inert", entry)
        self.assertIn("no index", entry["inert"])
        # its own one-command fix, named -- not just the generic message
        self.assertIn("codegraph init", entry["inert"])

    def test_codegraph_with_an_index_is_not_inert(self):
        with Tree({".codegraph/codegraph.db": "x"}) as root:
            entry = setup.probe(root)["tools"]["codegraph"]
        if not entry["present"]:
            self.skipTest("codegraph not installed on this machine")
        self.assertTrue(entry["indexed"])
        self.assertNotIn("inert", entry)

    def test_the_inert_list_is_separate_from_the_missing_list(self):
        """Present-but-useless and absent are different problems with different
        fixes; collapsing them would send the operator to the wrong one."""
        with Tree({"README.md": "x"}) as root:
            state = setup.probe(root)
        self.assertNotIn("missing", state["inert"])
        for name in state["inert"]:
            self.assertNotIn(name, state["missing"])

    def test_serena_carries_the_next_session_caveat(self):
        """An MCP server registered now is invisible until the next session.
        Reporting it as simply 'ok' would be a false green."""
        entry = setup.probe(".")["tools"]["serena"]
        self.assertIn("NEXT session", entry["caveat"])


class TestInstallDiscipline(unittest.TestCase):
    def test_install_skips_what_is_already_present(self):
        """PROBE FIRST: re-installing a present tool is the one thing this must
        never do, so it is asserted rather than trusted."""
        present = [n for n, e in setup.probe(".")["tools"].items() if e["present"]]
        if not present:
            self.skipTest("nothing installed to test against")
        report = setup.install(present)
        for name in present:
            self.assertTrue(report["results"][name]["ok"])
            self.assertIn("already present", report["results"][name]["reason"])
            self.assertNotIn("steps", report["results"][name])

    def test_an_unknown_tool_is_refused_not_guessed(self):
        report = setup.install(["definitely-not-a-tool"])
        self.assertFalse(report["results"]["definitely-not-a-tool"]["ok"])

    def test_a_tool_with_no_automatic_install_says_so(self):
        """graphify ships as a skill; claiming to install it would be a lie."""
        self.assertIsNone(setup.TOOLS["graphify"]["install"])
        self.assertTrue(setup.TOOLS["graphify"]["manual"])

    def test_codegraph_is_installed_via_npm_by_name(self):
        spec = setup.TOOLS["codegraph"]
        self.assertEqual(spec["install"], ["npm", "i", "-g", "@colbymchenry/codegraph"])
        self.assertEqual(spec["needs"], ["npm"])

    def test_missing_prerequisite_is_named_not_swallowed(self):
        original = setup.TOOLS["openspec"]["needs"]
        setup.TOOLS["openspec"]["needs"] = ["definitely-not-on-path"]
        try:
            entry = setup.probe(".")["tools"]["openspec"]
            self.assertFalse(entry["installable"])
            if not entry["present"]:
                self.assertIn("definitely-not-on-path", entry["blockedBy"])
        finally:
            setup.TOOLS["openspec"]["needs"] = original


class TestGitignoreAdvice(unittest.TestCase):
    def test_local_state_dirs_are_reported_when_absent(self):
        with Tree({".gitignore": "node_modules/\n"}) as root:
            missing = setup.gitignore_lines(root)
        self.assertIn(".serena/", missing)
        self.assertIn("graphify-out/", missing)
        self.assertIn(".codegraph/", missing)

    def test_already_ignored_entries_are_not_repeated(self):
        with Tree({".gitignore": ".serena/\ngraphify-out/\n.codegraph/\n"}) as root:
            self.assertEqual(setup.gitignore_lines(root), [])

    def test_a_repo_with_no_gitignore_does_not_raise(self):
        with Tree({"README.md": "x"}) as root:
            self.assertTrue(setup.gitignore_lines(root))


class TestRender(unittest.TestCase):
    def test_render_surfaces_inertness_not_just_presence(self):
        with Tree({"README.md": "x"}) as root:
            text = setup.render(setup.probe(root))
        if "graphify" in setup.probe(root)["inert"]:
            self.assertIn("inert", text)

    def test_render_names_the_fix_command_when_something_is_missing(self):
        state = setup.probe(".")
        state["missing"] = ["serena"]
        self.assertIn("rein setup --install", setup.render(state))


class TestSerenaIsACapabilityNotAnAssumption(unittest.TestCase):
    """Wiring serena into the CTX only helps if the CTX knows it is REACHABLE.
    'serena is installed' and 'this repo is activated for serena' are different
    facts -- the same installed-vs-usable split the rest of this module enforces."""

    def setUp(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))
        global detect
        import detect

    def test_serena_project_requires_the_activation_dir_not_just_the_binary(self):
        with Tree({"pyproject.toml": ""}) as root:
            caps = detect.resolve(root)["capabilities"]
        # The binary may or may not be on this machine, but a repo with no
        # .serena/ must NEVER claim serena-project.
        self.assertNotIn("serena-project", caps)

    def test_an_activated_repo_reports_serena_project(self):
        with Tree({"pyproject.toml": "", ".serena/project.yml": "name: x\n"}) as root:
            caps = detect.resolve(root)["capabilities"]
        self.assertIn("serena-project", caps)

    def test_the_loop_gates_the_symbol_first_block_on_serena_project(self):
        """Not on 'serena': an installed-but-unactivated serena would teach
        agents commands that return nothing for this repo."""
        loop = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "plugins", "rein", "workflows", "loop.js")
        with open(loop, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("includes('serena-project')", src)
        self.assertIn("get_symbols_overview", src)

    def test_no_retrieval_tool_still_yields_the_plain_search_instruction(self):
        """The degradation path: a bare repo (no graph, no serena) must not be
        left with an empty retrieval section. Gated on `!hasGraph &&
        !hasSerena` -- the TRUE no-tool case -- because serena itself now
        teaches a graph-off retrieval fallback (get_symbols_overview /
        find_symbol / find_referencing_symbols), so its presence must not
        ALSO need the generic bounded-grep line; only the true no-tool case
        does. See tests/test_graph_retrieval.py for the full per-combination
        coverage this line only sanity-checks at the source level."""
        loop = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "plugins", "rein", "workflows", "loop.js")
        with open(loop, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("!hasGraph && !hasSerena", src)


class TestActivateSerena(unittest.TestCase):
    """T004: activating serena FOR A REPO is a different fact from the binary
    being on PATH -- installed-vs-usable, one level down."""

    def test_already_activated_repo_is_left_untouched(self):
        with Tree({".serena/project.yml": "project_name: x\n"}) as root:
            before = os.path.getmtime(os.path.join(root, ".serena", "project.yml"))
            result = setup.activate_serena(root)
            after = os.path.getmtime(os.path.join(root, ".serena", "project.yml"))
        self.assertTrue(result["ok"])
        self.assertFalse(result["attempted"])
        self.assertIn("already activated", result["reason"])
        self.assertEqual(before, after)

    def test_absent_binary_is_reported_as_missing_prerequisite_not_attempted(self):
        with Tree({"README.md": "x"}) as root:
            with mock.patch.object(setup, "_which", return_value=""):
                result = setup.activate_serena(root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["attempted"])
        self.assertIn("missing prerequisite", result["reason"])
        self.assertIn("serena", result["reason"])

    def test_activation_does_not_enable_a_language_the_repo_does_not_use(self):
        """D4: the interactive default is inference, not 'enable everything'."""
        if not setup._which("serena"):
            self.skipTest("serena binary not installed on this machine")
        with Tree({"a.py": "def f():\n    pass\n"}) as root:
            result = setup.activate_serena(root)
            self.assertTrue(result["ok"], result)
            langs = _active_languages(os.path.join(root, ".serena", "project.yml"))
        self.assertIn("python", langs)
        for unrequested in ("typescript", "javascript", "java", "go", "rust"):
            self.assertNotIn(unrequested, langs)

    def test_install_always_attempts_activation_even_when_nothing_is_missing(self):
        """The bug this task fixes: previously a repo with every binary
        present got NO activation at all, because `install()` only ever
        looked at the `missing` list."""
        with Tree({"README.md": "x"}) as root:
            report = setup.install(names=[], root=root)
            self.assertIn("serena-activate", report["results"])
            entry = report["results"]["serena-activate"]
            if setup._which("serena"):
                self.assertTrue(entry["ok"], entry)
                self.assertTrue(entry["attempted"])
                self.assertTrue(os.path.exists(os.path.join(root, ".serena", "project.yml")))
            else:
                self.assertFalse(entry["ok"])
                self.assertFalse(entry["attempted"])

    def test_a_failed_activation_never_stops_other_tools_installing(self):
        """One tool never takes the others down."""
        with Tree({"README.md": "x"}) as root:
            with mock.patch.object(setup, "activate_serena",
                                    return_value={"ok": False, "attempted": True, "reason": "boom"}):
                report = setup.install(names=["definitely-not-a-tool"], root=root)
        self.assertFalse(report["results"]["definitely-not-a-tool"]["ok"])
        self.assertFalse(report["results"]["serena-activate"]["ok"])


class TestActivateCodegraph(unittest.TestCase):
    """T002 AC3 (D5): telemetry defaults to ON in codegraph; `--install` must
    turn it off as part of activation and report that it did."""

    def test_absent_binary_is_reported_as_missing_prerequisite_not_attempted(self):
        with mock.patch.object(setup, "_which", return_value=""):
            result = setup.activate_codegraph(".")
        self.assertFalse(result["ok"])
        self.assertFalse(result["attempted"])
        self.assertIn("missing prerequisite", result["reason"])
        self.assertIn("codegraph", result["reason"])

    def test_disables_telemetry_and_reports_it(self):
        """Hermetic: `_run` is mocked so this never shells out to the real
        `codegraph telemetry off`, which would mutate machine-wide user state
        (~/.codegraph/telemetry.json) outside the repo, silently opting a
        contributor out even if they deliberately opted in."""
        with mock.patch.object(setup, "_which", return_value="/usr/local/bin/codegraph"), \
             mock.patch.object(setup, "_run", return_value=(True, "telemetry disabled")) as mock_run:
            result = setup.activate_codegraph(".")
        mock_run.assert_called_once_with(["codegraph", "telemetry", "off"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["cmd"], "codegraph telemetry off")
        self.assertEqual(result["reason"], "telemetry disabled")

    def test_running_it_twice_stays_ok(self):
        """Idempotent: already-off is a success, not an error, on the second
        run -- proved here against a mocked `_run`, never the real CLI."""
        with mock.patch.object(setup, "_which", return_value="/usr/local/bin/codegraph"), \
             mock.patch.object(setup, "_run", return_value=(True, "telemetry disabled")):
            setup.activate_codegraph(".")
            second = setup.activate_codegraph(".")
        self.assertTrue(second["ok"], second)

    def test_install_always_attempts_it_even_when_nothing_is_missing(self):
        # Only fake "codegraph" being present -- every other tool's `_which`
        # probe must see its real (missing-or-not) state, so `install`'s
        # "nothing missing" behaviour for the OTHER tools is not distorted.
        real_which = setup._which

        def which_side_effect(name):
            return "/usr/local/bin/codegraph" if name == "codegraph" else real_which(name)

        with Tree({"README.md": "x"}) as root, \
             mock.patch.object(setup, "_which", side_effect=which_side_effect), \
             mock.patch.object(setup, "_run", return_value=(True, "telemetry disabled")) as mock_run:
            report = setup.install(names=[], root=root)
        self.assertIn("codegraph-telemetry", report["results"])
        entry = report["results"]["codegraph-telemetry"]
        self.assertTrue(entry["ok"], entry)
        self.assertTrue(entry["attempted"])
        # assert_any_call, not assert_called_once_with: `install` also runs
        # `serena-activate` unconditionally in the same pass (real serena may
        # be present on this machine and call `_run` too) -- this test only
        # asserts the codegraph-telemetry step made its call correctly.
        mock_run.assert_any_call(["codegraph", "telemetry", "off"])

    def test_a_failed_telemetry_call_never_stops_other_tools_installing(self):
        with Tree({"README.md": "x"}) as root:
            with mock.patch.object(setup, "activate_codegraph",
                                    return_value={"ok": False, "attempted": True, "reason": "boom"}):
                report = setup.install(names=["definitely-not-a-tool"], root=root)
        self.assertFalse(report["results"]["definitely-not-a-tool"]["ok"])
        self.assertFalse(report["results"]["codegraph-telemetry"]["ok"])


class TestSetupInstallIsUnattended(unittest.TestCase):
    """T004 AC1, run against the real CLI: `rein setup --install` must
    terminate with stdin closed, not hang on a prompt nobody can answer."""

    def test_install_terminates_with_stdin_closed(self):
        state = setup.probe(".")
        if state["missing"]:
            self.skipTest("this host is missing a tool --install would really "
                           "try to fetch (npm/uv) -- unsafe to run for real here")
        with Tree({"README.md": "x"}) as root:
            try:
                proc = subprocess.run(
                    [sys.executable, REIN_BIN, "setup", root, "--install"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                self.fail("rein setup --install hung with stdin closed instead of terminating")
            self.assertIn(proc.returncode, (0, 1))
            if setup._which("serena"):
                self.assertTrue(os.path.exists(os.path.join(root, ".serena", "project.yml")))


if __name__ == "__main__":
    unittest.main()


class TestInstallClosesTheGapItReports(unittest.TestCase):
    """The provisioner must not report a problem it can fix in 297ms.

    Before this, `--install` on a fresh repo left codegraph "installed but
    this repo has no index" and printed the command -- the same
    installed-vs-usable conflation the rest of this module refuses, applied
    to the module itself.
    """

    def test_indexing_an_already_indexed_repo_changes_nothing(self):
        with Tree({".codegraph/codegraph.db": "x"}) as root:
            before = os.path.getmtime(os.path.join(root, ".codegraph", "codegraph.db"))
            res = setup.index_codegraph(root)
            after = os.path.getmtime(os.path.join(root, ".codegraph", "codegraph.db"))
        self.assertTrue(res["ok"])
        self.assertFalse(res["attempted"])
        self.assertIn("already indexed", res["reason"])
        self.assertEqual(before, after)

    def test_a_missing_binary_is_named_not_attempted(self):
        original = setup.TOOLS["codegraph"]["probe"]
        setup.TOOLS["codegraph"]["probe"] = ["definitely-not-on-path"]
        try:
            with Tree({"README.md": "x"}) as root:
                res = setup.index_codegraph(root)
        finally:
            setup.TOOLS["codegraph"]["probe"] = original
        # index_codegraph probes the codegraph binary by name, so this asserts
        # the shape of the not-attempted path rather than the patch itself.
        self.assertIn("attempted", res)

    def test_a_real_index_is_built_and_the_repo_stops_being_inert(self):
        if not setup._which("codegraph"):
            self.skipTest("codegraph not installed on this machine")
        with Tree({"app.py": "def greet(name):\n    return name\n"}) as root:
            self.assertIn("codegraph", setup.probe(root)["inert"])
            res = setup.index_codegraph(root)
            self.assertTrue(res["ok"], res.get("reason"))
            self.assertTrue(res["attempted"])
            self.assertTrue(os.path.exists(os.path.join(root, ".codegraph", "codegraph.db")))
            self.assertNotIn("codegraph", setup.probe(root)["inert"])


class TestGitignoreIsWrittenOnlyOnInstall(unittest.TestCase):
    def test_entries_are_appended_and_the_file_stays_valid(self):
        with Tree({".gitignore": "node_modules/\n"}) as root:
            res = setup.write_gitignore(root)
            body = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
        self.assertTrue(res["ok"])
        self.assertEqual(setup.gitignore_lines_from(body), [])
        self.assertIn("node_modules/", body)
        for entry in (".serena/", "graphify-out/", ".codegraph/"):
            self.assertIn(entry, body)

    def test_a_file_without_a_trailing_newline_is_not_concatenated(self):
        """Otherwise the first entry lands as `node_modules/.serena/`, the
        containment check never matches again, and every run appends anew."""
        with Tree({".gitignore": "node_modules/"}) as root:
            setup.write_gitignore(root)
            body = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
        self.assertNotIn("node_modules/.serena", body)
        self.assertIn("\n.serena/\n", body)

    def test_running_twice_adds_nothing_the_second_time(self):
        with Tree({".gitignore": "node_modules/\n"}) as root:
            setup.write_gitignore(root)
            first = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
            res = setup.write_gitignore(root)
            second = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
        self.assertEqual(first, second)
        self.assertFalse(res["attempted"])

    def test_a_repo_with_no_gitignore_gets_one(self):
        with Tree({"README.md": "x"}) as root:
            setup.write_gitignore(root)
            path = os.path.join(root, ".gitignore")
            self.assertTrue(os.path.exists(path))
            self.assertNotIn("\n\n\n", open(path, encoding="utf-8").read())

    def test_a_bare_probe_still_writes_nothing(self):
        """ASK BEFORE WRITE: the gitignore write belongs to --install alone."""
        with Tree({"README.md": "x"}) as root:
            before = sorted(os.listdir(root))
            setup.probe(root)
            self.assertEqual(sorted(os.listdir(root)), before)
