"""The v2 skill set: small, boilerplate-free, and still readable by `rein role`.

Skills are re-read on every turn of the session that loads them, so their size
is a cost, not a style choice. And every one of them is a plain markdown file
the `rein` CLI (on PATH since v2) can be called from directly -- the `$R`
path-resolution block and the `rein event` call were scaffolding for a world
where the binary had to be hunted for and every invocation was recorded.
"""

from __future__ import annotations

import glob
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "rein")
SKILLS = os.path.join(PLUGIN, "skills")
sys.path.insert(0, os.path.join(PLUGIN, "lib"))

import roles  # noqa: E402

BUDGETS = {"rein-plan": 700, "ship": 200}


def _skill_files() -> list:
    return sorted(glob.glob(os.path.join(SKILLS, "*", "SKILL.md")))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestSkillBudget(unittest.TestCase):
    def test_skill_set(self):
        names = {os.path.basename(os.path.dirname(p)) for p in _skill_files()}
        self.assertEqual(names, {"rein-plan", "rein-discover", "rein-audit", "rein-role", "ship"})

    def test_word_budgets(self):
        for name, limit in BUDGETS.items():
            words = len(_read(os.path.join(SKILLS, name, "SKILL.md")).split())
            self.assertLessEqual(words, limit, f"{name} is {words} words, budget {limit}")

    def test_no_event_or_path_boilerplate(self):
        files = _skill_files()
        self.assertEqual(len(files), 5, "the sweep must cover every skill")
        for path in files:
            text = _read(path)
            for banned in ("rein event", "plugins/cache", '"$R"', "$R="):
                self.assertNotIn(banned, text, f"{os.path.relpath(path, REPO)} still has {banned!r}")

    def test_no_retired_command_is_referenced(self):
        retired = ("rein-apply", "rein-step", "rein-linear", "rein-setup", "loop.js",
                   "rein dashboard", "rein backlog")
        for path in _skill_files():
            text = _read(path)
            for word in retired:
                self.assertNotIn(word, text, f"{os.path.relpath(path, REPO)} mentions {word!r}")

    def test_frontmatter_is_present(self):
        for path in _skill_files():
            text = _read(path)
            self.assertTrue(text.startswith("---\nname: "), path)
            self.assertIn("\ndescription: ", text.split("\n---", 2)[0], path)

    def test_roles_still_parse(self):
        for role in ("implementer", "reviewer", "planner"):
            result = roles.profile(role, PLUGIN)
            self.assertTrue(result["ok"], result["error"])
            self.assertTrue(result["text"].strip())

    def test_audit_round_cap_matches_the_spec(self):
        text = _read(os.path.join(SKILLS, "rein-audit", "SKILL.md"))
        self.assertNotIn("3 rounds", text)
        self.assertIn("2 rounds", text)
        self.assertNotIn("verifyPolicy", text)
        self.assertNotIn("Verify-phase", text)

    def test_ship_and_plan_pass_the_change_and_root(self):
        ship = _read(os.path.join(SKILLS, "ship", "SKILL.md"))
        self.assertIn("rein tasks <root> --change <change>", ship)
        self.assertIn("background", ship)
        self.assertIn("/rein:ship", ship)
        plan = _read(os.path.join(SKILLS, "rein-plan", "SKILL.md"))
        self.assertIn("rein tasks <root> --change <c>", plan)
        self.assertIn("/rein:ship", plan)

    def test_ship_runs_the_cli_and_never_edits(self):
        text = _read(os.path.join(SKILLS, "ship", "SKILL.md"))
        self.assertIn("rein ship", text)
        self.assertIn("decision gate", text)
        self.assertIn("Orca", text)


if __name__ == "__main__":
    unittest.main()
