"""Tests for the gate precheck in plugins/rein/workflows/loop.js (T003).

Same discipline as test_render_policy.py: `decideGatePrecheck` is extracted
straight out of loop.js's source by regex and run with `new Function`, so
this proves the actual shipped logic, not a reimplementation that could
silently drift from it. A source-substring assertion does not count (T003
acceptance) -- every branch below actually EXECUTES the function.
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
const decideGatePrecheck = new Function(
  'verifyGate', 'monorepoUnconfigured',
  extract('decideGatePrecheck', 'verifyGate, monorepoUnconfigured')
);
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => decideGatePrecheck(s.verifyGate, s.monorepoUnconfigured));
process.stdout.write(JSON.stringify(out));
"""


def _slot(configured, invocable, outcome=""):
    return {"configured": configured, "invocable": invocable, "outcome": outcome}


def _gate(test=None, lint=None, typecheck=None):
    return {
        "test": test or _slot(False, True),
        "lint": lint or _slot(False, True),
        "typecheck": typecheck or _slot(False, True),
    }


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class GatePrecheckTestCase(unittest.TestCase):
    def _run(self, scenarios: list) -> list:
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


class TestFunctionIsExtractable(unittest.TestCase):
    def test_function_exists_with_expected_signature(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function decideGatePrecheck(verifyGate, monorepoUnconfigured)", src)


class TestContextSchemaCarriesVerifyGate(unittest.TestCase):
    def test_context_schema_has_verify_gate_field(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        schema_start = src.index("const CONTEXT_SCHEMA")
        schema_end = src.index("const ISOLATE_SCHEMA")
        schema_src = src[schema_start:schema_end]
        self.assertIn("verifyGate", schema_src)
        # the field is REQUIRED, the same "report it literally" style as the
        # existing cmdTest/verifyPolicy fields -- not an optional afterthought.
        self.assertIn("'verifyGate'", schema_src)
        for slot in ("test", "lint", "typecheck"):
            self.assertIn(slot, schema_src)
        for prop in ("configured", "invocable", "outcome"):
            self.assertIn(prop, schema_src)

    def test_context_schema_has_monorepo_unconfigured_field(self):
        # Round-2 finding 6: the ONE fact that distinguishes "monorepo root
        # awaiting a sub-project choice" from an ordinary unconfigured slot.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        schema_start = src.index("const CONTEXT_SCHEMA")
        schema_end = src.index("const ISOLATE_SCHEMA")
        schema_src = src[schema_start:schema_end]
        self.assertIn("monorepoUnconfigured", schema_src)
        self.assertIn("'monorepoUnconfigured'", schema_src)


class TestTestNotInvocableStops(GatePrecheckTestCase):
    """No implementer is paid to work toward a gate that cannot pass (D3)."""

    def test_test_not_invocable_stops(self):
        [result] = self._run([
            {"verifyGate": _gate(test=_slot(True, False, "not_invocable"))},
        ])
        self.assertEqual(result["decision"], "stop")
        self.assertIn("test", result["reason"])
        self.assertIn("not_invocable", result["reason"])

    def test_test_not_invocable_stops_even_with_lint_and_typecheck_fine(self):
        [result] = self._run([
            {
                "verifyGate": _gate(
                    test=_slot(True, False, "not_invocable"),
                    lint=_slot(True, True, "ok"),
                    typecheck=_slot(True, True, "ok"),
                )
            },
        ])
        self.assertEqual(result["decision"], "stop")


class TestTestInvocableButFailingContinues(GatePrecheckTestCase):
    """The ordinary state of a repo mid-change -- stopping for it would make
    the loop unusable.
    """

    def test_test_invocable_but_failing_continues(self):
        [result] = self._run([
            {"verifyGate": _gate(test=_slot(True, True, "failed"))},
        ])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["warnings"], [])


class TestLintOrTypecheckNotInvocableIsWarningOnly(GatePrecheckTestCase):
    """Neither is required for a verdict -- a warning carried to the
    reviewer, never a stop.
    """

    def test_lint_not_invocable_warns_but_continues(self):
        [result] = self._run([
            {
                "verifyGate": _gate(
                    test=_slot(True, True, "ok"),
                    lint=_slot(True, False, "not_invocable"),
                )
            },
        ])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("lint", result["warnings"][0])

    def test_typecheck_not_invocable_warns_but_continues(self):
        [result] = self._run([
            {
                "verifyGate": _gate(
                    test=_slot(True, True, "ok"),
                    typecheck=_slot(True, False, "not_invocable"),
                )
            },
        ])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("typecheck", result["warnings"][0])

    def test_both_lint_and_typecheck_not_invocable_produce_two_warnings(self):
        [result] = self._run([
            {
                "verifyGate": _gate(
                    test=_slot(True, True, "ok"),
                    lint=_slot(True, False, "not_invocable"),
                    typecheck=_slot(True, False, "not_invocable"),
                )
            },
        ])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(len(result["warnings"]), 2)


class TestAllVerifiedContinues(GatePrecheckTestCase):
    def test_all_configured_and_invocable_continues_with_no_warnings(self):
        [result] = self._run([
            {
                "verifyGate": _gate(
                    test=_slot(True, True, "ok"),
                    lint=_slot(True, True, "ok"),
                    typecheck=_slot(True, True, "ok"),
                )
            },
        ])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["warnings"], [])

    def test_nothing_configured_continues_with_no_warnings(self):
        [result] = self._run([{"verifyGate": _gate()}])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["warnings"], [])


class TestMissingVerifyGateDegradesGracefully(GatePrecheckTestCase):
    def test_empty_object_continues(self):
        [result] = self._run([{"verifyGate": {}}])
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["warnings"], [])


class TestPreparePromptScopesVerifyToGateSlots(unittest.TestCase):
    """Coherence gap between T002 and T003 (round-1 finding 3): `decideGatePrecheck`
    reads only test/lint/typecheck, so the Prepare agent must be told to run
    `rein verify` scoped to exactly those slots -- otherwise `detect`'s `build`
    slot (and the whole `test` suite again) runs, unfiltered, in the operator's
    MAIN checkout before Isolate even starts, for information this precheck
    never consults. This is instruction TEXT for an LLM agent, not executable
    policy -- unlike `decideGatePrecheck` itself (tested above by execution),
    there is no function here to run, so a source assertion is the actual test.
    """

    def setUp(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            self.source = f.read()

    def test_prepare_prompt_passes_only_test_lint_typecheck(self):
        self.assertIn("--only test,lint,typecheck", self.source)

    def test_prepare_prompt_no_longer_runs_verify_unfiltered(self):
        # The OLD instruction ran every resolved slot unfiltered -- assert
        # the unscoped invocation is gone, not just that the new one exists.
        self.assertNotIn("verify ${ROOT} --json", self.source)


class TestMonorepoUnconfiguredStops(GatePrecheckTestCase):
    """Round-2 finding 6: for the exact repo that motivated this change --
    a monorepo with no `subproject` chosen -- detect resolves ZERO commands,
    so every verifyGate slot reads 'unconfigured' and, without this, the
    ordinary 'unconfigured is never a stop' default would run a full change
    with no mechanical gate whatsoever.
    """

    def test_monorepo_unconfigured_stops_even_with_every_slot_unconfigured(self):
        [result] = self._run([
            {"verifyGate": _gate(), "monorepoUnconfigured": True},
        ])
        self.assertEqual(result["decision"], "stop")
        self.assertIn("sub-project", result["reason"])
        self.assertIn("monorepo", result["reason"])

    def test_monorepo_unconfigured_false_continues_as_before(self):
        [result] = self._run([
            {"verifyGate": _gate(), "monorepoUnconfigured": False},
        ])
        self.assertEqual(result["decision"], "continue")

    def test_missing_monorepo_unconfigured_key_degrades_to_continue(self):
        # An older Prepare agent's ctx may not carry this field at all.
        [result] = self._run([{"verifyGate": _gate()}])
        self.assertEqual(result["decision"], "continue")

    def test_test_not_invocable_reason_takes_precedence(self):
        [result] = self._run([
            {
                "verifyGate": _gate(test=_slot(True, False, "not_invocable")),
                "monorepoUnconfigured": True,
            },
        ])
        self.assertEqual(result["decision"], "stop")
        self.assertIn("not_invocable", result["reason"])
        self.assertNotIn("sub-project", result["reason"])


if __name__ == "__main__":
    unittest.main()
