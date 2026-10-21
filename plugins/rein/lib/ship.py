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
import shlex
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
import verify as _verify
from orca_client import OrcaClient, OrcaError

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(PLUGIN, "bin", "rein")
CONTRACT = "orchestration.contract.v1"
DEFAULT_BASE_REF = "origin/develop"
WAIT_MS = 15000      # bounded: gate-resolve sends no inbox message, so a wait must wake to poll (and to apply queued answers)
IDLE_POLLS = 3       # empty waits in a row before asking worker-list what needs doing
SAFE_NEXT_ACTIONS = ("release", "retain")
_TAIL = 2000
_FAILURE_CAP = sd.GATE_FAILURE_CAP


# --- specs ---------------------------------------------------------------------

# What a ship worker needs to know about its role. Deliberately not the rein-role
# profile: that one describes a manual session (`rein next`, `rein close`, attempt
# caps), and here the driver claims, gates, retries and closes.
WORKER_PREAMBLE = (
    "You are a Rein ship worker with exactly one task. The driver runs the gate, retries and "
    "closes the task: do not run `rein gate`, `rein close` or tick the plan. Run the task's "
    "Verification command, fix what it reports, then commit as instructed below. "
    "If you are blocked, ask the owner with `orca orchestration ask --question <text>`."
)


DECISIONS_LIMIT = 1500


def _decisions_block(decisions: list) -> str:
    """One line per plan decision, whole lines only, at most DECISIONS_LIMIT characters."""
    out, size = [], 0
    for d in decisions:
        line = "- %s: %s" % (d.get("id"), d.get("title"))
        if d.get("rationale"):
            line += " \u2014 %s" % d["rationale"]
        line = line.replace("\n", " ")
        if size + len(line) + (1 if out else 0) > DECISIONS_LIMIT:
            if not out:
                out.append(line[:DECISIONS_LIMIT])
            break
        out.append(line)
        size += len(line) + (1 if len(out) > 1 else 0)
    return "\n".join(out)


def build_task_spec(task: ss.TaskState, plan: dict) -> str:
    entry = next((t for t in plan.get("tasks") or [] if t.get("id") == task.id), {})
    lines = [WORKER_PREAMBLE, "", "Task %s: %s" % (task.id, entry.get("title") or task.id)]
    if entry.get("acceptance"):
        lines += ["Acceptance criteria:"] + ["- %s" % c for c in entry["acceptance"]]
    if task.verification:
        lines.append("Verification command: %s" % task.verification)
    if plan.get("why"):
        lines += ["", "Why: %s" % plan["why"]]
    decisions = _decisions_block(plan.get("decisions") or [])
    if decisions:
        lines += ["", "Decisions (binding):", decisions]
    if plan.get("scopeOut"):
        lines += ["Out of scope:"] + ["- %s" % s for s in plan["scopeOut"]]
    if task.last_failure and task.attempts > 0:
        lines += ["", "The previous attempt failed this check; fix it:", task.last_failure]
    elif task.last_failure and task.id.startswith("AUDIT"):
        lines += ["", "The audit found these blocking problems; fix them:", task.last_failure]
    lines += ["", "Edit only the %s repository." % task.repo,
              "Commit your work on this worktree's branch when the verification passes. Never push.",
              "Never push, merge, open a pull request or deploy.",
              "Never read or print .env* files or credentials."]
    return "\n".join(lines)


REAUDIT_SCOPE = (
    "This is a re-audit after a fix round. Verify ONLY that each finding below is fixed. "
    "Report as BLOCKING only (a) a finding below that is not fixed, or (b) a defect the fix "
    "commits introduced that breaks the change. Everything else is IMPORTANT or SUGGESTION "
    "\u2014 do not re-audit the whole change."
)


INLINE_REVIEW = ("If you cannot launch the code-reviewer agent (Codex, Antigravity and other non-Claude "
                 "auditors cannot), do the five-axis review yourself: correctness, readability, "
                 "architecture, security, performance. Never ask the gate for a reviewer.")


def build_audit_spec(plan: dict, changed: dict, reviewer_profile: str,
                     audit_root: str = "", change: str = "", previous_blocking: list = (),
                     fix_commits: dict = None) -> str:
    """The auditor's brief. The verdict is recorded in the audit worktree, never the plan repo.

    With `previous_blocking` it is a re-audit: only those findings are verified."""
    lines = [reviewer_profile.strip(), ""]
    if previous_blocking:
        lines += ["You wrote none of the change.", REAUDIT_SCOPE] + ["- %s" % f for f in previous_blocking]
        for path, info in (fix_commits or {}).items():
            lines.append("Fix-round commits in %s (since %s):" % (path, info.get("base") or "?"))
            lines += ["  %s" % c for c in (info.get("log") or [])[:30]] or ["  (none)"]
    else:
        lines.append("Audit the change as a whole; you wrote none of it.")
    if plan.get("why"):
        lines.append("Why: %s" % plan["why"])
    lines.append(INLINE_REVIEW)
    lines.append("Audit worktree (your working directory): %s" % audit_root)
    for path, files in changed.items():
        lines.append("Run the rein-audit skill on %s" % path)
        # Files outside the audit worktree are listed absolute, so --files can name them.
        lines += ["  changed: %s" % (f if path == audit_root else os.path.join(path, f)) for f in files[:50]]
    lines += ["Record the verdict with exactly this command, run in the audit worktree:",
              "  rein review record --root %s --change %s --verdict <APPROVED|CHANGES_REQUESTED> "
              "--reviewer <your name> --files <comma-separated changed files that still exist; "
              "absolute paths for files outside %s> --findings \"BLOCKING: ...|IMPORTANT: ...\""
              % (shlex.quote(audit_root), shlex.quote(change), audit_root),
              "Never record into any other checkout. If `rein` is not on PATH, run: python3 %s" % BIN,
              "Never push, merge, open a pull request or deploy.",
              "Never read or print .env* files or credentials."]
    return "\n".join(lines)


def head_sha(path: str) -> str:
    """HEAD of a worktree, or "" when git cannot say."""
    try:
        return _git_lines(path, "rev-parse", "HEAD")[0]
    except (RuntimeError, IndexError, OSError):
        return ""


def fix_log(path: str, base: str) -> list:
    """`git log --oneline <base>..HEAD` of a worktree; [] when git cannot say."""
    try:
        return _git_lines(path, "log", "--oneline", "%s..HEAD" % base)
    except (RuntimeError, OSError):
        return []


def _git_lines(path: str, *args: str) -> list:
    proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:500] or "git %s failed" % args[0])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def changed_files(worktree_path: str, base_ref: str) -> list:
    """Everything the workers changed: commits since the base, uncommitted edits and new files.

    A worker that forgot to commit must not make its change invisible to the
    audit, so all three are united. Rein's own `.rein/` state is not a change.
    """
    found = set(_git_lines(worktree_path, "diff", "--name-only", "%s...HEAD" % base_ref))
    found.update(_git_lines(worktree_path, "diff", "--name-only", "HEAD"))
    found.update(_git_lines(worktree_path, "ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in found if not f.startswith(".rein/"))


_GATE_COMMANDS_BOUND = 6  # generous bound on configured gate commands (test, lint, typecheck, ...)


def _fit_blocks(blocks: list, cap: int) -> str:
    """`[(header, lines)]` joined within `cap` characters.

    Headers are never cut. A block that fits keeps all its lines; the rest
    share what is left evenly, and each keeps its decisive part (verify's
    selector: failure evidence first, the tail over the head)."""
    def render(parts):
        return "\n\n".join(("%s\n%s" % (h, "\n".join(ls))).rstrip() for h, ls in parts)

    whole = render(blocks)
    if len(whole) <= cap:
        return whole
    room = cap - sum(len(h) + 3 for h, _ in blocks)
    sizes = [sum(len(x) + 1 for x in ls) for _, ls in blocks]
    budget = [0] * len(blocks)
    left = list(range(len(blocks)))
    while left and room > 0:  # water-fill: small blocks keep everything, big ones share the rest
        share = room // len(left)
        small = [i for i in left if sizes[i] <= share]
        if not small:
            for i in left:
                budget[i] = share
            break
        for i in small:
            budget[i] = sizes[i]
            room -= sizes[i]
            left.remove(i)
    parts = []
    for (h, ls), size, b in zip(blocks, sizes, budget):
        if size > b:
            ls = _verify.failure_lines("\n".join(ls), cap=10 ** 6, max_chars=max(b, 0))
        parts.append((h, ls))
    return render(parts)[:cap]


def failure_detail(out: str) -> str:
    """What a failed gate tells the worker: the failing slots' decisive error lines.

    Built from the `rein gate --json` report when it parses. Only the slots the
    gate decides on count (`testOne`, `serve` and custom slots prove nothing).
    When no slot explains the verdict, the gate's own reason does. Anything
    else (timeout message, crash) keeps the plain tail."""
    out = out or ""
    try:
        data = json.loads(out)
        results = data["report"]["results"]
        decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}
        blocks = []
        for slot, res in sorted(results.items()):
            if slot not in _gate.GATE_SLOTS:
                continue
            if res.get("outcome") == "ok" or (
                    res.get("outcome") == "skipped"
                    or (res.get("outcome") == "proves_nothing"
                        and not (res.get("errorLines") or res.get("outputHead")))):
                continue
            lines = res.get("errorLines") or res.get("outputHead") or []
            if not lines and res.get("error"):
                lines = [str(res["error"])]
            blocks.append(("%s [%s] exit=%s $ %s" % (
                res.get("slot") or slot, res.get("outcome"), res.get("exitCode"), res.get("command")),
                [str(x) for x in lines]))
    except (ValueError, KeyError, TypeError, AttributeError):
        return out[-_TAIL:]
    if not blocks:
        reason = str(decision.get("reason") or "").strip()
        if reason:
            return "gate %s: %s" % (decision.get("decision") or "not green", reason)
        ran = ", ".join("%s [%s]" % (k, (v or {}).get("outcome")) for k, v in sorted(results.items()))
        return "the gate was not green but reported no failing check (%s)" % (ran or "nothing ran")
    return _fit_blocks(blocks, _FAILURE_CAP)


def default_gate_runner(path: str, unit_only: bool, timeout: float = 900.0) -> tuple:
    """`rein gate <path> --json [--unit-only] --timeout <s>`, with this process's Python and this checkout's bin.

    `timeout` is the per-command budget `rein gate` gives each configured command;
    the subprocess itself gets an outer bound so a hung gate cannot hang the driver.
    """
    argv = ([sys.executable, BIN, "gate", path, "--json"] + (["--unit-only"] if unit_only else [])
            + ["--timeout", "%g" % timeout])
    outer = timeout * _GATE_COMMANDS_BOUND + 60
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=outer)
    except subprocess.TimeoutExpired:
        return 126, "gate timed out: `rein gate` exceeded %gs overall" % outer
    # The JSON report alone when stdout is one: a warning on stderr must not break its parse.
    # Anything else keeps the whole capture, and `failure_detail` falls back to its tail.
    stdout = proc.stdout or ""
    try:
        json.loads(stdout)
        return proc.returncode, stdout
    except ValueError:
        return proc.returncode, stdout + (proc.stderr or "")


# --- preflight -----------------------------------------------------------------

def _load_plan(root: str, change: str) -> dict:
    plan_cfg = _detect.resolve(root).get("plan") or {}
    return _plan.read_plan(root, source=plan_cfg.get("source", ""), change=change,
                           configured=plan_cfg.get("path", ""))


def preflight(root: str, change: str, client: OrcaClient, env: dict,
              terminal_required: bool = True) -> tuple:
    """`(context, errors)`; any error means nothing may be started.

    `terminal_required=False` skips the ORCA_TERMINAL_HANDLE check, for `--detach`,
    which creates the Orca terminal the driver will run in.
    """
    root = os.path.abspath(root)
    errors: list = []
    default_repo = os.path.basename(root)
    ctx = {"root": root, "default_repo": default_repo, "repos": {}}
    if terminal_required and not env.get("ORCA_TERMINAL_HANDLE"):
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
    prof = _roles.profile("reviewer", PLUGIN)  # the auditor's brief; workers get WORKER_PREAMBLE
    ctx["reviewer_profile"] = prof["text"]
    if not prof["ok"]:
        errors.append(prof["error"])
    ctx["cfg"] = sd.ShipConfig.from_flow_config(_detect.resolve(root))
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


_GONE = ("succeeded", "failed", "stopped", "cancelled", "canceled", "abandoned", "exited", "terminated")


def settled_outcome(show: dict):
    """`"succeeded"`/`"failed"` once `worker-show` proves the worker is no longer running, else None.

    Positive proof only: a worker that is merely not listed, or a stop that was
    merely accepted, is not settled.
    """
    show = show if isinstance(show, dict) else {}
    worker, dispatch = show.get("worker") or {}, show.get("dispatch") or {}
    state = worker.get("state")
    if (worker.get("stage") == "settled" or dispatch.get("status") == "completed"
            or state in _GONE or dispatch.get("status") in _GONE):
        return "succeeded" if state == "succeeded" else "failed"
    return None


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
        self.change = state.change  # run_ship refuses a state that belongs to another change
        self.finished = False
        self.changed_by_wt: dict = {}
        self.ignored: set = set()
        self.noted: set = set()

    # -- plumbing
    def say(self, text):
        print("ship: %s" % text, flush=True)

    def ring(self, text):
        """`say` plus a terminal bell: Orca turns it into a notification for a background tab."""
        print("ship: %s%s" % (text, "" if self.dry_run else "\a"), flush=True)

    def save(self):
        if not self.dry_run:
            ss.save(self.state, self.spath)

    def limits(self):
        try:
            return quota.parse_rate_limits({"result": self.client.account_list()})
        except OrcaError as exc:
            self.say("could not read rate limits (%s); treating usage as unknown" % exc)
            return quota.parse_rate_limits({})

    def note_quota_before(self, limits):
        """Persist the run's first usable quota snapshot; deltas are measured from it, across resumes."""
        known = any(v is not None for window in limits.values() for v in window.values())
        if known and not self.state.quota_before:
            self.state.quota_before = {p: dict(w) for p, w in limits.items()}
            self.save()

    def recover_exhausted_findings(self, event):
        """A "fix" on an audit_exhausted gate opened by an older Rein: it stored no findings.
        Take them from the latest review episode, and the changed files from the worktrees."""
        gate = self.state.gates.get(event.gate_id) or {}
        if (gate.get("kind") != "audit_exhausted" or event.resolution != "fix"
                or self.state.phase != "auditing" or self.state.pending_findings):
            return
        findings = []
        if self.state.audit_root:
            found = _gate.check_review(self.state.audit_root, self.change).get("findings") or []
            findings = ["%s: %s" % (f["severity"], f["text"]) for f in found]
        self.state.pending_findings = findings
        if not self.state.pending_changed_files:
            changes = self.compute_changes()
            if isinstance(changes, sd.ChangesComputed):
                self.state.pending_changed_files = list(changes.changed_files)
            else:
                self.state.pending_changed_files = list(self.state.changed_files)
        self.save()

    def feed(self, event):
        if isinstance(event, sd.GateResolved):
            self.recover_exhausted_findings(event)
        queue = [event]
        while queue:
            rounds = self.state.audit_rounds
            self.state, actions = sd.decide(self.state, queue.pop(0), self.cfg)
            if self.state.audit_rounds > rounds and self.state.phase == "fixing":
                self.state.fix_round_base = {r: head_sha(p) for r, p in self.state.worktree_paths.items()}
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
        elif isinstance(a, sd.StopWorker):
            return self.stop_worker(a)
        elif isinstance(a, sd.AbandonWorker):
            try:
                self.client.worker_abandon(a.dispatch_id)
            except OrcaError as exc:  # the owner already confirmed it is gone
                self.say("%s: could not abandon worker %s: %s" % (a.task_id, a.dispatch_id, exc))
            else:
                self.say("%s: worker %s abandoned (owner confirmed it is gone)" % (a.task_id, a.dispatch_id))
        elif isinstance(a, sd.OpenGate):
            pass  # decide queued it in state.pending_gates; open_pending() creates it in Orca
        elif isinstance(a, sd.Relay):
            try:
                self.client.reply(a.message_id, a.body)
            except OrcaError as exc:
                if exc.code == "dispatch_inactive":  # the asker is gone: nobody can read the answer
                    self.say("dropped the answer to %s: its worker is no longer active (dispatch_inactive)"
                             % a.message_id)
                else:
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

    def orca_deps(self, task):
        """The CURRENT Orca task ids of this task's dependencies (a retried dependency has a new one)."""
        return [self.state.tasks[d].orca_task_id for d in task.deps
                if d in self.state.tasks and self.state.tasks[d].orca_task_id]

    def create_task(self, task):
        spec = build_task_spec(task, self.plan)
        entry = next((t for t in self.plan.get("tasks") or [] if t["id"] == task.id), {})
        deps = self.orca_deps(task)
        task.orca_task_id = oc.task_id_of(self.client.task_create(
            spec, ("%s %s" % (task.id, entry.get("title") or "")).strip(), deps))
        task.orca_deps = deps
        self.save()

    def dispatch(self, a):
        task = self.state.tasks[a.task_id]
        try:
            # Every worker start consumes an Orca task, so each dispatch gets a fresh one; so does a
            # task created against a dependency id that has since been replaced by a retry.
            if not task.orca_task_id or task.dispatches > 0 or task.orca_deps != self.orca_deps(task):
                self.create_task(task)
            task.dispatches += 1
            self.save()
            repo = self.ctx["repos"].get(a.repo) or {}
            fresh = a.worktree == "new-top-level"  # a reused worktree is already set up and named
            res = self.client.worker_start(
                task.orca_task_id, worktree=a.worktree, agent=a.agent, model=a.model,
                repo="id:%s" % repo["id"] if fresh and repo.get("id") else "",
                name=self.change if fresh else "", setup="run" if fresh else "")
        except OrcaError as exc:
            task.dispatch_id = ""
            task.status = "gated"
            question = "Orca could not start a worker for %s: %s" % (task.id, exc)
            residual = oc.residual_resources_of(exc)
            if residual:
                question += " (left behind in Orca: %s)" % ", ".join(residual)
            self.state.pending_gates.append({
                "task_id": task.id, "kind": "setup", "message_id": "", "options": ["retry", "skip"],
                "question": question})
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

    def stop_worker(self, a):
        """Stop a timed-out worker and report whether Orca proves it is gone."""
        detail = ""
        try:
            self.client.worker_stop(a.dispatch_id)
        except OrcaError as exc:  # it may already have exited; the look below decides
            detail = "worker-stop failed: %s" % exc
        try:
            confirmed = settled_outcome(self.client.worker_show(a.dispatch_id)) is not None
        except OrcaError as exc:
            return sd.WorkerStopped(a.task_id, a.resolution, False, "worker-show failed: %s" % exc)
        self.say("%s: worker %s %s" % (a.task_id, a.dispatch_id, "stopped" if confirmed else "not confirmed stopped"))
        return sd.WorkerStopped(a.task_id, a.resolution, confirmed,
                                detail or "Orca still reports the worker as running")

    def open_pending(self):
        """Create every owner gate decide asked for and Orca does not have yet.

        Orca refuses a gate on a task whose supervised dispatch is active, so
        every gate lives on its own worker-less "holder" task. The holder is
        recorded in the entry before gate-create runs, so a failed gate-create
        (or a crash) retries on the same holder instead of creating another.
        A failed call leaves the request in `state.pending_gates`; the next
        call (every loop iteration, and on resume) tries again.
        """
        for entry in list(self.state.pending_gates):
            task_id = entry.get("task_id")
            kind = entry.get("kind")
            try:
                if not entry.get("holder_task"):
                    holder = oc.task_id_of(self.client.task_create(
                        spec="Rein ship owner decision (%s) for %s. No worker is ever started for this task."
                             % (kind, task_id or "run"),
                        title="gate %s %s" % (kind, task_id or "run"), deps=[]))
                    if not holder:
                        raise OrcaError("no_task_id", "task-create returned no task id")
                    entry["holder_task"] = holder
                    self.save()
                gate_id = oc.gate_id_of(self.client.gate_create(
                    entry["holder_task"], str(entry.get("question"))[:2000], entry.get("options") or []))
            except OrcaError as exc:
                self.say("could not open the %s gate, will retry: %s" % (kind, exc))
                continue
            self.state.gates[gate_id] = {"task_id": task_id, "kind": kind,
                                         "message_id": entry.get("message_id", ""), "audit": entry.get("audit", ""),
                                         "holder_task": entry["holder_task"]}
            self.state.pending_gates.remove(entry)
            self.save()
            self.ring("waiting for the owner (%s): %s" % (kind, str(entry.get("question"))[:200]))

    def run_gate(self, a):
        task = self.state.tasks[a.task_id]
        path = self.state.worktree_paths.get(task.repo, "")
        if not path:
            return sd.GateVerdict(a.task_id, 126, "no worktree is recorded for %s" % task.repo)
        try:
            code, out = self.gate_runner(path, a.unit_only, self.cfg.gate_timeout_seconds)
        except OSError as exc:
            code, out = 126, "gate could not run: %s" % exc
        self.say("%s gate exit %s" % (task.id, code))
        return sd.GateVerdict(a.task_id, code, failure_detail(out))

    def compute_changes(self):
        if self.state.audit_dispatch:
            return None
        flat, by_wt, errors = set(), {}, []
        for repo, wt in self.state.worktrees.items():
            path = self.state.worktree_paths.get(repo) or oc.worktree_path_of(wt)
            base = (self.ctx["repos"].get(repo) or {}).get("worktreeBaseRef")
            if not base:
                base = DEFAULT_BASE_REF
                self.say("warning: Orca has no worktreeBaseRef for %s; diffing %s against %s" % (repo, path, base))
            try:
                files = changed_files(path, base)
            except (RuntimeError, OSError) as exc:
                errors.append("%s (%s) against %s: %s" % (repo, path, base, exc))
                continue
            by_wt[path] = files
            flat.update("%s/%s" % (repo, f) for f in files)
        if errors:  # auditing nothing would pass an unreviewed change
            return sd.ChangesFailed("; ".join(errors))
        self.changed_by_wt = by_wt
        return sd.ChangesComputed(sorted(flat), self.limits())

    def start_audit(self, a):
        if not self.state.worktrees or self.state.audit_dispatch:
            if not self.state.worktrees:
                self.say("no worktree was used; nothing to audit")
                self.state.phase, self.finished = "done", True
            return
        repo, first = next(iter(self.state.worktrees.items()))
        audit_root = self.state.worktree_paths.get(repo) or oc.worktree_path_of(first)
        fix_commits = {}
        if self.state.previous_blocking:
            for r, p in self.state.worktree_paths.items():
                base = self.state.fix_round_base.get(r)
                if base:
                    fix_commits[p] = {"base": base, "log": fix_log(p, base)}
        spec = build_audit_spec(self.plan, self.changed_by_wt, self.ctx["reviewer_profile"],
                                audit_root, self.change, self.state.previous_blocking, fix_commits)
        task_id = oc.task_id_of(self.client.task_create(
            spec, "%s audit round %d" % (self.change, self.state.audit_rounds + 1), []))
        res = self.client.worker_start(task_id, worktree="id:%s" % first, agent=a.agent, model=a.model)
        self.state.audit_dispatch = oc.dispatch_id_of(res)
        self.state.audit_root = audit_root
        self.state.audit_baseline = _episode_stamp(_gate.latest_episode(audit_root, self.change))
        self.state.audit_started_at = 0.0
        self.save()
        self.say("audit started on %s/%s in %s (%s)" % (a.agent, a.model, audit_root, self.state.audit_dispatch))

    # -- events from Orca
    def apply_answers(self):
        """Resolve, as the coordinator, the gates the owner answered through `rein answer` (the mailbox)."""
        answers = ss.read_answers(self.root, self.change)
        if not answers:
            return
        try:
            pending = {g.get("id") for g in oc.gates_of(self.client.gate_list()) if g.get("status") == "pending"}
        except OrcaError as exc:
            if ("answers", "list") not in self.noted:
                self.noted.add(("answers", "list"))
                self.say("could not read the gates to apply queued answers, will retry: %s" % exc)
            return
        for a in answers:
            gid = a["gate"]
            if gid not in self.state.gates or gid not in pending:
                ss.drop_answer(self.root, self.change, gid)  # already resolved or not this run's
                if gid not in self.state.gates:
                    self.say("dropped a queued answer for %s: it is not an open gate of this run" % gid)
                continue
            try:
                self.client.gate_resolve(gid, a["resolution"])  # no --from: this terminal is the coordinator
            except OrcaError as exc:
                if (gid, "answer") not in self.noted:
                    self.noted.add((gid, "answer"))
                    self.say("could not apply the queued answer for %s, will retry: %s" % (gid, exc))
                continue
            ss.drop_answer(self.root, self.change, gid)

    def poll_gates(self):
        """Feed every newly resolved gate; True when one was, so the loop acts on it before it waits."""
        fed = False
        for g in oc.gates_of(self.client.gate_list()):
            gid = g.get("id")
            if g.get("status") == "resolved" and gid in self.state.gates and gid not in self.ignored:
                self.feed(sd.GateResolved(gid, str(g.get("resolution") or "")))
                fed = True
                if gid in self.state.gates:  # decide refused this resolution; do not replay it forever
                    self.ignored.add(gid)
        return fed

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
        audit_root = self.state.audit_root
        episode = _gate.latest_episode(audit_root, self.change) if audit_root else None
        if outcome != "succeeded" or episode is None or _episode_stamp(episode) == self.state.audit_baseline:
            return sd.AuditVerdict("NO_VERDICT", 1, changed,
                                   ["the audit worker ended (%s) without recording a review episode in %s"
                                    % (outcome or "?", audit_root or "(no audit worktree)")])
        findings = _gate.check_review(audit_root, self.change).get("findings") or []
        blocking = sum(1 for f in findings if f["severity"] == "BLOCKING")
        return sd.AuditVerdict(str(episode.get("verdict")), blocking, changed,
                               ["%s: %s" % (f["severity"], f["text"]) for f in findings])

    def probe_audit(self):
        """Ask Orca about the audit worker; a finished one whose message we missed is read now."""
        did = self.state.audit_dispatch
        if not did:
            return
        outcome = settled_outcome(self.client.worker_show(did))
        if outcome is not None:
            self.worker_done(did, outcome)

    def settled_row(self, row):
        """`"succeeded"`/`"failed"` when a `worker-list` row clearly says how its worker ended, else None.

        Like `settled_outcome`, positive proof only: a terminal dispatch status
        without a clear outcome is not settled (the task timeout covers it).
        """
        outcome = (row.get("projection") or {}).get("outcome") or row.get("workerState")
        return outcome if outcome in ("succeeded", "failed") else None

    def lost_completions(self, rows):
        """`WorkerDone` events for tasks whose worker Orca reports settled but whose done we never folded in.

        A task is waiting for its worker when it is running, or gated on a
        question (a worker whose ask expired goes on and finishes). Positive
        evidence only: a live row, or a worker Orca cannot be asked about, is
        left alone. Feeding the event moves the task out of those states, so
        the next poll finds nothing to recover.
        """
        events = []
        for t in self.state.tasks.values():
            if not t.dispatch_id or not (t.status == "running" or sd.holds_question(self.state, t)):
                continue
            row = rows.get(t.dispatch_id)
            if row is not None:
                outcome = self.settled_row(row)
            else:
                # Not listed is not the same as exited: only Orca's word that it settled counts.
                try:
                    outcome = settled_outcome(self.client.worker_show(t.dispatch_id))
                except OrcaError as exc:
                    outcome = None
                    self.say("%s: cannot look up %s (%s)" % (t.id, t.dispatch_id, exc))
                if outcome is None and (t.dispatch_id, "unconfirmed") not in self.noted:
                    self.noted.add((t.dispatch_id, "unconfirmed"))
                    self.say("%s: %s is not in worker-list and Orca does not confirm it exited; "
                             "leaving it be (the task timeout covers it)" % (t.id, t.dispatch_id))
            if outcome is not None:
                events.append(sd.WorkerDone(t.dispatch_id, outcome))
        return events

    def idle_poll(self):
        rows = self.rows()
        recovered = set()
        for event in self.lost_completions({str(r.get("dispatchId")): r for r in rows}):
            recovered.add(event.dispatch_id)
            self.say("recovered a lost worker_done for %s (%s)" % (event.dispatch_id, event.outcome))
            self.feed(event)
        # After the feed above: a recovered task's worker is held by `gating`, or already released.
        held = {t.dispatch_id for t in self.state.tasks.values()
                if t.dispatch_id and t.status in ("running", "gating", "gated")}
        for row in rows:
            action = (row.get("projection") or {}).get("nextAction")
            kind = action.get("kind") if isinstance(action, dict) else ""
            did = str(row.get("dispatchId") or "")
            if not kind or did == self.state.audit_dispatch or did in recovered:
                continue  # a recovered worker was already released by decide; `rows` is a stale snapshot
            if kind not in SAFE_NEXT_ACTIONS:
                if (did, kind) not in self.noted:  # never acted on: only release/retain run
                    self.noted.add((did, kind))
                    self.say("ignoring unknown nextAction %r for %s" % (kind, did))
            elif did not in held and action.get("argv") == ["orchestration", "worker-%s" % kind, "--dispatch", did]:
                try:
                    self.client.call(*action["argv"])
                except OrcaError as exc:
                    self.say("could not %s %s: %s" % (kind, did, exc))
                    continue
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
                if t.dispatch_id:
                    # A worker may still be running (a lost timeout or question): retry/skip must
                    # stop it and see proof of exit, exactly like a timeout gate.
                    kind, options = "timeout", ["retry", "skip"]
                    question = "Task %s was waiting on a decision that was lost. Retry it, or skip it?" % t.id
                elif t.human_review:
                    kind, options = "human_review", ["approve", "reject"]
                    question = "Task %s needs human review before it can be closed." % t.id
                else:
                    kind, options = "setup", ["retry", "skip"]
                    question = "Task %s was waiting on a decision that was lost. Retry it, or skip it?" % t.id
                self.state.pending_gates.append({
                    "task_id": t.id, "kind": kind, "message_id": "", "options": options, "question": question})
        if self.state.phase == "braked" and not any(
                g.get("kind") == "brake" for g in list(self.state.gates.values()) + self.state.pending_gates):
            self.state.pending_gates.append({
                "task_id": None, "kind": "brake", "message_id": "",
                "options": ["retry", "pause"],
                "question": "The run was braked on quota. Retry once quota frees up, or pause?"})
        self.save()

    def supersede_old_exhausted_gates(self):
        """An audit_exhausted gate opened by an older Rein offers only accept/reject. Stop tracking it
        (Orca may keep it pending; it is never resolved with a verdict) and queue one that offers "fix"."""
        olds = [gid for gid, g in self.state.gates.items() if g.get("kind") == "audit_exhausted"]
        if not olds:
            return
        try:
            listed = {g.get("id"): g for g in oc.gates_of(self.client.gate_list()) if isinstance(g, dict)}
        except OrcaError as exc:
            self.say("could not read the gates to check the audit gate, will retry on the next resume: %s" % exc)
            return
        for gid in olds:
            orca = listed.get(gid)
            if orca is None or orca.get("status") != "pending" or "fix" in (orca.get("options") or []):
                continue
            del self.state.gates[gid]
            self.state.pending_gates.append({
                "task_id": None, "kind": "audit_exhausted", "message_id": "", "audit": "",
                "options": ["fix", "accept", "reject"], "question": str(orca.get("question") or "")})
            self.say("replaced the old audit gate %s with one that also offers \"fix\"" % gid)
        self.save()

    def resume(self, owner_restart: bool = True):
        if owner_restart:  # running `rein ship` again is the owner asking to continue; an automatic resume keeps a pause
            self.state.paused = False
        self.supersede_old_exhausted_gates()
        self.requeue_orphans()
        self.open_pending()
        self.apply_answers()
        rows = {str(r.get("dispatchId")): r for r in self.rows()}
        events = self.lost_completions(rows)
        for t in self.state.tasks.values():
            if t.status == "running" and not t.dispatch_id:
                t.status = "pending"  # decided but never started
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
            elif any(g.get("kind") in sd.AUDIT_HOLDS and g.get("task_id") is None
                     for g in list(self.state.gates.values()) + self.state.pending_gates):
                self.ring("waiting for the owner about the audit; not restarting it")
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
            self.note_quota_before(limits)
            self.feed(sd.Tick(self.clock(), limits))
            if self.finished:
                break
            self.apply_answers()
            if self.poll_gates() and not self.finished:
                continue  # the owner's answer may free work: Tick before waiting for the next message
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
        if self.state.phase == "done":  # a paused run is not in review: it will be resumed
            for repo, wt in self.state.worktrees.items():
                try:
                    self.client.worktree_set("id:%s" % wt, workspace_status="in-review", comment=summary)
                except OrcaError as exc:
                    self.say("could not mark %s in-review: %s" % (repo, exc))
        else:
            self.say("stopped before the audit; re-run `rein ship %s` to continue" % self.change)
        self.ring(summary)
        delta = {}
        try:
            delta = quota.usage_delta(self.state.quota_before, self.limits())
            for provider, windows in delta.items():
                self.say("usage %s: %s" % (provider, ", ".join(
                    "%s %s" % (w, "n/a" if v is None else "%+g%%" % v) for w, v in windows.items())))
        except Exception as exc:  # best effort, never crash a finished run
            self.say("usage report unavailable: %s" % exc)
        self.token_report(delta)
        return 0 if ok else 1

    def token_report(self, delta=None):
        # Quota use is per provider for the whole run, so every repo's ledger row carries the same figures.
        used = {p: w for p, w in (delta or {}).items() if any(v is not None for v in w.values())}
        for repo, path in self.state.worktree_paths.items():
            try:
                folder = os.path.join(_tr.PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", path))
                summary = _tr.summarize(folder) if os.path.isdir(folder) else {}
                if not summary.get("agents_counted"):
                    self.say("tokens: no Claude transcripts for %s" % path)
                    continue
                summary["wf_id"], summary["change"] = "ship-%s-%s" % (self.state.run_id, repo), self.change
                if used:
                    summary["quota"] = used
                recorded = _tr.append_to_ledger(summary, _tr.LEDGER_PATH)
                print(_tr.render_text(summary, recorded), flush=True)
            except Exception as exc:
                self.say("tokens: report for %s failed: %s" % (path, exc))


def ship_tab_title(change: str) -> str:
    return "rein ship %s" % change


def detach_ship(root: str, change: str, client: OrcaClient, *, override: tuple | None = None) -> int:
    """Open a new Orca terminal in the plan repo that runs `rein ship <change>`, then return.

    Plan errors surface here (exit 126) before any terminal exists, and a live
    tab for the same change in the same worktree refuses (exit 1) so a second
    driver is never started. Starting nothing but the terminal: the loop runs there.
    """
    root = os.path.abspath(root)
    _, errors = preflight(root, change, client, {}, terminal_required=False)
    if errors:
        for e in errors:
            print("ship: preflight: %s" % e)
        return 126
    title = ship_tab_title(change)
    try:
        for t in oc.terminals_of(client.terminal_list("path:%s" % root)):
            same_tree = os.path.realpath(str(t.get("worktreePath") or "")) == os.path.realpath(root)
            if t.get("title") == title and same_tree and t.get("connected") is not False:
                print("ship: a '%s' tab already exists in %s; if its run finished, close that tab and try again"
                      % (title, root))
                return 1
        # Not a bare `rein`: it may not be on PATH in the new tab. This interpreter and checkout always work.
        argv = [sys.executable, BIN, "ship", change, "--root", root]
        if override:
            argv += ["--agent", override[0], "--model", override[1]]
        handle = oc.terminal_handle_of(client.terminal_create(
            "path:%s" % root, title, shlex.join(argv)))
    except OrcaError as exc:
        print("ship: could not open the Orca tab (%s)" % exc)
        return 1
    print("terminal: %s" % (handle or "(no handle reported)"))
    print("rein ship is running in the Orca tab '%s'; its questions come back to the session that runs `rein watch`." % title)
    return 0


RESUME_POLL_SECONDS = 5
RESUME_WAIT_SECONDS = 600
RESUME_MAX = 3
STATUS_TIMEOUT_SECONDS = 15  # one `orca status` during the wait; a hung CLI counts as not back yet


def is_transient(exc: OrcaError) -> bool:
    """An Orca restart (auto-update, crash) looks like a closed connection or a missing runtime."""
    return (exc.code in ("runtime_unavailable", "unavailable")
            or (exc.code.startswith("exit_") and "connection" in exc.message.lower()))


def _wait_for_orca(client: OrcaClient, clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    deadline = clock() + RESUME_WAIT_SECONDS
    while True:
        try:
            client.status(timeout=STATUS_TIMEOUT_SECONDS)
            return True
        except OrcaError:
            pass
        if clock() >= deadline:
            return False
        sleep(RESUME_POLL_SECONDS)


def run_ship(root: str, change: str, client: OrcaClient, gate_runner: Callable, *, env: dict,
             clock: Callable[[], float], dry_run: bool = False, override: tuple | None = None,
             state_path: str = "", sleep: Callable[[float], None] = time.sleep) -> int:
    ctx, errors = preflight(root, change, client, env)
    if errors:
        for e in errors:
            print("ship: preflight: %s" % e)
        return 126
    ctx["cfg"].override = override
    spath = state_path or ss.state_path(ctx["root"], change)
    plan = ctx["plan"]
    resumes = 0
    while True:
        try:
            state = None if dry_run else ss.load(spath)
        except ss.ShipStateCorrupt as exc:
            print("ship: %s" % exc)
            return 126
        if state is not None and state.change != change:
            print("ship: %s belongs to change %r, not %r; move it aside or ship that change"
                  % (spath, state.change, change))
            return 126
        if state is None:
            state = ss.from_plan(plan, ctx["default_repo"], change)
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
                driver.resume(owner_restart=resumes == 0)
            driver.loop()
            return driver.finish()
        except OrcaError as exc:
            driver.save()
            if not dry_run and is_transient(exc) and resumes < RESUME_MAX:
                print("ship: Orca is restarting (%s); waiting to resume…" % exc.code, flush=True)
                if _wait_for_orca(client, clock, sleep):
                    resumes += 1
                    print("ship: Orca is back; resuming", flush=True)
                    continue
            print("ship: Orca failed (%s); state saved, re-run `rein ship %s` to resume" % (exc, change))
            return 126
