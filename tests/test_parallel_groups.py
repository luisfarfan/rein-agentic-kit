"""Tests for T001 "Tasks that cannot collide run at the same time"
(plugins/rein/workflows/loop.js) -- planParallelGroups(tasks, codeMap) plus
the merge-order (D4), fallback (D3) and concurrency-reporting (D5) pure
functions the parallel path is built from.

Same discipline as test_graph_retrieval.py: every function under test is
extracted straight out of loop.js's source by regex and run with `new
Function`, so this proves the actual shipped function, not a reimplementation
that could silently drift from it.
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
const planParallelGroups = new Function(
  'tasks', 'codeMap',
  extract('planParallelGroups', 'tasks, codeMap')
);
const cases = JSON.parse(casesJson);
const out = cases.map((c) => planParallelGroups(c.tasks, c.codeMap).map((g) => g.map((t) => t.id)));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class PlanParallelGroupsTestCase(unittest.TestCase):
    def _run(self, cases):
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

    @staticmethod
    def _task(id_, depends_on=None):
        return {"id": id_, "dependsOn": depends_on or []}

    @staticmethod
    def _map_entry(touchpoints):
        return {"touchpoints": touchpoints, "orientation": ""}

    # ── acceptance 1: chain, two independent, two independent sharing a
    # file, and an empty map -- executed, not asserted from source text ────

    def test_chain_produces_single_task_groups_in_order(self):
        tasks = [self._task("T001"), self._task("T002", ["T001"]), self._task("T003", ["T002"])]
        codeMap = {
            "T001": self._map_entry(["a.js"]),
            "T002": self._map_entry(["b.js"]),
            "T003": self._map_entry(["c.js"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001"], ["T002"], ["T003"]])

    def test_two_independent_tasks_with_disjoint_touchpoints_group_together(self):
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {
            "T001": self._map_entry(["a.js"]),
            "T004": self._map_entry(["d.js"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001", "T004"]])

    def test_two_independent_tasks_sharing_a_touchpoint_stay_serial(self):
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {
            "T001": self._map_entry(["shared.js", "a.js"]),
            "T004": self._map_entry(["shared.js"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001"], ["T004"]])

    def test_scout_format_touchpoints_in_same_file_different_symbols_stay_serial(self):
        # The scout never emits bare paths (buildScoutPrompt: "path:symbol").
        # Two touchpoints in the SAME FILE at different symbols/lines must
        # still collide -- this is D1's exact target case: two independent
        # tasks editing one file.
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {
            "T001": self._map_entry(["plugins/rein/workflows/loop.js:planParallelGroups"]),
            "T004": self._map_entry(["plugins/rein/workflows/loop.js:mergeOrderOf"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001"], ["T004"]])

    def test_scout_format_touchpoints_in_disjoint_files_still_group(self):
        # Normalising "path:symbol" to its file must not be over-applied --
        # genuinely disjoint files still group together.
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {
            "T001": self._map_entry(["a.js:foo"]),
            "T004": self._map_entry(["b.js:bar"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001", "T004"]])

    def test_empty_code_map_places_every_task_in_its_own_group(self):
        tasks = [self._task("T001"), self._task("T004")]
        [groups] = self._run([{"tasks": tasks, "codeMap": {}}])
        self.assertEqual(groups, [["T001"], ["T004"]])

    # ── acceptance 2: no map entry, or an entry with empty touchpoints, is
    # UNKNOWN and always gets its own group -- asserted directly ───────────

    def test_task_with_no_code_map_entry_is_alone(self):
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {"T004": self._map_entry(["d.js"])}
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001"], ["T004"]])

    def test_task_with_empty_touchpoints_is_alone(self):
        tasks = [self._task("T001"), self._task("T004")]
        codeMap = {
            "T001": self._map_entry([]),
            "T004": self._map_entry(["d.js"]),
        }
        [groups] = self._run([{"tasks": tasks, "codeMap": codeMap}])
        self.assertEqual(groups, [["T001"], ["T004"]])

    def test_two_unknown_tasks_do_not_group_with_each_other_either(self):
        tasks = [self._task("T001"), self._task("T004")]
        [groups] = self._run([{"tasks": tasks, "codeMap": {}}])
        # both unknown: neither joins the other's group, each stands alone
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(len(g) == 1 for g in groups))

    # ── acceptance 3: the five real dependency shapes from this repo's own
    # history (see tasks.md "Why" table) -- four chains, one independent pair ─

    def _repo_history_cases(self):
        # Every task's own touchpoints are distinct, so grouping decisions in
        # these cases turn on dependsOn alone, exactly like acceptance 3 asks.
        touchpoints = lambda id_: self._map_entry([f"{id_.lower()}.js"])
        chain_2 = [self._task("T001"), self._task("T002", ["T001"])]
        chain_3 = [self._task("T001"), self._task("T002", ["T001"]), self._task("T003", ["T002"])]
        independent_pair = [self._task("T001"), self._task("T004")]
        shapes = {
            "graph-reaches-the-agents": chain_2,       # T002<-T001
            "one-owner-for-retrieval": chain_2,        # T002<-T001
            "measure-itself": chain_2,                 # T002<-T001
            "the-dashboard-answers-the-question": chain_3,  # T003<-T002<-T001
            "works-in-any-repo": independent_pair,     # T001, T004 independent
        }
        cases = []
        for name, tasks in shapes.items():
            codeMap = {t["id"]: touchpoints(t["id"]) for t in tasks}
            cases.append({"name": name, "tasks": tasks, "codeMap": codeMap})
        return cases

    def test_repo_history_chains_are_all_single_task_groups(self):
        shapes = self._repo_history_cases()
        chains = [s for s in shapes if s["name"] != "works-in-any-repo"]
        results = self._run([{"tasks": s["tasks"], "codeMap": s["codeMap"]} for s in chains])
        for shape, groups in zip(chains, results):
            with self.subTest(shape=shape["name"]):
                self.assertTrue(
                    all(len(g) == 1 for g in groups),
                    f"{shape['name']} is a dependency chain and must not group any tasks together: {groups}",
                )

    def test_repo_history_only_the_independent_pair_groups_together(self):
        shapes = self._repo_history_cases()
        independent = next(s for s in shapes if s["name"] == "works-in-any-repo")
        [groups] = self._run([{"tasks": independent["tasks"], "codeMap": independent["codeMap"]}])
        self.assertEqual(groups, [["T001", "T004"]])


# ── acceptance 4/5/6: the pure functions the parallel-worktree/merge/
# fallback/concurrency-reporting path is built from -- each extracted and
# executed the same way as planParallelGroups above ─────────────────────────

_EXTRACT_AND_CALL_JS = r"""
const fs = require('fs');
const [, , loopPath, fnName, fnParams, argsJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const params = fnParams.split(',').filter(Boolean);
const fn = new Function(...params, extract(fnName, fnParams.replace(/,/g, ', ')));
const args = JSON.parse(argsJson);
process.stdout.write(JSON.stringify(fn(...args)));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class ParallelWorktreeHelpersTestCase(unittest.TestCase):
    def _call(self, fn_name, fn_params, args):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_EXTRACT_AND_CALL_JS)
            script_path = f.name
        try:
            proc = subprocess.run(
                [_NODE, script_path, LOOP_JS, fn_name, fn_params, json.dumps(args)],
                capture_output=True, text=True, check=True,
            )
        finally:
            os.unlink(script_path)
        return json.loads(proc.stdout)

    # -- acceptance 4: merge order is by task id, not completion time --------

    def test_merge_order_sorts_by_id_regardless_of_input_order(self):
        # T003 "finished" before T001 and T002 -- simulates a fast task racing
        # ahead of slower ones that appear earlier in the plan.
        outcomes = [{"id": "T003"}, {"id": "T001"}, {"id": "T002"}]
        result = self._call("mergeOrderOf", "outcomes", [outcomes])
        self.assertEqual([o["id"] for o in result], ["T001", "T002", "T003"])

    def test_merge_order_is_stable_when_already_sorted(self):
        outcomes = [{"id": "T001"}, {"id": "T002"}]
        result = self._call("mergeOrderOf", "outcomes", [outcomes])
        self.assertEqual([o["id"] for o in result], ["T001", "T002"])

    # -- acceptance 5: any parallel-path failure falls back to serial --------

    def test_successful_implementation_and_merge_does_not_fall_back(self):
        outcome = {"id": "T001", "status": "implemented", "worktreeFailed": False, "mergeConflict": False}
        result = self._call("decideParallelFallback", "outcome", [outcome])
        self.assertFalse(result["fallbackToSerial"])

    def test_merge_conflict_falls_back_even_though_implementation_succeeded(self):
        outcome = {
            "id": "T001",
            "status": "implemented",
            "worktreeFailed": False,
            "mergeConflict": True,
            "detail": "merge: conflict in loop.js",
        }
        result = self._call("decideParallelFallback", "outcome", [outcome])
        self.assertTrue(result["fallbackToSerial"])
        self.assertIn("conflict", result["reason"])

    def test_worktree_creation_failure_falls_back(self):
        outcome = {"id": "T001", "status": "error", "worktreeFailed": True, "detail": "worktree: git error"}
        result = self._call("decideParallelFallback", "outcome", [outcome])
        self.assertTrue(result["fallbackToSerial"])

    def test_a_dead_agent_reported_as_blocked_falls_back(self):
        outcome = {"id": "T001", "status": "blocked", "worktreeFailed": False, "mergeConflict": False}
        result = self._call("decideParallelFallback", "outcome", [outcome])
        self.assertTrue(result["fallbackToSerial"])

    # -- acceptance 6: the run reports parallelGroups + whether it actually
    # ran concurrently, never merely claimed --------------------------------

    def test_fully_serial_plan_reports_groups_of_one_and_no_concurrency_claim(self):
        groups = [[{"id": "T001"}], [{"id": "T002"}], [{"id": "T003"}]]
        result = self._call("summarizeConcurrency", "groups,ranParallelFlags", [groups, [False, False, False]])
        self.assertEqual(result["parallelGroups"], [["T001"], ["T002"], ["T003"]])
        self.assertFalse(result["concurrent"])

    def test_a_multi_task_group_that_actually_ran_in_parallel_is_reported_concurrent(self):
        groups = [[{"id": "T001"}, {"id": "T004"}]]
        result = self._call("summarizeConcurrency", "groups,ranParallelFlags", [groups, [True]])
        self.assertEqual(result["parallelGroups"], [["T001", "T004"]])
        self.assertTrue(result["concurrent"])

    def test_a_multi_task_group_that_fell_back_entirely_is_not_reported_concurrent(self):
        # D3: planParallelGroups judged these groupable, but D3's fallback made
        # every member land serially -- "it merits" must not be claimed anyway.
        groups = [[{"id": "T001"}, {"id": "T004"}]]
        result = self._call("summarizeConcurrency", "groups,ranParallelFlags", [groups, [False]])
        self.assertFalse(result["concurrent"])

    # -- acceptance 7: `ranParallel` must reflect that at least TWO tasks
    # actually entered the parallel path, not merely that one of them landed --

    def test_only_one_of_two_reaching_worktree_creation_is_not_concurrency(self):
        # T004's worktree-creation agent fails immediately (worktreeFailed);
        # only T001 ever implements and lands. One task ran -- not concurrent.
        outcomes = [
            {"id": "T001", "worktreeFailed": False},
            {"id": "T004", "worktreeFailed": True},
        ]
        landed = [{"id": "T001"}]
        result = self._call("didRunConcurrently", "outcomes,landed", [outcomes, landed])
        self.assertFalse(result)

    def test_both_reaching_worktree_creation_and_one_landing_is_concurrency(self):
        outcomes = [
            {"id": "T001", "worktreeFailed": False},
            {"id": "T004", "worktreeFailed": False},
        ]
        landed = [{"id": "T001"}]
        result = self._call("didRunConcurrently", "outcomes,landed", [outcomes, landed])
        self.assertTrue(result)

    def test_nothing_landed_is_not_concurrency_even_if_both_attempted(self):
        outcomes = [
            {"id": "T001", "worktreeFailed": False},
            {"id": "T004", "worktreeFailed": False},
        ]
        result = self._call("didRunConcurrently", "outcomes,landed", [outcomes, []])
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
