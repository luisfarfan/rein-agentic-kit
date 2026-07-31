"""Tests for T001 -- the loop records its own run.

Same discipline as test_loop_policy.py / test_render_policy.py: `decideMeasureOutcome`
is extracted straight out of loop.js's source by regex and run with `new Function`,
so this proves the actual shipped decision logic, not a reimplementation that could
silently drift from it (T001 acceptance: "a source-substring assertion does not count").
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
const decideMeasureOutcome = new Function(
  'result', 'threw',
  extract('decideMeasureOutcome', 'result, threw')
);
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => decideMeasureOutcome(s.result, s.threw));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestDecideMeasureOutcomeIsExtractable(unittest.TestCase):
    def test_function_exists_with_expected_signature(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function decideMeasureOutcome(result, threw)", src)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestDecideMeasureOutcomePolicy(unittest.TestCase):
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

    def test_success_reports_recorded_and_wf_id(self):
        [out] = self._run([{"result": {"ok": True, "wfId": "wf_abc123", "error": ""}, "threw": ""}])
        self.assertEqual(out, {"recorded": True, "wfId": "wf_abc123", "error": ""})

    def test_missing_cli_is_reported_not_thrown(self):
        [out] = self._run([{
            "result": {"ok": False, "wfId": "", "error": "command not found: rein"},
            "threw": "",
        }])
        self.assertFalse(out["recorded"])
        self.assertIn("command not found", out["error"])

    def test_failed_parse_is_reported_not_thrown(self):
        [out] = self._run([{
            "result": {"ok": False, "wfId": "", "error": "invalid JSON"},
            "threw": "",
        }])
        self.assertFalse(out["recorded"])
        self.assertIn("invalid JSON", out["error"])

    def test_dead_agent_is_reported_not_thrown(self):
        """agentRetry returns null (never throws) when the agent dies without an exception."""
        [out] = self._run([{"result": None, "threw": ""}])
        self.assertFalse(out["recorded"])
        self.assertIn("died", out["error"])

    def test_thrown_agent_is_reported_not_thrown(self):
        """agentRetry rethrows on its final attempt -- must still resolve, not propagate."""
        [out] = self._run([{"result": None, "threw": "connection closed mid-response"}])
        self.assertFalse(out["recorded"])
        self.assertIn("connection closed mid-response", out["error"])

    def test_ok_true_with_no_wf_id_is_not_recorded(self):
        """A parse that "succeeded" but produced nothing stable to dedupe on is not a record."""
        [out] = self._run([{"result": {"ok": True, "wfId": "", "error": ""}, "threw": ""}])
        self.assertFalse(out["recorded"])

    def test_none_of_the_failure_shapes_carry_ok_approved_or_merged(self):
        """The function's own output vocabulary has no ok/approved/merged key at all --
        structurally it cannot be the thing that flips them (D4)."""
        scenarios = [
            {"result": {"ok": False, "wfId": "", "error": "missing CLI"}, "threw": ""},
            {"result": None, "threw": "parse failure"},
            {"result": None, "threw": ""},
        ]
        for out in self._run(scenarios):
            self.assertEqual(set(out), {"recorded", "wfId", "error"})


class TestMeasureStepRunsLast(unittest.TestCase):
    """AC5: a measurement taken before Review/Integrate would understate the run
    it claims to describe -- assert the step is wired in AFTER both, by position
    in the shipped source (the workflow script has no standalone entry point to
    execute end-to-end; source order is what the harness actually runs)."""

    def setUp(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            self.src = f.read()

    def test_measure_call_is_after_review_and_integrate_phases(self):
        review_idx = self.src.index("phase('Review')")
        integrate_idx = self.src.index("phase('Integrate')")
        measure_phase_idx = self.src.index("phase('Measure')")
        measure_call_idx = self.src.index("await runMeasureStep()")
        self.assertGreater(measure_phase_idx, review_idx)
        self.assertGreater(measure_phase_idx, integrate_idx)
        self.assertGreater(measure_call_idx, measure_phase_idx)

    def test_measure_result_feeds_the_returned_measure_field_not_a_string(self):
        # The old bug: `measure: `${REIN} token-report`,` -- a suggestion string.
        self.assertNotIn("measure: `${REIN} token-report`", self.src)
        self.assertIn("measure,", self.src)

    def test_measure_step_runs_after_the_return_objects_other_fields_are_decided(self):
        # ok/approved/merged must already be fixed by the time Measure runs --
        # this only checks the textual/positional precondition; the pure-function
        # test above proves decideMeasureOutcome itself cannot touch them.
        merged_decl_idx = self.src.index("let merged = false")
        measure_call_idx = self.src.index("await runMeasureStep()")
        self.assertGreater(measure_call_idx, merged_decl_idx)


class TestMeasureStepCommand(unittest.TestCase):
    """AC1: the final step actually EXECUTES `rein token-report --record
    [--change <label>]` -- not just a string sitting in the return value.

    Command construction lives in `buildMeasureCommand` (extracted in round 1
    so the empty-CHANGE case is testable -- see test_loop_policy.py's
    TestBuildMeasureCommandPolicy for the actual command values); this test
    only proves runMeasureStep calls it and hands the result to a real
    agentRetry(), not a suggestion string."""

    def setUp(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            self.src = f.read()

    def test_run_measure_step_executes_the_built_command_via_agent_retry(self):
        start = self.src.index("async function runMeasureStep()")
        end = self.src.index("\n}\n", start)
        body = self.src[start:end]
        self.assertIn("buildMeasureCommand(WD, REIN, CHANGE)", body)
        self.assertIn("agentRetry(", body)

    def test_build_measure_command_itself_shells_out_to_token_report_record(self):
        self.assertIn("token-report --record${changeFlag} --json", self.src)


if __name__ == "__main__":
    unittest.main()
