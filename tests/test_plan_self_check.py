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

    def test_mechanical_findings_ignores_structural_defects(self):
        """Behavioural proof, not a prose match: mechanical_findings() must
        implement NO structural rule of its own (missing Acceptance block,
        malformed task header, missing required section) -- that is
        openspec's job (D4). If a re-derived structural rule were added to
        plan_check.py tomorrow, this plan (which has none of the two defects
        mechanical_findings DOES decide -- no reused Verification, no broken
        dependency) would start producing findings and this test would fail."""
        # A plan with a genuinely absent Acceptance block, a header line that
        # doesn't match any of Why/Scope/Decisions, and no Scope/Decisions
        # section at all -- structurally malformed by any openspec-style rule,
        # but with valid, non-conflicting Verification commands and dependencies
        # so classes 1 and 3 (the ONLY two this module decides) stay silent.
        malformed = (
            "# Change: not-a-recognized-header\n\n"
            "not even a proper section title\n\n"
            "- [ ] T001 Task with no Acceptance block at all\n"
            "  - Type: implementation\n"
            "  - Depends on: none\n"
            "  - Verification: `python3 -m unittest tests.test_one`\n"
            "- [ ] T002 Task with no Acceptance block either\n"
            "  - Type: implementation\n"
            "  - Depends on: T001\n"
            "  - Verification: `python3 -m unittest tests.test_two`\n"
        )
        self.assertEqual(plan_check.mechanical_findings(malformed), [])

    def test_missing_binary_is_named_as_a_skip_not_a_failure(self):
        self.assertIn("missing `openspec` binary skips this half silently", self.flat)
        self.assertIn("not a failure", self.flat)

    def test_skill_runs_command_v_openspec_itself(self):
        """D4/D5's openspec-availability check is a single, honest mechanism:
        the shipped skill's own `command -v openspec` in step 5(a), not a
        Python helper no production path calls. (Round-1 finding 1 closed
        this exact gap for mechanical_findings; this closes it for the
        openspec half by deleting the unreached plan_check.openspec_binary
        rather than leaving it covered only by tests that monkeypatch
        shutil.which.)"""
        self.assertFalse(hasattr(plan_check, "openspec_binary"))
        self.assertIn("command -v openspec", _read(SKILL_MD))


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

    def test_cli_reports_the_real_defect_fixture(self):
        """End-to-end through the subprocess CLI: the defect must reach the
        caller. IMPORTANT, not BLOCKING -- see TestRealDefectFixtureIsReported."""
        fixture = os.path.join(FIXTURES, "plan_defect_t003.md")
        proc = subprocess.run(
            [sys.executable, REIN_BIN, "plan-check", fixture],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(proc.returncode, 0, "D5: plan-check never fails the caller")
        report = json.loads(proc.stdout)
        t003 = [f for f in report["findings"] if f["taskId"] == "T003"]
        self.assertTrue(t003, f"expected a finding on T003 from the CLI, got {report['findings']}")
        self.assertEqual(t003[0]["severity"], "IMPORTANT")

    def test_cli_never_fails_on_a_missing_file(self):
        proc = subprocess.run(
            [sys.executable, REIN_BIN, "plan-check", "/no/such/plan.md"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["error"])


class TestRealDefectFixtureIsReported(unittest.TestCase):
    """The measured defect must SURFACE. It must not hold the write.

    Three mechanical rules were tried and measured over this repo's own 50
    historical plan texts:

        bare reuse of a verification           -> blocked 26 of them
        an earlier task's criteria name it     -> blocked 12
        the task contradicts its own criteria  ->  0, and stopped catching
                                                   the real defect too

    Every rule that caught the defect also refused plans this project wrote,
    approved, executed and merged. Two tasks sharing one test module by
    convention and a task naming the WRONG module are indistinguishable in
    the plan text; separating them means judging whether those criteria could
    be proven by that module, which is semantics, not a regex.

    So the mechanical half reports IMPORTANT and the agent critique makes the
    blocking call. A check that cannot decide must not decide.
    """

    def _findings(self):
        with open(os.path.join(FIXTURES, "plan_defect_t003.md"), encoding="utf-8") as fh:
            return plan_check.mechanical_findings(fh.read())

    def test_the_defect_is_reported_and_names_the_task(self):
        fs = [f for f in self._findings() if f["taskId"] == "T003"]
        self.assertTrue(fs, "the measured defect is no longer surfaced at all")
        self.assertEqual(fs[0]["severity"], "IMPORTANT")

    def test_the_finding_names_whose_verification_it_is(self):
        f = [x for x in self._findings() if x["taskId"] == "T003"][0]
        self.assertIn("T002", f["text"])

    def test_the_mechanical_half_never_blocks_on_a_reused_verification(self):
        """The property the whole change turns on: this cannot refuse a plan."""
        for f in self._findings():
            if f.get("classId") == plan_check.BLOCKING_CLASSES[0]:
                self.assertNotEqual(f["severity"], "BLOCKING")


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


class TestTheCheckIsQuietOnPlansThisRepoApproved(unittest.TestCase):
    """The property two hand-picked fixtures cannot establish.

    Criterion 4 said "a healthy plan produces NO blocking findings", and it
    was satisfied by checking in the two plans that happened to pass. The
    review replayed the rule over git history instead and found it blocking
    the MAJORITY of the plans this project wrote, approved, executed and
    merged -- an acceptance criterion met in letter by fixtures that avoid
    the case, which is the exact failure this repo has named all along, here
    committed by the check built to catch it.

    So the corpus is the repo's own history, read at test time. It grows on
    its own and cannot be curated to pass.
    """

    def _historical_plans(self) -> list:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shas = subprocess.run(
            ["git", "-C", repo, "log", "--format=%H", "--", "tasks.md"],
            capture_output=True, text=True,
        ).stdout.split()
        seen, out = set(), []
        for sha in shas:
            body = subprocess.run(
                ["git", "-C", repo, "show", f"{sha}:tasks.md"],
                capture_output=True, text=True,
            ).stdout
            if body and body not in seen:
                seen.add(body)
                out.append((sha[:8], body))
        return out

    def test_no_plan_in_this_repos_history_is_refused(self):
        plans = self._historical_plans()
        if len(plans) < 5:
            self.skipTest("not enough plan history in this checkout to be meaningful")
        refused = []
        for sha, body in plans:
            blocking = [f for f in plan_check.mechanical_findings(body)
                        if f.get("severity") == "BLOCKING"]
            if blocking:
                refused.append((sha, body.splitlines()[0][:40], [f["taskId"] for f in blocking]))
        self.assertEqual(
            refused, [],
            f"the check would have refused to write {len(refused)} of {len(plans)} plans this "
            f"project already approved and merged: {refused[:4]}",
        )

    def test_the_corpus_is_large_enough_to_mean_something(self):
        """A guard on the guard: if history stops being read, the test above
        passes vacuously and the property goes unproven again."""
        plans = self._historical_plans()
        self.assertGreater(len(plans), 20, "the historical corpus shrank — is git history reachable?")


class TestThePlansShapeIsReported(unittest.TestCase):
    """Two facts about a plan AS A WHOLE that cost hours, and that no per-task
    lens could ever have seen.

    Neither blocks. How much time to spend is the operator's call, and a gate
    that refused a big plan would be this tool deciding something that is not
    its to decide.
    """

    def _find(self, tasks: str, cls: str) -> list:
        text = "# Change: probe\n## Why\nx\n---\n" + tasks
        return [f for f in plan_check.mechanical_findings(text) if f.get("classId") == cls]

    def _task(self, tid: str, human: str = "false") -> str:
        return (f"- [ ] {tid} t\n  - Type: implementation\n  - Depends on: none\n"
                f"  - Human review: {human}\n  - Verification: `python3 -m unittest tests.test_{tid.lower()}`\n"
                f"  - Acceptance:\n    - covered by tests/test_{tid.lower()}.py\n\n")

    def test_a_nine_task_plan_is_reported(self):
        """The measured shape: 95 minutes of implementation, zero review."""
        tasks = "".join(self._task(f"T00{i}") for i in range(1, 10))
        found = self._find(tasks, "plan shape")
        self.assertTrue(any("9 tasks in one change" in f["text"] for f in found))
        self.assertTrue(all(f["severity"] == "IMPORTANT" for f in found),
                        "plan size is a time estimate, never a defect that blocks")

    def test_a_small_plan_says_nothing(self):
        """A check that fires on ordinary plans stops being read."""
        tasks = "".join(self._task(f"T00{i}") for i in range(1, 4))
        self.assertEqual(self._find(tasks, "plan shape"), [])

    def test_a_supervised_task_among_buildable_ones_is_reported(self):
        """The one most likely to block, and in a measured run it took the
        review of eight landed tasks down with it."""
        tasks = "".join(self._task(f"T00{i}") for i in range(1, 3)) + self._task("T009", human="true")
        found = self._find(tasks, "plan shape")
        self.assertTrue(any("supervised task" in f["text"] for f in found))
        self.assertTrue(any(f["taskId"] == "T009" for f in found), "the finding must name it")

    def test_a_plan_that_is_ONLY_supervised_is_not_reported(self):
        """Nothing to separate it from -- the finding is about mixing."""
        tasks = self._task("T001", human="true") + self._task("T002", human="true")
        self.assertEqual(
            [f for f in self._find(tasks, "plan shape") if "supervised" in f["text"]], [])

    def test_completed_tasks_do_not_count_toward_the_size(self):
        """A resumed run's plan is mostly ticked boxes; counting them would
        fire on every continuation."""
        done = "".join(self._task(f"T00{i}").replace("- [ ]", "- [x]") for i in range(1, 9))
        tasks = done + self._task("T009")
        self.assertEqual([f for f in self._find(tasks, "plan shape") if "tasks in one change" in f["text"]], [])
