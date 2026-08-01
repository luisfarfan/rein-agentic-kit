"""T001 -- the-plan-checks-itself: the plan is critiqued before it is written.

Three kinds of proof, matching the three kinds of claim in the acceptance
criteria:
  * D1 (unconditional step) and D5 (never a silent skip, never a hard stop)
    are prose the SHIPPED skill must contain -- read as text, the same way
    tests/test_loop_policy.py reads the shipped loop.js as text.
  * D3 (one definition, two consumers) is a substring match against BOTH
    shipped files -- the moment either one's wording drifts, this fails.
  * The two fixtures are the mechanical half (plan_check.mechanical_findings)
    exercised against real, checked-in plan text: the actual defect that cost
    81k tokens, and a plan that was actually approved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "plugins", "rein", "lib")
REIN_BIN = os.path.join(ROOT, "plugins", "rein", "bin", "rein")
LOOP_JS = os.path.join(ROOT, "plugins", "rein", "workflows", "loop.js")
SKILL_MD = os.path.join(ROOT, "plugins", "rein", "skills", "plan", "SKILL.md")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

sys.path.insert(0, LIB)

import plan_check  # noqa: E402

_NODE = shutil.which("node")

# Same discipline as tests/test_loop_policy.py: extract buildPlanCheckPrompt
# straight out of the shipped source by regex and RUN it with `new Function`,
# so this proves the actual rendered prompt text -- not a reimplementation,
# and not a raw-source-text match that a `+`-concatenated template literal
# could dodge by wrapping mid-phrase.
_EXTRACT_JS = r"""
const fs = require('fs');
const [, , loopPath] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const buildPlanCheckPrompt = new Function('planPath', 'taskIds', extract('buildPlanCheckPrompt', 'planPath, taskIds'));
process.stdout.write(buildPlanCheckPrompt('tasks.md', []));
"""


def _build_plan_check_prompt() -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(_EXTRACT_JS)
        script_path = f.name
    try:
        proc = subprocess.run([_NODE, script_path, LOOP_JS], capture_output=True, text=True, check=True)
    finally:
        os.unlink(script_path)
    return proc.stdout

# D3: verbatim-identical to plan_check.BLOCKING_CLASSES and to the four
# lenses embedded in loop.js's buildPlanCheckPrompt.
CANONICAL_CLASSES = (
    "a verification that cannot mechanically confirm the criteria it is attached to",
    "a criterion no command can check",
    "a dependency that is circular or names a task that does not exist",
    "a criterion that contradicts a stated decision",
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _flatten(text: str) -> str:
    """Collapse whitespace (including markdown's hard line wraps) to single
    spaces, so a multi-word assertion is not accidentally sensitive to where
    a line happens to wrap."""
    return " ".join(text.split())


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestBlockingClassesDefinedOnce(unittest.TestCase):
    """D2/D3: one definition, two consumers -- the loop's PlanCheck prompt and
    the plan skill must name the SAME four, or the two gates drift."""

    def test_plan_check_module_defines_the_four_classes(self):
        self.assertEqual(plan_check.BLOCKING_CLASSES, CANONICAL_CLASSES)

    def test_loop_js_plan_check_prompt_names_all_four(self):
        prompt = _build_plan_check_prompt()
        for cls in CANONICAL_CLASSES:
            self.assertIn(cls, prompt, f"loop.js's rendered PlanCheck prompt is missing: {cls!r}")

    def test_plan_skill_names_all_four(self):
        src = _read(SKILL_MD)
        for cls in CANONICAL_CLASSES:
            self.assertIn(cls, src, f"plan skill's critique step is missing: {cls!r}")

    def test_neither_shipped_text_paraphrases_a_class_away(self):
        """A test that only checked ONE side could pass while the other side
        drifted -- this is the "test asserts...name the SAME four" itself,
        run against BOTH the loop's actual rendered prompt and the shipped
        skill file, with the identical class strings."""
        prompt = _build_plan_check_prompt()
        skill_src = _read(SKILL_MD)
        for cls in CANONICAL_CLASSES:
            self.assertIn(cls, prompt)
            self.assertIn(cls, skill_src)


class TestCritiqueIsUnconditional(unittest.TestCase):
    """D1: the critique runs on every invocation, not behind a flag or a
    user request, and it runs before the plan is written."""

    def setUp(self):
        self.text = _read(SKILL_MD)
        self.flat = _flatten(self.text)

    def test_critique_step_present(self):
        self.assertIn("Critique the draft before it is shown or written", self.flat)

    def test_critique_step_states_it_is_unconditional(self):
        self.assertIn("UNCONDITIONAL", self.flat)
        self.assertIn("never behind a flag", self.flat)
        self.assertIn("never only when the user asks for it", self.flat)

    def test_critique_step_precedes_dry_run_and_write(self):
        critique_at = self.text.index("Critique the draft before it is shown or written")
        dry_run_at = self.text.index("**Dry run.**")
        write_at = self.text.index("write it to `plan.path`")
        self.assertLess(critique_at, dry_run_at, "critique must run before the dry run")
        self.assertLess(dry_run_at, write_at, "the dry run/confirm gate must still precede the write")

    def test_no_conditional_escape_hatch_wraps_the_step(self):
        # A step gated behind "if the user asks" / "when requested" / "optionally"
        # would satisfy a naive "the words exist somewhere" check while still
        # being conditional in practice -- this is the D1 failure mode itself.
        step_start = self.text.index("5. **Critique the draft")
        step_end = self.text.index("6. **Dry run.**")
        step_text = self.text[step_start:step_end]
        for escape in ("if the user asks", "when requested", "optionally", "may skip"):
            self.assertNotIn(escape, step_text.lower())


class TestFailureNeverSilentNeverHardStop(unittest.TestCase):
    """D5: no agent, a timeout, a malformed response -- each results in the
    plan being WRITTEN with the failure stated, never a silent skip and
    never a hard stop."""

    def setUp(self):
        self.text = _read(SKILL_MD)
        self.flat = _flatten(self.text)

    def test_names_all_three_failure_shapes(self):
        self.assertIn("no critique agent is available", self.flat)
        self.assertIn("times out", self.flat)
        self.assertIn("malformed", self.flat)

    def test_states_write_anyway_with_failure_named(self):
        self.assertIn("write the plan anyway", self.flat)
        self.assertIn("do NOT skip it silently", self.flat)
        self.assertIn("do NOT hard-stop", self.flat)


class TestOpenspecIsReportedNotReimplemented(unittest.TestCase):
    """D4: structural checking belongs to openspec where openspec is in use.
    Its errors are reported as its own, never paraphrased or re-derived."""

    def setUp(self):
        self.text = _read(SKILL_MD)
        self.flat = _flatten(self.text)

    def test_runs_openspec_validate_strict_first_and_verbatim(self):
        self.assertIn("openspec validate --strict", self.flat)
        self.assertIn("VERBATIM", self.flat)
        self.assertIn("labeled as openspec's own", self.flat)

    def test_disclaims_reimplementing_structural_rules(self):
        self.assertIn("reimplements none of openspec's structural checks", self.flat)

    def test_missing_binary_is_named_as_a_skip_not_a_failure(self):
        self.assertIn("missing `openspec` binary skips this half silently", self.flat)
        self.assertIn("not a failure", self.flat)

    def test_openspec_binary_absent_is_reported_not_raised(self):
        orig = plan_check.shutil.which
        plan_check.shutil.which = lambda name: None
        try:
            self.assertEqual(plan_check.openspec_binary(), "")
        finally:
            plan_check.shutil.which = orig

    def test_openspec_binary_present_is_returned(self):
        orig = plan_check.shutil.which
        plan_check.shutil.which = lambda name: "/usr/local/bin/openspec" if name == "openspec" else None
        try:
            self.assertEqual(plan_check.openspec_binary(), "/usr/local/bin/openspec")
        finally:
            plan_check.shutil.which = orig


class TestPlanCheckIsShippedAndReachable(unittest.TestCase):
    """Round-1 finding 1: `plan_check.py` was dead code -- nothing in the
    shipped path ever ran it. `rein plan-check <file>` is the entry point;
    these tests prove it is actually wired in (the SKILL names the exact
    command) and actually works end-to-end (the CLI subprocess, not a direct
    `import plan_check`, produces the real defect's BLOCKING finding)."""

    def test_skill_names_the_plan_check_command(self):
        src = _read(SKILL_MD)
        self.assertIn('"$R" plan-check', src, "SKILL.md step 5 must name the concrete `rein plan-check` command")

    def test_plan_check_command_precedes_the_agent_critique(self):
        flat = _flatten(_read(SKILL_MD))
        mechanical_at = flat.index('"$R" plan-check')
        agent_at = flat.index("Critique every task yourself against the two classes no command can decide")
        self.assertLess(mechanical_at, agent_at, "the mechanical command must run before the agent critique")

    def test_cli_rejects_the_real_defect_fixture(self):
        """End-to-end: the subprocess CLI, not plan_check.mechanical_findings()
        called directly, must produce the BLOCKING T003 finding."""
        fixture = os.path.join(FIXTURES, "plan_defect_t003.md")
        proc = subprocess.run(
            [sys.executable, REIN_BIN, "plan-check", fixture],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(proc.returncode, 0, "D5: plan-check never fails the caller")
        report = json.loads(proc.stdout)
        blocking = [f for f in report["findings"] if f["severity"] == "BLOCKING"]
        t003 = [f for f in blocking if f["taskId"] == "T003"]
        self.assertTrue(t003, f"expected a BLOCKING finding on T003 from the CLI, got {report['findings']}")

    def test_cli_never_fails_on_a_missing_file(self):
        proc = subprocess.run(
            [sys.executable, REIN_BIN, "plan-check", "/no/such/plan.md"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["error"])


class TestRealDefectFixtureIsRejected(unittest.TestCase):
    """The REAL defect, checked in verbatim: T003's Verification named
    tests.test_verify_commands -- T002's own test module. The commit that
    fixed it (34c2cff) records the loop's PlanCheck catching this for 81k
    tokens; this is that same plan text, unfixed."""

    def setUp(self):
        self.text = _read(os.path.join(FIXTURES, "plan_defect_t003.md"))

    def test_fixture_is_the_real_unfixed_plan(self):
        # T003 and T002 share the exact verification command this defect is.
        self.assertIn("tests.test_verify_commands", self.text)

    def test_t003_is_rejected_blocking(self):
        findings = plan_check.mechanical_findings(self.text)
        blocking = [f for f in findings if f["severity"] == "BLOCKING"]
        self.assertTrue(blocking, "the T003/T002 duplicate verification must produce a BLOCKING finding")
        t003 = [f for f in blocking if f["taskId"] == "T003"]
        self.assertTrue(t003, f"expected a BLOCKING finding on T003, got taskIds {[f['taskId'] for f in blocking]}")

    def test_finding_names_the_class_and_the_criteria_it_fails_to_prove(self):
        findings = plan_check.mechanical_findings(self.text)
        t003 = next(f for f in findings if f["taskId"] == "T003" and f["severity"] == "BLOCKING")
        self.assertEqual(t003["classId"], CANONICAL_CLASSES[0])
        # T003's own acceptance criteria -- what the finding says it fails to prove.
        self.assertIn("STOPS the run before Isolate", t003["text"])
        self.assertIn("does NOT stop the run", t003["text"])

    def test_t002_itself_is_not_blamed(self):
        findings = plan_check.mechanical_findings(self.text)
        self.assertFalse(
            [f for f in findings if f["taskId"] == "T002" and f["severity"] == "BLOCKING"],
            "the first owner of a verification command is not the defect",
        )


class TestHealthyPlanHasNoBlockingFindings(unittest.TestCase):
    """A check that always fires is a check nobody reads (D2).

    Round-1 finding 2: a single-task plan is structurally incapable of
    triggering either mechanical class (a duplicate verification needs two
    tasks; a cycle or missing dependency needs a `Depends on` edge), so it
    proves nothing about the false-positive property. The primary case here
    is `plan_healthy_dashboard.md` (the-dashboard-answers-the-question,
    approved, three tasks, distinct verification commands, a real
    T001->T002->T003 dependency chain) -- it has discriminating power. The
    single-task plan is kept as an ADDITIONAL case, not the only one."""

    def test_approved_multitask_plan_is_clean(self):
        text = _read(os.path.join(FIXTURES, "plan_healthy_dashboard.md"))
        findings = plan_check.mechanical_findings(text)
        blocking = [f for f in findings if f["severity"] == "BLOCKING"]
        self.assertEqual(blocking, [], f"a healthy, approved multi-task plan must not BLOCK: {blocking}")

    def test_approved_single_task_plan_is_also_clean(self):
        text = _read(os.path.join(FIXTURES, "plan_healthy_ledger.md"))
        findings = plan_check.mechanical_findings(text)
        blocking = [f for f in findings if f["severity"] == "BLOCKING"]
        self.assertEqual(blocking, [], f"a healthy, approved plan must not BLOCK: {blocking}")


class TestDependencyClassMechanics(unittest.TestCase):
    """Class 3 (circular / missing-task dependency) exercised directly --
    neither required fixture above triggers it, so it needs its own proof
    that the logic is real, not merely defined."""

    def test_dependency_on_a_nonexistent_task_blocks(self):
        text = (
            "- [ ] T001 Do the thing\n"
            "  - Depends on: T999\n"
            "  - Verification: `python3 -m unittest tests.test_thing`\n"
            "  - Acceptance:\n"
            "    - it works\n"
        )
        findings = plan_check.mechanical_findings(text)
        blocking = [f for f in findings if f["severity"] == "BLOCKING"]
        self.assertTrue(blocking)
        self.assertEqual(blocking[0]["classId"], CANONICAL_CLASSES[2])
        self.assertIn("T999", blocking[0]["text"])

    def test_circular_dependency_blocks(self):
        text = (
            "- [ ] T001 A\n"
            "  - Depends on: T002\n"
            "  - Verification: `python3 -m unittest tests.test_a`\n"
            "\n"
            "- [ ] T002 B\n"
            "  - Depends on: T001\n"
            "  - Verification: `python3 -m unittest tests.test_b`\n"
        )
        findings = plan_check.mechanical_findings(text)
        blocking_ids = {f["taskId"] for f in findings if f["severity"] == "BLOCKING"}
        self.assertEqual(blocking_ids, {"T001", "T002"})

    def test_ordinary_plan_has_no_dependency_findings(self):
        text = (
            "- [ ] T001 A\n"
            "  - Verification: `python3 -m unittest tests.test_a`\n"
            "\n"
            "- [ ] T002 B\n"
            "  - Depends on: T001\n"
            "  - Verification: `python3 -m unittest tests.test_b`\n"
        )
        self.assertEqual(plan_check.mechanical_findings(text), [])


class TestReusedVerificationSeverityIsNarrowed(unittest.TestCase):
    """Round-1 finding 5: equating "two tasks share a verification command
    string" with BLOCKING would fire on legitimate plans -- two tasks that
    honestly verify through the same suite, with no way to accept the
    finding (D2's own "a check that always fires is a check nobody reads"
    failure, arriving through the mechanical half). A reused per-module
    `python3 -m unittest <module>` command -- this repo's own task-scoped
    convention, and the real T003/T002 shape -- stays BLOCKING. A reused
    bare or filtered whole-suite command is reported IMPORTANT instead:
    visible, but not unfixable."""

    def test_reused_scoped_unittest_module_is_still_blocking(self):
        text = (
            "- [ ] T001 A\n"
            "  - Verification: `python3 -m unittest tests.test_shared`\n"
            "\n"
            "- [ ] T002 B\n"
            "  - Depends on: T001\n"
            "  - Verification: `python3 -m unittest tests.test_shared`\n"
        )
        findings = plan_check.mechanical_findings(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "BLOCKING")
        self.assertEqual(findings[0]["classId"], CANONICAL_CLASSES[0])

    def test_reused_generic_whole_suite_command_is_important_not_blocking(self):
        text = (
            "- [ ] T001 Widgets ship\n"
            "  - Verification: `npm test -- widgets`\n"
            "\n"
            "- [ ] T002 Widgets ship faster\n"
            "  - Depends on: T001\n"
            "  - Verification: `npm test -- widgets`\n"
        )
        findings = plan_check.mechanical_findings(text)
        self.assertEqual(len(findings), 1, f"a legitimately shared whole-suite command must still be reported: {findings}")
        self.assertEqual(findings[0]["severity"], "IMPORTANT",
                          "a reused generic/whole-suite command must not be an unfixable BLOCKING finding")
        self.assertEqual(findings[0]["classId"], CANONICAL_CLASSES[0])

    def test_reused_unittest_discover_is_important_not_blocking(self):
        text = (
            "- [ ] T001 A\n"
            "  - Verification: `python3 -m unittest discover`\n"
            "\n"
            "- [ ] T002 B\n"
            "  - Depends on: T001\n"
            "  - Verification: `python3 -m unittest discover`\n"
        )
        findings = plan_check.mechanical_findings(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "IMPORTANT")


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestLoopLensAsymmetryIsRecorded(unittest.TestCase):
    """Round-1 finding 4: D3 ("the two gates cannot drift") was only checked
    in one direction -- both shipped texts contain the four canonical
    classes, but nothing asserted the converse. loop.js's lens 2 (unbounded
    verification) has no counterpart in plan_check.BLOCKING_CLASSES or in
    SKILL.md step 5's mechanical/agent split, and that asymmetry was
    undocumented. This test is the record: the loop's lens-2 wording is
    pinned, confirmed absent from the shared mechanical classes, and its
    exclusion carries a reason -- so a REAL, unrecorded drift (a fifth loop
    lens nobody decided to exclude) still fails this test."""

    def test_unbounded_verification_lens_is_pinned_and_excluded_with_a_reason(self):
        prompt = _build_plan_check_prompt()
        self.assertIn(
            plan_check.UNBOUNDED_VERIFICATION_LENS, prompt,
            "the loop's lens-2 wording moved -- update the pinned copy in plan_check.py",
        )
        self.assertNotIn(plan_check.UNBOUNDED_VERIFICATION_LENS, plan_check.BLOCKING_CLASSES)
        self.assertIn(plan_check.UNBOUNDED_VERIFICATION_LENS, plan_check.LOOP_ONLY_LENSES)
        self.assertTrue(plan_check.LOOP_ONLY_LENSES[plan_check.UNBOUNDED_VERIFICATION_LENS].strip())


if __name__ == "__main__":
    unittest.main()
