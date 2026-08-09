"""Tests for T002 -- "transitions are emitted while they happen, not guessed
afterwards".

Four things, four suites:
  AC1  `rein event task <id> <transition>` (plugins/rein/lib/events.py +
       plugins/rein/bin/rein) -- the accepted set, and that an unknown
       transition exits non-zero without writing.
  AC2  `transitionsFor(step)` in plugins/rein/workflows/loop.js -- extracted
       straight out of the SHIPPED source by regex and run with `new
       Function`, same discipline as tests/test_loop_policy.py, so this
       proves the actual logic a step runs, not a reimplementation that
       could drift from it (D2's defect class: prose whose words never
       execute the path they claim to prove).
  AC3/AC4  plugins/rein/lib/product_state.py's `state(root)` -- folding is
       last-write-wins BY TIMESTAMP over an out-of-order, duplicated event
       log, stable across two calls, and a task with no events still
       appears as "planned" rather than vanishing.
  AC5  `change_age_days` -- the newest commit touching the change directory,
       against a fixture with backdated commits.
  AC6  `rein state` -- the per-task record printed, and folded per member
       when `root` sits inside a `.rein/workspace.json` workspace.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REIN_BIN = os.path.join(REPO_ROOT, "plugins", "rein", "bin", "rein")
LIB_DIR = os.path.join(REPO_ROOT, "plugins", "rein", "lib")
LOOP_JS = os.path.join(REPO_ROOT, "plugins", "rein", "workflows", "loop.js")

sys.path.insert(0, LIB_DIR)
import events as ev  # noqa: E402
import product_state as ps  # noqa: E402

_NODE = shutil.which("node")

TASKS_MD = (
    "# Change: demo\n\n"
    "- [x] T001 First task\n"
    "- [ ] T002 Second task\n"
    "- [ ] T003 Third task\n"
)


def _git(repo: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env, check=True)


def _init_git_repo(repo: str) -> None:
    os.makedirs(repo, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: str, filename: str, content: str, when: str | None = None) -> str:
    with open(os.path.join(repo, filename), "w", encoding="utf-8") as fh:
        fh.write(content)
    _git(repo, "add", filename)
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    _git(repo, "commit", "-q", "-m", f"add {filename}", env=env)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ═══════════════════════════════════════════════════════════ AC1: events.py ══


class TestTaskTransitionsAccepted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        _init_git_repo(self.repo)

    def test_accepted_set_is_exactly_started_verified_blocked_merged(self):
        self.assertEqual(set(ev.TASK_TRANSITIONS), {"started", "verified", "blocked", "merged"})

    def test_each_accepted_transition_is_recorded_with_task_id_change_and_repo(self):
        path = os.path.join(self.tmp.name, "events.jsonl")
        for transition in ev.TASK_TRANSITIONS:
            ok, error = ev.record_task_event("T099", transition, change="demo", root=self.repo, events_path=path)
            self.assertTrue(ok, error)
        rows = ev.read_events(path)
        self.assertEqual([r["transition"] for r in rows], list(ev.TASK_TRANSITIONS))
        for row in rows:
            self.assertEqual(row["task_id"], "T099")
            self.assertEqual(row["change"], "demo")
            self.assertEqual(row["repo"], os.path.realpath(self.repo))
            self.assertIn("commit", row)
            self.assertIn("ts", row)

    def test_unknown_transition_is_rejected_by_name_and_writes_nothing(self):
        path = os.path.join(self.tmp.name, "events.jsonl")
        ok, error = ev.record_task_event("T099", "closed", root=self.repo, events_path=path)
        self.assertFalse(ok)
        self.assertIn("closed", error)
        self.assertFalse(os.path.exists(path))


class TaskEventCliFixture(unittest.TestCase):
    """A tmp HOME with its own ~/.claude/rein -- never the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.events_path = os.path.join(self.home, ".claude", "rein", "events.jsonl")
        self.repo = os.path.join(self.tmp.name, "repo")
        _init_git_repo(self.repo)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run([sys.executable, REIN_BIN, *args], capture_output=True, text=True, env=env, timeout=30)


class TestEventTaskCli(TaskEventCliFixture):
    def test_appends_transition_task_id_change_and_resolved_repo(self):
        result = self._run("event", "task", "T002", "started", "--root", self.repo, "--change", "product-observability")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(self.events_path, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["task_id"], "T002")
        self.assertEqual(row["transition"], "started")
        self.assertEqual(row["change"], "product-observability")
        self.assertEqual(row["repo"], os.path.realpath(self.repo))

    def test_each_accepted_transition_exits_zero(self):
        for transition in ("started", "verified", "blocked", "merged"):
            result = self._run("event", "task", "T003", transition, "--root", self.repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_transition_exits_nonzero_without_writing(self):
        result = self._run("event", "task", "T002", "closed", "--root", self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.events_path))

    def test_missing_task_id_or_transition_exits_nonzero_without_writing(self):
        result = self._run("event", "task", "T002", "--root", self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.events_path))


# ═══════════════════════════════════════════════════ AC2: loop.js transitionsFor ══

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
const transitionsFor = new Function('step', extract('transitionsFor', 'step'));
const scenarios = JSON.parse(scenariosJson);
const out = scenarios.map((s) => transitionsFor(s));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestTransitionsForIsExtractable(unittest.TestCase):
    def test_function_exists_with_expected_signature(self):
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function transitionsFor(step)", src)

    def test_a_step_actually_calls_it_not_just_defines_it(self):
        # D2: dead code that only DEFINES the decision proves nothing about
        # what a step actually does. Must appear at least once more than the
        # `function transitionsFor(step) {` declaration itself.
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertGreater(src.count("transitionsFor("), 1)


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestTransitionsForPolicy(unittest.TestCase):
    def _run(self, scenarios: list[dict]) -> list[list[str]]:
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

    def test_a_passing_step_emits_started_then_verified(self):
        [result] = self._run([{"attempt": 1, "maxAttempts": 5, "verifyExitCode": 0}])
        self.assertEqual(result, ["started", "verified"])

    def test_a_failing_first_attempt_emits_only_started(self):
        [result] = self._run([{"attempt": 1, "maxAttempts": 5, "verifyExitCode": 1}])
        self.assertEqual(result, ["started"])

    def test_a_failing_mid_attempt_emits_nothing(self):
        [result] = self._run([{"attempt": 2, "maxAttempts": 5, "verifyExitCode": 1}])
        self.assertEqual(result, [])

    def test_a_capped_attempt_that_still_fails_emits_blocked(self):
        [result] = self._run([{"attempt": 5, "maxAttempts": 5, "verifyExitCode": 1}])
        self.assertEqual(result, ["blocked"])

    def test_a_capped_attempt_that_passes_emits_verified_not_blocked(self):
        [result] = self._run([{"attempt": 5, "maxAttempts": 5, "verifyExitCode": 0}])
        self.assertEqual(result, ["verified"])


# ═══════════════════════════════════════════════ AC3/AC4: product_state fold ══


class TestProductStateFold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        _init_git_repo(self.repo)
        _commit(self.repo, "tasks.md", TASKS_MD)
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")

    def _write_events(self, rows: list[dict]) -> None:
        with open(self.events_path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _row(self, task_id: str, transition: str, ts: str, commit: str = "c0") -> dict:
        return {
            "kind": "task", "task_id": task_id, "transition": transition,
            "change": "demo", "repo": os.path.realpath(self.repo),
            "commit": commit, "ts": ts,
        }

    def test_fold_is_last_write_wins_by_timestamp_not_file_order(self):
        self._write_events([
            self._row("T001", "started", "2026-01-01T00:00:00+00:00", commit="c1"),
            self._row("T001", "verified", "2026-01-02T00:00:00+00:00", commit="c2"),
            # A DUPLICATE 'started' lands LAST in the file but carries an
            # EARLIER timestamp -- file order must not win over it.
            self._row("T001", "started", "2026-01-01T00:00:00+00:00", commit="c1"),
            # T002's events are OUT OF ORDER in the file (blocked appears
            # before started) but 'blocked' has the LATER timestamp.
            self._row("T002", "blocked", "2026-01-05T00:00:00+00:00", commit="c4"),
            self._row("T002", "started", "2026-01-04T00:00:00+00:00", commit="c3"),
        ])
        rec = ps.state(self.repo, events_path=self.events_path)
        by_id = {t["taskId"]: t for t in rec["tasks"]}
        self.assertEqual(by_id["T001"]["transition"], "verified")
        self.assertEqual(by_id["T001"]["commit"], "c2")
        self.assertEqual(by_id["T002"]["transition"], "blocked")
        self.assertEqual(by_id["T002"]["commit"], "c4")

    def test_stable_across_two_calls(self):
        self._write_events([
            self._row("T001", "started", "2026-01-01T00:00:00+00:00"),
            self._row("T001", "verified", "2026-01-02T00:00:00+00:00"),
            self._row("T002", "started", "2026-01-03T00:00:00+00:00"),
        ])
        first = ps.state(self.repo, events_path=self.events_path)
        second = ps.state(self.repo, events_path=self.events_path)
        # `lastTouchedDays` is a live clock reading (AC5) and is not itself
        # part of the FOLD's stability claim -- exercised on its own below.
        self.assertEqual(first["tasks"], second["tasks"])
        self.assertEqual({k: v for k, v in first.items() if k != "lastTouchedDays"},
                          {k: v for k, v in second.items() if k != "lastTouchedDays"})

    def test_a_task_with_no_events_appears_as_planned_not_omitted(self):
        self._write_events([
            self._row("T001", "started", "2026-01-01T00:00:00+00:00"),
        ])
        rec = ps.state(self.repo, events_path=self.events_path)
        ids = {t["taskId"] for t in rec["tasks"]}
        self.assertEqual(ids, {"T001", "T002", "T003"})
        by_id = {t["taskId"]: t for t in rec["tasks"]}
        self.assertEqual(by_id["T002"]["transition"], "planned")
        self.assertEqual(by_id["T003"]["transition"], "planned")
        self.assertEqual(by_id["T002"]["when"], "")
        self.assertEqual(by_id["T002"]["commit"], "")

    def test_events_from_a_different_repo_are_not_folded_in(self):
        other = os.path.join(self.tmp.name, "other-repo")
        self._write_events([
            {**self._row("T001", "verified", "2026-01-01T00:00:00+00:00"), "repo": os.path.realpath(other)},
        ])
        rec = ps.state(self.repo, events_path=self.events_path)
        by_id = {t["taskId"]: t for t in rec["tasks"]}
        self.assertEqual(by_id["T001"]["transition"], "planned")


class TestTwoChangesInOneRepoDoNotShareTaskIds(unittest.TestCase):
    """Task ids are always T001..T00N, so the repo alone is not an identity.

    Filtering events on `repo` only made every change in a repository share
    one namespace: emitting `verified` for `alpha`'s T001 reported `beta`'s
    T001 as verified too, and the sync then wrote beta's card to Plane as
    completed — work that had never started, marked done on the board.

    This is the plan's PRIMARY shape, not an edge case: the Why counts 118
    OpenSpec changes in one workspace. The suite was green because no test
    built two changes in one repo; the repo dimension was covered and the
    change dimension was not.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        _init_git_repo(self.repo)
        for name in ("alpha", "beta"):
            d = os.path.join(self.repo, "openspec", "changes", name)
            os.makedirs(d)
            with open(os.path.join(d, "tasks.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Change: {name}\n\n- [ ] T001 task of {name}\n  - Depends on: none\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "two changes")
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")

    def _transition(self, change):
        return ps.state(self.repo, change=change, events_path=self.events_path)["tasks"][0]["transition"]

    def test_closing_one_change_does_not_close_the_other(self):
        ev.record_task_event("T001", "verified", change="alpha",
                             root=self.repo, events_path=self.events_path)
        self.assertEqual(self._transition("alpha"), "verified")
        self.assertEqual(self._transition("beta"), "planned")

    def test_each_change_folds_its_own_transition(self):
        ev.record_task_event("T001", "started", change="alpha",
                             root=self.repo, events_path=self.events_path)
        ev.record_task_event("T001", "blocked", change="beta",
                             root=self.repo, events_path=self.events_path)
        self.assertEqual(self._transition("alpha"), "started")
        self.assertEqual(self._transition("beta"), "blocked")


class TestTransitionsEmittedFromAWorktreeSurvive(unittest.TestCase):
    """The default execution mode, which nothing else here exercised.

    The loop runs every task inside a worktree and emits with
    `--root <worktree>`; `rein state` folds against the main repo. Keying the
    event on the literal path made the two never match — and the worktree is
    removed when the run ends, so the transition was written to a name nobody
    could look up again. Measured before the fix: main repo `planned`,
    worktree `verified`. Every task would have read `planned` forever and
    every Plane card would have sat in `backlog` forever.

    `test_events_from_a_different_repo_are_not_folded_in` pins the opposite
    direction only, which is why a green suite said nothing about this.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        _init_git_repo(self.repo)
        with open(os.path.join(self.repo, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write("# Change: c\n\n- [ ] T001 do it\n  - Depends on: none\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "plan")
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")
        self.wt = os.path.join(self.tmp.name, "wt")
        _git(self.repo, "worktree", "add", "-q", "-b", "wt-branch", self.wt)

    def test_the_main_repo_sees_a_transition_emitted_in_its_worktree(self):
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        rec = ps.state(self.repo, events_path=self.events_path)
        by_id = {t["taskId"]: t for t in rec["tasks"]}
        self.assertEqual(by_id["T001"]["transition"], "verified")

    def test_it_still_reads_after_the_worktree_is_removed(self):
        """The run deletes the worktree. The record has to outlive it."""
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        _git(self.repo, "worktree", "remove", "--force", self.wt)
        rec = ps.state(self.repo, events_path=self.events_path)
        by_id = {t["taskId"]: t for t in rec["tasks"]}
        self.assertEqual(by_id["T001"]["transition"], "verified")

    def test_the_commit_recorded_is_the_worktrees_not_the_main_repos(self):
        """Canonical repo for identity, real commit for evidence."""
        _git(self.wt, "commit", "-q", "--allow-empty", "-m", "work")
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        wt_head = _git(self.wt, "rev-parse", "HEAD").stdout.strip()
        main_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(wt_head, main_head)
        rec = ps.state(self.repo, events_path=self.events_path)
        self.assertEqual(rec["tasks"][0]["commit"], wt_head)

    def test_state_read_from_inside_the_worktree_sees_it_too(self):
        """The other half: both SIDES must canonicalise, not just the emitter.

        Emitting canonically is enough for `rein state` in the main repo,
        because there the resolved root already IS the canonical repo. It is
        not enough when the reader is itself inside a worktree — which is
        exactly where the loop asks for the plan's state mid-run. Without the
        fold canonicalising too, this reads `planned` while the event sits in
        the log two lines away.
        """
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        rec = ps.state(self.wt, events_path=self.events_path)
        self.assertEqual(rec["tasks"][0]["transition"], "verified")

    def test_a_genuinely_unrelated_repo_is_still_excluded(self):
        """The fix must not make the fold indiscriminate."""
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        _init_git_repo(other)
        _git(other, "commit", "-q", "--allow-empty", "-m", "init")
        ev.record_task_event("T001", "verified", root=other, events_path=self.events_path)
        rec = ps.state(self.repo, events_path=self.events_path)
        self.assertEqual(rec["tasks"][0]["transition"], "planned")


# ══════════════════════════════════════════════════════ AC5: last-touched age ══


class TestChangeAgeDays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        _init_git_repo(self.repo)

    def test_age_in_days_matches_a_backdated_commit(self):
        ten_days_ago = int(time.time()) - 10 * 86400
        _commit(self.repo, "tasks.md", TASKS_MD, when=f"@{ten_days_ago} +0000")
        age = ps.change_age_days(self.repo, self.repo)
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 10.0, delta=0.05)

    def test_the_newest_commit_touching_the_path_wins(self):
        _commit(self.repo, "tasks.md", TASKS_MD, when=f"@{int(time.time()) - 20 * 86400} +0000")
        _commit(self.repo, "tasks.md", TASKS_MD + "- [ ] T004 more\n", when=f"@{int(time.time()) - 1 * 86400} +0000")
        age = ps.change_age_days(self.repo, os.path.join(self.repo, "tasks.md"))
        self.assertAlmostEqual(age, 1.0, delta=0.05)

    def test_no_commits_touching_the_path_yields_none(self):
        age = ps.change_age_days(self.repo, os.path.join(self.repo, "never-committed"))
        self.assertIsNone(age)

    def test_state_carries_last_touched_days(self):
        five_days_ago = int(time.time()) - 5 * 86400
        _commit(self.repo, "tasks.md", TASKS_MD, when=f"@{five_days_ago} +0000")
        rec = ps.state(self.repo)
        self.assertIsNotNone(rec["lastTouchedDays"])
        self.assertAlmostEqual(rec["lastTouchedDays"], 5.0, delta=0.05)


# ═══════════════════════════════════════════════════════════ AC6: rein state ══


class TestReinStateCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run([sys.executable, REIN_BIN, *args], capture_output=True, text=True, env=env, timeout=30)

    def test_prints_the_per_task_record_for_a_single_repo(self):
        repo = os.path.join(self.tmp.name, "solo")
        _init_git_repo(repo)
        _commit(repo, "tasks.md", TASKS_MD)
        result = self._run("state", repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("T001", result.stdout)
        self.assertIn("T002", result.stdout)
        self.assertIn("planned", result.stdout)

    def test_json_output_matches_the_library_record(self):
        repo = os.path.join(self.tmp.name, "solo-json")
        _init_git_repo(repo)
        _commit(repo, "tasks.md", TASKS_MD)
        result = self._run("state", repo, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(len(doc["tasks"]), 3)

    def test_workspace_output_names_each_member_repo(self):
        ws = os.path.join(self.tmp.name, "ws")
        api = os.path.join(ws, "api")
        web = os.path.join(ws, "web")
        os.makedirs(os.path.join(ws, ".rein"))
        _init_git_repo(api)
        _commit(api, "tasks.md", TASKS_MD)
        _init_git_repo(web)
        _commit(web, "tasks.md", TASKS_MD)
        with open(os.path.join(ws, ".rein", "workspace.json"), "w", encoding="utf-8") as fh:
            json.dump({"members": [{"name": "api", "path": "api"}, {"name": "web", "path": "web"}]}, fh)

        result = self._run("state", ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("api", result.stdout)
        self.assertIn("web", result.stdout)

    def test_workspace_json_output_keys_each_member_by_name(self):
        ws = os.path.join(self.tmp.name, "ws-json")
        api = os.path.join(ws, "api")
        web = os.path.join(ws, "web")
        os.makedirs(os.path.join(ws, ".rein"))
        _init_git_repo(api)
        _commit(api, "tasks.md", TASKS_MD)
        _init_git_repo(web)
        _commit(web, "tasks.md", TASKS_MD)
        with open(os.path.join(ws, ".rein", "workspace.json"), "w", encoding="utf-8") as fh:
            json.dump({"members": [{"name": "api", "path": "api"}, {"name": "web", "path": "web"}]}, fh)

        result = self._run("state", ws, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(set(doc.keys()), {"api", "web"})



# ═══════════════════════════════════════ AC2 (cont.): loop.js emits `merged` ══

_MERGED_BLOCK_JS = r"""
const fs = require('fs');
const [, , loopPath, resultsJson] = process.argv;
const src = fs.readFileSync(loopPath, 'utf8');
// The arrow body, between `=> {` and the closing `}` of the const.
const m = src.match(/const mergedEventsBlock = \(taskResults, root\) => \{\n([\s\S]*?)\n\}\n/);
if (!m) throw new Error('mergedEventsBlock not found in loop.js');
const REIN = 'rein';
const CHANGE = '';
const fn = new Function('taskResults', 'root', 'REIN', 'CHANGE', m[1]);
const out = fn(JSON.parse(resultsJson), '/main/repo', REIN, CHANGE);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_NODE, "node not on PATH -- loop.js is a node workflow script")
class TestMergedIsActuallyEmitted(unittest.TestCase):
    """`merged` was in the enum, mapped to a Plane group, emitted by nothing.

    `events.TASK_TRANSITIONS`, `plane_sync.TRANSITION_GROUP` and
    `plane_projection`'s history ordering all carried `merged`, and no code
    path ever produced one -- a state the product could not reach, while the
    plan's Why names it outright ("not when it merged"). No criterion broke,
    because T002 AC2 enumerates started/verified/blocked only.

    Executed, not grepped: the same discipline `transitionsFor` established.
    """

    def _run(self, results):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_MERGED_BLOCK_JS)
            script = f.name
        try:
            proc = subprocess.run(
                [_NODE, script, LOOP_JS, json.dumps(results)],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            os.unlink(script)
        if proc.returncode != 0:
            self.fail(proc.stderr.strip())
        return json.loads(proc.stdout)

    def test_landed_tasks_each_get_a_merged_event(self):
        out = self._run([
            {"id": "T001", "status": "implemented"},
            {"id": "T002", "status": "implemented-proxy"},
        ])
        self.assertIn("event task T001 merged", out)
        self.assertIn("event task T002 merged", out)

    def test_it_targets_the_main_repo_never_the_worktree(self):
        """Integrate removes the worktree in the very next step."""
        out = self._run([{"id": "T001", "status": "implemented"}])
        self.assertIn("--root /main/repo", out)
        self.assertNotIn("rein-wt", out)

    def test_a_task_that_did_not_land_is_not_marked_merged(self):
        out = self._run([
            {"id": "T001", "status": "implemented"},
            {"id": "T002", "status": "blocked"},
        ])
        self.assertIn("T001", out)
        self.assertNotIn("T002", out)

    def test_nothing_landed_emits_no_block_at_all(self):
        self.assertEqual(self._run([{"id": "T001", "status": "blocked"}]), "")

    def test_the_integrate_prompt_actually_calls_it(self):
        """Defining the decision proves nothing about what Integrate does.

        Counted precisely rather than by `>1`: this is an arrow assigned to a
        const, so the DECLARATION reads `const mergedEventsBlock = (` and does
        not itself match `mergedEventsBlock(`. Every match is a real call.
        """
        with open(LOOP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("const mergedEventsBlock = (taskResults, root) =>", src)
        self.assertGreaterEqual(src.count("mergedEventsBlock(results"), 1)
        # And it is wired into the Integrate agent, not some other prompt.
        integrate = src[src.index("Integrate the APPROVED change into"):]
        self.assertIn("mergedEventsBlock(", integrate[:1200])


class TestAHeaderlessPlanAgreesAcrossAWorktree(unittest.TestCase):
    """The change axis needs the same normalisation the repo axis got.

    `read_plan` falls back to `basename(root)` when a flat `tasks.md` has no
    `# Change:` heading — and plan.py documents such a plan as valid. In a
    worktree that basename is `rein-wt-<label>`, a sibling directory, while
    the reader in the main repo computes the repo's own name. So the emitter
    wrote `change="wt-feature"`, the fold looked for `change="mainrepo"`, and
    every task read `planned` forever: the exact failure `canonical_repo`
    removed, moved one column over by the fix that added change filtering.

    Every other worktree fixture in this file writes `# Change: c`, which
    takes the header branch and never crosses this one — the fake avoiding
    the case it claims to cover.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "mainrepo")
        _init_git_repo(self.repo)
        # No `# Change:` line, on purpose.
        with open(os.path.join(self.repo, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write("- [ ] T001 do the thing\n  - Depends on: none\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "plan")
        self.events_path = os.path.join(self.tmp.name, "events.jsonl")
        self.wt = os.path.join(self.tmp.name, "wt-feature")
        _git(self.repo, "worktree", "add", "-q", "-b", "feature", self.wt)

    def test_emitter_and_reader_resolve_the_same_change(self):
        self.assertEqual(
            ev.resolve_change(self.wt), ev.resolve_change(self.repo),
            "a worktree and its main repo must name the same change",
        )

    def test_the_transition_is_visible_from_the_main_repo(self):
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        rec = ps.state(self.repo, events_path=self.events_path)
        self.assertEqual(rec["tasks"][0]["transition"], "verified")

    def test_merged_from_the_main_repo_lands_in_the_same_namespace(self):
        """`mergedEventsBlock` emits with --root <main repo> while started and
        verified emit with --root <worktree>. One task must not end up with
        two change namespaces inside one run."""
        ev.record_task_event("T001", "verified", root=self.wt, events_path=self.events_path)
        ev.record_task_event("T001", "merged", root=self.repo, events_path=self.events_path)
        rows = [r for r in ev.read_events(self.events_path) if r.get("kind") == "task"]
        self.assertEqual(len({r["change"] for r in rows}), 1, "two namespaces in one run")
        self.assertEqual(ps.state(self.repo, events_path=self.events_path)["tasks"][0]["transition"], "merged")


class TestStateEnumeratesEveryOpenspecChange(unittest.TestCase):
    """`rein state` is the standalone half of T001-T002, on the corpus the
    Why is written about: 24 repos, 118 openspec changes.

    Enumeration lived only in `plane_sync`, so `state` folded exactly one
    change per repo and printed `(no change) / (no tasks in the plan)` for
    every member of a real workspace. Every AC6 fixture used a flat
    `tasks.md`, where one change per repo happens to be the truth.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        _init_git_repo(self.repo)
        for name in ("alpha", "beta", "gamma"):
            d = os.path.join(self.repo, "openspec", "changes", name)
            os.makedirs(d)
            with open(os.path.join(d, "tasks.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Change: {name}\n\n- [ ] T001 task of {name}\n  - Depends on: none\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "three changes")

    def test_changes_for_lists_them_all(self):
        self.assertEqual(sorted(ps.changes_for(self.repo)), ["alpha", "beta", "gamma"])

    def test_a_flat_repo_still_resolves_one_implicit_change(self):
        flat = os.path.join(self.tmp.name, "flat")
        _init_git_repo(flat)
        with open(os.path.join(flat, "tasks.md"), "w", encoding="utf-8") as fh:
            fh.write("# Change: solo\n\n- [ ] T001 x\n")
        self.assertEqual(ps.changes_for(flat), [""])

    def test_state_all_folds_one_record_per_change(self):
        recs = ps.state_all(self.repo)
        self.assertEqual(sorted(r["change"] for r in recs), ["alpha", "beta", "gamma"])
        for r in recs:
            self.assertEqual(len(r["tasks"]), 1)

    def test_the_cli_prints_every_change_not_just_one(self):
        env = dict(os.environ)
        env["HOME"] = self.tmp.name
        out = subprocess.run([sys.executable, REIN_BIN, "state", self.repo],
                             capture_output=True, text=True, env=env, timeout=60).stdout
        for name in ("alpha", "beta", "gamma"):
            self.assertIn(name, out)
        self.assertNotIn("(no change)", out)


if __name__ == "__main__":
    unittest.main()
