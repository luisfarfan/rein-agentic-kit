"""Tests for T001 "Tasks that cannot collide run at the same time"
(plugins/rein/workflows/loop.js) -- the pure planParallelGroups(tasks, codeMap).

Same discipline as test_graph_retrieval.py: `planParallelGroups` is extracted
straight out of loop.js's source by regex and run with `new Function`, so this
proves the actual shipped function, not a reimplementation that could silently
drift from it.
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


if __name__ == "__main__":
    unittest.main()
