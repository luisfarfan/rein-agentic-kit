"""Tests for the per-stack verification policy emitted by `detect.resolve()`.

stdlib unittest + temp project trees only, same discipline as test_detect.py:
no dependency on the operator's actual machine/home directory, so results are
reproducible for anyone who clones the project.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "lib"))

import detect  # noqa: E402


class Project:
    """Throwaway project tree, described as {relative path: contents}."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def __enter__(self) -> str:
        self.tmp = tempfile.TemporaryDirectory()
        for rel, body in self.files.items():
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        return self.tmp.name

    def __exit__(self, *exc):
        self.tmp.cleanup()


PKG_VITE = json.dumps(
    {"scripts": {"dev": "vite", "build": "vite build"}, "devDependencies": {"vite": "^5"}}
)


class TestRenderedMode(unittest.TestCase):
    def test_frontend_subtype_is_rendered(self):
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"], "rendered mode must require an observed render")
        self.assertIn("browser", " ".join(r["verifyPolicy"]["requires"]))

    def test_no_tools_named_when_none_reachable(self):
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["tools"], [])

    def test_only_reachable_tools_are_named(self):
        mcp_cfg = json.dumps({"mcpServers": {"claude-in-chrome": {"type": "stdio"}}})
        files = {
            "package.json": json.dumps(
                {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5", "@playwright/test": "^1"}}
            ),
            ".mcp.json": mcp_cfg,
            ".claude/skills/browser-testing-with-devtools/SKILL.md": "# browser testing",
        }
        with Project(files) as root:
            r = detect.resolve(root)
        tools = r["verifyPolicy"]["tools"]
        self.assertEqual(sorted(tools), ["browser-testing", "claude-in-chrome", "playwright"])

    def test_chrome_mcp_in_claude_settings_json_is_reachable(self):
        settings = json.dumps({"mcpServers": {"claude-in-chrome": {"type": "stdio"}}})
        files = {
            "package.json": PKG_VITE,
            ".claude/settings.json": settings,
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertIn("claude-in-chrome", r["verifyPolicy"]["tools"])

    def test_chrome_mcp_in_claude_settings_local_json_is_reachable(self):
        settings = json.dumps({"mcpServers": {"claude-in-chrome": {"type": "stdio"}}})
        files = {
            "package.json": PKG_VITE,
            ".claude/settings.local.json": settings,
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertIn("claude-in-chrome", r["verifyPolicy"]["tools"])

    def test_playwright_mentioned_only_in_comment_is_not_reachable(self):
        files = {
            "package.json": json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}}),
            "requirements.txt": "# consider adding playwright later\nrequests==2.31\n",
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertNotIn("playwright", r["verifyPolicy"]["tools"])

    def test_playwright_in_requirements_dependency_line_is_reachable(self):
        files = {
            "package.json": json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}}),
            "requirements.txt": "playwright==1.40\n",
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertIn("playwright", r["verifyPolicy"]["tools"])

    def test_absent_tools_never_named_individually(self):
        # Only playwright is reachable -- the other two must not appear.
        files = {
            "package.json": json.dumps(
                {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5", "playwright": "^1"}}
            ),
        }
        with Project(files) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["tools"], ["playwright"])


EXAMPLE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "flow.config.example.json"
)


class TestShippedExampleConfig(unittest.TestCase):
    """The example config that ships in the repo and that users copy verbatim
    must never silently disable Phase 2 for a frontend project. Regression
    test for the 'mode: auto' sentinel being treated as an explicit override.
    """

    def test_example_config_on_frontend_project_is_rendered(self):
        with open(EXAMPLE_CONFIG_PATH, encoding="utf-8") as f:
            example_cfg = f.read()
        with Project({"package.json": PKG_VITE, "flow.config.json": example_cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"])
        self.assertNotIn("warnings", r["verifyPolicy"])
        # The shipped example config must not itself be a source of complaints.
        # Warnings ABOUT THE ENVIRONMENT (no browser tool installed in this temp
        # tree) are expected and correct here, so assert on the config-validity
        # ones specifically rather than on the absence of any warning at all --
        # `assertNotIn("verifyWarnings", r)` would silently forbid every future
        # environment diagnostic.
        for warning in r.get("verifyWarnings", []):
            self.assertNotIn("verify.mode 'auto'", warning)
            self.assertNotIn("is not one of", warning)


class TestBadVerifyMode(unittest.TestCase):
    def test_unknown_mode_falls_back_to_detection_and_warns(self):
        cfg = json.dumps({"verify": {"mode": "yolo"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        policy = r["verifyPolicy"]
        # Falls back to detection (frontend -> rendered) instead of failing
        # open with an unknown mode loop.js's policy blocks don't recognize.
        self.assertEqual(policy["mode"], "rendered")
        # The warning must NOT land inside verifyPolicy: loop.js's
        # CONTEXT_SCHEMA declares that object with additionalProperties:
        # false and exactly {mode, requires, forbids, tools}, and the
        # Prepare agent copies config.verifyPolicy into ctx.verifyPolicy
        # literally. A fifth key there would make a legal CLI response
        # violate the workflow's own schema.
        self.assertEqual(set(policy.keys()), {"mode", "requires", "forbids", "tools"})
        self.assertIn("verifyWarnings", r)
        self.assertIn("yolo", r["verifyWarnings"][0])

    def test_empty_string_mode_is_treated_as_unset(self):
        cfg = json.dumps({"verify": {"mode": ""}})
        with Project({"pyproject.toml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertNotIn("warnings", r["verifyPolicy"])
        self.assertNotIn("verifyWarnings", r)


class TestPlanOnlyMode(unittest.TestCase):
    def test_infra_without_test_command_is_plan_only(self):
        pkg = json.dumps({"scripts": {"build": "webpack"}})
        with Project({"serverless.yml": "", "package.json": pkg}) as root:
            r = detect.resolve(root)
        self.assertIn("infra", r["subtypes"])
        self.assertNotIn("test", r["commands"])
        policy = r["verifyPolicy"]
        self.assertEqual(policy["mode"], "plan-only")
        for op in ("deploy", "apply", "destroy"):
            self.assertIn(op, policy["forbids"])

    def test_infra_with_test_command_is_not_plan_only(self):
        with Project({"serverless.yml": "", "pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        # python autodetect always configures a `test` command.
        self.assertNotEqual(r["verifyPolicy"]["mode"], "plan-only")


class TestUnitMode(unittest.TestCase):
    def test_library_project_is_unit_mode_unchanged(self):
        with Project({"pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        policy = r["verifyPolicy"]
        self.assertEqual(policy["mode"], "unit")
        self.assertEqual(policy["requires"], [])
        self.assertEqual(policy["forbids"], [])
        self.assertEqual(policy["tools"], [])

    def test_cli_go_project_is_unit_mode(self):
        with Project({"go.mod": "module x"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")


class TestConfigOverride(unittest.TestCase):
    def test_config_mode_wins_over_detection(self):
        cfg = json.dumps({"verify": {"mode": "rendered"}})
        with Project({"pyproject.toml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "rendered")
        self.assertTrue(r["verifyPolicy"]["requires"])

    def test_config_mode_overrides_frontend_detection(self):
        cfg = json.dumps({"verify": {"mode": "unit"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertIn("frontend", r["subtypes"])
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertEqual(r["verifyPolicy"]["requires"], [])

    def test_config_mode_overrides_infra_plan_only(self):
        cfg = json.dumps({"verify": {"mode": "unit"}})
        with Project({"serverless.yml": "", "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["mode"], "unit")
        self.assertEqual(r["verifyPolicy"]["forbids"], [])


class TestServeBlock(unittest.TestCase):
    def test_frontend_serve_prefers_dev_with_pm_prefix(self):
        pkg = json.dumps({"scripts": {"dev": "vite", "start": "vite preview"}, "devDependencies": {"vite": "^5"}})
        with Project({"package.json": pkg, "pnpm-lock.yaml": ""}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "pnpm dev")

    def test_frontend_serve_falls_back_to_start(self):
        pkg = json.dumps({"scripts": {"start": "next start"}, "dependencies": {"next": "^14"}})
        with Project({"package.json": pkg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "npm run start")

    def test_url_from_config_wins(self):
        cfg = json.dumps({"verify": {"url": "http://localhost:5173/app"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["url"], "http://localhost:5173/app")

    def test_url_parsed_from_script_port_flag(self):
        pkg = json.dumps({"scripts": {"dev": "vite --port 4321"}, "devDependencies": {"vite": "^5"}})
        with Project({"package.json": pkg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["url"], "http://localhost:4321")

    def test_url_falls_back_to_the_framework_default_then_3000(self):
        """Documented deviation from T004's literal "3000 as a last resort".

        Vite serves on 5173, not 3000, so the old assertion encoded a URL nothing
        listens on -- satisfying the criterion's letter while breaking its intent
        ("never invent one that would fail at run time"). 3000 remains the last
        resort; it is just no longer reached before the framework's own default.
        """
        with Project({"package.json": PKG_VITE}) as root:
            self.assertEqual(detect.resolve(root)["serve"]["url"], "http://localhost:5173")

        nx = json.dumps({"scripts": {"dev": "next dev"}, "dependencies": {"next": "^14"}})
        with Project({"package.json": nx}) as root:
            self.assertEqual(detect.resolve(root)["serve"]["url"], "http://localhost:3000")

        # Genuinely unknown framework -> 3000 is still the last resort.
        unknown = json.dumps({"scripts": {"dev": "serve"}, "dependencies": {"@remix-run/react": "^2"}})
        with Project({"package.json": unknown, "flow.config.json": '{"subtypes":["frontend"]}'}) as root:
            self.assertEqual(detect.resolve(root)["serve"]["url"], "http://localhost:3000")

    def test_explicit_port_still_wins_over_the_framework_default(self):
        pkg = json.dumps({"scripts": {"dev": "vite --port 4000"}, "devDependencies": {"vite": "^5"}})
        with Project({"package.json": pkg}) as root:
            self.assertEqual(detect.resolve(root)["serve"]["url"], "http://localhost:4000")

    def test_config_serve_without_url_warns_that_the_url_is_a_guess(self):
        """The case commands.serve exists for: no framework default to fall back on."""
        cfg = json.dumps({"commands": {"serve": "docker compose up"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "docker compose up")
        self.assertTrue(any("is a guess" in w for w in r.get("verifyWarnings", [])))

    def test_npm_script_without_a_port_flag_does_not_warn(self):
        """A warning on every ordinary frontend project is noise, not a signal."""
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertFalse(any("is a guess" in w for w in r.get("verifyWarnings", [])))

    def test_rendered_mode_with_no_reachable_tool_warns(self):
        """Otherwise the implementer is handed an instruction it cannot satisfy."""
        with Project({"package.json": PKG_VITE}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["verifyPolicy"]["tools"], [])
        self.assertTrue(any("no browser tool" in w for w in r.get("verifyWarnings", [])))

    def test_frontend_with_no_runnable_script_reports_empty_command(self):
        pkg = json.dumps({"scripts": {"build": "vite build"}, "devDependencies": {"vite": "^5"}})
        with Project({"package.json": pkg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "")
        self.assertIn("serve", r["missingCommands"])

    def test_non_frontend_project_has_no_serve_block(self):
        with Project({"pyproject.toml": ""}) as root:
            r = detect.resolve(root)
        self.assertNotIn("serve", r)

    def test_config_serve_command_wins_over_dev_script(self):
        # A frontend dev server that isn't an npm script (static site,
        # Django/Rails-served front end, docker compose) must be
        # configurable -- flow.config.json's commands.serve is the highest
        # precedence source, same rule as every other command slot.
        pkg = json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}})
        cfg = json.dumps({"commands": {"serve": "python3 -m http.server 8080"}})
        with Project({"package.json": pkg, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "python3 -m http.server 8080")
        self.assertNotIn("serve", r["missingCommands"])

    def test_config_serve_command_satisfies_static_site_with_no_package_json(self):
        cfg = json.dumps({"subtypes": ["frontend"], "commands": {"serve": "python3 -m http.server 8080"}})
        with Project({"flow.config.json": cfg, "index.html": "<html></html>"}) as root:
            r = detect.resolve(root)
        self.assertEqual(r["serve"]["command"], "python3 -m http.server 8080")
        self.assertNotIn("serve", r["missingCommands"])


class TestShape(unittest.TestCase):
    def test_shape_has_all_keys(self):
        with Project({"pyproject.toml": ""}) as root:
            policy = detect.resolve(root)["verifyPolicy"]
        self.assertEqual(set(policy.keys()), {"mode", "requires", "forbids", "tools"})
        self.assertIsInstance(policy["mode"], str)
        self.assertIsInstance(policy["requires"], list)
        self.assertIsInstance(policy["forbids"], list)
        self.assertIsInstance(policy["tools"], list)

    def test_shape_has_all_keys_on_bad_mode_path_too(self):
        # Regression: the bad-mode path used to add a fifth "warnings" key
        # directly onto the policy dict, which loop.js's CONTEXT_SCHEMA
        # (additionalProperties: false) declares illegal. The warning must
        # travel as a sibling ("verifyWarnings") of the resolve() result,
        # never inside verifyPolicy itself, regardless of mode validity.
        cfg = json.dumps({"verify": {"mode": "yolo"}})
        with Project({"package.json": PKG_VITE, "flow.config.json": cfg}) as root:
            r = detect.resolve(root)
        policy = r["verifyPolicy"]
        self.assertEqual(set(policy.keys()), {"mode", "requires", "forbids", "tools"})
        self.assertIn("verifyWarnings", r)


LOOP_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "rein", "workflows", "loop.js"
)

# Stack/framework/port literals this project's detect.py can report. If any of
# these shows up hard-coded in loop.js, the workflow stopped being portable —
# it should always be threaded through from `ctx` (as reported by Prepare),
# never assumed. "go" is deliberately excluded: it is also a common English
# word ("go test" isn't, but plain "go" appears in ordinary prose in loop.js).
_STACK_OR_FRAMEWORK_LITERALS = (
    "python", "rust", "golang", "cargo", "pytest", "rustc",
    "react", "vue", "angular", "svelte", "next.js", "nextjs", "astro", "nuxt",
    "django", "flask", "express", "typescript", "javascript",
    "npm ", "yarn ", "pnpm ",
)
_PORT_LITERALS = ("3000", "5173", "8080", "4000", "8000", "9000", "localhost")


class TestLoopScriptIsPortable(unittest.TestCase):
    def setUp(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            self.source = f.read()

    def test_no_hardcoded_stack_framework_or_port(self):
        # Strip comments first: a future comment merely mentioning e.g. pnpm
        # (as this file's own header comments do, in prose) is not a
        # portability loss and must not fail the suite. Anchored to line-leading
        # `//` so a "//" inside a string literal (an http:// URL) cannot swallow
        # the rest of that line and quietly weaken this guard -- it must fail
        # loudly, not open.
        code_only = re.sub(r"/\*.*?\*/", "", self.source, flags=re.DOTALL)
        code_only = re.sub(r"^\s*//.*$", "", code_only, flags=re.MULTILINE)
        lowered = code_only.lower()
        hits = [
            tok for tok in _STACK_OR_FRAMEWORK_LITERALS + _PORT_LITERALS
            if re.search(r"\b" + re.escape(tok.strip()) + r"\b", lowered)
        ]
        self.assertEqual(hits, [], f"loop.js hard-codes: {hits} — thread these through ctx/config instead")

    def test_context_schema_carries_verify_policy_and_serve(self):
        self.assertIn("verifyPolicy", self.source)
        self.assertIn("'serve'", self.source)

    def test_prompts_derive_policy_text_from_verify_policy_mode(self):
        # Both prompts key off VERIFY_POLICY.mode — never a literal stack/framework name.
        self.assertIn("implementerPolicyBlock()", self.source)
        self.assertIn("reviewerPolicyBlock()", self.source)
        self.assertIn("VERIFY_POLICY.mode === 'rendered'", self.source)
        self.assertIn("VERIFY_POLICY.mode === 'plan-only'", self.source)


_NODE = shutil.which("node")

# Extracts implementerPolicyBlock()/reviewerPolicyBlock()'s bodies straight out
# of loop.js's source (regex-bounded by the 0-indent closing brace) and runs
# them with VERIFY_POLICY/SERVE/ctx turned into ordinary Function parameters
# instead of the harness-injected globals loop.js normally reads them from.
# This is what makes the "byte-identical in unit mode" / "names the serve
# command in rendered mode" / "lists every forbidden op in plan-only mode"
# claims actually checked, not just asserted by comment.
_EXTRACT_AND_RUN_JS = r"""
const fs = require('fs');
const [, , loopPath, scenariosJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name) {
  const re = new RegExp(`function ${name}\\(\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const implFn = new Function('VERIFY_POLICY', 'SERVE', 'ctx', extract('implementerPolicyBlock'));
const revFn = new Function('VERIFY_POLICY', 'SERVE', extract('reviewerPolicyBlock'));
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => ({
  implementer: implFn(s.verifyPolicy, s.serve, { stack: s.stack }),
  reviewer: revFn(s.verifyPolicy, s.serve),
}));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestPolicyBlockRenderedContent(unittest.TestCase):
    """Runs the actual prompt-building functions instead of grepping loop.js
    source, so a regression to empty/wrong prompt bodies fails loudly.
    """

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

    def test_unit_mode_prompts_are_byte_identical_to_today(self):
        [result] = self._run([{
            "verifyPolicy": {"mode": "unit", "requires": [], "forbids": [], "tools": []},
            "serve": {"command": "", "url": ""},
            "stack": "python",
        }])
        self.assertEqual(result["implementer"], "")
        self.assertEqual(result["reviewer"], "")

    def test_rendered_mode_prompt_names_serve_command_url_and_tools(self):
        [result] = self._run([{
            "verifyPolicy": {"mode": "rendered", "requires": ["x"], "forbids": [], "tools": ["playwright"]},
            "serve": {"command": "npm run dev", "url": "http://localhost:5173"},
            "stack": "node",
        }])
        self.assertIn("npm run dev", result["implementer"])
        self.assertIn("http://localhost:5173", result["implementer"])
        self.assertIn("playwright", result["implementer"])
        self.assertIn("http://localhost:5173", result["reviewer"])

    def test_rendered_mode_with_no_serve_command_says_so_rather_than_inventing(self):
        [result] = self._run([{
            "verifyPolicy": {"mode": "rendered", "requires": ["x"], "forbids": [], "tools": []},
            "serve": {"command": "", "url": ""},
            "stack": "node",
        }])
        self.assertIn("no serve command is configured", result["implementer"])

    def test_plan_only_prompt_lists_every_forbidden_op(self):
        forbids = ["deploy", "apply", "destroy"]
        [result] = self._run([{
            "verifyPolicy": {"mode": "plan-only", "requires": [], "forbids": forbids, "tools": []},
            "serve": {"command": "", "url": ""},
            "stack": "terraform",
        }])
        for op in forbids:
            self.assertIn(op, result["implementer"])
            self.assertIn(op, result["reviewer"])


class TestPlanOnlyIsReachable(unittest.TestCase):
    """The plan-only guarantee must hold for the repo shapes that actually need it.

    These exist because the original coverage bolted a package.json onto
    serverless.yml — the one shape where plan-only was reachable — so the
    acceptance criterion passed while the guarantee did not hold for any real
    infra repo. A test that only exercises the path the bug avoids proves nothing.
    """

    def _assert_plan_only(self, root: str, why: str):
        policy = detect.resolve(root)["verifyPolicy"]
        self.assertEqual(policy["mode"], "plan-only", why)
        for op in detect.DESTRUCTIVE_OPS:
            self.assertIn(op, policy["forbids"], f"{op} must stay forbidden: {why}")

    def test_serverless_repo_with_no_language_manifest(self):
        """The archetypal infra repo: no pyproject, no package.json, just config."""
        with Project({"serverless.yml": "service: x\n"}) as root:
            self.assertIn("infra", detect.resolve(root)["subtypes"])
            self._assert_plan_only(root, "a serverless-only repo is infra")

    def test_terraform_repo_is_detected_by_extension(self):
        """DESTRUCTIVE_OPS names terraform's verbs; the repo must be visible too."""
        with Project({"main.tf": 'resource "aws_s3_bucket" "b" {}\n'}) as root:
            self.assertIn("infra", detect.resolve(root)["subtypes"])
            self._assert_plan_only(root, "a terraform repo is the canonical plan-only case")

    def test_empty_test_command_counts_as_not_set(self):
        """An empty string means "not set" everywhere else in this system."""
        cfg = json.dumps({"subtypes": ["infra"], "commands": {"test": ""}})
        with Project({"serverless.yml": "", "package.json": "{}", "flow.config.json": cfg}) as root:
            self._assert_plan_only(root, 'commands.test="" must not read as configured')

    def test_a_real_test_command_still_leaves_plan_only(self):
        """The escape hatch must keep working: a testable infra repo is not plan-only."""
        cfg = json.dumps({"commands": {"test": "pytest -q"}})
        with Project({"serverless.yml": "", "flow.config.json": cfg}) as root:
            self.assertEqual(detect.resolve(root)["verifyPolicy"]["mode"], "unit")

    def test_empty_command_is_reported_as_missing(self):
        cfg = json.dumps({"commands": {"test": "", "lint": "   "}})
        with Project({"pyproject.toml": "", "flow.config.json": cfg}) as root:
            missing = detect.resolve(root)["missingCommands"]
        self.assertIn("test", missing)
        self.assertIn("lint", missing)


if __name__ == "__main__":
    unittest.main()
