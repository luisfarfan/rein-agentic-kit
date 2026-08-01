"""T001 acceptance 4/5 against REAL git, not extracted pure functions.

test_parallel_groups.py proves planParallelGroups/mergeOrderOf/
decideParallelFallback/summarizeConcurrency -- but those are pure functions
over hand-written outcome dicts; nothing in that suite ever runs the git
commands loop.js actually emits. That is exactly how the branch-name defect
(taskBranch joined with `/`, colliding with the run branch's own ref) shipped
green: `buildTaskWorktreePrompt`'s literal command was never executed against
a real repo.

This suite extracts the SHIPPED prompt-building functions the same way
test_serena_worktree.py does, then pulls the literal shell commands out of
the returned prompt text with a regex and actually RUNS them with `git`, so a
regression in the branch-naming scheme or the merge mechanics fails a git
command, not a string assertion.
"""

from __future__ import annotations

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
_GIT = shutil.which("git")

_EXTRACT_PROMPTS_JS = r"""
const fs = require('fs');
const [, , loopPath, argsJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
function extract(name, params) {
  const re = new RegExp(`function ${name}\\(${params}\\) \\{\\n([\\s\\S]*?)\\n\\}\\n`);
  const m = src.match(re);
  if (!m) throw new Error('not found in loop.js: ' + name);
  return m[1];
}
const buildTaskWorktreePrompt = new Function(
  'root', 'runBranch', 'wd', 'branch', 'rein',
  extract('buildTaskWorktreePrompt', 'root, runBranch, wd, branch, rein')
);
const buildTaskMergePrompt = new Function(
  'wd', 'branch', 'runBranch', 'taskId', 'taskWd',
  extract('buildTaskMergePrompt', 'wd, branch, runBranch, taskId, taskWd')
);
const args = JSON.parse(argsJson);
const out = {};
if (args.worktree) {
  const w = args.worktree;
  out.worktreePrompt = buildTaskWorktreePrompt(w.root, w.runBranch, w.wd, w.branch, w.rein);
}
if (args.merge) {
  const m = args.merge;
  out.mergePrompt = buildTaskMergePrompt(m.wd, m.branch, m.runBranch, m.taskId, m.taskWd);
}
process.stdout.write(JSON.stringify(out));
"""


def _run_node(args: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(_EXTRACT_PROMPTS_JS)
        script_path = f.name
    try:
        proc = subprocess.run(
            [_NODE, script_path, LOOP_JS, json.dumps(args)],
            capture_output=True, text=True, check=True,
        )
    finally:
        os.unlink(script_path)
    return json.loads(proc.stdout)


def _sh(cmd: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a literal shell command exactly as an implementer agent would."""
    return subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True)


def _init_repo(path: str, default_branch: str) -> None:
    subprocess.run([_GIT, "init", "-q", "-b", default_branch, path], check=True)
    subprocess.run([_GIT, "-C", path, "config", "user.email", "test@example.com"], check=True)
    subprocess.run([_GIT, "-C", path, "config", "user.name", "Test"], check=True)


def _commit_file(repo: str, name: str, content: str, message: str) -> None:
    with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run([_GIT, "-C", repo, "add", name], check=True)
    subprocess.run([_GIT, "-C", repo, "commit", "-q", "-m", message], check=True)


@unittest.skipUnless(_NODE and _GIT, "node and git must both be on PATH")
class TaskWorktreeCreationTestCase(unittest.TestCase):
    """Acceptance 4, first half: a task's own worktree is really created, off
    the actual `<prefix>/<label>` run-branch shape, with the fixed `-`-joined
    task branch (finding 1) -- reproduces the exact scenario the review found
    broken (a `/`-joined branch collides with the run branch's own ref)."""

    def test_taskBranch_uses_a_separator_that_cannot_collide_with_the_run_branch(self):
        # Pin the exact formula loop.js computes taskBranch with -- a silent
        # reversion to `/` must fail this test, not just the git commands below.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("const taskBranch = `${BRANCH}-${task.id}`", src)

    def test_worktree_add_succeeds_for_a_realistic_run_branch_and_task_branch(self):
        run_branch = "rein-wt/parallel-when-it-merits"  # `${PREFIX}/${LABEL}` shape
        task_id = "T001"
        task_branch = f"{run_branch}-{task_id}"  # the shipped formula, `-`-joined

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            _init_repo(root, run_branch)
            _commit_file(root, "README.md", "hello\n", "init")

            task_wd = os.path.join(tmp, "wt-t001")
            prompts = _run_node({"worktree": {
                "root": root, "runBranch": run_branch, "wd": task_wd, "branch": task_branch, "rein": "rein",
            }})
            prompt = prompts["worktreePrompt"]

            create_cmd = re.search(r"Create it: '(.+?)' \(reuse", prompt).group(1)
            verify_cmd = re.search(r"Verify: '(.+?)' reports", prompt).group(1)

            created = _sh(create_cmd)
            self.assertEqual(created.returncode, 0, f"stdout={created.stdout!r} stderr={created.stderr!r}")
            self.assertTrue(os.path.isdir(task_wd))

            verified = _sh(verify_cmd)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(verified.stdout.strip(), task_branch)

    def test_the_slash_joined_branch_this_review_flagged_actually_fails(self):
        """Negative control: proves the review's repro is real, not asserted
        on faith -- the OLD (buggy) formula collides with the run branch's
        own ref and `git worktree add` fails, and the `||` fallback the
        review flagged does not save it either."""
        run_branch = "rein-wt/parallel-when-it-merits"
        old_task_branch = f"{run_branch}/T001"  # the pre-fix, `/`-joined formula

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            _init_repo(root, run_branch)
            _commit_file(root, "README.md", "hello\n", "init")

            task_wd = os.path.join(tmp, "wt-t001")

            # The `-b` create step alone, WITHOUT the prompt's own `2>/dev/null`
            # (which the shipped command uses so the fallback can run): this is
            # the review's exact reproduction, "cannot lock ref ... exists".
            first_step = f"git -C {root} worktree add {task_wd} -b {old_task_branch} {run_branch}"
            lock_attempt = _sh(first_step)
            self.assertNotEqual(lock_attempt.returncode, 0)
            self.assertIn("refs/heads/" + run_branch, lock_attempt.stderr + lock_attempt.stdout)

            # And the shipped command as a whole (first step's stderr silenced,
            # then the `||` fallback) still fails -- just with a different
            # message, because the branch the fallback needs was never created.
            prompts = _run_node({"worktree": {
                "root": root, "runBranch": run_branch, "wd": task_wd, "branch": old_task_branch, "rein": "rein",
            }})
            create_cmd = re.search(r"Create it: '(.+?)' \(reuse", prompts["worktreePrompt"]).group(1)
            created = _sh(create_cmd)
            self.assertNotEqual(created.returncode, 0)
            self.assertIn("invalid reference", created.stderr + created.stdout)


@unittest.skipUnless(_NODE and _GIT, "node and git must both be on PATH")
class TaskMergeTestCase(unittest.TestCase):
    """Acceptance 4, second half + acceptance 5: two disjoint-file tasks land
    in task-id order; a genuine conflict aborts cleanly."""

    def _setup_run_and_tasks(self, tmp, run_branch="rein-wt/x"):
        root = os.path.join(tmp, "repo")
        _init_repo(root, run_branch)
        _commit_file(root, "README.md", "base\n", "init")
        base_sha = subprocess.run(
            [_GIT, "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()

        def make_task_branch(task_id, filename, content):
            task_wd = os.path.join(tmp, f"wt-{task_id.lower()}")
            subprocess.run(
                [_GIT, "-C", root, "worktree", "add", task_wd, "-b", f"{run_branch}-{task_id}", base_sha],
                check=True, capture_output=True, text=True,
            )
            _commit_file(task_wd, filename, content, f"feat({task_id}): add {filename}")
            return task_wd, f"{run_branch}-{task_id}"

        return root, make_task_branch

    def test_two_disjoint_file_tasks_merge_in_task_id_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_branch = "rein-wt/x"
            root, make_task_branch = self._setup_run_and_tasks(tmp, run_branch)
            t1_wd, t1_branch = make_task_branch("T001", "a.txt", "from T001\n")
            t2_wd, t2_branch = make_task_branch("T002", "b.txt", "from T002\n")

            # mergeOrderOf sorts by id -- merge T001 then T002, exactly as the
            # loop does, and assert history landed in that order.
            for task_id, branch, task_wd in [("T001", t1_branch, t1_wd), ("T002", t2_branch, t2_wd)]:
                prompts = _run_node({"merge": {
                    "wd": root, "branch": branch, "runBranch": run_branch, "taskId": task_id, "taskWd": task_wd,
                }})
                merge_cmd = re.search(r'and \'(git merge --no-ff .+?)\'\.\n', prompts["mergePrompt"]).group(1)
                merged = _sh(merge_cmd, cwd=root)
                self.assertEqual(merged.returncode, 0, f"stdout={merged.stdout!r} stderr={merged.stderr!r}")

            self.assertTrue(os.path.exists(os.path.join(root, "a.txt")))
            self.assertTrue(os.path.exists(os.path.join(root, "b.txt")))

            log = subprocess.run(
                [_GIT, "-C", root, "log", "--format=%s", "--reverse"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            t1_idx = next(i for i, m in enumerate(log) if "T001" in m)
            t2_idx = next(i for i, m in enumerate(log) if "T002" in m)
            self.assertLess(t1_idx, t2_idx, f"T001 must land before T002 in history: {log}")

    def test_a_genuine_conflict_aborts_and_leaves_the_run_worktree_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_branch = "rein-wt/x"
            root, make_task_branch = self._setup_run_and_tasks(tmp, run_branch)
            # Both tasks edit the SAME file, same line -- a real conflict.
            t1_wd, t1_branch = make_task_branch("T001", "README.md", "T001 changed this\n")
            t2_wd, t2_branch = make_task_branch("T002", "README.md", "T002 changed this\n")

            # T001 merges cleanly first (run branch has not diverged yet).
            p1 = _run_node({"merge": {
                "wd": root, "branch": t1_branch, "runBranch": run_branch, "taskId": "T001", "taskWd": t1_wd,
            }})
            m1 = re.search(r'and \'(git merge --no-ff .+?)\'\.\n', p1["mergePrompt"]).group(1)
            r1 = _sh(m1, cwd=root)
            self.assertEqual(r1.returncode, 0, f"stdout={r1.stdout!r} stderr={r1.stderr!r}")

            # T002 now conflicts with what T001 landed.
            p2 = _run_node({"merge": {
                "wd": root, "branch": t2_branch, "runBranch": run_branch, "taskId": "T002", "taskWd": t2_wd,
            }})
            merge_prompt = p2["mergePrompt"]
            m2 = re.search(r'and \'(git merge --no-ff .+?)\'\.\n', merge_prompt).group(1)
            r2 = _sh(m2, cwd=root)
            self.assertNotEqual(r2.returncode, 0, "same-line edits on both branches must conflict")

            # The prompt's own conflict instruction: abort, do not force a resolution.
            self.assertIn("git merge --abort", merge_prompt)
            abort = _sh("git merge --abort", cwd=root)
            self.assertEqual(abort.returncode, 0, abort.stderr)

            status = subprocess.run(
                [_GIT, "-C", root, "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout
            self.assertEqual(status, "", f"run worktree must be clean after abort, got: {status!r}")


if __name__ == "__main__":
    unittest.main()
