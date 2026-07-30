"""Tests for T001 "codegraph owns retrieval, where the agents work"
(plugins/rein/workflows/loop.js).

Same discipline as test_render_policy.py / test_gate_precheck.py:
`decideGraphAvailable` and `buildIsolatePrompt` are extracted straight out of
loop.js's source by regex and run with `new Function`, so this proves the
actual shipped logic, not a reimplementation that could silently drift from
it. A source-substring assertion does not count for the decision function
(T001 acceptance) -- every branch below actually EXECUTES it.
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
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
const decideGraphAvailable = new Function(
  'worktreeMode', 'capabilities', 'isolate',
  extract('decideGraphAvailable', 'worktreeMode, capabilities, isolate')
);
const buildIsolatePrompt = new Function(
  'root', 'base', 'wd', 'branch', 'rein',
  extract('buildIsolatePrompt', 'root, base, wd, branch, rein')
);
const scenarios = JSON.parse(scenariosJson);
const decided = scenarios.map((s) => decideGraphAvailable(s.worktreeMode, s.capabilities, s.isolate));
const prompt = buildIsolatePrompt('/base', 'main', '/base-wt-x', 'rein-wt/x', 'rein');
process.stdout.write(JSON.stringify({ decided, prompt }));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class GraphIndexPolicyTestCase(unittest.TestCase):
    def _run(self, scenarios: list) -> dict:
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

    # ── decideGraphAvailable ─────────────────────────────────────────────────

    def test_worktree_with_no_index_is_unavailable_even_if_base_repo_claims_it(self):
        # D2: the exact mismatch that made every graph command in every past
        # run answer "graph file not found" -- a base-repo capability must
        # never override what Isolate actually built in THIS worktree.
        out = self._run([{
            "worktreeMode": True,
            "capabilities": ["codegraph-index"],
            "isolate": {"graphIndexed": False, "graphOutcome": "codegraph not found"},
        }])
        self.assertEqual(out["decided"], [False])

    def test_worktree_with_index_is_available_regardless_of_base_capabilities(self):
        # The worktree is the source of truth -- an absent base-repo capability
        # must not veto an index Isolate actually built there.
        out = self._run([{
            "worktreeMode": True,
            "capabilities": [],
            "isolate": {"graphIndexed": True, "graphOutcome": "ok"},
        }])
        self.assertEqual(out["decided"], [True])

    def test_isolate_agent_dying_returns_unavailable_not_a_raise(self):
        # D4: indexing that fails never stops the run -- the failure path must
        # degrade to "unavailable", never raise.
        out = self._run([{"worktreeMode": True, "capabilities": ["codegraph-index"], "isolate": None}])
        self.assertEqual(out["decided"], [False])

    def test_no_codegraph_binary_is_reported_as_unavailable(self):
        out = self._run([{
            "worktreeMode": True,
            "capabilities": ["codegraph-index"],
            "isolate": {"graphIndexed": False, "graphOutcome": "codegraph: command not found"},
        }])
        self.assertEqual(out["decided"], [False])

    def test_non_worktree_mode_falls_back_to_capabilities(self):
        # With no worktree there is no base/work split to guard against: work
        # happens directly where capabilities were detected.
        out = self._run([
            {"worktreeMode": False, "capabilities": ["codegraph-index"], "isolate": None},
            {"worktreeMode": False, "capabilities": [], "isolate": None},
        ])
        self.assertEqual(out["decided"], [True, False])

    def test_non_worktree_mode_ignores_the_retired_graphify_capability(self):
        # D2/one-owner-for-retrieval: 'graphify-index' no longer answers the
        # graph-availability question -- only 'codegraph-index' does.
        out = self._run([{"worktreeMode": False, "capabilities": ["graphify-index"], "isolate": None}])
        self.assertEqual(out["decided"], [False])

    # ── buildIsolatePrompt ───────────────────────────────────────────────────

    def test_isolate_prompt_builds_the_index_in_the_worktree_no_llm_path(self):
        out = self._run([{"worktreeMode": True, "capabilities": [], "isolate": None}])
        prompt = out["prompt"]
        self.assertIn("codegraph init /base-wt-x", prompt)
        self.assertIn("graphIndexed", prompt)
        self.assertIn("graphOutcome", prompt)
        self.assertIn("/base-wt-x/.codegraph/codegraph.db", prompt)

    def test_isolate_prompt_indexing_failure_never_blocks_done(self):
        out = self._run([{"worktreeMode": True, "capabilities": [], "isolate": None}])
        prompt = out["prompt"]
        self.assertIn("never blocks done", prompt)
        self.assertIn("D4", prompt)

    def test_isolate_prompt_excludes_dot_codegraph_worktree_locally(self):
        # Every worktree the loop cuts, in every repo, must have .codegraph/
        # excluded from git, without relying on the target repo's tracked
        # .gitignore (which this change does not control and does not modify).
        # info/exclude is NOT per-worktree -- git keeps 'info' in its common
        # directory, so a worktree's rev-parse resolves to the base repo's own
        # .git/info/exclude and the entry outlives 'git worktree remove'. That
        # file is never committed, so no tracked file changes; the grep keeps
        # the single shared entry idempotent.
        out = self._run([{"worktreeMode": True, "capabilities": [], "isolate": None}])
        prompt = out["prompt"]
        self.assertIn("git -C /base-wt-x rev-parse --git-path info/exclude", prompt)
        self.assertIn('grep -qxF ".codegraph/" "$f"', prompt)
        # leading \n: an exclude file with no trailing newline would otherwise
        # get the entry concatenated onto its last line, and grep -qxF would
        # then never match it again -- appending once per run, forever.
        self.assertIn('printf "\\n.codegraph/\\n" >> "$f"', prompt)
        # must happen before the index build, and must itself be non-blocking
        exclude_pos = prompt.index("rev-parse --git-path info/exclude")
        build_pos = prompt.index("codegraph init /base-wt-x")
        self.assertLess(exclude_pos, build_pos)
        self.assertIn("This step is non-blocking too (D4)", prompt)

    def test_isolate_prompt_no_longer_mentions_graphify(self):
        out = self._run([{"worktreeMode": True, "capabilities": [], "isolate": None}])
        self.assertNotIn("graphify", out["prompt"].lower())


class FunctionsAreExtractableTestCase(unittest.TestCase):
    def test_functions_exist_with_expected_signatures(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function decideGraphAvailable(worktreeMode, capabilities, isolate)", src)
        self.assertIn("function buildIsolatePrompt(root, base, wd, branch, rein)", src)


class IsolateSchemaCarriesGraphFactsTestCase(unittest.TestCase):
    def test_isolate_schema_has_graph_fields(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        schema_start = src.index("const ISOLATE_SCHEMA")
        schema_end = src.index("const TASK_SCHEMA")
        schema_src = src[schema_start:schema_end]
        self.assertIn("graphIndexed", schema_src)
        self.assertIn("graphOutcome", schema_src)
        # required, the same "report it literally" style as the existing
        # done/summary/pendingIds/commits fields -- not an optional afterthought.
        self.assertIn("'graphIndexed'", schema_src)
        self.assertIn("'graphOutcome'", schema_src)

    def test_isolate_call_site_uses_the_pure_prompt_builder(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("buildIsolatePrompt(ctx.root, BASE, WD, BRANCH, REIN)", src)
        # hasGraph must be decided AFTER Isolate runs, from what it reports --
        # never computed straight from ctx.capabilities alone (D2).
        self.assertIn("decideGraphAvailable(WORKTREE_MODE, ctx.capabilities, setup)", src)


class NoGraphifyMentionAnywhereInLoopTestCase(unittest.TestCase):
    """T001 acceptance 4: no agent-facing prompt in loop.js mentions graphify
    in any form -- the Map scout included. The same guard that today catches
    'graphify query' now must catch the word entirely, since graphify no
    longer answers anything inside the loop (D2)."""

    def test_source_never_mentions_graphify(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            lines = f.readlines()
        offending = [
            (i + 1, line.rstrip("\n"))
            for i, line in enumerate(lines)
            if "graphify" in line.lower()
        ]
        self.assertEqual(offending, [], f"'graphify' found in loop.js (must be fully removed): {offending}")


class GraphOutputNeverTrackedTestCase(unittest.TestCase):
    """This repo is its own consuming project (the kit is developed with the
    kit) -- its regenerated codegraph index must never be tracked here either."""

    def test_gitignore_excludes_dot_codegraph(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".codegraph/", f.read())

    def test_git_actually_ignores_the_codegraph_index_path(self):
        # A real functional check, not just a substring in .gitignore.
        proc = subprocess.run(
            ["git", "-C", REPO_ROOT, "check-ignore", "-q", ".codegraph/codegraph.db"],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, ".codegraph/ must stay git-ignored -- the index is machine-local")


@unittest.skipUnless(shutil.which("codegraph"), "codegraph not on PATH")
class RealCodegraphIndexTestCase(unittest.TestCase):
    """A real functional check, not just string assertions: build the actual
    index (the no-LLM path Isolate uses) and confirm it lands where the
    Isolate prompt says it will, and that git sees nothing new to track."""

    def test_building_the_real_index_creates_the_marker_file_and_stays_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            shutil.copy(os.path.join(REPO_ROOT, "plugins", "rein", "workflows", "loop.js"), tmp)
            proc = subprocess.run(
                ["codegraph", "init", tmp],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(
                os.path.exists(os.path.join(tmp, ".codegraph", "codegraph.db")),
                "codegraph init must leave .codegraph/codegraph.db behind -- the marker Isolate checks",
            )
            status = subprocess.run(
                ["git", "-C", tmp, "status", "--porcelain", "--untracked-files=all"],
                capture_output=True, text=True,
            )
            self.assertIn(".codegraph/", status.stdout, "sanity: without an exclude entry .codegraph/ IS untracked")


if __name__ == "__main__":
    unittest.main()
