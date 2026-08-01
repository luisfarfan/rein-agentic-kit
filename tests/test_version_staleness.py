"""Tests for T001 (doctor-knows-it-is-stale): version_staleness.py.

decide_staleness is pure (fixtures only, no filesystem); load_staleness_inputs
is the reading half (real tmp filesystem, HOME override, never raises); the
import-scan test makes "no network capability" mechanically checkable (D1).
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest

LIB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib")
sys.path.insert(0, LIB_DIR)

import version_staleness as vs  # noqa: E402

PLUGIN_KEY = "rein@rein-agentic-kit"
PLUGIN_NAME = "rein"


def installed_doc(version, key=PLUGIN_KEY):
    return {"version": 2, "plugins": {key: [{"scope": "user", "version": version}]}}


def marketplace_doc(version, name=PLUGIN_NAME):
    return {"name": "rein-agentic-kit", "plugins": [{"name": name, "version": version, "source": "./plugins/rein"}]}


class TestDecideStalenessIsPure(unittest.TestCase):
    """Every branch driven from fixtures -- decide_staleness never touches disk."""

    def test_equal_versions_is_up_to_date(self):
        result = vs.decide_staleness(installed_doc("0.4.0"), marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UP_TO_DATE)
        self.assertEqual(result.installed_version, "0.4.0")
        self.assertEqual(result.available_version, "0.4.0")
        self.assertIn("clone", result.reason)  # D3: says what it does not know

    def test_newer_available_is_stale(self):
        result = vs.decide_staleness(installed_doc("0.4.0"), marketplace_doc("0.6.3"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.STALE)
        self.assertEqual(result.installed_version, "0.4.0")
        self.assertEqual(result.available_version, "0.6.3")

    def test_newer_installed_is_unknown_not_stale(self):
        """A developer running from a checkout -- never a false 'stale'."""
        result = vs.decide_staleness(installed_doc("0.6.3"), marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)
        self.assertNotEqual(result.verdict, vs.STALE)

    def test_plugin_absent_from_installed_list_is_unknown(self):
        result = vs.decide_staleness(installed_doc("0.4.0", key="other@other"), marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)
        self.assertIn(PLUGIN_KEY, result.reason)

    def test_missing_version_key_on_installed_side_is_unknown(self):
        doc = {"plugins": {PLUGIN_KEY: [{"scope": "user"}]}}
        result = vs.decide_staleness(doc, marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_missing_version_key_on_marketplace_side_is_unknown(self):
        doc = {"name": "rein-agentic-kit", "plugins": [{"name": PLUGIN_NAME}]}
        result = vs.decide_staleness(installed_doc("0.4.0"), doc, PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_non_string_version_is_unknown(self):
        result = vs.decide_staleness(installed_doc(40), marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_plugin_not_offered_by_marketplace_is_unknown(self):
        result = vs.decide_staleness(installed_doc("0.4.0"), marketplace_doc("0.4.0", name="other"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_none_docs_are_unknown_not_raising(self):
        self.assertEqual(vs.decide_staleness(None, marketplace_doc("0.4.0"), PLUGIN_KEY, PLUGIN_NAME).verdict, vs.UNKNOWN)
        self.assertEqual(vs.decide_staleness(installed_doc("0.4.0"), None, PLUGIN_KEY, PLUGIN_NAME).verdict, vs.UNKNOWN)


def multi_scope_installed_doc(entries, key=PLUGIN_KEY):
    """entries: list of (scope, version, install_path)."""
    return {
        "version": 2,
        "plugins": {
            key: [
                {"scope": scope, "version": version, "installPath": install_path}
                for scope, version, install_path in entries
            ]
        },
    }


class TestDecideStalenessWithMultipleScopeEntries(unittest.TestCase):
    """installed_plugins.json keys each plugin as a LIST because scopes
    (user / project / local) coexist -- round-1 review finding 2: picking an
    arbitrary entry (e.g. always the last) can produce a false 'stale' or a
    false 'up-to-date' for the entry that is not actually running. The
    running entry must be identified by matching plugin_root against each
    entry's installPath.
    """

    USER_PATH = "/home/dev/.claude/plugins/cache/rein-agentic-kit/rein/0.4.0"
    PROJECT_PATH = "/home/dev/.claude/plugins/cache/rein-agentic-kit/rein/0.6.3"

    def _doc(self):
        return multi_scope_installed_doc(
            [("user", "0.4.0", self.USER_PATH), ("project", "0.6.3", self.PROJECT_PATH)]
        )

    def test_running_entry_identified_by_plugin_root_decides_the_verdict(self):
        # The OLDER (user-scope) entry is the one actually running here --
        # picking the last entry in the list (0.6.3, project-scope) would
        # wrongly report up-to-date instead of stale.
        result = vs.decide_staleness(
            self._doc(), marketplace_doc("0.6.3"), PLUGIN_KEY, PLUGIN_NAME, plugin_root=self.USER_PATH
        )
        self.assertEqual(result.verdict, vs.STALE)
        self.assertEqual(result.installed_version, "0.4.0")

        # And the reverse: the NEWER (project-scope) entry running.
        result2 = vs.decide_staleness(
            self._doc(), marketplace_doc("0.6.3"), PLUGIN_KEY, PLUGIN_NAME, plugin_root=self.PROJECT_PATH
        )
        self.assertEqual(result2.verdict, vs.UP_TO_DATE)
        self.assertEqual(result2.installed_version, "0.6.3")

    def test_unidentifiable_running_entry_is_unknown_not_a_guess(self):
        # No plugin_root at all.
        result = vs.decide_staleness(self._doc(), marketplace_doc("0.6.3"), PLUGIN_KEY, PLUGIN_NAME)
        self.assertEqual(result.verdict, vs.UNKNOWN)

        # A plugin_root that matches none of the entries' installPath.
        result2 = vs.decide_staleness(
            self._doc(),
            marketplace_doc("0.6.3"),
            PLUGIN_KEY,
            PLUGIN_NAME,
            plugin_root="/somewhere/else/rein/1.2.3",
        )
        self.assertEqual(result2.verdict, vs.UNKNOWN)


class TestLoaderNeverRaises(unittest.TestCase):
    """The reading half: resolves paths under ~/.claude/plugins, parses,
    and returns unknown-shaped results rather than raising when a path is
    absent (loader test required by acceptance criterion 5)."""

    def setUp(self):
        self.home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_tmp.cleanup)
        self._orig_home = os.environ.get("HOME")
        self._orig_dir = vs.CLAUDE_PLUGINS_DIR
        os.environ["HOME"] = self.home_tmp.name
        vs.CLAUDE_PLUGINS_DIR = os.path.join(self.home_tmp.name, ".claude", "plugins")
        self.plugin_root = os.path.join(
            self.home_tmp.name, ".claude", "plugins", "cache", "rein-agentic-kit", "rein", "0.4.0"
        )

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        vs.CLAUDE_PLUGINS_DIR = self._orig_dir

    def test_both_paths_absent_returns_unknown_no_raise(self):
        loaded = vs.load_staleness_inputs(self.plugin_root)
        self.assertIsNone(loaded.installed_doc)
        self.assertIsNone(loaded.marketplace_doc)
        result, _ = vs.resolve_verdict(self.plugin_root)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_plugin_root_outside_cache_is_unknown_no_raise(self):
        checkout_root = os.path.join(self.home_tmp.name, "dev", "rein-agentic-kit", "plugins", "rein")
        loaded = vs.load_staleness_inputs(checkout_root)
        self.assertIsNotNone(loaded.load_reason)
        result, _ = vs.resolve_verdict(checkout_root)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_loads_real_files_when_present(self):
        os.makedirs(self.plugin_root)
        os.makedirs(os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin"))
        with open(os.path.join(vs.CLAUDE_PLUGINS_DIR, "installed_plugins.json"), "w") as fh:
            json.dump(installed_doc("0.4.0"), fh)
        with open(
            os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin", "marketplace.json"),
            "w",
        ) as fh:
            json.dump(marketplace_doc("0.6.3"), fh)

        result, loaded = vs.resolve_verdict(self.plugin_root)
        self.assertEqual(loaded.plugin_key, PLUGIN_KEY)
        self.assertEqual(result.verdict, vs.STALE)
        self.assertEqual(result.available_version, "0.6.3")

    def test_malformed_json_is_unknown_no_raise(self):
        os.makedirs(self.plugin_root)
        with open(os.path.join(vs.CLAUDE_PLUGINS_DIR, "installed_plugins.json"), "w") as fh:
            fh.write("{not json")
        result, _ = vs.resolve_verdict(self.plugin_root)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_bad_byte_in_installed_plugins_json_is_unknown_no_raise(self):
        """A single non-UTF-8 byte raises UnicodeDecodeError, not
        json.JSONDecodeError -- the exact failure class commit 5450b53
        already fixed once for events.jsonl. _read_json must not let it
        escape, or the WHOLE `rein doctor` report is lost (D3, D4)."""
        os.makedirs(self.plugin_root)
        os.makedirs(os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin"))
        with open(os.path.join(vs.CLAUDE_PLUGINS_DIR, "installed_plugins.json"), "wb") as fh:
            fh.write(b'{"plugins": {}} \xff')
        with open(
            os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin", "marketplace.json"),
            "w",
        ) as fh:
            json.dump(marketplace_doc("0.6.3"), fh)
        result, _ = vs.resolve_verdict(self.plugin_root)
        self.assertEqual(result.verdict, vs.UNKNOWN)

    def test_bad_byte_in_marketplace_json_is_unknown_no_raise(self):
        os.makedirs(self.plugin_root)
        os.makedirs(os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin"))
        with open(os.path.join(vs.CLAUDE_PLUGINS_DIR, "installed_plugins.json"), "w") as fh:
            json.dump(installed_doc("0.4.0"), fh)
        with open(
            os.path.join(vs.CLAUDE_PLUGINS_DIR, "marketplaces", "rein-agentic-kit", ".claude-plugin", "marketplace.json"),
            "wb",
        ) as fh:
            fh.write(b'{"plugins": []} \xff')
        result, _ = vs.resolve_verdict(self.plugin_root)
        self.assertEqual(result.verdict, vs.UNKNOWN)


class TestNoNetworkCapability(unittest.TestCase):
    """D1: mechanically checkable where "makes no network call" is not."""

    BANNED = {"socket", "urllib", "http", "ssl", "requests"}

    def test_module_imports_no_network_modules(self):
        path = os.path.join(LIB_DIR, "version_staleness.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        offending = imported & self.BANNED
        self.assertFalse(offending, f"version_staleness.py imports network capability: {offending}")


class TestFixCommands(unittest.TestCase):
    def test_refreshes_marketplace_before_updating_plugin(self):
        cmds = vs.fix_commands("rein-agentic-kit", "rein")
        self.assertEqual(len(cmds), 2)
        self.assertIn("marketplace update rein-agentic-kit", cmds[0])
        self.assertIn("plugin update rein", cmds[1])
        self.assertLess(cmds.index(cmds[0]), cmds.index(cmds[1]))


if __name__ == "__main__":
    unittest.main()


class TestTheReviewsFourFindings(unittest.TestCase):
    """All four verified before fixing, three by execution."""

    def test_a_unicode_digit_version_returns_unknown_instead_of_raising(self):
        """`'²'.isdigit()` is True and `int('²')` raises, so a version string
        from external JSON escaped a function whose contract is "None for
        anything I cannot read" -- and only doctor's blanket except kept the
        CLI alive."""
        self.assertIsNone(vs._parse_version("1.²"))
        r = vs.decide_staleness(
            {"plugins": {"p@m": [{"version": "1.²", "installPath": "/x"}]}},
            {"plugins": [{"name": "p", "version": "1.0"}]},
            "p@m", "p", "/x",
        )
        self.assertEqual(r.verdict, vs.UNKNOWN)

    def test_two_spellings_of_one_version_are_not_stale(self):
        """(0, 4) < (0, 4, 0) lexicographically; they are the same release."""
        for a, b in (("0.4", "0.4.0"), ("1.2.0.0", "1.2")):
            r = vs.decide_staleness(
                {"plugins": {"p@m": [{"version": a, "installPath": "/x"}]}},
                {"plugins": [{"name": "p", "version": b}]},
                "p@m", "p", "/x",
            )
            self.assertEqual(r.verdict, vs.UP_TO_DATE, f"{a} vs {b}")

    def test_a_single_entry_for_another_install_gives_no_verdict(self):
        """Verified live: a copy of the plugin at another path reported
        `up-to-date` against the one real entry, whose installPath pointed
        somewhere else -- a verdict about an install that was not asking."""
        r = vs.decide_staleness(
            {"plugins": {"p@m": [{"version": "0.4.0", "installPath": "/somewhere/else"}]}},
            {"plugins": [{"name": "p", "version": "9.9.9"}]},
            "p@m", "p", "/the/running/one",
        )
        self.assertEqual(r.verdict, vs.UNKNOWN)
        self.assertIn("not the install currently running", r.reason)

    def test_a_single_entry_without_an_installpath_is_still_tolerated(self):
        """Older records lack the key; refusing them would trade a wrong
        answer for no answer."""
        r = vs.decide_staleness(
            {"plugins": {"p@m": [{"version": "0.4.0"}]}},
            {"plugins": [{"name": "p", "version": "0.9.0"}]},
            "p@m", "p", "/the/running/one",
        )
        self.assertEqual(r.verdict, vs.STALE)
