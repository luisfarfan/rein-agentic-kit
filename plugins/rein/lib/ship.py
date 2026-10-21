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
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

import affected as _affected
import detect as _detect
import gate as _gate
import gate_env as _gate_env
import gate_run as _gate_run
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
GATE_WAIT_MS = 5000  # the Orca wait while a gate runs in the background, so its verdict is fed soon after it lands
GATE_POLL_SECONDS = 1.0
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
    "Verification command and the tests affected by what you touched (the tests of the modules "
    "you changed and of the code that imports them), fix what they report, then commit as "
    "instructed below. Do not run the project's full test suite: after you, the driver runs lint, "
    "typecheck and the affected tests, and the full suite once per repo before the audit. "
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


def env_instruction(env_lines: list) -> str:
    """The brief's first line when the repo declares `gate.env`: Orca cannot pass an environment."""
    if not env_lines:
        return ""
    return ("Test environment: run `%s` in your shell before any test command (in the same command "
            "when your shell does not keep variables); every test you run must use it." % " && ".join(env_lines))


def build_task_spec(task: ss.TaskState, plan: dict, env_lines: list = ()) -> str:
    entry = next((t for t in plan.get("tasks") or [] if t.get("id") == task.id), {})
    lines = [WORKER_PREAMBLE, "", "Task %s: %s" % (task.id, entry.get("title") or task.id)]
    if env_lines:
        lines = [env_instruction(list(env_lines)), ""] + lines
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
    findings = task.audit_findings
    if not findings and sd._is_fix(task) and task.attempts == 0:
        findings = task.last_failure  # a state saved before audit_findings existed
    if findings:
        lines += ["", "The audit found these blocking problems; fix them (a finding that is not about "
                  "the %s repository belongs to another repository's fix task):" % task.repo, findings]
    if sd.is_gate_fix(task) and task.last_failure:
        lines += ["", "The full test suite of %s (run once every task was done, before the audit) failed; "
                  "fix it -- the failing tests may be outside what the change touched:" % task.repo,
                  task.last_failure]
    elif task.last_failure and task.last_failure != findings:
        # Also after the owner's "retry", which resets the attempts: a fresh worker must not start blind.
        lines += ["", "The previous attempt failed this check; fix it:", task.last_failure]
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


AUDIT_SKILL = os.path.join(PLUGIN, "skills", "rein-audit", "SKILL.md")

INLINE_REVIEW = ("If you cannot launch the code-reviewer agent (Codex, Antigravity and other non-Claude "
                 "auditors cannot), do the five-axis review yourself: correctness, readability, "
                 "architecture, security, performance. Never ask the gate for a reviewer.")


def build_audit_spec(plan: dict, changed: dict, reviewer_profile: str,
                     audit_root: str = "", change: str = "", previous_blocking: list = (),
                     fix_commits: dict = None, env_lines: dict = None, gate_commands: dict = None) -> str:
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
    # Not every auditor has Claude's skill loader (Codex, Antigravity): the skill is a plain file.
    lines.append("The rein-audit skill is the file %s: read it and follow its steps "
                 "(no skill loader is needed)." % AUDIT_SKILL)
    lines.append("Audit worktree (your working directory): %s" % audit_root)
    for path, files in changed.items():
        lines.append("Run the rein-audit skill on %s" % path)
        if (env_lines or {}).get(path):
            lines.append("  " + env_instruction(env_lines[path]))
        if (gate_commands or {}).get(path):
            lines.append("  Mechanical checks: run `%s` -- not the test, lint or typecheck commands "
                         "themselves, and never with a shorter --timeout. A check that passed on this "
                        "exact state is reused (printed `cached`)." % gate_commands[path])
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


def audit_gate_command(path: str, repo: str = "", env_root: str = "") -> str:
    """The `rein gate` an auditor runs on a worktree: the same slots and env as Rein's own full gate
    before the audit, so every slot that passed there is a cache hit."""
    argv = ["rein", "gate", path]
    if repo:
        argv += ["--repo", repo]
    if env_root:
        argv += ["--env-root", env_root]
    return shlex.join(argv + ["--tier", "full"])


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
    return _affected.changed_files(worktree_path, base_ref)


def gate_outer_bound(path: str, unit_only: bool, timeout: float, repo: str = "", tier: str = "") -> float:
    """What one `rein gate` may take at most: the sum of the budgets of the slots it will run
    (gate_run.slot_budget), never less than one `timeout`."""
    commands = _detect.resolve(path).get("commands") or {}
    repo = repo or _gate_env.repo_name_of(path)
    slots = _gate_run.gate_slots(commands, integration=tier == "full" or (not tier and not unit_only))
    if tier == "task":
        slots = [s for s in slots if s in _gate_run.TASK_SLOTS or s == "test"]
    total = 0.0
    for slot in slots:
        configured = commands[slot]
        if tier == "task" and slot == "test" and commands.get("testOne"):
            configured = commands["testOne"]  # the affected run (step_budget: at least the suite's)
        total += _gate_run.step_budget(repo, slot, configured, commands, timeout)
    return max(total, timeout)


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


class GateCancelled(Exception):
    """The driver no longer needs this gate (its task timed out while gating)."""


def _gate_process(argv: list, timeout: float, cancel=None):
    """Run one `rein gate` to completion: a CompletedProcess, TimeoutExpired past `timeout`, or
    GateCancelled once `cancel` is set. Either way out, its whole process group is killed."""
    if cancel is None:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            return subprocess.CompletedProcess(argv, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            stop = cancel.is_set()
            if stop or time.monotonic() >= deadline:
                # SIGTERM first: `rein gate` then kills the check it is running (its own group).
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.communicate(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired, OSError, ValueError):
                    pass
                _verify._kill_group(proc)
                try:
                    proc.communicate(timeout=5)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
                if stop:
                    raise GateCancelled()
                raise subprocess.TimeoutExpired(argv, timeout)


def default_gate_runner(path: str, unit_only: bool, timeout: float = 900.0, *,
                        repo: str = "", env_root: str = "", tier: str = "", base: str = "",
                        cancel=None) -> tuple:
    """`rein gate <path> --json [--unit-only] --timeout <s>`, with this process's Python and this checkout's bin.

    `timeout` is the per-command budget `rein gate` gives each configured command;
    the subprocess itself gets an outer bound so a hung gate cannot hang the driver.
    `repo` and `env_root` (the plan root) let it expand the worktree's `gate.env`. `tier`
    "task" (with the change's `base`) runs lint, typecheck and the affected tests; "full"
    runs every gate slot, the repo's pre-audit gate. `cancel` (a threading.Event) stops it.
    """
    argv = ([sys.executable, BIN, "gate", path, "--json"] + (["--unit-only"] if unit_only else [])
            + ["--timeout", "%g" % timeout])
    if repo:
        argv += ["--repo", repo]
    if env_root:
        argv += ["--env-root", env_root]
    if tier:
        argv += ["--tier", tier]
    if base:
        argv += ["--base", base]
    outer = gate_outer_bound(path, unit_only, timeout, repo, tier) + 60
    try:
        proc = _gate_process(argv, outer, cancel)
    except subprocess.TimeoutExpired:
        return 126, "gate timed out: `rein gate` exceeded %gs overall" % outer
    except GateCancelled:
        return 126, "gate cancelled: the driver no longer needs it"
    # The JSON report alone when stdout is one: a warning on stderr must not break its parse.
    # Anything else keeps the whole capture, and `failure_detail` falls back to its tail.
    stdout = proc.stdout or ""
    try:
        json.loads(stdout)
        return proc.returncode, stdout
    except ValueError:
        return proc.returncode, stdout + (proc.stderr or "")


# Task types whose Verification is a person or a browser looking, never a command the driver runs.
UNRUN_VERIFICATION_TYPES = ("verification", "manual")

# A Verification is usually prose for a person. The driver runs only a field that BEGINS with one
# backticked command, and never one that reaches outside the worktree or the machine.
REFUSED_IN_VERIFICATION = ("--profile", "deploy", "git push", "push ", "rm -rf", "sudo", "curl ",
                           "wget ", "aws ", "cdk ", "terraform", "kubectl", "docker push", "npm publish",
                           "pnpm publish", "> /")
# "prod" as its own word (proxima-prod, --env prod, prod.yml), never inside "products".
_PROD_WORD = re.compile(r"(?<![a-z0-9])prod(?![a-z0-9])")
_REDIRECT_TARGET = re.compile(r">>?\|?\s*([^\s;&|<>()]+)")
_TEE_TARGET = re.compile(r"\btee\b((?:\s+-\S+)*)\s+([^\s;&|<>()]+)")


def _writes_outside(cmd: str) -> str:
    targets = [m.group(1) for m in _REDIRECT_TARGET.finditer(cmd)]
    targets += [m.group(2) for m in _TEE_TARGET.finditer(cmd)]
    for target in targets:
        if target.startswith("&") or target == "/dev/null":
            continue  # a descriptor duplication, or nowhere
        if target.startswith(("/", "~", "$")) or ".." in target.split("/"):
            return target
    return ""


def verification_refusal(cmd: str) -> str:
    """Why the driver must not run `cmd` on its own, or "" when it may."""
    low = cmd.lower()
    for bad in REFUSED_IN_VERIFICATION:
        if bad in low:
            return "it contains %r" % bad.strip()
    if _PROD_WORD.search(low):
        return "it contains 'prod'"
    outside = _writes_outside(cmd)
    if outside:
        return "it writes outside the worktree (%s)" % outside
    return ""


def task_verification_command(field: str) -> tuple:
    """`(command, "")` when the field begins with one backticked command the driver may run
    (the prose after it is ignored), else `("", why it is not run)`: the gate alone decides."""
    text = (field or "").strip()
    if not text.startswith("`") or text.startswith("``"):
        return "", "it is not a backticked command"
    end = text.find("`", 1)
    command = text[1:end].strip() if end > 0 else ""
    if not command:
        return "", "it is not a backticked command"
    refused = verification_refusal(command)
    if refused:
        return "", "refused: %s" % refused
    return command, ""


call_with = _gate_run.call_with  # older gate runners take three arguments


def run_task_verification(command: str, path: str, timeout: float, env: dict = None) -> tuple:
    """Run a task's own Verification command in its worktree: `(0, "")` when it passed,
    `(1, decisive lines)` when it failed or timed out, `(126, why)` when it could not run."""
    res = _verify.run_one("verification", command, path, timeout=timeout, env=env or None)
    if res.outcome == _verify.OUTCOME_OK:
        return 0, ""
    if res.outcome == _verify.OUTCOME_NOT_INVOCABLE:
        return 126, res.error or "the command could not be invoked"
    lines = _verify.failure_lines(res.output or "") or list(res.output_head or [])
    if res.outcome == _verify.OUTCOME_TIMEOUT:
        lines = ["timed out after %gs" % timeout] + lines
    elif "pytest" in command and res.exit_code == 5:
        lines = ["pytest selected no tests (exit 5): this verification confirms nothing"] + lines
    return 1, "\n".join(lines)


def _verification_block(command: str, code: int, text: str) -> str:
    return "task verification [%s] exit=%s $ %s\n%s" % (
        "failed" if code == 1 else "could not run", code, command, text)


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

class GatePool:
    """Gates on background threads, so a 10-minute suite never blocks the driver loop.

    The driver's thread is the only one that touches the ship state: a job gets its
    inputs captured beforehand and returns a verdict, which `results()` hands back to
    the driver to feed. One job per key at a time (a task's attempt, a repo); a
    cancelled job's verdict is dropped. The pool outlives a driver across an Orca
    restart, so a resumed run does not start a second gate on the same worktree."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running: dict = {}   # key -> cancel Event
        self._landed: list = []
        self._wake = threading.Event()

    def submit(self, key, job) -> bool:
        with self._lock:
            if key in self._running:
                return False
            cancel = threading.Event()
            self._running[key] = cancel

        def work():
            try:
                verdict = job(cancel)
            except Exception as exc:  # a crashed gate is a setup problem, never a hung driver
                verdict = _crashed(key, exc)
            with self._lock:
                self._running.pop(key, None)
                if not cancel.is_set():
                    self._landed.append((key, verdict))
                self._wake.set()
        threading.Thread(target=work, name="rein-gate-%s" % "-".join(map(str, key)), daemon=True).start()
        return True

    def results(self) -> list:
        with self._lock:
            out, self._landed = self._landed, []
            self._wake.clear()
            return out

    def running(self) -> list:
        with self._lock:
            return list(self._running)

    def busy(self) -> bool:
        with self._lock:
            return bool(self._running or self._landed)

    def wait(self, timeout: float) -> bool:
        """Block until a verdict lands (True) or `timeout` passes (False)."""
        with self._lock:
            if self._landed:
                return True
        return self._wake.wait(timeout) and bool(self._landed)

    def cancel(self, key) -> None:
        with self._lock:
            cancel = self._running.get(key)
        if cancel is not None:
            cancel.set()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel every running gate and give the threads a moment to kill their processes."""
        for key in self.running():
            self.cancel(key)
        deadline = time.monotonic() + timeout
        while self.running() and time.monotonic() < deadline:
            time.sleep(0.05)


def _crashed(key, exc):
    why = "the gate crashed: %s: %s" % (type(exc).__name__, exc)
    if key[0] == "repo":
        return sd.RepoGateVerdict(key[1], 126, why)
    return sd.GateVerdict(key[1], 126, why, key[2] if len(key) > 2 else "")


class _Driver:
    def __init__(self, ctx, state, client, gate_runner, clock, spath, dry_run, pool=None):
        self.pool = pool
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
        self.cancel_stale_gates()

    # -- actions
    def execute(self, a):
        if isinstance(a, sd.Dispatch):
            self.dispatch(a)
        elif isinstance(a, sd.RunGate):
            task = self.state.tasks[a.task_id]
            return self.submit_gate(("task", a.task_id, task.dispatch_id), self.task_gate_job(a))
        elif isinstance(a, sd.RunRepoGate):
            return self.submit_gate(("repo", a.repo), self.repo_gate_job(a))
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

    def worktree_env(self, repo: str, path: str) -> dict:
        """The worktree's expanded `gate.env` (the plan root's declaration, the repo's on top)."""
        if not path:
            return {}
        return _gate_env.for_worktree(path, repo=repo, env_root=getattr(self, "root", ""))

    def env_lines(self, repo: str) -> list:
        """What the worker's brief exports: the literal values once the worktree exists, else a
        `rein env` the worker evaluates in its new worktree; [] when nothing is declared."""
        path = self.state.worktree_paths.get(repo, "")
        if path:
            return _gate_env.export_lines(self.worktree_env(repo, path))
        repo_path = str((self.ctx.get("repos", {}).get(repo) or {}).get("path") or "")
        declared = _gate_env.for_worktree(repo_path or self.root, repo=repo, env_root=self.root)
        if not declared:
            return []
        return ['eval "$(python3 %s env --repo %s --env-root %s)"'
                % (shlex.quote(BIN), shlex.quote(repo), shlex.quote(self.root))]

    def create_task(self, task):
        spec = build_task_spec(task, self.plan, self.env_lines(task.repo))
        entry = next((t for t in self.plan.get("tasks") or [] if t["id"] == task.id), {})
        deps = self.orca_deps(task)
        task.orca_task_id = oc.task_id_of(self.client.task_create(
            spec, ("%s %s" % (task.id, entry.get("title") or "")).strip(), deps))
        task.orca_deps = deps
        self.save()

    @staticmethod
    def _row_worktree(row: dict) -> str:
        return str((row.get("resource") or {}).get("worktreeId")
                   or ((row.get("projection") or {}).get("workspace") or {}).get("id") or "")

    def _started_row(self, orca_task_id: str, rows=None):
        """The worker-list row Orca made for `orca_task_id`, or None. Raises OrcaError when Orca cannot say."""
        rows = self.rows() if rows is None else rows
        return next((r for r in rows if str(r.get("taskId") or "") == orca_task_id and r.get("dispatchId")), None)

    def adopt_started(self, task, rows=None, keep: bool = False) -> bool:
        """A worker-start whose answer was lost (a kill, a dropped connection): if Orca did start
        the worker, supervise it rather than starting a second one in a second worktree.

        True when adopted. When Orca lists no such worker the intent is cleared -- unless `keep`:
        right after the error Orca may not list it YET, so only a later look may give up on it.
        When Orca cannot be asked (OrcaError) it stays, for the next dispatch or resume."""
        if not task.starting:
            return False
        row = self._started_row(task.starting, rows)
        if row is None:
            if not keep:
                task.starting = ""
                self.save()
            return False
        task.starting = ""
        task.dispatch_id, task.status = str(row["dispatchId"]), "running"
        wt = self._row_worktree(row)
        if wt and task.repo not in self.state.worktrees:
            self.state.worktrees[task.repo] = wt
            self.state.worktree_paths[task.repo] = oc.worktree_path_of(wt)
        provider = str(((row.get("projection") or {}).get("provider") or {}).get("id") or "")
        if provider:
            task.agent = provider
        if task.agent and task.agent not in self.state.launched_agents:
            self.state.launched_agents.append(task.agent)
        self.save()
        self.say("%s: adopted worker %s, started before the driver could record it" % (task.id, task.dispatch_id))
        return True

    def _setup_gate(self, task, question):
        task.dispatch_id = ""
        task.status = "gated"
        self.state.pending_gates.append({
            "task_id": task.id, "kind": "setup", "message_id": "", "options": ["retry", "skip"],
            "question": question})
        self.save()
        self.open_pending()

    def dispatch(self, a):
        task = self.state.tasks[a.task_id]
        if task.starting:  # an earlier start's answer was lost: never start a second worker blind
            try:
                if self.adopt_started(task):
                    return
            except OrcaError as exc:
                self._setup_gate(task, "Rein cannot tell whether Orca started the earlier worker for %s (%s). "
                                       "Check Orca, then retry or skip." % (task.id, exc))
                return
        try:
            # Every worker start consumes an Orca task, so each dispatch gets a fresh one; so does a
            # task created against a dependency id that has since been replaced by a retry.
            if not task.orca_task_id or task.dispatches > 0 or task.orca_deps != self.orca_deps(task):
                self.create_task(task)
            task.dispatches += 1
            task.starting = task.orca_task_id  # persisted before the start: a kill mid-start is recoverable
            self.save()
            repo = self.ctx["repos"].get(a.repo) or {}
            fresh = a.worktree == "new-top-level"  # a reused worktree is already set up and named
            res = self.client.worker_start(
                task.orca_task_id, worktree=a.worktree, agent=a.agent, model=a.model,
                repo="id:%s" % repo["id"] if fresh and repo.get("id") else "",
                name=self.change if fresh else "", setup="run" if fresh else "",
                timeout=START_TIMEOUT_SECONDS)
        except OrcaError as exc:
            try:  # Orca may have started it and lost only the answer; it may also list it only later
                if self.adopt_started(task, keep=True):
                    return
            except OrcaError:
                pass  # `starting` stays set: the retry or the resume asks again
            question = "Orca could not start a worker for %s: %s" % (task.id, exc)
            residual = oc.residual_resources_of(exc)
            if residual:
                question += " (left behind in Orca: %s)" % ", ".join(residual)
            self._setup_gate(task, question)
            return
        task.starting = ""
        task.dispatch_id = oc.dispatch_id_of(res)
        wt = oc.worktree_of(res)
        if wt and a.repo not in self.state.worktrees:
            self.state.worktrees[a.repo] = wt
            self.state.worktree_paths[a.repo] = oc.worktree_path_of(wt)
        launch = oc.effective_launch_of(res)
        if launch.get("agent") and (launch["agent"], launch.get("model")) != (a.agent, a.model):
            self.say("Orca launched %s/%s for %s, not %s/%s" % (launch["agent"], launch.get("model"), task.id, a.agent, a.model))
            task.agent, task.model = launch["agent"], launch.get("model") or a.model
        if task.agent and task.agent not in self.state.launched_agents:
            self.state.launched_agents.append(task.agent)  # the audit avoids the provider that really ran
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
            for extra in ("repo", "reason"):  # a repo gate's repo, and a red one's failure
                if entry.get(extra):
                    self.state.gates[gate_id][extra] = entry[extra]
            self.state.pending_gates.remove(entry)
            self.save()
            self.ring("waiting for the owner (%s): %s" % (kind, str(entry.get("question"))[:200]))

    def base_ref(self, repo: str) -> str:
        """What the repo's worktree branched from: Orca's worktreeBaseRef, else origin/develop."""
        ctx = getattr(self, "ctx", None) or {}
        return (ctx.get("repos", {}).get(repo) or {}).get("worktreeBaseRef") or DEFAULT_BASE_REF

    def task_base(self, task) -> str:
        """The ref a task's affected tests are computed from: an audit-fix task's own round start
        (only what the fix touched), else the change's base."""
        if sd._is_fix(task):
            base = (getattr(self.state, "fix_round_base", None) or {}).get(task.repo)
            if base:
                return base
        return self.base_ref(task.repo)

    def repo_gate_job(self, a):
        """A callable `job(cancel=None) -> RepoGateVerdict` for the repo's full gate before the audit.

        Everything it needs is captured here, on the driver's thread: the job may run on a
        background thread and never reads the ship state."""
        path = self.state.worktree_paths.get(a.repo, "")
        repo, runner, timeout = a.repo, self.gate_runner, self.cfg.gate_timeout_seconds
        root, say = getattr(self, "root", ""), self.say

        def job(cancel=None):
            if not path:
                return sd.RepoGateVerdict(repo, 126, "no worktree is recorded for %s" % repo)
            try:
                code, out = call_with(runner, path, True, timeout, repo=repo, env_root=root, tier="full",
                                      cancel=cancel)
            except OSError as exc:
                code, out = 126, "gate could not run: %s" % exc
            say("%s full gate exit %s" % (repo, code))
            return sd.RepoGateVerdict(repo, code, failure_detail(out) if code else "")
        return job

    def run_repo_gate(self, a):
        """The repo's full gate before the audit: every gate slot, the whole suite."""
        return self.repo_gate_job(a)()

    def task_gate_job(self, a):
        """A callable `job(cancel=None) -> GateVerdict` for a task's gate and its own Verification.

        Inputs are captured now, on the driver's thread; the job never reads the ship state, so
        it may run on a background thread while the driver keeps handling events."""
        task = self.state.tasks[a.task_id]
        task_id, repo, dispatch_id = task.id, task.repo, getattr(task, "dispatch_id", "")
        verification, task_type = task.verification, task.type
        path = self.state.worktree_paths.get(repo, "")
        env = self.worktree_env(repo, path)
        # A task's gate: lint, typecheck and the tests its change reaches (the full suite runs once
        # per repo before the audit). A verification-type task keeps the default, render-aware gate.
        tier = {"tier": "task", "base": self.task_base(task)} if a.unit_only else {}
        runner, timeout, unit_only = self.gate_runner, self.cfg.gate_timeout_seconds, a.unit_only
        root, say = getattr(self, "root", ""), self.say

        def job(cancel=None):
            if not path:
                return sd.GateVerdict(task_id, 126, "no worktree is recorded for %s" % repo, dispatch_id)
            try:
                code, out = call_with(runner, path, unit_only, timeout, repo=repo, env_root=root,
                                      cancel=cancel, **tier)
            except OSError as exc:
                code, out = 126, "gate could not run: %s" % exc
            say("%s gate exit %s" % (task_id, code))
            detail = failure_detail(out)
            if cancel is not None and cancel.is_set():
                return sd.GateVerdict(task_id, 126, "the gate was cancelled", dispatch_id)
            command, why_not = task_verification_command(verification)
            runnable = code != 126 and verification.strip() and task_type not in UNRUN_VERIFICATION_TYPES
            if runnable and not command:
                say("%s verification not run (%s): the gate alone decides" % (task_id, why_not))
            elif runnable:
                # The task's own check, not only the project's: done needs both.
                try:
                    vcode, vtext = call_with(run_task_verification, command, path, timeout, env=env)
                except OSError as exc:
                    vcode, vtext = 126, "could not run: %s" % exc
                say("%s verification exit %s" % (task_id, vcode))
                if vcode == 126:
                    # A missing tool is setup, not the worker's code: no retry is burned on it.
                    say("%s verification could not run ($ %s): %s; the gate alone decides"
                        % (task_id, command, (vtext or "").splitlines()[0] if vtext else "not invocable"))
                elif vcode != 0:
                    block = _verification_block(command, vcode, vtext)
                    if code == 0:
                        code, detail = vcode, block[:_FAILURE_CAP]
                    else:
                        detail = _fit_blocks([(block.split("\n", 1)[0], block.split("\n")[1:]),
                                              ("gate", detail.split("\n"))], _FAILURE_CAP)
            return sd.GateVerdict(task_id, code, detail, dispatch_id)
        return job

    def run_gate(self, a):
        return self.task_gate_job(a)()

    def submit_gate(self, key, job):
        """Run `job` on the gate pool and return None (its verdict is fed when it lands), or run it
        here and return the verdict when the driver has no pool (tests, a dry run)."""
        pool = getattr(self, "pool", None)
        if pool is None:
            return job()
        if not pool.submit(key, job):
            self.say("a gate for %s is already running; not starting another" % (key,))
        return None

    def collect_gates(self) -> bool:
        """Feed every gate verdict that landed; True when one did."""
        pool = getattr(self, "pool", None)
        if pool is None:
            return False
        landed = pool.results()
        for _key, verdict in landed:
            if verdict is not None:
                self.feed(verdict)
        return bool(landed)

    def cancel_stale_gates(self):
        """A task that left `gating` (its task timeout opened a gate) no longer needs its gate."""
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for key in pool.running():
            if key[0] == "task":
                task = self.state.tasks.get(key[1])
                if task is None or task.status != "gating" or task.dispatch_id != key[2]:
                    pool.cancel(key)

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
        if self.state.audit_starting:  # an earlier audit start's answer was lost: look again first
            try:
                if self.adopt_audit():
                    return
            except OrcaError as exc:
                if is_transient(exc):
                    raise
                self.state.pending_gates.append({
                    "task_id": None, "kind": "setup", "message_id": "", "audit": "", "options": ["retry", "skip"],
                    "question": "Rein cannot tell whether Orca started the earlier auditor (%s). Check Orca, "
                                "then retry or skip." % exc})
                self.save()
                self.open_pending()
                return
        repo, first = next(iter(self.state.worktrees.items()))
        audit_root = self.state.worktree_paths.get(repo) or oc.worktree_path_of(first)
        fix_commits = {}
        if self.state.previous_blocking:
            for r, p in self.state.worktree_paths.items():
                base = self.state.fix_round_base.get(r)
                if base:
                    fix_commits[p] = {"base": base, "log": fix_log(p, base)}
        repo_of = {p: r for r, p in self.state.worktree_paths.items()}
        env_lines = {p: _gate_env.export_lines(self.worktree_env(repo_of.get(p, ""), p))
                     for p in self.changed_by_wt}
        gate_commands = {p: audit_gate_command(p, repo_of.get(p, ""), self.root) for p in self.changed_by_wt}
        spec = build_audit_spec(self.plan, self.changed_by_wt, self.ctx["reviewer_profile"],
                                audit_root, self.change, self.state.previous_blocking, fix_commits,
                                env_lines, gate_commands)
        try:
            task_id = oc.task_id_of(self.client.task_create(
                spec, "%s audit round %d" % (self.change, self.state.audit_rounds + 1), []))
            # Persisted before the start, with what reading the verdict needs: a kill mid-start
            # is adopted on resume instead of starting a second auditor.
            self.state.audit_starting = task_id
            self.state.audit_root = audit_root
            self.state.audit_baseline = _episode_stamp(_gate.latest_episode(audit_root, self.change))
            self.save()
            res = self.client.worker_start(task_id, worktree="id:%s" % first, agent=a.agent, model=a.model,
                                           timeout=START_TIMEOUT_SECONDS)
        except OrcaError as exc:
            if self.state.audit_starting:
                try:  # kept when not listed yet: the resume or the owner's retry looks again
                    if self.adopt_audit(keep=True):
                        return
                except OrcaError:
                    pass
            if is_transient(exc):
                raise  # Orca is restarting: run_ship waits, and the resume adopts or restarts the audit
            if a.agent not in self.state.audit_excluded:
                self.state.audit_excluded.append(a.agent)
            self.state.pending_gates.append({
                "task_id": None, "kind": "setup", "message_id": "", "audit": "", "options": ["retry", "skip"],
                "question": "Orca could not start the audit on %s/%s: %s. Retry picks another reviewer when one "
                            "is free; skip ends the audit unapproved." % (a.agent, a.model, exc)})
            self.save()
            self.open_pending()
            return
        self.state.audit_starting = ""
        self.state.audit_dispatch = oc.dispatch_id_of(res)
        self.state.audit_started_at = 0.0
        agent, model = a.agent, a.model
        launch = oc.effective_launch_of(res)
        if launch.get("agent") and (launch["agent"], launch.get("model")) != (a.agent, a.model):
            agent, model = launch["agent"], launch.get("model") or a.model
            self.say("Orca launched %s/%s for the audit, not %s/%s%s" % (
                agent, model, a.agent, a.model,
                " -- it also implemented part of this change" if agent in self.state.launched_agents else ""))
        self.save()
        self.say("audit started on %s/%s in %s (%s)" % (agent, model, audit_root, self.state.audit_dispatch))

    def adopt_audit(self, rows=None, keep: bool = False) -> bool:
        """The audit's worker-start answer was lost: supervise the auditor Orca did start, if any.
        `keep`: as for `adopt_started`, a miss right after the error does not give up the intent."""
        row = self._started_row(self.state.audit_starting, rows)
        if row is None and keep:
            return False
        self.state.audit_starting = ""
        if row is not None:
            self.state.audit_dispatch = str(row["dispatchId"])
            self.state.audit_started_at = 0.0
            self.say("adopted audit worker %s, started before the driver could record it" % self.state.audit_dispatch)
        self.save()
        return row is not None

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
        if not self.state.launched_agents and self.state.implementer_agents:
            # A state from before launched agents were recorded: what was asked is all it knows.
            self.state.launched_agents = list(self.state.implementer_agents)
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
        listed = self.rows()
        rows = {str(r.get("dispatchId")): r for r in listed}
        events = self.lost_completions(rows)
        for t in self.state.tasks.values():
            if t.status == "running" and not t.dispatch_id:
                if t.starting and self.adopt_started(t, listed):
                    continue  # killed mid-start: Orca did start it
                t.starting = ""
                t.status = "pending"  # decided but never started
            elif t.status == "gating":
                events.append(sd.RunGate(t.id, t.type != "verification"))
        self.save()
        for e in events:
            if isinstance(e, sd.RunGate):
                self.feed_gate(e)
            else:
                self.feed(e)
        for repo, status in list(self.state.repo_gates.items()):
            if self.state.phase in ("verifying", "running", "fixing") and status == "running":
                follow = self.execute(sd.RunRepoGate(repo))  # interrupted mid-gate: run it again
                if follow is not None:
                    self.feed(follow)
        if self.state.phase == "auditing" and self.state.audit_starting and not self.state.audit_dispatch:
            self.adopt_audit(listed)
        if self.state.phase == "auditing":
            if self.state.audit_dispatch:
                self.probe_audit()
            elif any(g.get("kind") in sd.AUDIT_HOLDS and g.get("task_id") is None
                     for g in list(self.state.gates.values()) + self.state.pending_gates):
                self.ring("waiting for the owner about the audit; not restarting it")
            else:
                self.feed_changes()

    def feed_gate(self, run_gate):
        verdict = self.execute(run_gate)
        if verdict is not None:
            self.feed(verdict)

    def feed_changes(self):
        event = self.compute_changes()
        if event is not None:
            self.feed(event)

    def loop(self):
        idle = 0
        while not self.finished:
            self.collect_gates()
            if self.finished:
                break
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
            pool = getattr(self, "pool", None)
            if pool is not None and pool.busy():
                # A gate runs in the background: look at it often, and at Orca in between.
                if pool.wait(GATE_POLL_SECONDS):
                    continue
                res = self.client.check(wait=True, timeout_ms=GATE_WAIT_MS)
            else:
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
        skipped = {t.id for t in sd.skipped_fix_tasks(self.state)}
        # A fix task the owner skipped is not a failure once a later audit approves the change.
        tasks = [t for t in self.state.tasks.values() if t.id not in skipped]
        approved = self.state.audit_result == "approved"
        ok = all(t.status == "done" for t in tasks) and approved
        result = self.state.audit_result
        summary = "rein ship %s: %d/%d tasks done, audit %s" % (
            self.change, sum(t.status == "done" for t in tasks), len(tasks),
            "approved" if approved else ("%s (not approved)" % result if result else "not approved"))
        if skipped:
            summary += "; %d audit-fix task%s skipped by the owner (%s)" % (
                len(skipped), "" if len(skipped) == 1 else "s", ", ".join(sorted(skipped)))
        if not approved and result != "rejected" and self.state.previous_blocking:
            summary += "; the blocking findings of the last audit were NOT verified as fixed: %s" % (
                "; ".join(self.state.previous_blocking))[:600]
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


START_TIMEOUT_SECONDS = 1800  # one worker-start, `--setup run` included; a hung CLI must not hang the driver
RESUME_POLL_SECONDS = 5
RESUME_WAIT_SECONDS = 600
RESUME_MAX = 3  # automatic resumes per incident
RESUME_INCIDENT_SECONDS = 1800  # a restart this long after the last resume is a new incident
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
    pool = None if dry_run else GatePool()  # gates run off the driver loop; it outlives a resume
    try:
        return _run_ship_loop(ctx, change, client, gate_runner, clock, dry_run, spath, plan, sleep, pool)
    finally:
        if pool is not None:
            pool.shutdown()


def _run_ship_loop(ctx, change, client, gate_runner, clock, dry_run, spath, plan, sleep, pool) -> int:
    resumes, last_resume, auto = 0, None, False
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
        state.driver_pid = os.getpid()  # `rein watch` / `rein done` tell this driver is alive by it
        driver = _Driver(ctx, state, client, gate_runner, clock, spath, dry_run, pool)
        try:
            driver.setup()
            if dry_run:
                driver.feed(sd.Tick(clock(), driver.limits()))
                driver.say("dry run: stopped after the first dispatch round; nothing was saved or gated")
                return 0
            if state.phase == "done":
                driver.finished = True
            else:
                driver.resume(owner_restart=not auto)
            driver.loop()
            return driver.finish()
        except OrcaError as exc:
            driver.save()
            if last_resume is not None and clock() - last_resume > RESUME_INCIDENT_SECONDS:
                resumes = 0  # the last restart was recovered long ago: this is a new incident
            if not dry_run and is_transient(exc) and resumes < RESUME_MAX:
                print("ship: Orca is restarting (%s); waiting to resume…" % exc.code, flush=True)
                if _wait_for_orca(client, clock, sleep):
                    resumes, last_resume, auto = resumes + 1, clock(), True
                    print("ship: Orca is back; resuming", flush=True)
                    continue
            print("ship: Orca failed (%s); state saved, re-run `rein ship %s` to resume" % (exc, change))
            return 126
