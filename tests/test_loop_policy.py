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


if __name__ == "__main__":
    unittest.main()
