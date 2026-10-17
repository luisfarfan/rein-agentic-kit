"""The pure decision core of `rein ship`.

`decide(state, event, cfg)` answers one question: given the ship state and one
thing that just happened, what must the driver do next? It returns a new state
and a list of actions. It performs no I/O, reads no clock (time arrives as
`Tick.now`), uses no randomness and never mutates the state it is given, so
every rule is testable with a hand-built `ShipState`.

The driver owns everything with a side effect or an identifier it must obtain
from Orca: dispatch ids, Orca task ids, worktree ids and gate ids. It writes
those into the state it gets back (`dispatch_id`, `orca_task_id`, `worktrees`,
`worktree_paths`, `gates[gate_id] = {"task_id", "kind", "message_id"}`) before
the next event.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from fnmatch import fnmatch

import quota
from ship_state import ShipState, TaskState

_DEFAULT_RISKY = ["**/auth/**", "**/payments/**", "**/migrations/**"]
_FAILURE_CAP = 2000
_BUSY = ("running", "gating", "gated")  # statuses that hold a repo's worktree
_FIX_PREFIX = "AUDIT"


# --- configuration ----------------------------------------------------------

@dataclass
class ShipConfig:
    claude_weekly_threshold: float = 60
    max_concurrent_workers: int = 3
    max_retries: int = 2
    max_audit_rounds: int = 2
    task_timeout_minutes: float = 45
    large_change_files: int = 25
    risky_paths: list = field(default_factory=lambda: list(_DEFAULT_RISKY))
    override: tuple | None = None

    @classmethod
    def from_flow_config(cls, cfg: dict) -> "ShipConfig":
        """Read the `ship` block of flow.config.json; absent keys keep their defaults."""
        block = cfg.get("ship") if isinstance(cfg, dict) else None
        if not isinstance(block, dict):
            block = {}
        names = {
            "claudeWeeklyThreshold": "claude_weekly_threshold",
            "maxConcurrentWorkers": "max_concurrent_workers",
            "maxRetries": "max_retries",
            "maxAuditRounds": "max_audit_rounds",
            "taskTimeoutMinutes": "task_timeout_minutes",
            "largeChangeFiles": "large_change_files",
            "riskyPaths": "risky_paths",
        }
        kwargs = {}
        for key, attr in names.items():
            value = block.get(key)
            if value is None:
                continue
            kwargs[attr] = list(value) if attr == "risky_paths" else value
        return cls(**kwargs)


# --- events -----------------------------------------------------------------

@dataclass
class Tick:
    now: float
    limits: dict


@dataclass
class WorkerDone:
    dispatch_id: str
    outcome: str


@dataclass
class GateVerdict:
    task_id: str
    exit_code: int
    reason: str


@dataclass
class Question:
    dispatch_id: str
    message_id: str
    text: str


@dataclass
class GateResolved:
    gate_id: str
    resolution: str


@dataclass
class ChangesComputed:
    changed_files: list


@dataclass
class AuditVerdict:
    verdict: str
    blocking: int
    changed_files: list
    findings: list = field(default_factory=list)


# --- actions ----------------------------------------------------------------

@dataclass
class Dispatch:
    task_id: str
    repo: str
    worktree: str  # "new-top-level" or "id:<worktree id>"
    agent: str
    model: str


@dataclass
class RunGate:
    task_id: str
    unit_only: bool


@dataclass
class CloseTask:
    task_id: str


@dataclass
class Release:
    dispatch_id: str


@dataclass
class OpenGate:
    task_id: str | None
    question: str
    options: list
    kind: str
    message_id: str = ""


@dataclass
class Relay:
    message_id: str
    body: str


@dataclass
class ComputeChanges:
    pass


@dataclass
class StartAudit:
    agent: str
    model: str


@dataclass
class Brake:
    reason: str


@dataclass
class Finish:
    pass


# --- audit model ------------------------------------------------------------

def _matches(path: str, pattern: str) -> bool:
    # "**/auth/**" must also catch a repo-relative "auth/x.py".
    return fnmatch(path, pattern) or fnmatch("/" + path, pattern)


def audit_model(changed_files: list, implementer_agents: list, cfg: ShipConfig) -> tuple:
    """Opus for risky or large changes; otherwise a provider that did not implement."""
    if len(changed_files) > cfg.large_change_files:
        return quota.OPUS
    if any(_matches(f, p) for f in changed_files for p in cfg.risky_paths):
        return quota.OPUS
    used = set(implementer_agents)
    if used == {"codex"}:
        return quota.SONNET
    if used == {"claude"}:
        return quota.CODEX
    return quota.SONNET


# --- helpers ----------------------------------------------------------------

def _implementer(state: ShipState, limits: dict, cfg: ShipConfig) -> tuple:
    if state.forced_implementer:
        return tuple(state.forced_implementer)
    return quota.choose_implementer(limits, cfg.claude_weekly_threshold, cfg.override)


def _worktree(state: ShipState, repo: str) -> str:
    wt = state.worktrees.get(repo)
    return "id:%s" % wt if wt else "new-top-level"


def _holder(state: ShipState, dispatch_id: str):
    if not dispatch_id:
        return None
    for task in state.tasks.values():
        if task.dispatch_id == dispatch_id:
            return task
    return None


def _is_fix(task: TaskState) -> bool:
    return task.id.startswith(_FIX_PREFIX)


def _deps_done(state: ShipState, task: TaskState) -> bool:
    return all(
        d in state.tasks and state.tasks[d].status == "done" for d in task.deps
    )


def _ready(state: ShipState, task: TaskState) -> bool:
    return task.status == "pending" and _deps_done(state, task)


def _all_done(state: ShipState) -> bool:
    return bool(state.tasks) and all(t.status == "done" for t in state.tasks.values())


def _record_agent(state: ShipState, agent: str) -> None:
    if agent not in state.implementer_agents:
        state.implementer_agents.append(agent)


def _open(state: ShipState, actions: list, task_id, question: str, options: list,
          kind: str, message_id: str = "") -> None:
    """Ask the owner. The driver creates the gate in Orca; until it has, the
    request waits in `state.pending_gates`, so a failed or interrupted
    gate-create is retried instead of lost."""
    actions.append(OpenGate(task_id, question, options, kind, message_id))
    state.pending_gates.append({"task_id": task_id, "question": question, "options": list(options),
                                "kind": kind, "message_id": message_id})


def _owner_gate_open(state: ShipState, kind: str) -> bool:
    return (any(g.get("kind") == kind and g.get("task_id") is None for g in state.gates.values())
            or any(g.get("kind") == kind and g.get("task_id") is None for g in state.pending_gates))


def _release_held(task: TaskState, actions: list) -> None:
    if task.dispatch_id:
        actions.append(Release(task.dispatch_id))
        task.dispatch_id = ""


def _settle(state: ShipState, actions: list) -> None:
    """Move the run forward once nothing more can happen for the tasks.

    Everything done starts the audit. A failed task blocks the audit: once
    nothing is in flight, nothing is ready and no gate is open, the run ends.
    """
    if state.phase not in ("running", "fixing"):
        return
    if _all_done(state):
        state.phase = "auditing"
        state.changed_files = []
        # The diff is I/O: the driver computes it and answers with ChangesComputed,
        # which picks the auditor (risky or large changes go to Opus).
        actions.append(ComputeChanges())
        return
    if not any(t.status == "failed" for t in state.tasks.values()):
        return
    if state.gates or state.pending_gates:
        return
    if any(t.status in _BUSY for t in state.tasks.values()):
        return
    if any(_ready(state, t) for t in state.tasks.values()):
        return
    state.phase = "done"
    actions.append(Finish())


# --- decide -----------------------------------------------------------------

def decide(state: ShipState, event, cfg: ShipConfig) -> tuple:
    """Fold one event into the state. Returns `(new_state, actions)`; the input is untouched."""
    state = copy.deepcopy(state)
    actions: list = []
    if isinstance(event, Tick):
        _on_tick(state, event, cfg, actions)
    elif isinstance(event, WorkerDone):
        _on_worker_done(state, event, cfg, actions)
    elif isinstance(event, GateVerdict):
        _on_gate_verdict(state, event, cfg, actions)
    elif isinstance(event, Question):
        _on_question(state, event, actions)
    elif isinstance(event, GateResolved):
        _on_gate_resolved(state, event, cfg, actions)
    elif isinstance(event, ChangesComputed):
        _on_changes_computed(state, event, cfg, actions)
    elif isinstance(event, AuditVerdict):
        _on_audit_verdict(state, event, cfg, actions)
    return state, actions


def _audit_clock(state: ShipState, event: Tick, cfg: ShipConfig, actions: list) -> None:
    if not state.audit_dispatch:
        return
    if state.audit_started_at == 0.0:
        state.audit_started_at = event.now
        return
    if (event.now - state.audit_started_at > cfg.task_timeout_minutes * 60
            and not _owner_gate_open(state, "audit_timeout")
            and not _owner_gate_open(state, "question")):
        _open(state, actions, None,
              "The audit has run for more than %g minutes. Retry it, or skip it?" % cfg.task_timeout_minutes,
              ["retry", "skip"], "audit_timeout")


def _on_tick(state: ShipState, event: Tick, cfg: ShipConfig, actions: list) -> None:
    if state.phase == "auditing":
        _audit_clock(state, event, cfg, actions)
        return
    if state.phase not in ("running", "fixing"):
        return
    now = event.now

    # A task re-dispatched by a verdict has no clock yet; start it now.
    for task in state.tasks.values():
        if task.status == "running" and task.started_at == 0.0:
            task.started_at = now

    for task in state.tasks.values():
        if task.status == "running" and now - task.started_at > cfg.task_timeout_minutes * 60:
            task.status = "gated"
            _open(state, actions, task.id,
                  "Task %s has run for more than %g minutes. Retry it, or skip it?"
                  % (task.id, cfg.task_timeout_minutes),
                  ["retry", "skip"], "timeout")

    for task in state.tasks.values():
        if task.human_review and _ready(state, task):
            task.status = "gated"
            _open(state, actions, task.id,
                  "Task %s needs human review before it can be closed." % task.id,
                  ["approve", "reject"], "human_review")

    settled = len(actions)
    _settle(state, actions)
    if len(actions) > settled or state.phase == "done":
        return

    active = [t for t in state.tasks.values() if t.status in _BUSY and not t.human_review]
    busy_repos = {t.repo for t in active}
    slots = cfg.max_concurrent_workers - len(active)
    batch = []
    for task in state.tasks.values():
        if slots <= 0:
            break
        if task.human_review or not _ready(state, task) or task.repo in busy_repos:
            continue
        batch.append(task)
        busy_repos.add(task.repo)
        slots -= 1
    if not batch:
        return

    agent, model = _implementer(state, event.limits, cfg)
    reason = quota.brake_reason(event.limits, agent)
    if reason:
        state.phase = "braked"
        actions.append(Brake(reason))
        _open(state, actions, None, reason, ["switch-to-codex", "pause"], "brake")
        return

    for task in batch:
        actions.append(Dispatch(task.id, task.repo, _worktree(state, task.repo), agent, model))
        task.status = "running"
        task.agent, task.model = agent, model
        task.started_at = now
        _record_agent(state, agent)


def _red(state: ShipState, task: TaskState, reason: str, cfg: ShipConfig, actions: list) -> None:
    task.last_failure = reason[:_FAILURE_CAP]
    if task.attempts < cfg.max_retries:
        _release_held(task, actions)
        task.attempts += 1
        task.status = "running"
        task.started_at = 0.0  # stamped by the next Tick
        actions.append(Dispatch(task.id, task.repo, _worktree(state, task.repo), task.agent, task.model))
        return
    task.status = "failed"
    _open(state, actions, task.id,
          "Task %s failed after %d retries: %s" % (task.id, task.attempts, task.last_failure[:300]),
          ["retry", "skip"], "retry_exhausted")


def _on_worker_done(state: ShipState, event: WorkerDone, cfg: ShipConfig, actions: list) -> None:
    task = _holder(state, event.dispatch_id)
    if task is None or task.status != "running":
        return
    if event.outcome == "succeeded":
        task.status = "gating"
        actions.append(RunGate(task.id, task.type != "verification"))
    else:
        _red(state, task, "worker %s" % (event.outcome or "failed"), cfg, actions)


def _on_gate_verdict(state: ShipState, event: GateVerdict, cfg: ShipConfig, actions: list) -> None:
    task = state.tasks.get(event.task_id)
    if task is None or task.status != "gating":
        return
    if event.exit_code == 0:
        if not _is_fix(task):  # synthetic fix tasks have no checkbox in the plan
            actions.append(CloseTask(task.id))
        _release_held(task, actions)
        task.status = "done"
        _settle(state, actions)
    elif event.exit_code == 126:
        task.status = "gated"
        _open(state, actions, task.id,
              "The check for task %s could not run: %s" % (task.id, event.reason[:300]),
              ["retry", "skip"], "setup")
    else:
        _red(state, task, event.reason, cfg, actions)


def _on_question(state: ShipState, event: Question, actions: list) -> None:
    if event.dispatch_id and event.dispatch_id == state.audit_dispatch:
        if not _owner_gate_open(state, "question"):  # the auditor asks; the owner answers
            _open(state, actions, None, event.text, [], "question", event.message_id)
        return
    task = _holder(state, event.dispatch_id)
    if task is None or task.status != "running":
        return
    if any(g.get("kind") == "question" and g.get("task_id") == task.id
           for g in list(state.gates.values()) + state.pending_gates):
        return
    task.status = "gated"
    _open(state, actions, task.id, event.text, [], "question", event.message_id)


_RESOLUTIONS = {
    "human_review": ("approve", "reject"),
    "retry_exhausted": ("retry", "skip"),
    "setup": ("retry", "skip"),
    "timeout": ("retry", "skip"),
    "brake": ("switch-to-codex", "pause"),
    "audit_exhausted": ("accept", "reject"),
    "audit_timeout": ("retry", "skip"),
    "audit_failed": ("retry-audit", "accept", "reject"),
}


def _on_gate_resolved(state: ShipState, event: GateResolved, cfg: ShipConfig, actions: list) -> None:
    gate = state.gates.get(event.gate_id)
    if gate is None:
        return
    kind, resolution = gate.get("kind"), event.resolution
    allowed = _RESOLUTIONS.get(kind)
    if allowed is not None and resolution not in allowed:
        return
    if allowed is None and kind != "question":
        return
    del state.gates[event.gate_id]
    task = state.tasks.get(gate.get("task_id"))

    if kind == "question":
        actions.append(Relay(gate.get("message_id", ""), resolution))
        if task is not None and task.status == "gated":
            task.status = "running"
            task.started_at = 0.0  # time spent waiting on the human does not count
        if task is None and state.audit_dispatch:
            state.audit_started_at = 0.0  # likewise for the auditor
    elif kind == "human_review":
        if task is not None:
            if resolution == "approve":
                actions.append(CloseTask(task.id))
                task.status = "done"
            else:
                task.status = "failed"
        _settle(state, actions)
    elif kind in ("retry_exhausted", "setup", "timeout"):
        if task is not None:
            _release_held(task, actions)
            if resolution == "retry":
                task.status, task.attempts = "pending", 0
            else:
                task.status = "failed"
        _settle(state, actions)
    elif kind == "brake":
        if resolution == "switch-to-codex":
            state.forced_implementer = list(quota.CODEX)
        state.phase = "running"
        if resolution == "pause":
            actions.append(Finish())
    elif kind == "audit_exhausted":
        state.phase = "done"
        actions.append(Finish())
    elif kind in ("audit_timeout", "audit_failed") and state.phase != "auditing":
        pass  # the audit moved on while the owner was deciding
    elif kind == "audit_timeout":
        _drop_audit(state, actions)
        if resolution == "retry":
            actions.append(ComputeChanges())
        else:
            _end_audit(state, actions, "skipped")
    elif kind == "audit_failed":
        if resolution == "retry-audit":
            _drop_audit(state, actions)
            actions.append(ComputeChanges())
        else:
            _end_audit(state, actions, "approved" if resolution == "accept" else "rejected")


def _drop_audit(state: ShipState, actions: list) -> None:
    """Forget the running audit worker (releasing it) so a new one can start."""
    if state.audit_dispatch:
        actions.append(Release(state.audit_dispatch))
    state.audit_dispatch, state.audit_started_at = "", 0.0
    state.changed_files = []


def _end_audit(state: ShipState, actions: list, result: str) -> None:
    state.audit_result = result
    state.phase = "done"
    actions.append(Finish())


def _in_repo(state: ShipState, repo: str, path: str) -> bool:
    root = state.worktree_paths.get(repo)
    prefixes = [repo.rstrip("/") + "/"]
    if root:
        prefixes.append(root.rstrip("/") + "/")
    return any(path.startswith(p) for p in prefixes)


def _fix_repos(state: ShipState, changed_files: list) -> list:
    repos = []
    for task in state.tasks.values():
        if task.repo not in repos:
            repos.append(task.repo)
    touched = [r for r in repos if any(_in_repo(state, r, f) for f in changed_files)]
    return touched or repos


def _on_changes_computed(state: ShipState, event: ChangesComputed, cfg: ShipConfig, actions: list) -> None:
    if state.phase != "auditing":
        return
    state.changed_files = list(event.changed_files)
    actions.append(StartAudit(*audit_model(state.changed_files, state.implementer_agents, cfg)))


def _on_audit_verdict(state: ShipState, event: AuditVerdict, cfg: ShipConfig, actions: list) -> None:
    if state.phase != "auditing":
        return
    state.audit_started_at = 0.0
    if event.verdict == "NO_VERDICT":
        # The auditor produced nothing usable. That is not the implementer's
        # fault, so no fix round: the owner decides.
        _open(state, actions, None,
              "The audit produced no usable verdict: %s" % "; ".join(event.findings)[:400],
              ["retry-audit", "accept", "reject"], "audit_failed")
        return
    approved = event.verdict == "APPROVED" or (
        event.verdict == "CHANGES_REQUESTED" and event.blocking <= 0
    )
    if approved:
        _end_audit(state, actions, "approved")
        return
    if state.audit_rounds >= cfg.max_audit_rounds:
        summary = "\n".join(event.findings)[:600]
        _open(state, actions, None,
              "The audit still has %d blocking finding(s) after %d fix round(s). %s"
              % (event.blocking, state.audit_rounds, summary),
              ["accept", "reject"], "audit_exhausted")
        state.audit_result = "rejected"
        actions.append(Finish())
        return
    state.audit_rounds += 1
    findings = "\n".join(event.findings)[:_FAILURE_CAP]
    for repo in _fix_repos(state, event.changed_files):
        fix_id = "%s%d-%s" % (_FIX_PREFIX, state.audit_rounds, repo)
        state.tasks[fix_id] = TaskState(
            id=fix_id, repo=repo, deps=[], human_review=False, verification="",
            type="implementation", last_failure=findings,
        )
    state.phase = "fixing"
