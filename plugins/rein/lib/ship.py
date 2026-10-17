"""The `rein ship` driver: preflight, then a loop that feeds Orca events to the pure core.

`ship_decide.decide` chooses; this module performs. Everything with a side effect
or an id only Orca can mint lives here: Orca tasks, worker dispatches, worktrees,
gates, the plan checkbox, the gate runner, the git diff. State is saved after
every decision and every mutating Orca call, so a re-run resumes.

Safety: ship never pushes, merges, opens a PR, deploys, or resolves a gate (the
owner does). Worker message bodies are data; only their `type` and `payload`
ids are read, and a body is shown to the owner only as a gate question.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Callable

import detect as _detect
import gate as _gate
import orca_client as oc
import plan as _plan
import plan_check as _plan_check
import quota
import roles as _roles
import ship_decide as sd
import ship_state as ss
import token_report as _tr
from orca_client import OrcaClient, OrcaError

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(PLUGIN, "bin", "rein")
CONTRACT = "orchestration.contract.v1"
DEFAULT_BASE_REF = "origin/develop"
WAIT_MS = 60000      # bounded: gate-resolve sends no inbox message, so a wait must wake to poll
IDLE_POLLS = 3       # empty waits in a row before asking worker-list what needs doing
SAFE_NEXT_ACTIONS = ("release", "retain")
_TAIL = 2000


# --- specs ---------------------------------------------------------------------

def build_task_spec(task: ss.TaskState, plan: dict, implementer_profile: str) -> str:
    entry = next((t for t in plan.get("tasks") or [] if t.get("id") == task.id), {})
    lines = [implementer_profile.strip(), "", "Task %s: %s" % (task.id, entry.get("title") or task.id)]
    if entry.get("acceptance"):
        lines += ["Acceptance criteria:"] + ["- %s" % c for c in entry["acceptance"]]
    if task.verification:
        lines.append("Verification command: %s" % task.verification)
    if plan.get("why"):
        lines += ["", "Why: %s" % plan["why"]]
    if plan.get("scopeOut"):
        lines += ["Out of scope:"] + ["- %s" % s for s in plan["scopeOut"]]
    if task.last_failure and task.attempts > 0:
        lines += ["", "The previous attempt failed this check; fix it:", task.last_failure]
    elif task.last_failure and task.id.startswith("AUDIT"):
        lines += ["", "The audit found these blocking problems; fix them:", task.last_failure]
    lines += ["", "Edit only the %s repository." % task.repo,
              "Never push, merge, open a pull request or deploy.",
              "Never read or print .env* files or credentials."]
    return "\n".join(lines)


def build_audit_spec(plan: dict, changed: dict, reviewer_profile: str) -> str:
    lines = [reviewer_profile.strip(), "", "Audit the change as a whole; you wrote none of it."]
    if plan.get("why"):
        lines.append("Why: %s" % plan["why"])
    for path, files in changed.items():
        lines.append("Run the rein-audit skill on %s" % path)
        lines += ["  changed: %s" % f for f in files[:50]]
    lines += ["Record the verdict with rein review record (from the plan repository, with --change).",
              "Never push, merge, open a pull request or deploy.",
              "Never read or print .env* files or credentials."]
    return "\n".join(lines)


def changed_files(worktree_path: str, base_ref: str) -> list:
    proc = subprocess.run(["git", "-C", worktree_path, "diff", "--name-only", "%s...HEAD" % base_ref],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:500] or "git diff failed")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def default_gate_runner(path: str, unit_only: bool) -> tuple:
    """`rein gate <path> --json [--unit-only]`, with this process's Python and this checkout's bin."""
    argv = [sys.executable, BIN, "gate", path, "--json"] + (["--unit-only"] if unit_only else [])
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-_TAIL:]


# --- preflight -----------------------------------------------------------------

def _load_plan(root: str, change: str) -> dict:
    plan_cfg = _detect.resolve(root).get("plan") or {}
    return _plan.read_plan(root, source=plan_cfg.get("source", ""), change=change,
                           configured=plan_cfg.get("path", ""))


def _flow_config(root: str) -> dict:
    try:
        with open(os.path.join(root, "flow.config.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def preflight(root: str, change: str, client: OrcaClient, env: dict) -> tuple:
    """`(context, errors)`; any error means nothing may be started."""
    root = os.path.abspath(root)
    errors: list = []
    default_repo = os.path.basename(root)
    ctx = {"root": root, "default_repo": default_repo, "repos": {}}
    if not env.get("ORCA_TERMINAL_HANDLE"):
        errors.append("ORCA_TERMINAL_HANDLE is not set: run `rein ship` from inside an Orca terminal")
    try:
        caps = (client.status().get("runtime") or {}).get("capabilities") or []
        if CONTRACT not in caps:
            errors.append("this Orca does not advertise %s (update Orca)" % CONTRACT)
    except OrcaError as exc:
        errors.append("cannot reach Orca: %s" % exc)
    try:
        ctx["repos"] = {r.get("displayName"): r for r in client.repo_list().get("repos") or []}
    except OrcaError as exc:
        errors.append("cannot list Orca repos: %s" % exc)

    plan = _load_plan(root, change)
    ctx["plan"] = plan
    if not plan.get("exists"):
        return ctx, errors + [plan.get("error") or "no plan found"]
    pending = [t for t in plan["tasks"] if not t["checked"]]
    try:
        with open(plan["path"], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        findings = _plan_check.mechanical_findings(text)
        findings += [f for f in _plan_check.unbacked_findings(text, root)
                     if next((t["repo"] for t in plan["tasks"] if t["id"] == f.get("taskId")), "") in ("", default_repo)]
        decision = _plan_check.decide_plan_check(findings, [t["id"] for t in pending])
        if decision["decision"] == _plan_check.DECISION_STOP:
            errors += ["plan-check BLOCKING: %s" % f.get("text") for f in decision["blocking"]]
    except OSError as exc:
        errors.append("cannot read the plan: %s" % exc)
    for t in pending:
        if not t["verification"].strip() and not t["humanReview"]:
            errors.append("task %s has neither a runnable verification nor human review" % t["id"])
    if ctx["repos"]:
        for t in pending:
            name = t["repo"] or default_repo
            if name not in ctx["repos"]:
                errors.append("task %s: repo %r is not registered in Orca (registered: %s)"
                              % (t["id"], name, ", ".join(sorted(n for n in ctx["repos"] if n)) or "none"))
    for role in ("implementer", "reviewer"):
        prof = _roles.profile(role, PLUGIN)
        ctx["%s_profile" % role] = prof["text"]
        if not prof["ok"]:
            errors.append(prof["error"])
    ctx["cfg"] = sd.ShipConfig.from_flow_config(_flow_config(root))
    return ctx, errors


# --- dry run -------------------------------------------------------------------

class DryRunRunner:
    """An OrcaClient runner that answers with empty successes and records each argv."""

    def __init__(self, repo_names, record: Callable[[str], None]):
        self.repo_names, self.record, self.n = list(repo_names), record, 0

    def __call__(self, argv):
        self.record(" ".join(a if len(a) <= 100 else a.splitlines()[0][:80] + " ..." for a in argv))
        a = [x for x in argv[1:] if x != "--json"]
        self.n += 1
        result: dict = {}
        if a[:1] == ["status"]:
            result = {"runtime": {"capabilities": [CONTRACT]}}
        elif a[:2] == ["repo", "list"]:
            result = {"repos": [{"id": "repo-%s" % n, "displayName": n} for n in self.repo_names]}
        elif a[:1] == ["account"]:
            result = {"rateLimits": {"claude": {"status": "ok", "session": {"usedPercent": 0}, "weekly": {"usedPercent": 0}}}}
        elif a[1:2] == ["run-create"]:
            result = {"run": {"id": "run_dry"}}
        elif a[1:2] == ["task-create"]:
            result = {"task": {"id": "task_dry_%d" % self.n}}
        elif a[1:2] == ["worker-start"]:
            wt = "wt_dry_%d::/dry/%d" % (self.n, self.n)
            if "--worktree" in a and a[a.index("--worktree") + 1].startswith("id:"):
                wt = a[a.index("--worktree") + 1][3:]
            result = {"dispatchId": "dry_d_%d" % self.n, "effects": [{"kind": "worktree", "id": wt}]}
        return 0, json.dumps({"ok": True, "result": result})


def dry_run_client(root: str, change: str, record: Callable[[str], None]) -> OrcaClient:
    plan = _load_plan(os.path.abspath(root), change)
    names = {t["repo"] or os.path.basename(os.path.abspath(root)) for t in plan.get("tasks") or []}
    return OrcaClient(executable="orca", runner=DryRunRunner(sorted(names), record))


def _episode_stamp(episode) -> str:
    """Identity of a review episode: its path plus mtime, so a rewrite in the same second differs."""
    if not episode:
        return ""
    try:
        return "%s@%d" % (episode["path"], os.stat(episode["path"]).st_mtime_ns)
    except (OSError, KeyError):
        return str(episode.get("path", ""))


# --- the driver ----------------------------------------------------------------

class _Driver:
    def __init__(self, ctx, state, client, gate_runner, clock, spath, dry_run):
        self.ctx, self.state, self.client = ctx, state, client
        self.gate_runner, self.clock, self.spath, self.dry_run = gate_runner, clock, spath, dry_run
        self.root, self.cfg, self.plan = ctx["root"], ctx["cfg"], ctx["plan"]
        self.change = state.change
        self.finished = False
        self.before = None
        self.changed_by_wt: dict = {}
        self.ignored: set = set()
        self.noted: set = set()

    # -- plumbing
    def say(self, text):
        print("ship: %s" % text, flush=True)

    def save(self):
        if not self.dry_run:
            ss.save(self.state, self.spath)

    def limits(self):
        try:
            return quota.parse_rate_limits({"result": self.client.account_list()})
        except OrcaError as exc:
            self.say("could not read rate limits (%s); treating usage as unknown" % exc)
            return quota.parse_rate_limits({})

    def feed(self, event):
        queue = [event]
        while queue:
            self.state, actions = sd.decide(self.state, queue.pop(0), self.cfg)
            self.save()
            for action in actions:
                follow = self.execute(action)
                if follow is not None:
                    queue.append(follow)
            self.open_pending()

    # -- actions
    def execute(self, a):
        if isinstance(a, sd.Dispatch):
            self.dispatch(a)
        elif isinstance(a, sd.RunGate):
            return self.run_gate(a)
        elif isinstance(a, sd.CloseTask):
            if not _plan.close_task(self.state.plan_path, a.task_id):
                self.say("could not tick %s in %s" % (a.task_id, self.state.plan_path))
        elif isinstance(a, sd.Release):
            self.release(a.dispatch_id)
        elif isinstance(a, sd.OpenGate):
            pass  # decide queued it in state.pending_gates; open_pending() creates it in Orca
        elif isinstance(a, sd.Relay):
            try:
                self.client.reply(a.message_id, a.body)
            except OrcaError as exc:
                self.say("could not relay the answer: %s" % exc)
        elif isinstance(a, sd.ComputeChanges):
            return self.compute_changes()
        elif isinstance(a, sd.StartAudit):
            self.start_audit(a)
        elif isinstance(a, sd.Brake):
            self.say("braked: %s" % a.reason)
        elif isinstance(a, sd.Finish):
            self.finished = True
        return None

    def release(self, dispatch_id):
        try:
            self.client.worker_release(dispatch_id)
        except OrcaError as exc:
            self.say("could not release %s: %s" % (dispatch_id, exc))

    def create_task(self, task):
        spec = build_task_spec(task, self.plan, self.ctx["implementer_profile"])
        entry = next((t for t in self.plan.get("tasks") or [] if t["id"] == task.id), {})
        # A retry is dispatched directly, so it carries no Orca dependencies.
        deps = [] if task.attempts else [
            self.state.tasks[d].orca_task_id for d in task.deps
            if d in self.state.tasks and self.state.tasks[d].orca_task_id and self.state.tasks[d].status != "done"]
        task.orca_task_id = oc.task_id_of(self.client.task_create(
            spec, ("%s %s" % (task.id, entry.get("title") or "")).strip(), deps))
        self.save()

    def dispatch(self, a):
        task = self.state.tasks[a.task_id]
        try:
            if not task.orca_task_id or task.attempts > 0:
                self.create_task(task)
            repo = self.ctx["repos"].get(a.repo) or {}
            res = self.client.worker_start(
                task.orca_task_id, worktree=a.worktree, agent=a.agent, model=a.model,
                repo="id:%s" % repo["id"] if a.worktree == "new-top-level" and repo.get("id") else "")
        except OrcaError as exc:
            task.dispatch_id = ""
            task.status = "gated"
            self.state.pending_gates.append({
                "task_id": task.id, "kind": "setup", "message_id": "", "options": ["retry", "skip"],
                "question": "Orca could not start a worker for %s: %s" % (task.id, exc)})
            self.save()
            self.open_pending()
            return
        task.dispatch_id = oc.dispatch_id_of(res)
        wt = oc.worktree_of(res)
        if wt and a.repo not in self.state.worktrees:
            self.state.worktrees[a.repo] = wt
            self.state.worktree_paths[a.repo] = oc.worktree_path_of(wt)
        launch = oc.effective_launch_of(res)
        if launch.get("agent") and (launch["agent"], launch.get("model")) != (a.agent, a.model):
            self.say("Orca launched %s/%s for %s, not %s/%s" % (launch["agent"], launch.get("model"), task.id, a.agent, a.model))
            task.agent, task.model = launch["agent"], launch.get("model") or a.model
        self.save()
        self.say("%s dispatched to %s/%s (%s)" % (task.id, task.agent, task.model, task.dispatch_id))

    def open_pending(self):
        """Create every owner gate decide asked for and Orca does not have yet.

        A failed gate-create leaves the request in `state.pending_gates`; the
        next call (every loop iteration, and on resume) tries again.
        """
        for entry in list(self.state.pending_gates):
            task_id = entry.get("task_id")
            anchor = (self.state.tasks.get(task_id) if task_id else None) or next(
                (t for t in self.state.tasks.values() if t.orca_task_id), None)
            if anchor is None:
                continue
            try:
                gate_id = oc.gate_id_of(self.client.gate_create(
                    anchor.orca_task_id, str(entry.get("question"))[:2000], entry.get("options") or []))
            except OrcaError as exc:
                self.say("could not open the %s gate, will retry: %s" % (entry.get("kind"), exc))
                continue
            self.state.gates[gate_id] = {"task_id": task_id, "kind": entry.get("kind"),
                                         "message_id": entry.get("message_id", ""), "audit": entry.get("audit", "")}
            self.state.pending_gates.remove(entry)
            self.save()
            self.say("waiting for the owner (%s): %s" % (entry.get("kind"), str(entry.get("question"))[:200]))

    def run_gate(self, a):
        task = self.state.tasks[a.task_id]
        path = self.state.worktree_paths.get(task.repo, "")
        if not path:
            return sd.GateVerdict(a.task_id, 126, "no worktree is recorded for %s" % task.repo)
        try:
            code, out = self.gate_runner(path, a.unit_only)
        except OSError as exc:
            code, out = 126, "gate could not run: %s" % exc
        self.say("%s gate exit %s" % (task.id, code))
        return sd.GateVerdict(a.task_id, code, (out or "")[-_TAIL:])

    def compute_changes(self):
        if self.state.audit_dispatch:
            return None
        flat, by_wt = set(), {}
        for repo, wt in self.state.worktrees.items():
            path = self.state.worktree_paths.get(repo) or oc.worktree_path_of(wt)
            base = (self.ctx["repos"].get(repo) or {}).get("worktreeBaseRef") or DEFAULT_BASE_REF
            try:
                files = changed_files(path, base)
            except (RuntimeError, OSError) as exc:
                self.say("could not diff %s against %s: %s" % (path, base, exc))
                files = []
            by_wt[path] = files
            flat.update("%s/%s" % (repo, f) for f in files)
        self.changed_by_wt = by_wt
        return sd.ChangesComputed(sorted(flat))

    def start_audit(self, a):
        if not self.state.worktrees or self.state.audit_dispatch:
            if not self.state.worktrees:
                self.say("no worktree was used; nothing to audit")
                self.state.phase, self.finished = "done", True
            return
        spec = build_audit_spec(self.plan, self.changed_by_wt, self.ctx["reviewer_profile"])
        task_id = oc.task_id_of(self.client.task_create(
            spec, "%s audit round %d" % (self.change, self.state.audit_rounds + 1), []))
        first = next(iter(self.state.worktrees.values()))
        res = self.client.worker_start(task_id, worktree="id:%s" % first, agent=a.agent, model=a.model)
        self.state.audit_dispatch = oc.dispatch_id_of(res)
        self.state.audit_baseline = _episode_stamp(_gate.latest_episode(self.root, self.change))
        self.state.audit_started_at = 0.0
        self.save()
        self.say("audit started on %s/%s (%s)" % (a.agent, a.model, self.state.audit_dispatch))

    # -- events from Orca
    def poll_gates(self):
        for g in oc.gates_of(self.client.gate_list()):
            gid = g.get("id")
            if g.get("status") == "resolved" and gid in self.state.gates and gid not in self.ignored:
                self.feed(sd.GateResolved(gid, str(g.get("resolution") or "")))
                if gid in self.state.gates:  # decide refused this resolution; do not replay it forever
                    self.ignored.add(gid)

    def rows(self):
        res = self.client.worker_list(self.state.run_id)
        return [w for w in res.get("workers") or [] if isinstance(w, dict)]

    def handle_delivery(self, res):
        for m in oc.messages_of(res):
            payload = oc.payload_of(m)
            did = str(payload.get("dispatchId") or "")
            if m.get("type") == "worker_done":
                self.worker_done(did, str(payload.get("outcome") or ""))
            elif m.get("type") in ("question", "escalation"):
                did = did or next((str(r.get("dispatchId") or "") for r in self.rows()
                                   if r.get("agentTerminalHandle") == m.get("from_handle")), "")
                if did:
                    self.feed(sd.Question(did, str(m.get("id") or ""), str(m.get("body") or "")[:_TAIL]))
        delivery = oc.delivery_id_of(res)
        if delivery:
            self.client.check(wait=False, ack=delivery)

    def worker_done(self, dispatch_id, outcome):
        if not dispatch_id:
            return
        if dispatch_id != self.state.audit_dispatch:
            self.feed(sd.WorkerDone(dispatch_id, outcome))
            return
        verdict = self.read_verdict(outcome)
        self.state.audit_dispatch = ""
        self.save()
        self.release(dispatch_id)
        self.feed(verdict)

    def read_verdict(self, outcome):
        changed = list(self.state.changed_files)
        episode = _gate.latest_episode(self.root, self.change)
        if outcome != "succeeded" or episode is None or _episode_stamp(episode) == self.state.audit_baseline:
            return sd.AuditVerdict("NO_VERDICT", 1, changed,
                                   ["the audit worker ended (%s) without recording a review episode" % (outcome or "?")])
        findings = _gate.check_review(self.root, self.change).get("findings") or []
        blocking = sum(1 for f in findings if f["severity"] == "BLOCKING")
        return sd.AuditVerdict(str(episode.get("verdict")), blocking, changed,
                               ["%s: %s" % (f["severity"], f["text"]) for f in findings])

    def probe_audit(self):
        """Ask Orca about the audit worker; a finished one whose message we missed is read now."""
        did = self.state.audit_dispatch
        if not did:
            return
        show = self.client.worker_show(did)
        worker_state = (show.get("worker") or {}).get("state")
        if (show.get("dispatch") or {}).get("status") == "completed" or worker_state in ("succeeded", "failed"):
            self.worker_done(did, "succeeded" if worker_state == "succeeded" else "failed")

    def idle_poll(self):
        held = {t.dispatch_id for t in self.state.tasks.values()
                if t.dispatch_id and t.status in ("running", "gating")}
        for row in self.rows():
            action = (row.get("projection") or {}).get("nextAction")
            kind = action.get("kind") if isinstance(action, dict) else ""
            did = str(row.get("dispatchId") or "")
            if not kind or did == self.state.audit_dispatch:
                continue
            if kind not in SAFE_NEXT_ACTIONS:
                if (did, kind) not in self.noted:  # never acted on: only release/retain run
                    self.noted.add((did, kind))
                    self.say("ignoring unknown nextAction %r for %s" % (kind, did))
            elif did not in held and action.get("argv") == ["orchestration", "worker-%s" % kind, "--dispatch", did]:
                self.client.call(*action["argv"])
                self.say("%s %s" % (kind, did))
        self.probe_audit()

    # -- start, resume, loop, finish
    def setup(self):
        if self.state.run_id:
            self.client.run_use(self.state.run_id)
        else:
            self.state.run_id = oc.run_id_of(self.client.run_create("rein ship %s" % self.change))
            self.save()
        pending = [t for t in self.state.tasks.values() if not t.orca_task_id and t.status != "done"]
        while pending:
            ready = [t for t in pending if all(not self.state.tasks.get(d) or self.state.tasks[d].orca_task_id
                                               or self.state.tasks[d].status == "done" for d in t.deps)]
            for t in ready or pending:
                self.create_task(t)
                pending.remove(t)

    def requeue_orphans(self):
        """A task `gated` with no gate (open or queued) was left by an interrupted run: ask again.

        Only `gated` counts. A `failed` task is also what a skipped one looks
        like, and reopening those would undo the owner's decision.
        """
        covered = {g.get("task_id") for g in list(self.state.gates.values()) + self.state.pending_gates}
        for t in self.state.tasks.values():
            if t.status == "gated" and t.id not in covered:
                review = t.human_review
                self.state.pending_gates.append({
                    "task_id": t.id, "kind": "human_review" if review else "setup", "message_id": "",
                    "options": ["approve", "reject"] if review else ["retry", "skip"],
                    "question": ("Task %s needs human review before it can be closed." % t.id) if review
                    else "Task %s was waiting on a decision that was lost. Retry it, or skip it?" % t.id})
        if self.state.phase == "braked" and not any(
                g.get("kind") == "brake" for g in list(self.state.gates.values()) + self.state.pending_gates):
            self.state.pending_gates.append({
                "task_id": None, "kind": "brake", "message_id": "",
                "options": ["switch-to-codex", "pause"],
                "question": "The run was braked on quota. Switch to Codex, or pause?"})
        self.save()

    def resume(self):
        self.requeue_orphans()
        self.open_pending()
        rows = {str(r.get("dispatchId")): r for r in self.rows()}
        events = []
        for t in self.state.tasks.values():
            if t.status == "running" and not t.dispatch_id:
                t.status = "pending"  # decided but never started
            elif t.status == "running":
                row = rows.get(t.dispatch_id)
                if row is None:
                    events.append(sd.WorkerDone(t.dispatch_id, "failed"))
                elif row.get("dispatchStatus") == "completed" or row.get("workerState") in ("succeeded", "failed"):
                    events.append(sd.WorkerDone(t.dispatch_id, str((row.get("projection") or {}).get("outcome")
                                                                 or row.get("workerState") or "failed")))
            elif t.status == "gating":
                events.append(sd.RunGate(t.id, t.type != "verification"))
        self.save()
        for e in events:
            if isinstance(e, sd.RunGate):
                self.feed_gate(e)
            else:
                self.feed(e)
        if self.state.phase == "auditing":
            if self.state.audit_dispatch:
                self.probe_audit()
            elif any(g.get("kind") in ("audit_failed", "audit_timeout") and g.get("task_id") is None
                     for g in list(self.state.gates.values()) + self.state.pending_gates):
                self.say("waiting for the owner about the audit; not restarting it")
            else:
                self.feed_changes()

    def feed_gate(self, run_gate):
        verdict = self.run_gate(run_gate)
        self.feed(verdict)

    def feed_changes(self):
        event = self.compute_changes()
        if event is not None:
            self.feed(event)

    def loop(self):
        idle = 0
        while not self.finished:
            self.open_pending()
            limits = self.limits()
            self.before = self.before or limits
            self.feed(sd.Tick(self.clock(), limits))
            if self.finished:
                break
            self.poll_gates()
            if self.finished:
                break
            res = self.client.check(wait=True, timeout_ms=WAIT_MS)
            if oc.messages_of(res):
                idle = 0
                self.handle_delivery(res)
            else:
                idle += 1
                if res.get("connectionLost"):
                    time.sleep(2)
                if idle >= IDLE_POLLS:
                    idle = 0
                    self.idle_poll()

    def finish(self):
        tasks = self.state.tasks.values()
        approved = self.state.audit_result == "approved"
        ok = all(t.status == "done" for t in tasks) and approved
        summary = "rein ship %s: %d/%d tasks done, audit %s" % (
            self.change, sum(t.status == "done" for t in tasks), len(self.state.tasks),
            "approved" if approved else "not approved")
        for repo, wt in self.state.worktrees.items():
            try:
                self.client.worktree_set(wt, workspace_status="in-review", comment=summary)
            except OrcaError as exc:
                self.say("could not mark %s in-review: %s" % (repo, exc))
        self.say(summary)
        try:
            delta = quota.usage_delta(self.before or {}, self.limits())
            for provider, windows in delta.items():
                self.say("usage %s: %s" % (provider, ", ".join(
                    "%s %s" % (w, "n/a" if v is None else "%+g%%" % v) for w, v in windows.items())))
        except Exception as exc:  # best effort, never crash a finished run
            self.say("usage report unavailable: %s" % exc)
        self.token_report()
        return 0 if ok else 1

    def token_report(self):
        for repo, path in self.state.worktree_paths.items():
            try:
                folder = os.path.join(_tr.PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", path))
                summary = _tr.summarize(folder) if os.path.isdir(folder) else {}
                if not summary.get("agents_counted"):
                    self.say("tokens: no Claude transcripts for %s" % path)
                    continue
                summary["wf_id"], summary["change"] = "ship-%s-%s" % (self.state.run_id, repo), self.change
                recorded = _tr.append_to_ledger(summary, _tr.LEDGER_PATH)
                print(_tr.render_text(summary, recorded), flush=True)
            except Exception as exc:
                self.say("tokens: report for %s failed: %s" % (path, exc))


def run_ship(root: str, change: str, client: OrcaClient, gate_runner: Callable, *, env: dict,
             clock: Callable[[], float], dry_run: bool = False, override: tuple | None = None,
             state_path: str = "") -> int:
    ctx, errors = preflight(root, change, client, env)
    if errors:
        for e in errors:
            print("ship: preflight: %s" % e)
        return 126
    ctx["cfg"].override = override
    spath = state_path or ss.state_path(ctx["root"])
    plan = ctx["plan"]
    try:
        state = None if dry_run else ss.load(spath)
    except ss.ShipStateCorrupt as exc:
        print("ship: %s" % exc)
        return 126
    if state is None:
        state = ss.from_plan(plan, ctx["default_repo"])
    else:
        ss.reconcile(state, plan, ctx["default_repo"])
    driver = _Driver(ctx, state, client, gate_runner, clock, spath, dry_run)
    try:
        driver.setup()
        if dry_run:
            driver.feed(sd.Tick(clock(), driver.limits()))
            driver.say("dry run: stopped after the first dispatch round; nothing was saved or gated")
            return 0
        if state.phase == "done":
            driver.finished = True
        else:
            driver.resume()
        driver.loop()
        return driver.finish()
    except OrcaError as exc:
        driver.save()
        print("ship: Orca failed (%s); state saved, re-run `rein ship %s` to resume" % (exc, change))
        return 126
