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
const buildPlanCheckPrompt = new Function('planPath', extract('buildPlanCheckPrompt', 'planPath'));
const decidePlanCheck = new Function('findings', extract('decidePlanCheck', 'findings'));
const findings = JSON.parse(findingsJson);
process.stdout.write(JSON.stringify({
  prompt: buildPlanCheckPrompt(planPath),
  verdict: decidePlanCheck(findings),
}));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestPlanCheckExtractable(unittest.TestCase):
    def test_functions_exist_with_expected_signatures(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function buildPlanCheckPrompt(planPath) {", src)
        self.assertIn("function decidePlanCheck(findings) {", src)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestPlanCheckPolicy(unittest.TestCase):
    def _run(self, plan_path: str, findings: list[dict]) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_PLANCHECK_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, plan_path, json.dumps(findings)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)

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


class TestPlanCheckSkippable(unittest.TestCase):
    def test_loop_js_gates_the_phase_on_args_plan_check_false(self):
        # args.planCheck: false must skip the phase entirely (acceptance #4).
        # This does not require node/extraction -- it is a static guarantee
        # about how the phase is gated in the shipped source.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ARGS.planCheck !== false", src)
        self.assertIn("plan check skipped (planCheck: false)", src)

    def test_dead_plan_check_agent_degrades_to_a_warning_not_an_abort(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        # A null/blocked plan-check result logs a warning and falls through
        # to Isolate -- it must never `return { ok: false, ... }` on its own.
        self.assertIn("plan check unavailable", src)
        self.assertIn("continuing without it", src)


if __name__ == "__main__":
    unittest.main()
