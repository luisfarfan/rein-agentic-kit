"""The resumable record of one `rein ship` run.

Pure data plus load/save: no Orca calls, no decisions. The driver saves after
every mutating call, so a re-run of `rein ship <change>` can continue from
exactly where an interrupted one stopped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Optional


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
    # Worker starts attempted on this plan task. Every start consumes an Orca task,
    # so a re-dispatch creates a fresh one rather than reusing a spent task.
    dispatches: int = 0
    # The Orca task ids this task's Orca task was created depending on.
    orca_deps: list = field(default_factory=list)
    # The dispatch id whose stop Orca could not confirm; the owner's answer to that gate fences it.
    stop_unconfirmed: str = ""
    # An audit fix task's findings. Kept apart from `last_failure`, which a failed retry overwrites.
    audit_findings: str = ""
    # The Orca task a worker-start was sent for and not yet answered: a kill or a lost answer
    # leaves it set, and the next dispatch or resume looks for that worker before starting another.
    starting: str = ""
    # When the current worker was launched (0.0 = the next Tick stamps it), whether it has shown a
    # sign of work since (a worktree change or a question), and its worktree's HEAD at launch.
    # A worker with no sign for ship.stallMinutes opens a "stalled" gate.
    launched_at: float = 0.0
    active: bool = False
    launch_head: str = ""
    # The dispatch whose "stalled" gate the owner answered: Orca's "parked on a prompt" evidence no
    # longer re-asks at once for it, only the stall window does.
    stall_answered: str = ""
    # guard.outside_snapshot of the other checkouts Orca knows, taken when the worker was launched:
    # what changed there by the time its task ends was written outside its worktree.
    outside_before: dict = field(default_factory=dict)
    # The `gate.outsideIgnore` globs of this task's repo and of the plan root as they were when the worker
    # was launched: the ignores the auditor's look at this task's worktree uses, never a config reread.
    own_ignore: list = field(default_factory=list)
    # The repo-relative files this task's attempts changed (each gate adds its attempt's): an audit
    # or gate fix task reuses the Verification of the tasks that touched the files it must fix.
    touched: list = field(default_factory=list)
    # What Orca aborted this task's start on (an agent's startup dialog, as Orca names it); "" when
    # nothing. While set, Rein probes with a fresh start now and then instead of asking again.
    # The launch snapshot of the other checkouts kept while an external write of this task is
    # rejected or unresolved: every retry is judged against it, so a new snapshot never makes that
    # write the baseline. Cleared when the task closes clean or the owner accepts it.
    outside_held: dict = field(default_factory=dict)
    blocked_dialog: str = ""
    # The terminal Rein opened for the owner with the agent in the worktree, to answer that dialog.
    unblock_terminal: str = ""
    # Starts Orca aborted on that dialog (spaces the probes out).
    probes: int = 0


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
    # The agents Orca actually launched for implementation workers (it may swap the requested one).
    launched_agents: list = field(default_factory=list)
    # `ship.launchProfile: lean`: `{worktree path: launch_profile record}` of every worktree Rein put
    # the lean settings in, so it can put the owner's file back before the audit and at the end.
    launch_profiles: dict = field(default_factory=dict)
    forced_implementer: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)
    # Stamped by the first Tick after StartAudit; 0.0 = not started / reset.
    audit_started_at: float = 0.0
    # Newest review episode present when the audit started; a verdict is only
    # accepted from a different (newer) one, so a resume cannot take a stale one.
    audit_baseline: str = ""
    # "", "approved", "rejected", "skipped" or "unapproved" (a task failed, so no audit could approve).
    audit_result: str = ""
    # Owner gates decide asked for that the driver has not yet created in Orca.
    pending_gates: list = field(default_factory=list)
    # The audit worktree: where the auditor records its verdict and where the
    # driver reads it. Never the plan repo's primary checkout.
    audit_root: str = ""
    # The guard's look at the worktrees and the other checkouts as the auditor started (see
    # Ship.audit_guard_snapshot). `audit_guard_held` keeps the baseline of an audit the guard
    # flagged, so a re-audit does not absorb the infraction into its "before".
    audit_guard_before: dict = field(default_factory=dict)
    audit_guard_held: dict = field(default_factory=dict)
    # The verdict an audit_guard gate holds: what "accept" turns back into.
    pending_verdict: dict = field(default_factory=dict)
    # The first quota snapshot of the run (`{provider: {window: pct|None}}`); usage deltas are measured from it.
    quota_before: dict = field(default_factory=dict)
    # Set by the brake's "pause": dispatch nothing new, exit once running workers settle.
    paused: bool = False
    # Audits the owner granted beyond maxAuditRounds (the "fix" answer of an exhausted audit).
    extra_audit_rounds: int = 0
    # The verdict that opened an audit_exhausted gate: what a "fix" answer turns into fix tasks.
    pending_findings: list = field(default_factory=list)
    pending_changed_files: list = field(default_factory=list)
    # The BLOCKING findings the latest fix round was started for; the re-audit verifies only these.
    previous_blocking: list = field(default_factory=list)
    # `{repo: HEAD sha}` of each worktree when the latest fix round started; the re-audit lists the commits since.
    fix_round_base: dict = field(default_factory=dict)
    # The Orca task an audit worker-start was sent for and not yet answered (see TaskState.starting).
    audit_starting: str = ""
    # Reviewer agents Orca refused to start for the audit; a retry picks another one.
    audit_excluded: list = field(default_factory=list)
    # The pid of the `rein ship` process that last ran this change: exact liveness for `rein watch`.
    driver_pid: int = 0
    # The full gate of each repo before an audit (phase "verifying"): "running", "passed",
    # "failed" (a GATE fix task is on it), "setup" (the owner is asked) or "skipped" (by the owner).
    repo_gates: dict = field(default_factory=dict)
    # Red full gates per repo in this run: past maxRetries the owner decides.
    repo_gate_fails: dict = field(default_factory=dict)
    # Every gate the owner resolved: `{gate, kind, task_id, question, answer}`. The auditor reads
    # them as binding, so it never asks the owner again what they already decided.
    owner_decisions: list = field(default_factory=list)


def state_path(repo_root: str, change: str) -> str:
    """One file per change: `<root>/.rein/ship/<change>.json`.

    A second change shipped from the same plan repo must never pick up (or
    overwrite) the first one's Run.
    """
    name = re.sub(r"[^A-Za-z0-9._-]", "_", change or "")
    if name in ("", ".", ".."):
        name = "_" + name
    return os.path.join(repo_root, ".rein", "ship", name + ".json")


# -- the owner's answers: `rein answer` from a terminal Orca fences queues them here ------------

def answers_dir(repo_root: str, change: str) -> str:
    """`<root>/.rein/ship/<change>.answers/`, next to the state file."""
    return state_path(repo_root, change)[: -len(".json")] + ".answers"


def _answer_file(repo_root: str, change: str, gate_id: str) -> str:
    return os.path.join(answers_dir(repo_root, change), re.sub(r"[^A-Za-z0-9._-]", "_", gate_id) + ".json")


def write_answer(repo_root: str, change: str, gate_id: str, resolution: str, now: float) -> str:
    path = _answer_file(repo_root, change, gate_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".answer.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"gate": gate_id, "resolution": resolution, "at": now}, fh)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_answers(repo_root: str, change: str) -> list:
    """Every well-formed queued answer; anything else is left alone for the owner."""
    directory = answers_dir(repo_root, change)
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError:
        return []
    found = []
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("gate"), str) and data["gate"] \
                and isinstance(data.get("resolution"), str):
            found.append(data)
    return found


def drop_answer(repo_root: str, change: str, gate_id: str) -> None:
    try:
        os.unlink(_answer_file(repo_root, change, gate_id))
    except OSError:
        pass


def _task_state(task: dict, default_repo: str) -> TaskState:
    return TaskState(
        id=task["id"],
        repo=task.get("repo") or default_repo,
        deps=list(task.get("dependsOn") or []),
        human_review=bool(task.get("humanReview")),
        verification=task.get("verificationRaw") or task.get("verification") or "",
        type=task.get("type") or "",
        status="done" if task.get("checked") else "pending",
    )


def from_plan(plan: dict, default_repo: str, change: str = "") -> ShipState:
    """`change` is the name the owner asked to ship; it wins over the plan's own header."""
    state = ShipState(change=change or plan.get("change") or "", plan_path=plan.get("path") or "")
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
