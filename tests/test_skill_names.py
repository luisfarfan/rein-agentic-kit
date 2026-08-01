"""Tests for T001 -- 'the colliding verbs are renamed, everywhere'.

Three of this kit's skills shipped under names Claude Code already reserves
(`loop`, `run`, `review`); a fourth (`run-auto`) rode along so the family
reads as one. The rename is `loop -> apply`, `run -> step`, `run-auto ->
steps`, `review -> audit`. `plan`, `role` and the `ping` / `setup` commands
are untouched (Scope).

Mirrors tests/test_events.py's discipline for shipped SKILL.md files: glob
over the real skills directory and read what actually ships, not a
restatement of it.
"""

from __future__ import annotations

import glob
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "skills")
LOOP_JS = os.path.join(REPO_ROOT, "plugins", "rein", "workflows", "loop.js")

RENAMED = {
    "loop": "apply",
    "run": "step",
    "run-auto": "steps",
    "review": "audit",
}
UNTOUCHED_SKILLS = {"plan", "role"}

# Old invocation strings, anchored so the `run` skill's pattern does not also
# match inside the `run-auto` skill's invocation (the same pitfall
# tests/test_events.py's frontmatter guard already had to close for those
# two skill names).
OLD_INVOCATION_PATTERN = re.compile(
    r"/rein:(?:loop|run-auto|run(?![\w-])|review)"
)

# Files this repo-wide sweep does not hold to the same bar:
#   - tasks.md IS this change's own plan -- its Why/Scope/Decisions sections
#     document the rename by naming the old, colliding invocations on purpose.
#   - anything named like a changelog records history, not current usage.
# .git and *.egg-info are not hand-maintained references at all: the latter
# is packaging metadata regenerated from the README by the build, already
# observed drifted from it (different pinned versions) before this change.
EXEMPT_PATHS = {os.path.join(REPO_ROOT, "tasks.md")}


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    base = os.path.basename(path).lower()
    return "changelog" in base


def _iter_repo_files():
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [
            d
            for d in dirs
            if d != ".git" and not d.endswith(".egg-info") and d != "node_modules"
        ]
        for f in files:
            yield os.path.join(root, f)


class TestSkillDirectorySet(unittest.TestCase):
    """AC1 -- the exact directory set on disk is what `claude plugin details`
    reports as the registered identifiers."""

    def test_the_renamed_and_untouched_skill_dirs_are_exactly_what_ships(self):
        on_disk = {
            d
            for d in os.listdir(SKILLS_DIR)
            if os.path.isdir(os.path.join(SKILLS_DIR, d))
        }
        expected = set(RENAMED.values()) | UNTOUCHED_SKILLS
        self.assertEqual(on_disk, expected)


class TestNoCollidingAliasLeftBehind(unittest.TestCase):
    """AC4 -- D2: no compatibility alias, since a directory named `loop` (etc)
    would re-register the exact identifier that collides."""

    def test_no_retired_skill_directory_exists(self):
        on_disk = {
            d
            for d in os.listdir(SKILLS_DIR)
            if os.path.isdir(os.path.join(SKILLS_DIR, d))
        }
        for retired in RENAMED:
            self.assertNotIn(retired, on_disk, f"retired skill dir {retired!r} still exists")


class TestSkillFrontmatterNameMatchesDirectory(unittest.TestCase):
    """D3 of the Scope: `name:` stays the bare identifier and stays equal to
    the directory -- this repo's own guard (test_events.py) reads it."""

    def test_every_renamed_skills_frontmatter_name_equals_its_directory(self):
        for new_name in RENAMED.values():
            path = os.path.join(SKILLS_DIR, new_name, "SKILL.md")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            m = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
            self.assertIsNotNone(m, f"{path}: no name: in front matter")
            self.assertEqual(m.group(1).strip("\"'"), new_name)


class TestNoOldInvocationSurvivesInTheRepo(unittest.TestCase):
    """AC2 -- grep the whole repository for the four old invocations; fail on
    any survivor outside a changelog or this plan's own Why section."""

    def test_no_survivor_outside_the_exempted_files(self):
        survivors = []
        for path in _iter_repo_files():
            if _is_exempt(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if OLD_INVOCATION_PATTERN.search(line):
                    survivors.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}: {line.strip()}")
        self.assertEqual(survivors, [], "\n".join(survivors))

    def test_the_sweep_actually_covers_files(self):
        """Guard on the guard: an empty walk would make the test above vacuous."""
        count = sum(1 for _ in _iter_repo_files())
        self.assertGreater(count, 20)


class TestLoopPromptsNameNoRetiredSkill(unittest.TestCase):
    """AC5 -- buildIsolatePrompt, the step prompts (stepPrompt,
    buildTaskWorktreePrompt, buildTaskMergePrompt) and the review prompts
    (implementerPolicyBlock, reviewerPolicyBlock) name no retired skill.
    Extracted straight out of loop.js's shipped source by regex, the same
    technique tests/test_loop_policy.py uses for decideRound, so this proves
    the actual shipped strings rather than a restatement of them."""

    FUNCTIONS = [
        ("buildIsolatePrompt", r"root, base, wd, branch, rein"),
        ("buildTaskWorktreePrompt", r"root, runBranch, wd, branch, rein"),
        ("buildTaskMergePrompt", r"wd, branch, runBranch, taskId, taskWd"),
        ("stepPrompt", r"task, proxied, ledger, step, wtOverride, closesOwnTask = true"),
        ("implementerPolicyBlock", r""),
        ("reviewerPolicyBlock", r""),
    ]

    @classmethod
    def setUpClass(cls):
        with open(LOOP_JS, encoding="utf-8") as fh:
            cls.src = fh.read()

    def _extract(self, name: str, params: str) -> str:
        pattern = re.compile(
            rf"function {re.escape(name)}\({re.escape(params)}\) \{{\n([\s\S]*?)\n\}}\n"
        )
        m = pattern.search(self.src)
        self.assertIsNotNone(m, f"{name} not found in loop.js with the expected signature")
        return m.group(1)

    def test_every_prompt_builder_is_still_extractable(self):
        """Guard on the guard: if a signature drifts, extraction below would
        silently check nothing."""
        for name, params in self.FUNCTIONS:
            self._extract(name, params)

    def test_no_prompt_builder_names_a_retired_skill(self):
        survivors = []
        for name, params in self.FUNCTIONS:
            body = self._extract(name, params)
            if OLD_INVOCATION_PATTERN.search(body):
                for i, line in enumerate(body.splitlines(), 1):
                    if OLD_INVOCATION_PATTERN.search(line):
                        survivors.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(survivors, [], "\n".join(survivors))


if __name__ == "__main__":
    unittest.main()
