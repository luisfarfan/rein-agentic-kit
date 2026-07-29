"""Tests for the render policy in plugins/rein/workflows/loop.js (T002).

Same discipline as test_loop_policy.py: `decideRenderDispatch`,
`decideRenderOutcome` and `buildRenderPrompt` are extracted straight out of
loop.js's source by regex and run with `new Function`, so this proves the
actual shipped logic and prompt text, not a reimplementation that could
silently drift from it.
"""

import json
import os
import re
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
const decideRenderDispatch = new Function(
  'verifyPolicy', 'serve',
  extract('decideRenderDispatch', 'verifyPolicy, serve')
);
const decideRenderOutcome = new Function('render', extract('decideRenderOutcome', 'render'));
const buildRenderPrompt = new Function(
  'rein', 'command', 'url', 'tools', 'wd',
  extract('buildRenderPrompt', 'rein, command, url, tools, wd')
);
const decideRound = new Function(
  'review', 'round', 'maxRounds', 'render',
  extract('decideRound', 'review, round, maxRounds, render')
);
const buildFixFindings = new Function('review', 'decision', extract('buildFixFindings', 'review, decision'));
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => {
  if (s.kind === 'dispatch') return decideRenderDispatch(s.verifyPolicy, s.serve);
  if (s.kind === 'outcome') return decideRenderOutcome(s.render);
  if (s.kind === 'prompt') return buildRenderPrompt(s.rein, s.command, s.url, s.tools, s.wd);
  if (s.kind === 'round') return decideRound(s.review, s.round, s.maxRounds, s.render);
  if (s.kind === 'fixfindings') return buildFixFindings(s.review, s.decision);
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
        self.assertIn("function decideRenderDispatch(verifyPolicy, serve)", src)
        self.assertIn("function decideRenderOutcome(render)", src)
        self.assertIn("function buildRenderPrompt(rein, command, url, tools, wd)", src)


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

    _SERVE = {"command": "npm run dev", "url": "http://localhost:5173"}

    def test_empty_tools_is_unverified_not_dispatched(self):
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": []},
            "serve": self._SERVE,
        }])
        self.assertFalse(out["dispatch"])
        self.assertTrue(out["unverified"])
        self.assertTrue(out["reason"])

    def test_non_rendered_mode_never_dispatches(self):
        for mode in ("unit", "plan-only", ""):
            [out] = self._run([{
                "kind": "dispatch",
                "verifyPolicy": {"mode": mode, "requires": [], "forbids": [], "tools": ["playwright"]},
                "serve": self._SERVE,
            }])
            self.assertFalse(out["dispatch"], mode)
            self.assertFalse(out["unverified"], mode)

    def test_rendered_mode_with_a_reachable_tool_dispatches(self):
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": ["playwright"]},
            "serve": self._SERVE,
        }])
        self.assertTrue(out["dispatch"])
        self.assertFalse(out["unverified"])

    def test_empty_serve_command_is_unverified_not_dispatched(self):
        # Finding 4: SERVE defaults to {command:'', url:''} in loop.js -- a
        # REACHABLE state (detect.py ships an empty serve command rather than
        # an absent one; flow.config.json can set mode:'rendered' with no
        # `_serve()` at all). An empty command must degrade to
        # 'rendered-unverified', never dispatch into a guaranteed
        # `--command "" --url` usage failure.
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": ["playwright"]},
            "serve": {"command": "", "url": "http://localhost:5173"},
        }])
        self.assertFalse(out["dispatch"])
        self.assertTrue(out["unverified"])
        self.assertTrue(out["reason"])

    def test_empty_serve_url_is_unverified_not_dispatched(self):
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": ["playwright"]},
            "serve": {"command": "npm run dev", "url": ""},
        }])
        self.assertFalse(out["dispatch"])
        self.assertTrue(out["unverified"])
        self.assertTrue(out["reason"])

    def test_missing_serve_argument_is_unverified_not_dispatched(self):
        # Mirrors loop.js's own default (`ctx.serve || {command:'', url:''}`)
        # -- an absent `serve` must behave exactly like an empty one, not throw.
        [out] = self._run([{
            "kind": "dispatch",
            "verifyPolicy": {"mode": "rendered", "requires": [], "forbids": [], "tools": ["playwright"]},
        }])
        self.assertFalse(out["dispatch"])
        self.assertTrue(out["unverified"])


class TestBuildRenderPrompt(RenderPolicyTestCase):
    _REIN = "rein"
    _WD = "/work/rein-wt-change"

    def _scenario(self, **overrides):
        base = {
            "kind": "prompt",
            "rein": self._REIN, "command": "npm run dev", "url": "http://localhost:5173",
            "tools": ["playwright"], "wd": self._WD,
        }
        base.update(overrides)
        return base

    def test_prompt_names_command_url_and_only_reachable_tools(self):
        [prompt] = self._run([self._scenario()])
        self.assertIn("npm run dev", prompt)
        self.assertIn("http://localhost:5173", prompt)
        self.assertIn("playwright", prompt)
        # Never names a tool the project cannot reach — no other tool literal invented.
        for unreachable in ("chrome-devtools", "puppeteer", "cypress", "selenium"):
            self.assertNotIn(unreachable, prompt)

    def test_prompt_instructs_serve_probe_for_startup_and_teardown(self):
        [prompt] = self._run([self._scenario()])
        self.assertIn("rein serve-probe", prompt)
        self.assertNotIn(" & ", prompt)
        self.assertIn("do NOT", prompt)

    def test_multiword_command_survives_shell_quoting(self):
        # Finding 1: the old template nested single quotes around a
        # single-quoted command example -- `--command '${command}'` inside an
        # outer `'...'` -- which closes the outer quote early for any
        # multi-word command. `npm run dev` rendered as
        # `--command 'npm run dev'` (i.e. the ' after --command closes
        # instantly), leaving `run dev` as stray positional args when an
        # agent pastes it into bash. The command's value must survive intact
        # -- proven here by the exact double-quoted substring a shell would
        # actually parse as one argument.
        [prompt] = self._run([self._scenario()])
        self.assertIn('--command "npm run dev"', prompt)

    def test_prompt_starts_and_stops_the_same_server_via_pidfile(self):
        # Finding 2: the render needs the server held up WHILE a browser tool
        # navigates, which the single-shot serve-probe form (start, poll, tear
        # down, return) cannot provide -- and the reachable tools
        # (claude-in-chrome, browser-testing, ...) only navigate, they cannot
        # start a dev server themselves. The prompt must route the render
        # through `--start`/`--stop`, both keyed to the SAME pidfile, so one
        # deterministic CLI still owns the whole lifecycle (D2) across the
        # two calls a render actually needs.
        [prompt] = self._run([self._scenario()])
        self.assertIn("--start --pidfile", prompt)
        self.assertIn("--stop --pidfile", prompt)
        start_pidfile = re.search(r"--start --pidfile (\S+)", prompt).group(1)
        stop_pidfile = re.search(r"--stop --pidfile (\S+)", prompt).group(1)
        self.assertEqual(start_pidfile, stop_pidfile)
        # Never tells the render agent that a navigation-only browser tool
        # owns the server's start/stop -- that was the self-contradiction
        # between step 1 and step 2 of the old prompt.
        self.assertNotIn("let the tool own", prompt)

    def test_prompt_requires_the_full_evidence_shape(self):
        [prompt] = self._run([self._scenario()])
        for field in ("rendered", "httpStatus", "title", "consoleErrors", "evidence", "notes"):
            self.assertIn(field, prompt)

    def test_prompt_with_empty_tools_invents_nothing(self):
        [prompt] = self._run([self._scenario(tools=[])])
        self.assertIn("no browser tool is reachable", prompt)
        for unreachable in ("playwright", "chrome-devtools", "puppeteer", "cypress", "selenium"):
            self.assertNotIn(unreachable, prompt)

    def test_prompt_uses_the_passed_rein_binary_not_a_bare_rein(self):
        # Finding 2: the loop never assumes `rein` is on PATH elsewhere (line
        # 336: `const REIN = ctx.reinPath || 'rein'`) precisely because it
        # might not be, or might resolve to a stale installed plugin copy
        # with no `serve-probe` subcommand at all. The render prompt must
        # name the SAME resolved binary, not a hardcoded literal 'rein'.
        rein_path = "/Users/x/.claude/plugins/cache/rein-kit/rein/1.2.3/bin/rein"
        [prompt] = self._run([self._scenario(rein=rein_path)])
        self.assertIn(f"{rein_path} serve-probe --command", prompt)
        self.assertIn(f"{rein_path} serve-probe --stop", prompt)
        # A bare, un-interpolated invocation would show up as a quote
        # immediately followed by the literal word 'rein' -- the old
        # template's `'rein serve-probe ...'`. With a real path substituted
        # that exact substring cannot appear.
        self.assertNotIn("'rein serve-probe", prompt)

    def test_prompt_names_the_working_directory_on_both_invocations(self):
        # Finding 3: in WORKTREE_MODE the change under review lives in a
        # SIBLING directory of the loop's own cwd. The prompt must say so
        # explicitly, put an explicit --cwd on the --start call, and use an
        # ABSOLUTE pidfile on both calls -- a relative one would be written
        # in one bash round-trip's cwd and read in another's.
        [prompt] = self._run([self._scenario()])
        self.assertIn(self._WD, prompt)
        self.assertIn(f"--cwd {self._WD}", prompt)
        start_pidfile = re.search(r"--start --pidfile (\S+)", prompt).group(1)
        stop_pidfile = re.search(r"--stop --pidfile (\S+)", prompt).group(1)
        self.assertEqual(start_pidfile, stop_pidfile)
        self.assertTrue(start_pidfile.startswith(f"{self._WD}/"), start_pidfile)


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


class TestBuildFixFindingsRenderFailure(RenderPolicyTestCase):
    """Finding 4: a failed render must reach the fix agent even when the
    MECHANICAL gate is green and the reviewer already returned findings of
    its own -- the exact case `buildFixFindings` used to drop it in, since it
    only ever looked at `review.gateGreen` (the mechanical gate) and
    `base.length === 0`, never at the render outcome `decideRound` already
    computed.
    """

    def _green_gate_review_with_findings(self):
        return {
            "verdict": "CHANGES_REQUESTED",
            "approved": False,
            "gateGreen": True,
            "findings": [{"severity": "BLOCKING", "text": "unrelated reviewer finding"}],
        }

    def test_render_failed_is_prepended_even_with_a_green_gate_and_findings(self):
        review = self._green_gate_review_with_findings()
        [decision] = self._run([{
            "kind": "round",
            "review": review, "round": 1, "maxRounds": 5,
            "render": {"status": "failed", "reason": "HTTP 500"},
        }])
        self.assertTrue(decision["renderFailed"])
        [fix_findings] = self._run([{
            "kind": "fixfindings",
            "review": review, "decision": decision,
        }])
        self.assertGreater(len(fix_findings), len(review["findings"]))
        self.assertEqual(fix_findings[0]["severity"], "BLOCKING")
        self.assertIn("render", fix_findings[0]["text"])
        # the reviewer's own finding must still travel too, not be replaced
        self.assertTrue(any(f["text"] == "unrelated reviewer finding" for f in fix_findings))

    def test_render_passed_does_not_synthesize_a_finding_when_gate_green_and_findings_present(self):
        review = self._green_gate_review_with_findings()
        [decision] = self._run([{
            "kind": "round",
            "review": review, "round": 1, "maxRounds": 5,
            "render": {"status": "passed", "reason": ""},
        }])
        self.assertFalse(decision.get("renderFailed"))
        [fix_findings] = self._run([{
            "kind": "fixfindings",
            "review": review, "decision": decision,
        }])
        self.assertEqual(len(fix_findings), len(review["findings"]))


class TestRenderEvidenceIsFreshPerRound(RenderPolicyTestCase):
    """Finding 1: `renderEvidence` was computed exactly ONCE, in the Verify
    phase before the review round loop, and handed unchanged to `decideRound`
    on EVERY round -- so a round-1 render failure could never be approved,
    however thoroughly a later round's fix agent actually repaired it. Two
    checks: `decideRound` really does flip its answer when fed round 2's
    fresh (passed) evidence instead of round 1's stale (failed) one, and the
    loop's SOURCE actually re-runs the render step inside the round loop
    (after a fix round commits) rather than only once before it starts --
    the second check is what proves the fix is wired in, since `decideRound`
    itself was already stateless and would have passed the first check even
    before the loop.js fix.
    """

    def _approved_review(self):
        return {"verdict": "APPROVED", "approved": True, "gateGreen": True, "findings": []}

    def test_round_1_render_failure_does_not_doom_round_2_once_repaired(self):
        review = self._approved_review()
        [round1] = self._run([{
            "kind": "round",
            "review": review, "round": 1, "maxRounds": 3,
            "render": {"status": "failed", "reason": "HTTP 500"},
        }])
        self.assertEqual(round1["decision"], "fix")
        # Round 2 is fed a FRESH render outcome (the fix agent repaired it) --
        # not round 1's stale 'failed' object. This is exactly what loop.js
        # must do by re-running the render step before round 2's decideRound
        # call; see the source-level check below for proof it actually does.
        [round2] = self._run([{
            "kind": "round",
            "review": review, "round": 2, "maxRounds": 3,
            "render": {"status": "passed", "reason": ""},
        }])
        self.assertEqual(round2["decision"], "approve")
        self.assertFalse(round2.get("renderUnverified"))

    def test_loop_recomputes_render_evidence_inside_the_review_round_loop(self):
        # Structural: `runRender()` (the extracted dispatch+agent+outcome
        # step) must be invoked again after a fix round commits, not only
        # once in the Verify phase before the review loop starts -- otherwise
        # round 2's decideRound call is fed the exact same object round 1 saw
        # regardless of what the fix agent actually did.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("async function runRender()", src)
        calls = src.count("renderEvidence = await runRender()")
        self.assertGreaterEqual(
            calls, 2,
            "runRender() must be called again inside the review round loop "
            "(after a fix round), not just once before it, or a round-1 "
            "render failure outlives its own round",
        )
        # And that second call must live strictly AFTER the fix agent's own
        # dispatch (`label: \`fix#${round}\``) and BEFORE the loop's own
        # `round++` -- i.e. inside the round loop body, not beside it.
        fix_idx = src.index("label: `fix#${round}`")
        round_incr_idx = src.index("round++")
        second_call_idx = src.rindex("renderEvidence = await runRender()")
        self.assertGreater(second_call_idx, fix_idx)
        self.assertLess(second_call_idx, round_incr_idx)


if __name__ == "__main__":
    unittest.main()
