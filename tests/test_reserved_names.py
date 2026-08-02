"""Tests for T002 -- 'a reserved name cannot ship again'.

`claude plugin details` reports both skill directories and command files as
the plugin's registered identifiers, so either one can re-collide with a
name Claude Code already ships (the bug T001 fixed for `loop`/`run`/
`run-auto`/`review`). This module is the mechanical guard D4 asked for: it
sweeps `plugins/rein/skills/*/` and `plugins/rein/commands/*.md` and fails,
naming the offender, if any basename matches the reserved list.

Mirrors tests/test_events.py's and tests/test_skill_names.py's discipline:
glob over what actually ships, not a restatement of it.
"""

from __future__ import annotations

import glob
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "skills")
COMMANDS_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "commands")


# ---------------------------------------------------------------- reserved --

# Snapshot of identifiers Claude Code itself ships as skills/commands, taken
# 2026-08-01 from `claude plugin details` output plus this session's own
# skill listing (the system-reminder enumerating built-in skills such as
# `loop`, `run`, `review`, `init`, `simplify`, `schedule`). This list is NOT
# claimed complete -- Claude Code adds names over time, and this repo has no
# way to verify it exhaustively. To refresh it: run `claude plugin details`
# for the built-in plugin(s) and cross-check against the skill names listed
# in a fresh session's system reminder, then update this set and this
# comment's date.
RESERVED_NAMES = {
    "loop",
    "run",
    "review",
    # Visible in the same session listing this snapshot cites as its source;
    # omitting it made the list disagree with its own stated provenance.
    "security-review",
    "init",
    "simplify",
    "schedule",
}


def _skill_dir_names(skills_dir: str) -> list[str]:
    """Basenames of every skill directory under `skills_dir` that ships a
    SKILL.md -- the identifier `claude plugin details` reports for a skill."""
    return [
        os.path.basename(os.path.dirname(p))
        for p in sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md")))
    ]


def _command_names(commands_dir: str) -> list[str]:
    """Basenames (no `.md`) of every command file under `commands_dir` --
    the identifier `claude plugin details` reports for a command."""
    return [
        os.path.splitext(os.path.basename(p))[0]
        for p in sorted(glob.glob(os.path.join(commands_dir, "*.md")))
    ]


def _reserved_collisions(names: list[str], reserved: set[str]) -> list[str]:
    """Names that exactly match a reserved identifier. A name that merely
    CONTAINS a reserved word (e.g. `run-auto` containing `run`) is not a
    collision -- `run` was the registered identifier that collided, not any
    string containing it."""
    return [n for n in names if n in reserved]


# --------------------------------------------------------------------- AC2 --


class TestReservedListIsARealSnapshotNotAClaimOfCompleteness(unittest.TestCase):
    def test_the_list_is_non_empty_and_holds_the_known_collisions(self):
        # A guard against an accidentally emptied set making every sweep
        # below vacuously pass.
        self.assertTrue(RESERVED_NAMES)
        expected = {"loop", "run", "review", "init", "simplify", "schedule"}
        self.assertTrue(expected.issubset(RESERVED_NAMES))


# --------------------------------------------------------------------- AC3 --


class TestSweepActuallyReadsTheShippedDirectories(unittest.TestCase):
    def test_the_skills_sweep_is_non_empty(self):
        # Guards against a moved/renamed SKILLS_DIR silently making the
        # collision sweep below pass over nothing.
        names = _skill_dir_names(SKILLS_DIR)
        self.assertTrue(names, f"no SKILL.md found under {SKILLS_DIR}")
        self.assertIn("rein-plan", names)

    def test_the_commands_sweep_is_non_empty(self):
        names = _command_names(COMMANDS_DIR)
        self.assertTrue(names, f"no command file found under {COMMANDS_DIR}")
        self.assertIn("rein-ping", names)


class TestNoShippedSkillOrCommandTakesAReservedName(unittest.TestCase):
    def test_no_shipped_skill_directory_matches_a_reserved_name(self):
        offenders = _reserved_collisions(_skill_dir_names(SKILLS_DIR), RESERVED_NAMES)
        self.assertFalse(
            offenders,
            f"skill dir(s) collide with a name Claude Code already ships: {offenders}",
        )

    def test_no_shipped_command_file_matches_a_reserved_name(self):
        offenders = _reserved_collisions(_command_names(COMMANDS_DIR), RESERVED_NAMES)
        self.assertFalse(
            offenders,
            f"command file(s) collide with a name Claude Code already ships: {offenders}",
        )


# --------------------------------------------------------------------- AC4 --


class TestCommandsAreSweptJustLikeSkills(unittest.TestCase):
    """`claude plugin details` counts commands as skills, so a reserved
    command file must be caught exactly as a reserved skill directory is.
    Proven by pointing the sweep at a fixture, not the real commands dir."""

    def test_a_fixture_command_file_named_after_a_reserved_word_is_caught(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "loop.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ndescription: fixture\n---\n\nfixture command.\n")
        with open(os.path.join(tmp.name, "ping.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ndescription: fixture\n---\n\nfixture command.\n")

        names = _command_names(tmp.name)
        self.assertEqual(set(names), {"loop", "ping"})
        offenders = _reserved_collisions(names, RESERVED_NAMES)
        self.assertEqual(offenders, ["loop"])


# --------------------------------------------------------------------- AC5 --


class TestAWordContainingAReservedNameIsNotACollision(unittest.TestCase):
    """`run-auto` was never the collision -- `run` was. A name that merely
    contains a reserved word must be allowed through unchanged."""

    def test_run_auto_is_not_flagged_by_the_reserved_run(self):
        offenders = _reserved_collisions(["run-auto"], RESERVED_NAMES)
        self.assertEqual(offenders, [])

    def test_the_real_shipped_steps_skill_is_not_flagged(self):
        # `steps` ships in this repo today and must never be caught by the
        # reserved `step`-adjacent... it isn't even a substring match target,
        # but pin it directly: no reserved name is a substring collision.
        offenders = _reserved_collisions(["steps"], RESERVED_NAMES)
        self.assertEqual(offenders, [])

    def test_exact_match_is_still_caught_even_when_a_longer_sibling_exists(self):
        # `run` alongside `run-auto` in the same sweep: only the exact name
        # is an offender, the longer sibling is not swept up with it.
        offenders = _reserved_collisions(["run", "run-auto"], RESERVED_NAMES)
        self.assertEqual(offenders, ["run"])


if __name__ == "__main__":
    unittest.main()


class TestTheSkillSweepIsProvenToo(unittest.TestCase):
    """The command half was proved against a fixture; the skill half was only
    ever run against the real directory, where it finds nothing by
    construction. A sweep that has never seen an offender is a sweep nobody
    has watched work."""

    def test_a_fixture_skill_directory_named_loop_is_surfaced(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name in ("loop", "plan"):
                os.makedirs(os.path.join(d, name))
                with open(os.path.join(d, name, "SKILL.md"), "w", encoding="utf-8") as fh:
                    fh.write(f"---\nname: {name}\n---\n")
            names = _skill_dir_names(d)
        self.assertEqual(sorted(n for n in names if n in RESERVED_NAMES), ["loop"])
        self.assertIn("plan", names, "the sweep must see the innocent one too, or it proves nothing")


PREFIX = "rein-"


class TestEveryIdentifierIsNamespaced(unittest.TestCase):
    """The guard that replaces the snapshot, because the snapshot failed twice.

    RESERVED_NAMES was a list of identifiers Claude Code ships. It missed
    `security-review` (visible in the very listing it cites), and it never
    covered OTHER PLUGINS at all -- so `plan` shipped colliding with
    agent-skills' own `plan`, and the operator saw `/rein:plan` resolve to a
    bare `plan` with no way to tell whose it was.

    A list of everyone else's names cannot be kept correct: it depends on
    what is installed, which changes without us. Prefixing every identifier
    removes the dependency -- `rein-plan` cannot collide with anything that
    is not also called `rein-plan`, and no snapshot is needed to know it.

    The list above stays as documentation of what went wrong, not as the
    mechanism.
    """

    def _identifiers(self) -> list:
        return _skill_dir_names(SKILLS_DIR) + _command_names(COMMANDS_DIR)

    def test_every_shipped_identifier_carries_the_prefix(self):
        offenders = [n for n in self._identifiers() if not n.startswith(PREFIX)]
        self.assertEqual(
            offenders, [],
            f"unprefixed identifiers can be reinterpreted as another plugin's or a built-in's: "
            f"{offenders}",
        )

    def test_the_sweep_actually_found_the_identifiers(self):
        """Zero identifiers would pass the check above vacuously."""
        self.assertGreaterEqual(len(self._identifiers()), 8)

    def test_a_prefixed_name_cannot_hit_the_reserved_list(self):
        """The property that makes the snapshot unnecessary: no reserved name
        survives prefixing, whatever the list happens to contain."""
        for reserved in RESERVED_NAMES:
            self.assertNotIn(PREFIX + reserved, RESERVED_NAMES)


class TestDiscoverIsAStanceNotAWorkflow(unittest.TestCase):
    """`/rein:rein-discover` exists because a plan whose criteria describe the
    SHAPE of an artifact, rather than something reachable, cannot be caught
    after the fact -- each such criterion is individually true.

    Its whole value is that it does NOT produce one, so the boundaries are
    asserted rather than trusted to prose nobody re-reads.
    """

    def _skill(self) -> str:
        with open(os.path.join(SKILLS_DIR, "rein-discover", "SKILL.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_it_ships_and_carries_the_prefix(self):
        self.assertIn("rein-discover", _skill_dir_names(SKILLS_DIR))

    def test_it_refuses_to_write_a_plan(self):
        """The one boundary that makes it different from /rein:rein-plan. If it
        drafts tasks, it is just a slower planner and the phase is lost."""
        body = self._skill()
        self.assertIn("Write no plan", body)
        self.assertIn("Do not write the plan", body)

    def test_it_refuses_to_implement(self):
        body = self._skill()
        self.assertIn("Implement nothing", body)
        self.assertIn('Not "one small fix while I am here"', body)

    def test_it_hands_off_by_name(self):
        self.assertIn("/rein:rein-plan", self._skill())

    def test_it_carries_the_retrieval_discipline(self):
        """Discovery is the phase most likely to burn context, because
        exploring is the point -- so the discipline matters more here."""
        body = self._skill()
        self.assertIn("codegraph query", body)
        self.assertIn("offset/limit", body)
        self.assertIn('"$R" context .', body)
