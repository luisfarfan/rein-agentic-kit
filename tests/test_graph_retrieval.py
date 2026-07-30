"""Tests for T002 "Teach the commands that answer" (plugins/rein/workflows/loop.js).

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
  'root', 'planPath', 'artifactList', 'taskIds',
  extract('buildScoutPrompt', 'root, planPath, artifactList, taskIds')
);
const cases = JSON.parse(casesJson);
const retrieval = cases.retrieval.map((c) => buildRetrievalBlock(c.hasSerena, c.hasGraph));
const scout = buildScoutPrompt('/root', '/root/tasks.md', '', ['T001', 'T002']);
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

    # ── acceptance 1: pure function, unchanged behaviour for no-tool/serena ──

    def test_no_tool_case_is_the_original_bounded_search_text(self):
        out = self._run()
        block = out["retrieval"][self.NONE]
        self.assertIn(
            "Locate with bounded search (grep with a concrete path and pattern) before opening files.",
            block,
        )
        self.assertNotIn("serena", block)
        self.assertNotIn("graphify", block)

    def test_serena_case_is_unchanged(self):
        out = self._run()
        block = out["retrieval"][self.SERENA_ONLY]
        self.assertIn("SYMBOL-LEVEL FIRST", block)
        self.assertIn("serena get_symbols_overview <file>", block)
        self.assertIn("serena find_symbol <name>", block)
        self.assertIn("serena find_referencing_symbols", block)
        self.assertIn("serena get_diagnostics_for_file", block)
        self.assertNotIn("graphify", block)
        # no bounded-search fallback line once serena is present
        self.assertNotIn("Locate with bounded search", block)

    def test_all_four_combinations_run_without_raising(self):
        out = self._run()
        self.assertEqual(len(out["retrieval"]), 4)
        for block in out["retrieval"]:
            self.assertIsInstance(block, str)
            self.assertIn("RETRIEVAL", block)

    # ── acceptance 2: graph teaches explain/path, never query ───────────────

    def test_graph_case_teaches_explain_and_path_with_one_line_each(self):
        out = self._run()
        block = out["retrieval"][self.GRAPH_ONLY]
        self.assertIn('graphify explain "<symbol>"', block)
        self.assertIn('graphify path "<A>" "<B>"', block)
        # one-line statement of what each returns
        self.assertIn("returns its definition plus its direct callers/callees", block)
        self.assertIn("returns the call/reference chain between two symbols", block)

    def test_graph_case_never_teaches_query(self):
        out = self._run()
        for block in out["retrieval"]:
            self.assertNotIn("graphify query", block)

    def test_graph_and_serena_both_on_keeps_both_teachings(self):
        out = self._run()
        block = out["retrieval"][self.BOTH]
        self.assertIn("serena get_symbols_overview <file>", block)
        self.assertIn('graphify explain "<symbol>"', block)
        self.assertNotIn("graphify query", block)
        # no bounded-search fallback line once either tool is present
        self.assertNotIn("Locate with bounded search", block)

    # ── acceptance 3: the Map scout's prompt, same fix, same test ───────────

    def test_scout_prompt_teaches_explain_and_path_not_query(self):
        out = self._run()
        scout = out["scout"]
        self.assertIn('graphify explain "<symbol>"', scout)
        self.assertIn('graphify path "<A>" "<B>"', scout)
        self.assertIn("returns its definition plus its direct callers/callees", scout)
        self.assertIn("returns the call/reference chain between two symbols", scout)
        self.assertNotIn("graphify query", scout)

    def test_scout_prompt_carries_its_inputs(self):
        out = self._run()
        scout = out["scout"]
        self.assertIn("/root", scout)
        self.assertIn("T001, T002", scout)
        self.assertIn("Read /root/tasks.md ONCE.", scout)

    # ── acceptance 4: 'graphify query' cannot silently regress anywhere in
    # an agent-facing prompt in loop.js — every prompt string built from these
    # two functions (the RETRIEVAL block and the heaviest graph consumer, the
    # Map scout) is checked above; this asserts the shipped source itself
    # carries no other call site that inlines the same graph-teaching text
    # outside these two pure functions.

    def test_source_has_exactly_the_two_known_non_prompt_mentions_of_query(self):
        with open(LOOP_JS, "r", encoding="utf-8") as f:
            lines = f.readlines()
        offending = [
            (i + 1, line.rstrip("\n"))
            for i, line in enumerate(lines)
            if "graphify query" in line and not line.strip().startswith("//")
        ]
        self.assertEqual(
            offending, [],
            f"'graphify query' found outside a comment (agent-facing prompt regression): {offending}",
        )


if __name__ == "__main__":
    unittest.main()
