#!/usr/bin/env python3
"""Product state: what actually happened, folded from the event log -- not
reconstructed by guessing at a checkbox (T002).

`rein event task <id> <transition>` (plugins/rein/lib/events.py) appends one
line per transition, WHILE it happens. `state(root)` is the read side: it
folds those events over the plan's own task list (so a task that never
emitted anything still appears, as `planned` -- AC4) and reports, per task,
the transition it last reached, when, and the commit that was HEAD at that
moment (captured by `record_task_event` itself, not re-derived here).

Folding is LAST-WRITE-WINS BY TIMESTAMP, not by file order: `events.jsonl`
is append-only from possibly-concurrent sessions, so a later write can land
earlier in the file. Comparing ISO-8601 UTC timestamps (all the same
`isoformat(timespec="seconds")` shape `record_task_event` writes) sorts
correctly as plain strings. A tie keeps whichever row was iterated LAST, so
the result never depends on dict/set iteration order across two calls with
the same input -- the whole point of a fold that must be stable (AC3).
"""

from __future__ import annotations

import os
import subprocess
import time

import events as _events
import plan as _plan

PLANNED = "planned"

GIT_TIMEOUT = 15


def _fold_task_events(rows: list[dict]) -> dict[str, dict]:
    """The latest event per `task_id`, last-write-wins by `ts` (see module
    docstring). Rows without a `task_id` (skill-invocation events sharing
    the same log) are ignored.
    """
    latest: dict[str, dict] = {}
    for row in rows:
        task_id = row.get("task_id")
        if not task_id:
            continue
        ts = row.get("ts") or ""
        prev = latest.get(task_id)
        if prev is None or ts >= (prev.get("ts") or ""):
            latest[task_id] = row
    return latest


def _newest_commit_epoch(repo: str, path: str) -> int | None:
    """The author-date (unix seconds) of the newest commit touching `path`
    inside `repo`, or `None` when it cannot be determined -- no `git`
    binary, `path` never committed, or any other failure. Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "log", "-1", "--format=%ct", "--", path],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def change_age_days(repo: str, change_dir: str, now: float | None = None) -> float | None:
    """AC5: how long since the change directory was last touched, in days --
    taken from the NEWEST commit that touched it, never from `mtime` (which
    a checkout, a rebase, or a mere `touch` can rewrite without a commit
    behind it). `None` when no such commit can be found (a plan not yet
    committed, or `repo` is not a git repository at all).
    """
    epoch = _newest_commit_epoch(repo, change_dir)
    if epoch is None:
        return None
    reference = time.time() if now is None else now
    return (reference - epoch) / 86400.0


def state(root: str = ".", change: str = "", events_path: str = _events.EVENTS_PATH) -> dict:
    """One record per task in the plan at `root`, folded from the task-event
    log plus the plan's own task list plus (AC5) git history of the change
    directory.

    A task with no recorded transition still appears, as `planned` (AC4) --
    an unstarted task must never simply be absent, or a fold of a plan with
    N tasks and zero events would report the product as 0 tasks in flight
    instead of N tasks not yet begun.

    `events_path` defaults to the real, shared `~/.claude/rein/events.jsonl`
    -- overridable so a test can fold over a fixture log instead of the
    operator's real one.
    """
    resolved_root = os.path.realpath(os.path.abspath(root))
    plan_doc = _plan.read_plan(resolved_root, change=change)
    tasks = plan_doc.get("tasks") or []

    # The SAME identity the emitter used. A plain path comparison made every
    # transition emitted from a worktree invisible here, and the loop runs
    # every task in a worktree by default -- see `events.canonical_repo`.
    repo_key = _events.canonical_repo(resolved_root)

    all_rows = _events.read_events(events_path)
    task_rows = [r for r in all_rows if r.get("kind") == "task" and r.get("repo") == repo_key]
    latest = _fold_task_events(task_rows)

    records = []
    for t in tasks:
        task_id = t.get("id", "")
        ev = latest.get(task_id)
        records.append({
            "taskId": task_id,
            "title": t.get("title", ""),
            "checked": bool(t.get("checked")),
            "transition": ev.get("transition") if ev else PLANNED,
            "when": (ev.get("ts") if ev else "") or "",
            "commit": (ev.get("commit") if ev else "") or "",
            # Carried so the projection can HASH it: the work item's body is
            # built from this, and change-detection that ignores a field it
            # writes leaves the board stale forever.
            "dependsOn": list(t.get("dependsOn") or []),
        })

    plan_path = plan_doc.get("path") or ""
    change_dir = os.path.dirname(plan_path) if plan_path else resolved_root

    return {
        "root": resolved_root,
        "change": plan_doc.get("change", ""),
        "planPath": plan_path,
        "planExists": bool(plan_doc.get("exists")),
        "tasks": records,
        "lastTouchedDays": change_age_days(resolved_root, change_dir),
    }
