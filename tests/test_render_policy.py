"""Tests for the render policy in plugins/rein/workflows/loop.js (T002).

Same discipline as test_loop_policy.py: `decideRenderDispatch`,
`decideRenderOutcome` and `buildRenderPrompt` are extracted straight out of
loop.js's source by regex and run with `new Function`, so this proves the
actual shipped logic and prompt text, not a reimplementation that could
silently drift from it.
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
const decideRenderDispatch = new Function('verifyPolicy', extract('decideRenderDispatch', 'verifyPolicy'));
const decideRenderOutcome = new Function('render', extract('decideRenderOutcome', 'render'));
const buildRenderPrompt = new Function('command', 'url', 'tools', extract('buildRenderPrompt', 'command, url, tools'));
const decideRound = new Function(
  'review', 'round', 'maxRounds', 'render',
  extract('decideRound', 'review, round, maxRounds, render')
);
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => {
  if (s.kind === 'dispatch') return decideRenderDispatch(s.verifyPolicy);
  if (s.kind === 'outcome') return decideRenderOutcome(s.render);
  if (s.kind === 'prompt') return buildRenderPrompt(s.command, s.url, s.tools);
  if (s.kind === 'round') return decideRound(s.review, s.round, s.maxRounds, s.render);
  throw new Error('unknown scenario kind: ' + s.kind);
});
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class RenderPolicyTestCase(unittest.TestCase):
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


class TestFunctionsAreExtractable(unittest.TestCase):
    def test_functions_exist_with_expected_signatures(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function decideRenderDispatch(verifyPolicy)", src)
        self.assertIn("function decideRenderOutcome(render)", src)
        self.assertIn("function buildRenderPrompt(command, url, tools)", src)


class TestDecideRenderOutcome(RenderPolicyTestCase):
    """D3: a `rendered: true` with no facts alongside it is a failed render."""

    def test_evidence_less_true_is_failed(self):
        [out] = self._run([{
            "kind": "outcome",
            "render": {"rendered": True, "httpStatus": 200, "title": "x", "consoleErrors": [], "evidence": [], "notes": ""},
        }])
        self.assertTrue(out["failed"])
        self.assertIn("evidence", out["reason"])

    def test_non_2xx_status_is_failed(self):
        [out] = self._run([{
            "kind": "outcome",
            "render": {
                "rendered": True, "httpStatus": 500, "title": "x", "consoleErrors": [],
                "evidence": ["HTTP 500"], "notes": "",
            },
        }])
        self.assertTrue(out["failed"])
        self.assertIn("500", out["reason"])

    def test_absent_status_is_failed(self):
        [out] = self._run([{
            "kind": "outcome",
            "render": {"rendered": True, "title": "x", "consoleErrors": [], "evidence": ["HTTP 200"], "notes": ""},
        }])
        self.assertTrue(out["failed"])

    def test_rendered_false_is_failed_even_with_evidence(self):
        [out] = self._run([{
            "kind": "outcome",
            "render": {
                "rendered": False, "httpStatus": 200, "title": "", "consoleErrors": [],
                "evidence": ["HTTP 200"], "notes": "",
            },
        }])
        self.assertTrue(out["failed"])
        self.assertIn("rendered", out["reason"])

    def test_rendered_true_2xx_with_evidence_passes(self):
        [out] = self._run([{
            "kind": "outcome",
            "render": {
                "rendered": True, "httpStatus": 200, "title": "Dashboard", "consoleErrors": [],
                "evidence": ["HTTP 200", "title: Dashboard", "0 console errors"], "notes": "",
            },
        }])
        self.assertFalse(out["failed"])
        self.assertEqual(out["reason"], "")


class TestDecideRenderDispatch(RenderPolicyTestCase):
    """D4: no reachable browser tool is an explicit, carried outcome — never a
    silent pass, never a hard stop. Non-`rendered` modes dispatch nothing."""

    def test_empty_tools_is_unverified_not_dispatched(self):
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": []},
        }])
        self.assertFalse(out["dispatch"])
        self.assertTrue(out["unverified"])
        self.assertTrue(out["reason"])

    def test_non_rendered_mode_never_dispatches(self):
        for mode in ("unit", "plan-only", ""):
            [out] = self._run([{
                "kind": "dispatch",
                "verifyPolicy": {"mode": mode, "requires": [], "forbids": [], "tools": ["playwright"]},
            }])
            self.assertFalse(out["dispatch"], mode)
            self.assertFalse(out["unverified"], mode)

    def test_rendered_mode_with_a_reachable_tool_dispatches(self):
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": ["playwright"]},
        }])
        self.assertTrue(out["dispatch"])
        self.assertFalse(out["unverified"])


class TestBuildRenderPrompt(RenderPolicyTestCase):
    def test_prompt_names_command_url_and_only_reachable_tools(self):
        [prompt] = self._run([{
            "kind": "prompt",
            "command": "npm run dev", "url": "http://localhost:5173", "tools": ["playwright"],
        }])
        self.assertIn("npm run dev", prompt)
        self.assertIn("http://localhost:5173", prompt)
        self.assertIn("playwright", prompt)
        # Never names a tool the project cannot reach — no other tool literal invented.
        for unreachable in ("chrome-devtools", "puppeteer", "cypress", "selenium"):
            self.assertNotIn(unreachable, prompt)

    def test_prompt_instructs_serve_probe_for_startup_and_teardown(self):
        [prompt] = self._run([{
            "kind": "prompt",
            "command": "npm run dev", "url": "http://localhost:5173", "tools": ["playwright"],
        }])
        self.assertIn("rein serve-probe", prompt)
        self.assertNotIn(" & ", prompt)
        self.assertIn("do NOT", prompt)

    def test_prompt_requires_the_full_evidence_shape(self):
        [prompt] = self._run([{
            "kind": "prompt",
            "command": "npm run dev", "url": "http://localhost:5173", "tools": ["playwright"],
        }])
        for field in ("rendered", "httpStatus", "title", "consoleErrors", "evidence", "notes"):
            self.assertIn(field, prompt)

    def test_prompt_with_empty_tools_invents_nothing(self):
        [prompt] = self._run([{
            "kind": "prompt",
            "command": "npm run dev", "url": "http://localhost:5173", "tools": [],
        }])
        self.assertIn("no browser tool is reachable", prompt)
        for unreachable in ("playwright", "chrome-devtools", "puppeteer", "cypress", "selenium"):
            self.assertNotIn(unreachable, prompt)


class TestDecideRoundRenderOverride(RenderPolicyTestCase):
    """T003 AC2/AC3: the render outcome is folded into `decideRound` -- the
    same pure function the red-gate override already goes through -- so the
    override is executed by tests, not asserted by comment."""

    def _approved_review(self):
        return {"verdict": "APPROVED", "approved": True, "gateGreen": True, "findings": []}

    def test_render_failed_overrides_approved_to_fix(self):
        [out] = self._run([{
            "kind": "round",
            "review": self._approved_review(), "round": 1, "maxRounds": 5,
            "render": {"status": "failed", "reason": "HTTP 500"},
        }])
        self.assertEqual(out["decision"], "fix")
        self.assertIn("render", out["reason"])
        self.assertFalse(out.get("renderUnverified"))

    def test_render_failed_at_round_cap_rejects_not_approves(self):
        # Symmetric to the red-gate override never dispatching an unreviewable
        # fix round past the cap.
        [out] = self._run([{
            "kind": "round",
            "review": self._approved_review(), "round": 5, "maxRounds": 5,
            "render": {"status": "failed", "reason": "HTTP 500"},
        }])
        self.assertEqual(out["decision"], "reject")

    def test_render_passed_does_not_block_approval(self):
        [out] = self._run([{
            "kind": "round",
            "review": self._approved_review(), "round": 1, "maxRounds": 5,
            "render": {"status": "passed", "reason": ""},
        }])
        self.assertEqual(out["decision"], "approve")
        self.assertFalse(out.get("renderUnverified"))

    def test_render_unverified_does_not_block_approval_but_is_carried(self):
        # D4: 'we could not look' must never override approval, but must
        # survive on the returned decision so it can reach the operator.
        [out] = self._run([{
            "kind": "round",
            "review": self._approved_review(), "round": 1, "maxRounds": 5,
            "render": {"status": "rendered-unverified", "reason": "no browser tool reachable"},
        }])
        self.assertEqual(out["decision"], "approve")
        self.assertTrue(out.get("renderUnverified"))

    def test_non_rendered_mode_never_blocks_approval(self):
        # No `render` argument at all -- exactly what a non-'rendered' mode
        # passes (renderEvidence stays null in loop.js).
        [out] = self._run([{
            "kind": "round",
            "review": self._approved_review(), "round": 1, "maxRounds": 5,
        }])
        self.assertEqual(out["decision"], "approve")
        self.assertFalse(out.get("renderUnverified"))


if __name__ == "__main__":
    unittest.main()
