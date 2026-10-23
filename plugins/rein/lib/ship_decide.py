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
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch

import quota
from ship_state import ShipState, TaskState

_DEFAULT_RISKY = ["**/auth/**", "**/payments/**", "**/migrations/**"]
GATE_FAILURE_CAP = 6000  # a failed gate's detail: the real error lines, not a 2000-char tail
QUESTION_DETAIL = 1200   # how much of a failure an owner question quotes (Orca takes 2000)
_BUSY = ("running", "gating", "gated")  # statuses that hold a repo's worktree
_FIX_PREFIX = "AUDIT"
_GATE_FIX_PREFIX = "GATE"  # a fix task for a repo whose full gate was red before the audit
# Task-less owner gates that hold the audit: no audit may start behind one.
AUDIT_HOLDS = ("audit_failed", "audit_timeout", "audit_exhausted", "setup")


# --- configuration ----------------------------------------------------------

def _positive_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value and 0 < value < float("inf"))


@dataclass
class ShipConfig:
    claude_weekly_threshold: float = 60
    max_concurrent_workers: int = 3
    max_retries: int = 2
    max_audit_rounds: int = 2
    task_timeout_minutes: float = 45
    gate_timeout_seconds: float = 900.0
    large_change_files: int = 25
    stall_minutes: float = 10  # no sign of work this long after a launch asks the owner; 0 = never
    # Seconds before the first fresh start that probes whether the owner has answered the dialog Orca
    # aborted a launch on; each further probe waits twice as long, up to 16 times this.
    trust_probe_seconds: float = 30
    # Where a human_review gate's review artifact goes, relative to the plan root ("" = the plan's own
    # change folder: openspec/changes/<change>/, else .rein/review/<change>/).
    review_dir: str = ""
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
            "gateTimeoutSeconds": "gate_timeout_seconds",
            "largeChangeFiles": "large_change_files",
            "stallMinutes": "stall_minutes",
            "trustProbeSeconds": "trust_probe_seconds",
            "reviewDir": "review_dir",
            "riskyPaths": "risky_paths",
        }
        kwargs = {}
        for key, attr in names.items():
            value = block.get(key)
            if value is None:
                continue
            if attr == "gate_timeout_seconds" and not _positive_number(value):
                continue  # a bad value keeps the default rather than crashing a run
            if attr == "stall_minutes" and not (value == 0 or _positive_number(value)):
                continue
            if attr == "trust_probe_seconds" and not _positive_number(value):
                continue
            if attr == "review_dir" and not (isinstance(value, str) and value.strip()):
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
    # What the worker's terminal shows it was waiting for when it failed (a startup dialog only a
    # person can answer, startup_dialog.py); "" when nothing was recognised.
    dialog: str = ""


@dataclass
class GateVerdict:
    task_id: str
    exit_code: int
    reason: str
    # The worker attempt the gate judged: a verdict for an earlier attempt (its gate ran on after
    # a timeout gate and a retry) must not judge the new one. "" = unknown, accepted.
    dispatch_id: str = ""
    # The repo-relative files the judged attempt changed (since its launch), whatever the verdict.
    touched: list = field(default_factory=list)


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
class RepoGateVerdict:
    """The full gate of one repo (every gate slot, the whole suite) before the audit."""
    repo: str
    exit_code: int
    reason: str


@dataclass
class WorkerActivity:
    """What the driver saw of a running worker that has not shown a sign of work yet: `active` when
    its worktree changed; `waiting` is Orca's evidence that it is parked on a prompt only a person
    can answer (a trust or hooks-review dialog), "" when there is none."""
    dispatch_id: str
    active: bool
    waiting: str = ""
    # A startup dialog recognised on the worker's terminal (startup_dialog.py), in the owner's words.
    dialog: str = ""


@dataclass
class ChangesComputed:
    changed_files: list
    limits: dict = field(default_factory=dict)  # live quota, so the audit avoids a braked reviewer


@dataclass
class WorkerStopped:
    """The driver asked Orca to stop a worker and looked again.

    `confirmed` is true only when Orca reports the worker settled: a stop that
    was merely accepted is not proof that the worker exited.
    """
    task_id: str
    resolution: str
    confirmed: bool
    detail: str = ""


@dataclass
class ChangesFailed:
    """The driver could not list the changed files (a git failure), so nothing may be audited."""
    error: str


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
class RunRepoGate:
    """Run the full gate of `repo`'s worktree and answer with RepoGateVerdict."""
    repo: str


@dataclass
class CloseTask:
    task_id: str


@dataclass
class Release:
    dispatch_id: str


@dataclass
class OpenGate:
    """Informational: decide asked for an owner gate.

    The durable channel is `state.pending_gates`, which `decide` fills in the
    same step; the driver creates each queued request in Orca and retries it
    until it exists. Executing this action does nothing.
    """
    task_id: str | None
    question: str
    options: list
    kind: str
    message_id: str = ""


@dataclass
class StopWorker:
    """Stop the task's worker and report back with `WorkerStopped(task_id, resolution, ...)`."""
    task_id: str
    dispatch_id: str
    resolution: str


@dataclass
class AbandonWorker:
    """Fence a worker whose exit Rein could not confirm, after the owner said they checked."""
    task_id: str
    dispatch_id: str


@dataclass
class ResendBrief:
    """Type the task's brief into its worker's terminal again (a startup dialog swallowed it)."""
    task_id: str
    dispatch_id: str


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


def touches_risky(files: list, patterns: list) -> bool:
    """True when any of `files` matches any of the risky `patterns`."""
    return any(_matches(f, p) for f in files for p in patterns)


def _claude_usable(limits: dict) -> bool:
    """Claude may review: its usage is under the brake and, when it was read at all, known.

    Unknown usage (Orca could not say) never routes work onto Claude. A caller that
    passes no limits at all gets the routing without any quota check."""
    if "claude" in limits and (limits.get("claude") or {}).get("weekly") is None:
        return False
    return not quota.brake_reason(limits, "claude")


def audit_model(changed_files: list, implementer_agents: list, cfg: ShipConfig,
                limits: dict | None = None, reaudit: bool = False, exclude=()) -> tuple:
    """Opus for risky or large changes; otherwise a reviewer under the brake, preferring
    a provider that did not implement. A re-audit (after a fix round) is never Opus: it
    only verifies the earlier findings. Opus obeys the brake like any reviewer, and Claude is
    never picked on unknown usage. When every reviewer is braked, the first one, so the
    caller can see the brake and ask the owner. `("", "")` when Orca refused every reviewer."""
    limits = limits or {}
    claude = _claude_usable(limits) and "claude" not in exclude
    if not reaudit and claude:
        if len(changed_files) > cfg.large_change_files:
            return quota.OPUS
        if touches_risky(changed_files, cfg.risky_paths):
            return quota.OPUS
    pool = [m for m in (quota.SONNET, quota.CODEX) if m[0] != "claude" or claude]
    used = set(implementer_agents)
    if (limits.get("antigravity") or {}).get("weekly") is not None and quota.ANTIGRAVITY[0] not in used:
        pool.append(quota.ANTIGRAVITY)  # a last resort, never reviewing its own work
    pool = [m for m in pool if m[0] not in exclude]  # Orca refused to start these
    if not pool:
        return "", ""
    fresh = [m for m in pool if m[0] not in used]
    choices = fresh + [m for m in pool if m not in fresh]
    return quota.first_unbraked(choices, limits)


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


def _is_synthetic(task: TaskState) -> bool:
    """A task Rein made (an audit or full-gate fix): it has no checkbox in the plan."""
    return task.id.startswith((_FIX_PREFIX, _GATE_FIX_PREFIX))


def is_gate_fix(task: TaskState) -> bool:
    return task.id.startswith(_GATE_FIX_PREFIX)


def skipped_fix_tasks(state: ShipState) -> list:
    """Audit-fix tasks the owner skipped (a fix task only fails through the owner's skip)."""
    return [t for t in state.tasks.values() if _is_fix(t) and t.status == "failed"]


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
          kind: str, message_id: str = "", audit: str = "") -> None:
    """Ask the owner. The driver creates the gate in Orca; until it has, the
    request waits in `state.pending_gates`, so a failed or interrupted
    gate-create is retried instead of lost."""
    actions.append(OpenGate(task_id, question, options, kind, message_id))
    state.pending_gates.append({"task_id": task_id, "question": question, "options": list(options),
                                "kind": kind, "message_id": message_id, "audit": audit})


def _owner_gate_open(state: ShipState, *kinds: str) -> bool:
    """A task-less gate of one of these kinds is open in Orca or queued for it."""
    return any(g.get("kind") in kinds and g.get("task_id") is None
               for g in list(state.gates.values()) + state.pending_gates)


def _direct_review(task: TaskState) -> bool:
    """Human review with nothing to run: the owner decides, no worker is involved."""
    return task.human_review and not task.verification.strip()


def _task_gate_open(state: ShipState, task_id: str, kind: str) -> bool:
    return any(g.get("kind") == kind and g.get("task_id") == task_id
               for g in list(state.gates.values()) + state.pending_gates)


def holds_question(state: ShipState, task: TaskState) -> bool:
    """The task is gated on a question for its still-held worker (open in Orca or queued)."""
    return bool(task.status == "gated" and task.dispatch_id and _task_gate_open(state, task.id, "question"))


def _drop_task_gate(state: ShipState, task_id: str, kind: str) -> None:
    keep = lambda g: not (g.get("kind") == kind and g.get("task_id") == task_id)  # noqa: E731
    state.gates = {k: g for k, g in state.gates.items() if keep(g)}
    state.pending_gates = [g for g in state.pending_gates if keep(g)]


def _live_workers(state: ShipState) -> bool:
    """A worker may still be running: executing, being gated, or waiting on a question/timeout answer."""
    for task in state.tasks.values():
        if task.status in ("running", "gating"):
            return True
        if (task.status == "gated" and task.dispatch_id
                and (_task_gate_open(state, task.id, "question") or _task_gate_open(state, task.id, "timeout"))):
            return True
    return False


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
        if state.paused:
            return  # the owner paused the run; the audit starts on the next one
        _verify_repos(state, actions)
        return
    if not any(t.status == "failed" for t in state.tasks.values()):
        return
    if state.gates or state.pending_gates:
        return
    if any(t.status in _BUSY for t in state.tasks.values()):
        return
    if any(_ready(state, t) for t in state.tasks.values()):
        return
    if state.phase == "fixing" and all(t.status in ("done", "failed") for t in state.tasks.values()):
        # A fix task the owner skipped leaves its findings unverified: the scoped re-audit still
        # runs and reports them, so the run never ends "done" without a verdict.
        if state.paused:
            return
        _verify_repos(state, actions)
        return
    state.phase = "done"
    if not state.audit_result:
        state.audit_result = "unapproved"  # a task failed, so no audit could approve the change
    actions.append(Finish())


def _start_audit(state: ShipState, actions: list) -> None:
    state.phase = "auditing"
    state.changed_files = []
    # The diff is I/O: the driver computes it and answers with ChangesComputed,
    # which picks the auditor (risky or large changes go to Opus).
    actions.append(ComputeChanges())


def _verify_repos(state: ShipState, actions: list) -> None:
    """Every task is settled: run each repo's full gate once, then the audit.

    The full suite is the authoritative gate; a task's own gate only ran the tests its
    change reaches. A repo whose code did not change since its last pass costs nothing
    (the gate cache). No worktree means nothing was built here: straight to the audit."""
    repos = list(state.worktrees)
    if not repos:
        _start_audit(state, actions)
        return
    state.phase = "verifying"
    state.repo_gates = {r: "running" for r in repos}
    for repo in repos:
        actions.append(RunRepoGate(repo))


def _after_repo_gates(state: ShipState, actions: list) -> None:
    if state.phase != "verifying":
        return
    if all(v in ("passed", "skipped") for v in state.repo_gates.values()):
        _start_audit(state, actions)


def _gate_fix_task(state: ShipState, repo: str, reason: str) -> None:
    n = 1
    while "%s%d-%s" % (_GATE_FIX_PREFIX, n, repo) in state.tasks:
        n += 1
    fix_id = "%s%d-%s" % (_GATE_FIX_PREFIX, n, repo)
    state.tasks[fix_id] = TaskState(id=fix_id, repo=repo, deps=[], human_review=False, verification="",
                                    type="implementation", last_failure=reason[:GATE_FAILURE_CAP])
    state.repo_gates[repo] = "failed"
    if state.phase == "verifying":
        state.phase = "fixing" if state.audit_rounds > 0 else "running"


def _on_repo_gate_verdict(state: ShipState, event: RepoGateVerdict, cfg: ShipConfig, actions: list) -> None:
    repo = event.repo
    if state.repo_gates.get(repo) != "running":
        return  # nobody is waiting for this one
    if event.exit_code == 0:
        state.repo_gates[repo] = "passed"
        _after_repo_gates(state, actions)
    elif event.exit_code == 126:
        state.repo_gates[repo] = "setup"
        _open(state, actions, None,
              "The full gate of %s could not run before the audit: %s. Retry it, or skip it (the audit "
              "starts without it)?" % (repo, event.reason[:QUESTION_DETAIL]), ["retry", "skip"], "repo_gate")
        state.pending_gates[-1]["repo"] = repo
    else:
        fails = state.repo_gate_fails.get(repo, 0) + 1
        state.repo_gate_fails[repo] = fails
        if fails <= cfg.max_retries:
            _gate_fix_task(state, repo, event.reason)
            return
        state.repo_gates[repo] = "red"
        _open(state, actions, None,
              "The full gate of %s is still red after %d fix task(s): %s. Retry (one more fix task), or skip "
              "it (the audit starts without it)?" % (repo, fails - 1, event.reason[:QUESTION_DETAIL]),
              ["retry", "skip"], "repo_gate_red")
        state.pending_gates[-1]["repo"] = repo
        state.pending_gates[-1]["reason"] = event.reason[:GATE_FAILURE_CAP]


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
    elif isinstance(event, RepoGateVerdict):
        _on_repo_gate_verdict(state, event, cfg, actions)
    elif isinstance(event, ChangesComputed):
        _on_changes_computed(state, event, cfg, actions)
    elif isinstance(event, ChangesFailed):
        _on_changes_failed(state, event, actions)
    elif isinstance(event, WorkerStopped):
        _on_worker_stopped(state, event, actions)
    elif isinstance(event, AuditVerdict):
        _on_audit_verdict(state, event, cfg, actions)
    elif isinstance(event, WorkerActivity):
        _on_worker_activity(state, event, cfg, actions)
    return state, actions


STALL_OPTIONS = ["wait", "resend", "retry"]


def _ask_stalled(state: ShipState, task: TaskState, actions: list, why: str) -> None:
    """Ask the owner about a worker that shows no sign of work; once per open question."""
    if _task_gate_open(state, task.id, "stalled"):
        return
    _open(state, actions, task.id,
          "Task %s's worker (%s/%s) %s. It may be stuck on a startup dialog (a trust or hooks-review "
          "prompt) that swallowed its brief. Look at its tab, then answer: wait (ask again later), resend "
          "(type the task's brief into it again) or retry (stop it and start a fresh worker)."
          % (task.id, task.agent or "?", task.model or "?", why), list(STALL_OPTIONS), "stalled")
    state.pending_gates[-1]["dispatch"] = task.dispatch_id


def _stall_clock(state: ShipState, now: float, cfg: ShipConfig, actions: list) -> None:
    for task in state.tasks.values():
        if task.status != "running" or not task.dispatch_id or task.active:
            continue
        if not task.launched_at:
            task.launched_at = now  # launched before this was tracked, or re-armed by "wait"
        elif cfg.stall_minutes > 0 and now - task.launched_at > cfg.stall_minutes * 60:
            _ask_stalled(state, task, actions, "has shown no sign of work for %g minutes since it was "
                                               "launched: no change in its worktree and no question"
                         % cfg.stall_minutes)


def _on_worker_activity(state: ShipState, event: WorkerActivity, cfg: ShipConfig, actions: list) -> None:
    task = _holder(state, event.dispatch_id)
    if task is None or task.status != "running":
        return
    if event.active:
        task.active = True
        _drop_task_gate(state, task.id, "stalled")  # it started after all: nothing to decide
    elif event.dialog and cfg.stall_minutes > 0 and task.stall_answered != task.dispatch_id:
        _ask_stalled(state, task, actions, "is stuck on a startup dialog: %s. Answer it yourself in its "
                     "Orca terminal (Rein never changes an agent's trust settings), then resend the brief"
                     % event.dialog[:300])
    elif event.waiting and cfg.stall_minutes > 0 and task.stall_answered != task.dispatch_id:
        _ask_stalled(state, task, actions, "is waiting on a prompt only a person can answer (Orca: %s)"
                     % event.waiting[:300])


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
              ["retry", "skip"], "audit_timeout", audit=state.audit_dispatch)


def _on_tick(state: ShipState, event: Tick, cfg: ShipConfig, actions: list) -> None:
    if state.phase == "auditing":
        _audit_clock(state, event, cfg, actions)
        return
    if state.phase not in ("running", "fixing"):
        return
    now = event.now

    # A task re-dispatched by a verdict, or one that just entered (or resumed) its gate, has no
    # clock yet; start it now. The gate gets its own full budget, never the worker's leftover.
    for task in state.tasks.values():
        if task.status in ("running", "gating") and task.started_at == 0.0:
            task.started_at = now

    for task in state.tasks.values():
        # The clock covers the gate too: a task stuck in `gating` is bounded like a running one.
        if (task.status in ("running", "gating") and task.started_at
                and now - task.started_at > cfg.task_timeout_minutes * 60):
            gating = task.status == "gating"
            task.status = "gated"
            _open(state, actions, task.id,
                  "Task %s has run%s for more than %g minutes. Retry it, or skip it?"
                  % (task.id, " (and been gated)" if gating else "", cfg.task_timeout_minutes),
                  ["retry", "skip"], "timeout")

    _stall_clock(state, now, cfg, actions)

    for task in state.tasks.values():
        if _direct_review(task) and _ready(state, task):
            task.status = "gated"
            _open(state, actions, task.id,
                  "Task %s needs human review before it can be closed." % task.id,
                  ["approve", "reject"], "human_review")

    settled = len(actions)
    _settle(state, actions)
    if len(actions) > settled or state.phase == "done":
        return

    if state.paused:  # no new work; leave once every running worker has settled
        if not _live_workers(state):
            state.paused = False
            actions.append(Finish())
        return

    active = [t for t in state.tasks.values() if t.status in _BUSY and not _direct_review(t)]
    busy_repos = {t.repo for t in active}
    slots = cfg.max_concurrent_workers - len(active)
    batch = []
    for task in state.tasks.values():
        if slots <= 0:
            break
        if _direct_review(task) or not _ready(state, task) or task.repo in busy_repos:
            continue
        batch.append(task)
        busy_repos.add(task.repo)
        slots -= 1
    if not batch:
        return

    agent, model = _implementer(state, event.limits, cfg)
    reason = quota.brake_reason(event.limits, agent)
    if reason:
        if not state.forced_implementer and cfg.override is None:
            # every candidate is braked: say so, or the owner would chase a provider that is also full
            reason = "; ".join(quota.brake_reason(event.limits, c[0]) for c in
                               quota.candidates(event.limits, cfg.claude_weekly_threshold))
        state.phase = "braked"
        actions.append(Brake(reason))
        _open(state, actions, None, reason, ["retry", "pause"], "brake")
        return

    for task in batch:
        actions.append(Dispatch(task.id, task.repo, _worktree(state, task.repo), agent, model))
        task.status = "running"
        task.agent, task.model = agent, model
        task.started_at = now
        task.launched_at, task.active, task.launch_head = now, False, ""
        _record_agent(state, agent)


def _red(state: ShipState, task: TaskState, reason: str, cfg: ShipConfig, actions: list) -> None:
    task.last_failure = reason[:GATE_FAILURE_CAP]
    if task.attempts < cfg.max_retries:
        # Back to pending: the next Tick re-dispatches it, so a retry meets the
        # same brake, caps, one-worker-per-repo rule and implementer choice as
        # any other dispatch.
        _release_held(task, actions)
        task.attempts += 1
        task.status = "pending"
        task.started_at = 0.0
        return
    task.status = "failed"
    _open(state, actions, task.id,
          "Task %s failed after %d retries: %s" % (task.id, task.attempts, task.last_failure[:QUESTION_DETAIL]),
          ["retry", "skip"], "retry_exhausted")


def _on_worker_done(state: ShipState, event: WorkerDone, cfg: ShipConfig, actions: list) -> None:
    task = _holder(state, event.dispatch_id)
    if task is None:
        return
    if task.status == "gated":
        # It finished after all (its ask expired and it went on): the timeout or
        # question the owner was being asked is moot. Any other gate stays.
        for kind in ("timeout", "question"):
            if _task_gate_open(state, task.id, kind):
                _drop_task_gate(state, task.id, kind)
                task.status = "running"
    _drop_task_gate(state, task.id, "stalled")  # it finished: nothing left to decide about its start
    if task.status != "running":
        return
    if event.outcome == "succeeded":
        task.status = "gating"
        task.started_at = 0.0  # the gate's own clock starts at the next tick
        actions.append(RunGate(task.id, task.type != "verification"))
    elif event.dialog:
        # A relaunch would meet the same dialog: ask the owner once, and spend no retry on it.
        _release_held(task, actions)
        task.status = "gated"
        task.last_failure = "worker stopped on a startup dialog: %s" % event.dialog
        _open(state, actions, task.id, startup_question(task, event.dialog), ["retry", "skip"], "setup")
    else:
        _red(state, task, "worker %s" % (event.outcome or "failed"), cfg, actions)


def startup_question(task: TaskState, waits: str, handle: str = "") -> str:
    """The one question about a worker stuck on its agent's startup dialog."""
    return ("Task %s's worker%s is stuck on a startup dialog: %s. Answer that dialog yourself in its "
            "Orca terminal%s; Rein never changes an agent's trust settings. Then retry (a fresh worker, "
            "no retry spent) or skip the task."
            % (task.id, " (%s)" % task.agent if task.agent else "", waits, " %s" % handle if handle else ""))


def aborted_launch_question(task: TaskState, blocker: str, path: str, terminal: str, why_no_terminal: str = "") -> str:
    """The one question about a launch Orca aborted on its agent's startup dialog."""
    where = "%s%s" % (task.repo, " (%s)" % path if path else "")
    if terminal:
        opened = ("Rein opened a terminal with %s in that worktree (%s, Orca does not close it): answer the "
                  "dialog there" % (task.agent or "the agent", terminal))
    elif task.agent:
        opened = ("Rein could not open a terminal for you (%s): open %s in that worktree yourself and answer "
                  "the dialog there" % (why_no_terminal or "no reason given", task.agent))
    else:
        opened = "Open the agent in that worktree yourself and answer the dialog there"
    return ("Orca aborted the start of task %s's worker%s in %s because the agent stopped on a startup dialog: "
            "%s (Orca: %s). %s. Rein never answers it nor changes an agent's trust settings; it retries by "
            "itself once a new start is no longer aborted (no retry is spent). Or skip the task."
            % (task.id, " (%s)" % task.agent if task.agent else "", where,
               blocker_words(blocker), blocker, opened))


def blocker_words(blocker: str) -> str:
    return re.sub(r"^agent-|-prompt$", "", blocker).replace("-", " ")


def _on_gate_verdict(state: ShipState, event: GateVerdict, cfg: ShipConfig, actions: list) -> None:
    task = state.tasks.get(event.task_id)
    if task is None or task.status != "gating":
        return
    if event.dispatch_id and task.dispatch_id and event.dispatch_id != task.dispatch_id:
        return  # the gate of an earlier attempt
    task.touched = sorted(set(task.touched or []) | set(event.touched or []))
    if event.exit_code == 0:
        if task.human_review:  # the gate is green; a person still has to say so
            _release_held(task, actions)
            task.status = "gated"
            _open(state, actions, task.id,
                  "Task %s passed its gate. Approve it to close it, or reject it." % task.id,
                  ["approve", "reject"], "human_review")
            return
        if not _is_synthetic(task):  # synthetic fix tasks have no checkbox in the plan
            actions.append(CloseTask(task.id))
        _release_held(task, actions)
        task.status = "done"
        _settle(state, actions)
    elif event.exit_code == 126:
        task.status = "gated"
        _open(state, actions, task.id,
              "The check for task %s could not run: %s" % (task.id, event.reason[:QUESTION_DETAIL]),
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
    task.active = True  # a worker that asks has started
    _drop_task_gate(state, task.id, "stalled")
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
    "brake": ("retry", "pause", "switch-to-codex"),  # switch-to-codex: gates opened by an older Rein
    "audit_exhausted": ("fix", "accept", "reject"),
    "audit_timeout": ("retry", "skip"),
    "audit_failed": ("retry-audit", "accept", "reject"),
    "repo_gate": ("retry", "skip"),
    "repo_gate_red": ("retry", "skip"),
    "stalled": tuple(STALL_OPTIONS),
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
    if not gate.get("auto"):  # Rein's own probe retry is not a decision the owner made
        state.owner_decisions.append({"gate": event.gate_id, "kind": kind, "task_id": gate.get("task_id"),
                                      "question": str(gate.get("question") or ""), "answer": resolution})
    task = state.tasks.get(gate.get("task_id"))

    if kind == "stalled":
        if task is None or task.status != "running" or task.dispatch_id != gate.get("dispatch"):
            return  # the worker finished, asked, or was replaced meanwhile: the answer is moot
        task.stall_answered = task.dispatch_id
        if resolution == "retry":
            task.status = "gated"  # what a stop's WorkerStopped expects, as after a timeout
            actions.append(StopWorker(task.id, task.dispatch_id, "retry"))
            return
        if resolution == "resend":
            actions.append(ResendBrief(task.id, task.dispatch_id))
        task.launched_at = 0.0  # the next Tick re-arms the window
    elif kind == "question":
        if gate.get("task_id") is not None and (task is None or task.status != "gated"):
            return  # the worker already moved on (it finished, or was stopped): nobody is waiting for this answer
        actions.append(Relay(gate.get("message_id", ""), resolution))
        if task is not None:
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
    elif kind == "setup" and gate.get("task_id") is None:
        # The changed files could not be listed. Retry lists them again; skip ends the audit unapproved.
        if state.phase == "auditing":
            if resolution == "retry":
                actions.append(ComputeChanges())
            else:
                _end_audit(state, actions, "skipped")
    elif kind == "timeout":
        if task is not None:
            if task.dispatch_id and task.stop_unconfirmed == task.dispatch_id:
                # The owner already checked this worker after an unconfirmed stop: fence it, never stop it again.
                actions.append(AbandonWorker(task.id, task.dispatch_id))
                _after_stop(state, task, resolution, actions)
            elif task.dispatch_id:  # the worker may still be running: stop it, and wait for proof
                actions.append(StopWorker(task.id, task.dispatch_id, resolution))
            else:
                _after_stop(state, task, resolution, actions)
    elif kind in ("retry_exhausted", "setup"):
        if task is not None:
            _release_held(task, actions)
            if resolution == "retry":
                task.status, task.attempts = "pending", 0
            else:
                task.status = "failed"
        _settle(state, actions)
    elif kind == "brake":
        state.forced_implementer = []  # route by quota again; a legacy forced Codex could loop on the brake
        state.phase = "running"
        if resolution == "pause":
            # Nothing new starts, but running workers stay supervised until they settle.
            state.paused = True
            if not _live_workers(state):
                state.paused = False
                actions.append(Finish())
    elif kind == "audit_exhausted":
        if state.phase == "auditing":
            if resolution == "fix" and not state.pending_findings:
                _open(state, actions, None,
                      "Rein found no audit findings to fix (none were stored and the latest review episode has "
                      "none), so no fix round was started. Retry re-lists the changes and audits again; skip ends "
                      "the audit unapproved.", ["retry", "skip"], "setup")
            elif resolution == "fix":
                state.extra_audit_rounds += 1
                _start_fix_round(state, state.pending_changed_files, state.pending_findings)
                state.pending_findings, state.pending_changed_files = [], []
            else:
                _end_audit(state, actions, "approved" if resolution == "accept" else "rejected")
    elif kind in ("repo_gate", "repo_gate_red"):
        repo = gate.get("repo") or ""
        if state.repo_gates.get(repo) not in ("setup", "red"):
            return  # already moved on
        if resolution == "skip":
            state.repo_gates[repo] = "skipped"
            _after_repo_gates(state, actions)
        elif kind == "repo_gate":
            state.repo_gates[repo] = "running"
            actions.append(RunRepoGate(repo))
        else:
            _gate_fix_task(state, repo, gate.get("reason") or "the full gate of %s was red" % repo)
    elif kind in ("audit_timeout", "audit_failed") and state.phase != "auditing":
        pass  # the audit moved on while the owner was deciding
    elif kind == "audit_timeout" and gate.get("audit") != state.audit_dispatch:
        pass  # it belonged to an earlier audit; resolving it must not touch this one
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


def _after_stop(state: ShipState, task: TaskState, resolution: str, actions: list) -> None:
    task.stop_unconfirmed = ""
    _release_held(task, actions)
    if resolution == "retry":
        task.status, task.attempts = "pending", 0
    else:
        task.status = "failed"
    _settle(state, actions)


def _on_worker_stopped(state: ShipState, event: WorkerStopped, actions: list) -> None:
    task = state.tasks.get(event.task_id)
    if task is None or task.status != "gated":
        return
    if event.confirmed:
        _after_stop(state, task, event.resolution, actions)
        return
    if task.dispatch_id and task.stop_unconfirmed == task.dispatch_id:
        # The owner already answered the unconfirmed-stop gate: that answer is their confirmation.
        actions.append(AbandonWorker(task.id, task.dispatch_id))
        _after_stop(state, task, event.resolution, actions)
        return
    # No proof the worker is gone: re-running or abandoning the task could leave
    # two workers on one worktree. Ask the owner once; their answer fences the worker.
    task.stop_unconfirmed = task.dispatch_id
    _open(state, actions, task.id,
          "Rein could not confirm that the worker of %s has stopped (%s). Check Orca, then retry it or skip it."
          % (task.id, event.detail[:300] or "no detail"), ["retry", "skip"], "timeout")


def _drop_audit(state: ShipState, actions: list) -> None:
    """Forget the running audit worker (releasing it) so a new one can start."""
    if state.audit_dispatch:
        actions.append(Release(state.audit_dispatch))
    state.audit_dispatch, state.audit_started_at = "", 0.0
    state.changed_files = []


def _end_audit(state: ShipState, actions: list, result: str) -> None:
    if state.audit_dispatch:  # never end the run with an auditor still running
        actions.append(Release(state.audit_dispatch))
        state.audit_dispatch = ""
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


def _repo_relative(state: ShipState, repo: str, path: str) -> str:
    root = state.worktree_paths.get(repo)
    prefixes = [repo.rstrip("/") + "/"]
    if root:
        prefixes.append(root.rstrip("/") + "/")
    for p in prefixes:
        if path.startswith(p):
            return path[len(p):]
    return ""


def _names_file(finding: str, rel: str) -> bool:
    """True when the finding mentions the repo-relative path or a delimited basename."""
    if not rel:
        return False
    if rel in finding:
        return True
    base = rel.rsplit("/", 1)[-1]
    return bool(base) and re.search(r"(?<![\w.\-])%s(?![\w\-])(?!\.\w)" % re.escape(base), finding) is not None


def _fix_assignments(state: ShipState, changed_files: list, findings: list) -> list:
    """[(repo, findings)] for the fix tasks: each repo gets the findings naming its files
    plus the unassigned ones, and only repos with an assigned finding get a task. When no
    finding names any file, every touched repo gets every finding."""
    repos = _fix_repos(state, changed_files)
    rels = {r: [x for x in (_repo_relative(state, r, f) for f in changed_files) if x] for r in repos}
    assigned = {r: [f for f in findings if any(_names_file(f, x) for x in rels[r])] for r in repos}
    named = {f for fs in assigned.values() for f in fs}
    if not named:
        return [(r, list(findings)) for r in repos]

    def homes(finding):
        # A finding that names no file goes to the repo it names, else to every touched repo:
        # never only to a repo that happens to have another finding.
        return [r for r in repos if _names_repo(finding, r)] or repos

    out = []
    for r in repos:
        mine = [f for f in findings if f in assigned[r] or (f not in named and r in homes(f))]
        if mine:
            out.append((r, mine))
    return out


def _names_repo(finding: str, repo: str) -> bool:
    """True when the finding mentions the repo's name (or its last dash part, `admin` of `proxima-admin`)."""
    names = {repo, repo.rsplit("-", 1)[-1]}
    return any(n and re.search(r"(?<![\w\-])%s(?![\w\-])" % re.escape(n), finding, re.IGNORECASE)
               for n in names if len(n) >= 3)


def _on_changes_computed(state: ShipState, event: ChangesComputed, cfg: ShipConfig, actions: list) -> None:
    if state.phase != "auditing":
        return
    if state.audit_dispatch or _owner_gate_open(state, *AUDIT_HOLDS):
        return  # an audit is running, or the owner has not yet decided about the last one
    state.changed_files = list(event.changed_files)
    implementers = state.launched_agents or state.implementer_agents  # what Orca ran, not what was asked
    agent, model = audit_model(state.changed_files, implementers, cfg, event.limits,
                               reaudit=state.audit_rounds > 0, exclude=state.audit_excluded)
    if not agent:
        refused = ", ".join(state.audit_excluded)
        state.audit_excluded = []  # the retry tries every reviewer again, once the owner fixed Orca
        _open(state, actions, None,
              "Orca refused to start the audit on every reviewer (%s): no other reviewer is available. Fix "
              "Orca, then retry (every reviewer is tried again), or skip the audit (the change stays "
              "unapproved)?" % refused, ["retry", "skip"], "setup")
        return
    braked = quota.brake_reason(event.limits, agent)
    if braked:
        _open(state, actions, None,
              "Every reviewer is over the quota brake (%s), so the audit cannot start. Retry once quota "
              "frees up, or skip the audit (the change stays unapproved)?" % braked,
              ["retry", "skip"], "setup")
        return
    actions.append(StartAudit(agent, model))


def _on_changes_failed(state: ShipState, event: ChangesFailed, actions: list) -> None:
    if state.phase != "auditing":
        return
    if state.audit_dispatch or _owner_gate_open(state, *AUDIT_HOLDS):
        return
    _open(state, actions, None,
          "Rein could not list the changed files, so the audit cannot start: %s" % event.error[:600],
          ["retry", "skip"], "setup")


def _on_audit_verdict(state: ShipState, event: AuditVerdict, cfg: ShipConfig, actions: list) -> None:
    if state.phase != "auditing":
        return
    state.audit_started_at = 0.0
    # Any verdict ends the audit a timeout gate was about.
    state.gates = {k: g for k, g in state.gates.items()
                   if not (g.get("kind") == "audit_timeout" and g.get("task_id") is None)}
    state.pending_gates = [g for g in state.pending_gates
                           if not (g.get("kind") == "audit_timeout" and g.get("task_id") is None)]
    if event.verdict == "NO_VERDICT":
        if _owner_gate_open(state, "audit_failed"):
            return
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
    # maxAuditRounds counts audits: with the default 2, one fix round and a second, final audit.
    exhausted = state.audit_rounds + 1 >= cfg.max_audit_rounds + state.extra_audit_rounds
    # A fix the owner skipped is never reopened by a round nobody asked for: the owner decides.
    skipped = skipped_fix_tasks(state)
    if exhausted or skipped:
        if _owner_gate_open(state, "audit_exhausted"):
            return
        state.pending_findings = list(event.findings)
        state.pending_changed_files = list(event.changed_files)
        summary = "\n".join(event.findings)[:600]
        why = ("after %d audit(s)" % (state.audit_rounds + 1) if exhausted else
               "and you skipped the fix task(s) %s" % ", ".join(t.id for t in skipped))
        # The run ends only when the owner answers: accept makes the audit approved, reject rejected.
        _open(state, actions, None,
              "The audit still has %d blocking finding(s) %s. Fix them in one more round, accept the change anyway, or reject it? %s"
              % (event.blocking, why, summary),
              ["fix", "accept", "reject"], "audit_exhausted")
        return
    _start_fix_round(state, event.changed_files, list(event.findings))


_SEVERITY_ORDER = {"BLOCKING": 0, "IMPORTANT": 1, "SUGGESTION": 2}


def _by_severity(findings: list) -> list:
    """BLOCKING first, then IMPORTANT, then SUGGESTION; the order within a severity is kept."""
    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.split(":", 1)[0].strip().upper(), 3))


def _start_fix_round(state: ShipState, changed_files: list, findings: list) -> None:
    state.audit_rounds += 1
    findings = _by_severity(findings)
    blocking = [f for f in findings if f.split(":", 1)[0].strip().upper() == "BLOCKING"]
    state.previous_blocking = blocking or list(findings)  # unlabelled findings: verify them all
    for repo, repo_findings in _fix_assignments(state, list(changed_files), list(findings)):
        fix_id = "%s%d-%s" % (_FIX_PREFIX, state.audit_rounds, repo)
        text = "\n".join(repo_findings)[:GATE_FAILURE_CAP]
        state.tasks[fix_id] = TaskState(
            id=fix_id, repo=repo, deps=[], human_review=False, verification="",
            type="implementation", last_failure=text, audit_findings=text,
        )
    state.phase = "fixing"
