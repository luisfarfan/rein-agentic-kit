"""`rein role`: the operating profiles, reachable without Claude Code.

The three profiles live in `skills/rein-role/SKILL.md`, which only Claude Code
can invoke (`/rein:rein-role`). Codex, OpenCode and a plain shell cannot, and
the whole point of routing roles to different vendors is that the reviewer is
not the implementer — which is worth nothing if the reviewer cannot be told
what a reviewer is bound by.

The rule these tests defend is that there is ONE source. This kit already
matches two shipped files against each other to catch drift; reading the single
file instead makes drift impossible, so the tests below assert the identity
rather than the similarity.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN = os.path.join(REPO, "plugins", "rein", "bin", "rein")
PLUGIN_ROOT = os.path.join(REPO, "plugins", "rein")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))

import roles  # noqa: E402


def _run(*args):
    return subprocess.run([sys.executable, REIN, "role", *args], capture_output=True, text=True)


class RoleProfileTestCase(unittest.TestCase):

    def test_every_declared_role_is_actually_available(self):
        """ROLES is a constant; `available` reports what this installation can
        really produce. A wheel that shipped no SKILL.md would leave them
        unequal, which is a confident wrong answer waiting to happen."""
        self.assertEqual(sorted(roles.available(PLUGIN_ROOT)), sorted(roles.ROLES))

    def test_each_profile_carries_its_own_rules(self):
        marks = {
            "planner": "never write a plan into the project",
            "implementer": "max 3 implementation attempts",
            "reviewer": "never review your own implementation",
        }
        for role, mark in marks.items():
            with self.subTest(role=role):
                result = roles.profile(role, PLUGIN_ROOT)
                self.assertTrue(result["ok"], result["error"])
                self.assertIn(mark, result["text"])

    def test_a_profile_does_not_bleed_into_the_next_one(self):
        planner = roles.profile("planner", PLUGIN_ROOT)["text"]
        self.assertNotIn("max 3 implementation attempts", planner,
                         "the implementer's rules must not appear in the planner's profile")

    def test_the_skill_s_closing_instructions_are_not_part_of_a_role(self):
        """The file ends with a sentence aimed at a Claude Code session; it
        belongs to the skill, not to the role an agent is handed."""
        reviewer = roles.profile("reviewer", PLUGIN_ROOT)["text"]
        self.assertNotIn("Confirm briefly", reviewer)

    def test_an_unknown_role_names_the_ones_that_exist(self):
        result = roles.profile("architect", PLUGIN_ROOT)
        self.assertFalse(result["ok"])
        for role in roles.ROLES:
            self.assertIn(role, result["error"])

    def test_role_names_are_case_insensitive_and_trimmed(self):
        self.assertTrue(roles.profile("  Reviewer ", PLUGIN_ROOT)["ok"])

    def test_a_missing_skill_file_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as empty:
            result = roles.profile("reviewer", empty)
            self.assertFalse(result["ok"])
            self.assertIn("cannot read", result["error"])
            self.assertEqual(roles.available(empty), [])


class RoleCliTestCase(unittest.TestCase):

    def test_it_prints_the_profile(self):
        res = _run("implementer")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("# role: implementer", res.stdout)
        self.assertIn("max 5 failed commands", res.stdout)

    def test_json_carries_the_same_text(self):
        res = _run("reviewer", "--json")
        payload = json.loads(res.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["text"], roles.profile("reviewer", PLUGIN_ROOT)["text"])

    def test_an_unknown_role_is_the_caller_s_mistake_not_a_setup_failure(self):
        res = _run("architect")
        self.assertEqual(res.returncode, 2, "2 is a usage error; 126 would blame the machine")

    def test_no_argument_lists_what_is_available(self):
        res = _run()
        self.assertEqual(res.returncode, 2)
        for role in roles.ROLES:
            self.assertIn(role, res.stdout)

    def test_list_is_a_question_not_an_error(self):
        res = _run("--list")
        self.assertEqual(res.returncode, 0)

    def test_the_output_is_the_file_not_a_paraphrase_of_it(self):
        """One source, two consumers: what the CLI prints must be a literal
        slice of the same SKILL.md Claude Code reads."""
        with open(roles.skill_path(PLUGIN_ROOT), encoding="utf-8") as fh:
            skill = fh.read()
        for role in roles.ROLES:
            with self.subTest(role=role):
                self.assertIn(roles.profile(role, PLUGIN_ROOT)["text"], skill)


if __name__ == "__main__":
    unittest.main()
