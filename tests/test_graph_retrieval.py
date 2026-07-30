"""Tests for T001 "codegraph owns retrieval, where the agents work"
(plugins/rein/workflows/loop.js) -- the RETRIEVAL block and the Map scout.

Same discipline as test_graph_index.py: `buildRetrievalBlock` and
`buildScoutPrompt` are extracted straight out of loop.js's source by regex and
run with `new Function`, so this proves the actual shipped prompt text, not a
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
const [, , loopPath, casesJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const buildRetrievalBlock = new Function(
  'hasSerena', 'hasGraph',
  extract('buildRetrievalBlock', 'hasSerena, hasGraph')
);
const buildScoutPrompt = new Function(
  'wd', 'planPath', 'artifactList', 'taskIds',
  extract('buildScoutPrompt', 'wd, planPath, artifactList, taskIds')
);
const cases = JSON.parse(casesJson);
const retrieval = cases.retrieval.map((c) => buildRetrievalBlock(c.hasSerena, c.hasGraph));
const scout = buildScoutPrompt('/worktree-path', '/root/tasks.md', '', ['T001', 'T002']);
process.stdout.write(JSON.stringify({ retrieval, scout }));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class GraphRetrievalPromptTestCase(unittest.TestCase):
    def _run(self) -> dict:
        cases = {
            "retrieval": [
                {"hasSerena": False, "hasGraph": False},
                {"hasSerena": True, "hasGraph": False},
                {"hasSerena": False, "hasGraph": True},
                {"hasSerena": True, "hasGraph": True},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_AND_RUN_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, json.dumps(cases)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)

    # index order matches the `cases["retrieval"]` list above
    NONE, SERENA_ONLY, GRAPH_ONLY, BOTH = range(4)

    # ── acceptance 6: pure function, executed for all four combinations,
    # no-tool branch byte-identical to before codegraph existed ─────────────

    def test_no_tool_case_is_the_original_bounded_search_text(self):
        out = self._run()
        block = out["retrieval"][self.NONE]
        self.assertEqual(
            block,
            "RETRIEVAL — do not burn context. The real cost is cache_read: every turn re-reads everything "
            "accumulated so far, so cost ≈ context-size × turns.\n"
            "  · Locate with bounded search (grep with a concrete path and pattern) before opening files.\n"
            "  · Read ONLY the symbols/regions you will touch (Read with offset/limit), NEVER whole files "
            "\"just in case\" — every large file you pull in is RE-READ on every later turn.\n"
            "  · Keep command output small: filter and scope it (head, -q, concrete paths) instead of "
            "dumping everything.\n"
            "  · Aim to finish in FEW turns with precise reads, not to explore incrementally.",
        )
        self.assertNotIn("serena", block)
        self.assertNotIn("graphify", block)
        self.assertNotIn("codegraph", block)

    def test_all_four_combinations_run_without_raising(self):
        out = self._run()
        self.assertEqual(len(out["retrieval"]), 4)
        for block in out["retrieval"]:
            self.assertIsInstance(block, str)
            self.assertIn("RETRIEVAL", block)

    # ── acceptance 3: with the graph on, teach query/callers/callees/node/
    # impact, each with a one-line statement of what it returns, never explore

    def test_graph_case_teaches_the_five_codegraph_commands_with_one_liners(self):
        out = self._run()
        block = out["retrieval"][self.GRAPH_ONLY]
        self.assertIn('codegraph query "<concept>"', block)
        self.assertIn("symbols matching a concept, with file:line", block)
        self.assertIn("codegraph callers <symbol>", block)
        self.assertIn("codegraph callees <symbol>", block)
        self.assertIn("codegraph node <symbol>", block)
        self.assertIn("instead of reading the file", block)
        self.assertIn("codegraph impact <symbol>", block)
        self.assertIn("before an edit", block)

    def test_graph_case_never_teaches_explore(self):
        # D4: 'explore' is a whole-file Read in disguise (3,701 tokens on this
        # repo) -- the block's entire point is bounded orientation.
        out = self._run()
        for block in out["retrieval"]:
            self.assertNotIn("codegraph explore", block)

    def test_graph_case_never_mentions_graphify(self):
        out = self._run()
        for block in out["retrieval"]:
            self.assertNotIn("graphify", block)

    # ── acceptance 4 (D2): serena no longer teaches retrieval codegraph owns

    def test_serena_case_drops_retrieval_teaching_keeps_diagnostics_and_edits(self):
        out = self._run()
        block = out["retrieval"][self.SERENA_ONLY]
        self.assertIn("serena get_diagnostics_for_file", block)
        self.assertIn("type errors", block)
        # symbol-level EDIT operations remain taught
        self.assertIn("replace_symbol_body", block)
        self.assertIn("rename_symbol", block)
        # retrieval that codegraph now owns must be GONE from serena's part
        self.assertNotIn("find_referencing_symbols", block)
        self.assertNotIn("get_symbols_overview", block)
        self.assertNotIn("find_symbol <name>", block)
        self.assertNotIn("graphify", block)
        # serena no longer teaches locating code (D2) -- with the graph off,
        # the bounded-search fallback must still be there so the agent has
        # SOME way to find code, not zero.
        self.assertIn("Locate with bounded search", block)

    def test_find_referencing_symbols_is_never_taught_as_the_way_to_find_callers(self):
        # D2: one owner per question -- codegraph answers "who calls this" now.
        out = self._run()
        for block in out["retrieval"]:
            self.assertNotIn("find_referencing_symbols", block)

    def test_graph_and_serena_both_on_keeps_both_narrowed_teachings(self):
        out = self._run()
        block = out["retrieval"][self.BOTH]
        self.assertIn("codegraph query", block)
        self.assertIn("serena get_diagnostics_for_file", block)
        self.assertIn("replace_symbol_body", block)
        self.assertNotIn("find_referencing_symbols", block)
        self.assertNotIn("codegraph explore", block)
        self.assertNotIn("graphify", block)
        # no bounded-search fallback line once either tool is present
        self.assertNotIn("Locate with bounded search", block)

    # ── the Map scout's prompt, same fix, same test ─────────────────────────

    def test_scout_prompt_teaches_query_and_node_not_explore_or_graphify(self):
        out = self._run()
        scout = out["scout"]
        self.assertIn('codegraph query "<concept>"', scout)
        self.assertIn("symbols matching a concept, with file:line", scout)
        self.assertIn("codegraph node <symbol>", scout)
        self.assertIn("instead of reading the file", scout)
        self.assertNotIn("codegraph explore", scout)
        self.assertNotIn("graphify", scout)

    def test_scout_prompt_carries_its_inputs(self):
        out = self._run()
        scout = out["scout"]
        # the scout must open with the WORKTREE it was handed, not any other
        # directory (e.g. the base repo root) -- '/worktree-path' here is
        # deliberately distinct from '/root' (the plan path's directory) so
        # this assertion cannot pass by coincidence if the wrong directory
        # were threaded through instead.
        self.assertIn("You work in /worktree-path.", scout)
        self.assertIn("T001, T002", scout)
        self.assertIn("Read /root/tasks.md ONCE.", scout)

    def test_scout_call_site_passes_the_worktree_not_ctx_root(self):
        with open(LOOP_JS, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            "buildScoutPrompt(WD, ctx.planPath, artifactList, tasks.map((t) => t.id))",
            src,
            "the scout dispatch call site must pass WD (the worktree), not ctx.root (the base repo)",
        )
        self.assertNotIn(
            "buildScoutPrompt(ctx.root,",
            src,
            "the scout dispatch call site must not pass ctx.root -- that is the base repo, not where "
            "the worktree's graph index lives",
        )

    # ── acceptance 5: 'graphify' cannot silently regress anywhere in an
    # agent-facing prompt in loop.js -- every prompt string built from these
    # two functions (the RETRIEVAL block and the heaviest graph consumer, the
    # Map scout) is checked above; this asserts the shipped source itself
    # carries no other mention outside these two pure functions either.

    def test_source_has_no_mention_of_graphify_at_all(self):
        with open(LOOP_JS, "r", encoding="utf-8") as f:
            lines = f.readlines()
        offending = [
            (i + 1, line.rstrip("\n"))
            for i, line in enumerate(lines)
            if "graphify" in line.lower()
        ]
        self.assertEqual(
            offending, [],
            f"'graphify' found in loop.js (agent-facing prompt regression): {offending}",
        )


if __name__ == "__main__":
    unittest.main()
