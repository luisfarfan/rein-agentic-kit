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
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import setup  # noqa: E402


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

    def test_already_ignored_entries_are_not_repeated(self):
        with Tree({".gitignore": ".serena/\ngraphify-out/\n"}) as root:
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
        """The degradation path: a bare repo must not be left with an empty
        retrieval section."""
        loop = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "plugins", "rein", "workflows", "loop.js")
        with open(loop, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("!hasSerena && !hasGraph", src)


if __name__ == "__main__":
    unittest.main()
