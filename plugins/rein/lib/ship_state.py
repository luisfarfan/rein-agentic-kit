"""The resumable record of one `rein ship` run.

Pure data plus load/save: no Orca calls, no decisions. The driver saves after
every mutating call, so a re-run of `rein ship <change>` can continue from
exactly where an interrupted one stopped.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

TASK_STATUSES = ("pending", "running", "gating", "gated", "done", "failed")
PHASES = ("running", "auditing", "fixing", "braked", "done")


class ShipStateCorrupt(Exception):
    """An existing ship-state file could not be read; it was moved aside, not discarded."""


@dataclass
class TaskState:
    id: str
    repo: str
    deps: list
    human_review: bool
    verification: str
    type: str
    status: str = "pending"
    attempts: int = 0
    orca_task_id: str = ""
    dispatch_id: str = ""
    agent: str = ""
    model: str = ""
    started_at: float = 0.0
    last_failure: str = ""


@dataclass
class ShipState:
    change: str
    plan_path: str
    run_id: str = ""
    tasks: dict = field(default_factory=dict)
    worktrees: dict = field(default_factory=dict)
    worktree_paths: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    phase: str = "running"
    audit_rounds: int = 0
    audit_dispatch: str = ""
    implementer_agents: list = field(default_factory=list)
    forced_implementer: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)


def state_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".rein", "ship-state.json")


def _task_state(task: dict, default_repo: str) -> TaskState:
    return TaskState(
        id=task["id"],
        repo=task.get("repo") or default_repo,
        deps=list(task.get("dependsOn") or []),
        human_review=bool(task.get("humanReview")),
        verification=task.get("verification") or "",
        type=task.get("type") or "",
        status="done" if task.get("checked") else "pending",
    )


def from_plan(plan: dict, default_repo: str) -> ShipState:
    state = ShipState(change=plan.get("change") or "", plan_path=plan.get("path") or "")
    for task in plan.get("tasks") or []:
        state.tasks[task["id"]] = _task_state(task, default_repo)
    return state


def reconcile(state: ShipState, plan: dict, default_repo: str) -> ShipState:
    """Fold the current plan into a saved state, in place, and return it.

    Existing tasks keep their runtime fields (status, dispatch, attempts); a
    task now ticked in the plan becomes `done`; new tasks are added `pending`
    (or `done` when already ticked). Tasks that vanished from the plan are
    kept: their Orca work already exists. Plan-derived fields of tasks not yet
    started are refreshed so an edited plan takes effect.
    """
    state.plan_path = plan.get("path") or state.plan_path
    for task in plan.get("tasks") or []:
        existing = state.tasks.get(task["id"])
        if existing is None:
            state.tasks[task["id"]] = _task_state(task, default_repo)
            continue
        if task.get("checked"):
            existing.status = "done"
        elif existing.status == "pending":
            fresh = _task_state(task, default_repo)
            existing.repo = fresh.repo
            existing.deps = fresh.deps
            existing.human_review = fresh.human_review
            existing.verification = fresh.verification
            existing.type = fresh.type
    return state


def save(state: ShipState, path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ship-state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(state), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def load(path: str) -> Optional[ShipState]:
    """The saved state; None only when no file exists.

    An existing file that cannot be parsed is quarantined next to itself as
    `<name>.corrupt-<epoch>` and `ShipStateCorrupt` is raised: silently starting
    fresh would create a second Run and orphan live workers.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        tasks = {
            tid: TaskState(**_known(TaskState, t))
            for tid, t in (data.get("tasks") or {}).items()
        }
        data = _known(ShipState, {**data, "tasks": tasks})
        return ShipState(**data)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        quarantined = f"{path}.corrupt-{int(time.time())}"
        try:
            os.replace(path, quarantined)
        except OSError:
            quarantined = path
        raise ShipStateCorrupt(
            f"ship state unreadable ({exc}); kept at {quarantined}"
        ) from exc
