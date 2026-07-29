"""Tests for the round-decision policy in plugins/rein/workflows/loop.js.

Same discipline as test_verify_policy.py's TestPolicyBlockRenderedContent:
`decideRound` is extracted straight out of loop.js's source by regex and run
with `new Function`, so this proves the actual shipped logic, not a
reimplementation that could silently drift from it.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

LOOP_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "rein", "workflows", "loop.js",
)

_NODE = shutil.which("node")

_EXTRACT_AND_RUN_JS = r"""
const fs = require('fs');
const [, , loopPath, scenariosJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const decideRound = new Function(
  'review', 'round', 'maxRounds',
  extract('decideRound', 'review, round, maxRounds')
);
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => decideRound(s.review, s.round, s.maxRounds));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestDecideRoundIsExtractable(unittest.TestCase):
    def test_function_exists_with_expected_signature(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function decideRound(review, round, maxRounds)", src)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestDecideRoundPolicy(unittest.TestCase):
    def _run(self, scenarios: list[dict]) -> list[dict]:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_AND_RUN_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, json.dumps(scenarios)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)

    def test_blockers_present_costs_a_round(self):
        [result] = self._run([{
            "review": {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "findings": [
                    {"severity": "BLOCKING", "text": "off-by-one in the paginator"},
                    {"severity": "SUGGESTION", "text": "rename this variable"},
                ],
                "gateGreen": True,
                "needsHumanDecision": False,
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "fix")
        # SUGGESTION never reaches the fix prompt.
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "BLOCKING")

    def test_only_suggestions_resolves_to_approve(self):
        [result] = self._run([{
            "review": {
                "approved": True,
                "verdict": "APPROVED",
                "findings": [{"severity": "SUGGESTION", "text": "consider a docstring"}],
                "gateGreen": True,
                "needsHumanDecision": False,
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "approve")
        self.assertFalse(result["overridden"])
        # The observation is still carried, not dropped.
        self.assertEqual(len(result["findings"]), 1)

    def test_only_importants_resolves_to_approve_with_override_logged(self):
        # D1: only BLOCKING repeats the round. A reviewer that said
        # CHANGES_REQUESTED with only IMPORTANT findings is overridden —
        # symmetric to the existing red-gate override.
        [result] = self._run([{
            "review": {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "findings": [{"severity": "IMPORTANT", "text": "missing a test for the edge case"}],
                "gateGreen": True,
                "needsHumanDecision": False,
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "approve")
        self.assertTrue(result["overridden"])
        self.assertEqual(len(result["findings"]), 1)

    def test_gate_red_with_no_blockers_still_costs_a_round(self):
        [result] = self._run([{
            "review": {
                "approved": True,
                "verdict": "APPROVED",
                "findings": [],
                "gateGreen": False,
                "needsHumanDecision": False,
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "fix")
        self.assertIn("gate is red", result["reason"])

    def test_needs_human_decision_escalates_regardless_of_findings(self):
        [result] = self._run([{
            "review": {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "findings": [{"severity": "BLOCKING", "text": "should never matter here"}],
                "gateGreen": True,
                "needsHumanDecision": True,
                "humanDecisionReason": "owner must confirm the pricing copy",
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "escalate")
        self.assertEqual(result["humanDecisionReason"], "owner must confirm the pricing copy")

    def test_last_round_with_blockers_rejects_instead_of_spending_a_round(self):
        [result] = self._run([{
            "review": {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "findings": [{"severity": "BLOCKING", "text": "still broken"}],
                "gateGreen": True,
                "needsHumanDecision": False,
            },
            "round": 3, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "reject")

    def test_untagged_string_findings_do_not_unlock_the_override(self):
        # A reviewer whose structured-output schema still declares
        # `findings: string[]` (an older harness) says CHANGES_REQUESTED with
        # plain-string findings. gate.record_review refuses to even record
        # that verdict (D2: no BLOCKING-tagged finding). decideRound must not
        # be quieter than that refusal by silently flipping it to approve.
        [result] = self._run([{
            "review": {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "findings": ["something is wrong"],
                "gateGreen": True,
                "needsHumanDecision": False,
            },
            "round": 1, "maxRounds": 3,
        }])
        self.assertEqual(result["decision"], "fix")

    def test_empty_findings_with_changes_requested_do_not_unlock_the_override(self):
        """The louder half of the same violation: a reviewer that said
        CHANGES_REQUESTED and returned ZERO findings spoke the vocabulary
        less, not more, than one that returned untagged strings. The gate CLI
        refuses to record that episode; the loop must not merge on it."""
        for findings in ([], None):
            review = {
                "approved": False,
                "verdict": "CHANGES_REQUESTED",
                "gateGreen": True,
                "needsHumanDecision": False,
            }
            if findings is not None:
                review["findings"] = findings
            [result] = self._run([{"review": review, "round": 1, "maxRounds": 3}])
            self.assertEqual(result["decision"], "fix", f"findings={findings!r}")


# ── buildFixFindings: the red-gate reason must reach the fix agent ──────────
# Regression coverage: an APPROVED verdict with a red gate produces a 'fix'
# decision whose fixWorthy findings are empty (D2: APPROVED carries no
# BLOCKING findings), which used to render the fix prompt with an empty
# findings list and no mention that the gate is red. buildFixFindings is what
# the loop actually calls to build lastFindings for the fix prompt, so this
# is extracted from the shipped source the same way decideRound is.

_EXTRACT_BUILD_FIX_FINDINGS_JS = r"""
const fs = require('fs');
const [, , loopPath, scenariosJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const buildFixFindings = new Function(
  'review', 'decision',
  extract('buildFixFindings', 'review, decision')
);
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => buildFixFindings(s.review, s.decision));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestBuildFixFindingsIsExtractable(unittest.TestCase):
    def test_function_exists_with_expected_signature(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function buildFixFindings(review, decision)", src)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestBuildFixFindingsPolicy(unittest.TestCase):
    def _run(self, scenarios: list[dict]) -> list[list[dict]]:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_BUILD_FIX_FINDINGS_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, json.dumps(scenarios)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)

    def test_red_gate_with_approved_verdict_and_no_findings_still_yields_a_reason(self):
        # This is exactly the branch the loop override exists for: APPROVED with a
        # red gate. By D2, APPROVED carries zero BLOCKING findings, so fixWorthy
        # (decision.findings) is empty -- yet the fix agent must still be told the
        # gate is red instead of receiving an empty findings list.
        [result] = self._run([{
            "review": {"approved": True, "gateGreen": False},
            "decision": {
                "decision": "fix",
                "findings": [],
                "reason": "the mechanical gate is red; it must be green before approval",
            },
        }])
        self.assertTrue(result, "findings list must be non-empty when the gate is red")
        self.assertIn("gate is red", " ".join(f["text"] for f in result))
        self.assertTrue(any(f["severity"] == "BLOCKING" for f in result))

    def test_green_gate_passes_findings_through_unchanged(self):
        [result] = self._run([{
            "review": {"approved": False, "gateGreen": True},
            "decision": {
                "decision": "fix",
                "findings": [{"severity": "BLOCKING", "text": "off-by-one"}],
                "reason": "1 BLOCKING finding(s)",
            },
        }])
        self.assertEqual(result, [{"severity": "BLOCKING", "text": "off-by-one"}])


# ── PlanCheck (T003): a plan-only reviewer that runs before any implementer ──
# is paid. buildPlanCheckPrompt and decidePlanCheck are extracted the same way
# decideRound is above -- straight regex pull of the shipped source, run with
# `new Function` -- so these prove the actual prompt and stop-decision, not a
# reimplementation that could silently drift from what loop.js ships.

_EXTRACT_PLANCHECK_JS = r"""
const fs = require('fs');
const [, , loopPath, planPath, findingsJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const buildPlanCheckPrompt = new Function('planPath', 'taskIds', extract('buildPlanCheckPrompt', 'planPath, taskIds'));
const decidePlanCheck = new Function('findings', 'runIds', extract('decidePlanCheck', 'findings, runIds'));
const shouldRunPlanCheck = new Function('args', extract('shouldRunPlanCheck', 'args'));
const payload = JSON.parse(findingsJson);
process.stdout.write(JSON.stringify({
  prompt: buildPlanCheckPrompt(planPath, payload.taskIds || []),
  verdict: decidePlanCheck(payload.findings || [], payload.runIds || []),
  shouldRun: shouldRunPlanCheck(payload.args === undefined ? {} : payload.args),
}));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestPlanCheckExtractable(unittest.TestCase):
    def test_functions_exist_with_expected_signatures(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function buildPlanCheckPrompt(planPath, taskIds) {", src)
        self.assertIn("function decidePlanCheck(findings, runIds) {", src)
        self.assertIn("function shouldRunPlanCheck(args) {", src)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class _PlanCheckHarness(unittest.TestCase):
    """Shared extraction runner. Not a test class itself — subclassing a class
    that HAS tests re-runs them once per subclass, padding the count."""

    def _run(self, plan_path: str, findings: list[dict], run_ids=None, task_ids=None, args=None) -> dict:
        payload = {"findings": findings, "runIds": run_ids, "taskIds": task_ids, "args": args}
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_PLANCHECK_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, plan_path, json.dumps(payload)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestPlanCheckPolicy(_PlanCheckHarness):
    def test_prompt_names_all_four_lenses_and_forbids_reading_the_codebase(self):
        out = self._run("tasks.md", [])
        prompt = out["prompt"]
        # Lens 1: criteria that cannot be checked as written.
        self.assertIn("cannot be checked as written", prompt)
        # Lens 2: unbounded verification (whole suite where one test would do).
        self.assertIn("unbounded", prompt)
        self.assertIn("one test", prompt)
        # Lens 3: a fixture that avoids the case -- the repeat failure.
        self.assertIn("fixture", prompt)
        # Lens 4: contradicts the plan's own Scope or dependency order.
        self.assertIn("Scope", prompt)
        self.assertIn("dependency order", prompt)
        # The plan path is the ONLY thing this agent is told to read.
        self.assertIn("tasks.md", prompt)
        self.assertIn("Do NOT read", prompt)
        self.assertIn("codebase", prompt)

    def test_blocking_finding_stops(self):
        out = self._run("tasks.md", [
            {"taskId": "T005", "severity": "BLOCKING", "text": "verification runs the whole suite"},
            {"taskId": "T006", "severity": "SUGGESTION", "text": "wording could be tighter"},
        ])
        verdict = out["verdict"]
        self.assertEqual(verdict["decision"], "stop")
        self.assertIn("BLOCKING", verdict["reason"])
        # Findings are carried in the return value (D4), not dropped.
        self.assertEqual(len(verdict["findings"]), 2)

    def test_suggestion_only_continues(self):
        out = self._run("tasks.md", [
            {"taskId": "T006", "severity": "SUGGESTION", "text": "wording could be tighter"},
        ])
        verdict = out["verdict"]
        self.assertEqual(verdict["decision"], "continue")
        # Non-blocking findings are still carried, not dropped, per D4.
        self.assertEqual(len(verdict["findings"]), 1)

    def test_no_findings_continues(self):
        out = self._run("tasks.md", [])
        self.assertEqual(out["verdict"]["decision"], "continue")
        self.assertEqual(out["verdict"]["findings"], [])


class TestPlanCheckScopedToTheRun(_PlanCheckHarness):
    """A BLOCKING finding on a task this run will not execute must not stop the
    run (round-4 IMPORTANT): the defect cannot waste this run's implementers,
    and the only escape was planCheck: false — which throws the check away for
    the tasks that ARE running."""

    BLOCKER = {"taskId": "T009", "severity": "BLOCKING", "text": "vague criterion"}

    def test_blocking_on_an_out_of_run_task_does_not_stop(self):
        out = self._run("tasks.md", [self.BLOCKER], run_ids=["T001", "T002"])
        self.assertEqual(out["verdict"]["decision"], "continue")
        # ...but the finding is still carried, not dropped.
        self.assertEqual(len(out["verdict"]["findings"]), 1)

    def test_blocking_on_an_in_run_task_still_stops(self):
        out = self._run("tasks.md", [dict(self.BLOCKER, taskId="T001")], run_ids=["T001", "T002"])
        self.assertEqual(out["verdict"]["decision"], "stop")

    def test_plan_level_finding_without_task_id_stops(self):
        """A Scope contradiction has no single task id — it concerns the run."""
        out = self._run("tasks.md", [{"taskId": "", "severity": "BLOCKING", "text": "Scope contradicts itself"}],
                        run_ids=["T001"])
        self.assertEqual(out["verdict"]["decision"], "stop")

    def test_task_id_comparison_is_case_insensitive(self):
        out = self._run("tasks.md", [dict(self.BLOCKER, taskId="t001")], run_ids=["T001"])
        self.assertEqual(out["verdict"]["decision"], "stop")

    def test_prompt_names_the_run_scope_when_ids_are_given(self):
        out = self._run("tasks.md", [], task_ids=["T001", "T002"])
        self.assertIn("Judge ONLY these tasks", out["prompt"])
        self.assertIn("[T001, T002]", out["prompt"])


class TestPlanCheckSkippable(_PlanCheckHarness):
    def test_skip_gate_is_executed_not_grepped(self):
        """args.planCheck: false skips; everything else runs. Executed via the
        extracted shouldRunPlanCheck — a source substring can be satisfied by a
        comment, which is the fixture-avoids-the-case failure in test form."""
        for args, expect in (({}, True), ({"planCheck": False}, False),
                             ({"planCheck": True}, True), (None, True)):
            out = self._run("tasks.md", [], args=args)
            self.assertEqual(out["shouldRun"], expect, f"args={args!r}")

    def test_phase_call_site_uses_the_gate(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("if (shouldRunPlanCheck(ARGS)) {", src)
        self.assertIn("plan check skipped (planCheck: false)", src)

    def test_dead_plan_check_agent_degrades_to_a_warning_not_an_abort(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        # A null/blocked plan-check result logs a warning and falls through
        # to Isolate -- it must never `return { ok: false, ... }` on its own.
        self.assertIn("plan check unavailable", src)
        self.assertIn("continuing without it", src)

    def test_non_blocking_plan_findings_are_carried_into_the_return(self):
        # T003 AC3's continue half: non-blocking plan-check findings must reach
        # the loop's return value, not just the log line. decidePlanCheck's pure
        # 'findings are not dropped' guarantee (test_suggestion_only_continues
        # above) proves nothing about the shipped call site unless the source
        # actually threads verdict.findings through to the final return object.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("let planFindings = []", src)
        self.assertIn("planFindings = verdict.findings", src)
        self.assertIn("planFindings,", src)


if __name__ == "__main__":
    unittest.main()
