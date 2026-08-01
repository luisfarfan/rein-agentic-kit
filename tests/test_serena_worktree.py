"""Serena was announced to agents in a directory serena did not know.

`.serena/` is gitignored -- correctly, it is machine-local -- so it can never
travel into a worktree cut from a committed HEAD. But `hasSerena` was read
from `ctx.capabilities`, which describes the BASE repo. Verified before the
fix: a fresh worktree has no `.serena/` and reports no `serena-project`,
while the base repo reports it, so every implementer in every run was handed
serena's edit tools for a path serena treats as unknown.

This is the same rule `decideGraphAvailable` already enforces for codegraph
(D2, graph-reaches-the-agents): a capability is only claimed where its tools
will actually RUN. Same discipline as test_graph_index.py -- the decision
function is extracted from the SHIPPED loop.js and EXECUTED, never asserted
by substring.
"""

from __future__ import annotations

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

_RUN_JS = r"""
const fs = require('fs');
const [, , loopPath, casesJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const decideSerenaAvailable = new Function(
  'worktreeMode', 'capabilities', 'isolate',
  extract('decideSerenaAvailable', 'worktreeMode, capabilities, isolate')
);
const buildIsolatePrompt = new Function(
  'root', 'base', 'wd', 'branch', 'rein',
  extract('buildIsolatePrompt', 'root, base, wd, branch, rein')
);
const cases = JSON.parse(casesJson);
process.stdout.write(JSON.stringify({
  decided: cases.map((c) => decideSerenaAvailable(c.worktreeMode, c.capabilities, c.isolate)),
  prompt: buildIsolatePrompt('/base', 'main', '/base-wt-x', 'rein-wt/x', 'rein'),
}));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class SerenaAvailabilityTestCase(unittest.TestCase):
    def _run(self, cases: list) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_RUN_JS)
            path = f.name
        try:
            proc = subprocess.run(
                [_NODE, path, LOOP_JS, json.dumps(cases)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(path)
        return json.loads(proc.stdout)

    def test_the_base_repo_being_activated_does_not_activate_the_worktree(self):
        """THE bug. Reproduced on a real worktree before the fix: the base
        repo reports serena-project, the worktree has no `.serena/` at all."""
        out = self._run([{
            "worktreeMode": True,
            "capabilities": ["serena-project"],
            "isolate": {"serenaActivated": False, "serenaOutcome": "not activated"},
        }])
        self.assertEqual(out["decided"], [False])

    def test_an_activated_worktree_is_available_even_if_the_base_is_not(self):
        out = self._run([{
            "worktreeMode": True, "capabilities": [],
            "isolate": {"serenaActivated": True, "serenaOutcome": "ok"},
        }])
        self.assertEqual(out["decided"], [True])

    def test_a_dead_isolate_agent_degrades_to_unavailable_not_a_raise(self):
        out = self._run([{"worktreeMode": True, "capabilities": ["serena-project"], "isolate": None}])
        self.assertEqual(out["decided"], [False])

    def test_without_a_worktree_the_capability_still_answers(self):
        out = self._run([
            {"worktreeMode": False, "capabilities": ["serena-project"], "isolate": None},
            {"worktreeMode": False, "capabilities": [], "isolate": None},
        ])
        self.assertEqual(out["decided"], [True, False])

    def test_the_isolate_prompt_activates_serena_in_the_worktree_non_blocking(self):
        prompt = self._run([{"worktreeMode": True, "capabilities": [], "isolate": None}])["prompt"]
        self.assertIn("rein setup /base-wt-x --activate", prompt)
        self.assertIn("/base-wt-x/.serena/project.yml", prompt)
        self.assertIn("serenaActivated", prompt)
        # D4: it must never gate `done`, exactly like the graph index step.
        self.assertIn("never blocks done", prompt)


class SerenaActivateCliTestCase(unittest.TestCase):
    """`--activate` is the repo-scoped half ONLY: no installs, and never a
    write to a tracked file (the loop runs it inside a worktree)."""

    REIN = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins", "rein", "bin", "rein",
    )

    def test_activate_never_reports_failure_to_its_caller(self):
        """D4: activation is a capability, not a gate. Even pointed at a
        directory where it cannot work, it must exit 0."""
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                ["python3", self.REIN, "setup", d, "--activate"],
                capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("serena-activate", proc.stdout)

    def test_activate_does_not_install_anything(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                ["python3", self.REIN, "setup", d, "--activate"],
                capture_output=True, text=True, timeout=120,
            )
        self.assertNotIn("installing:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
