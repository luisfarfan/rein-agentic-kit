"""Quick lane driver: `rein quick "<sentence>"`.

Resolves the repo, picks the implementer by quota, checks the workspace,
launches the agent through Orca and writes `.rein/quick.json`. Every
decision comes from `quick.py` and `quota.py`; this module owns the side
effects: Orca calls, git and the record. `done_quick` closes the task: the
light gate, the audit of risky changes and the summary.

Exit codes of `start_quick`: 0 started, 1 every provider braked, 2 usage or
refusal, 126 Orca or git could not do it. Of `done_quick`: 0 green, 1 red (or
the owner must decide), 2 no quick task here, 126 not a verdict on the code
(setup, Orca, git, an audit that could not run).
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from typing import Callable

import detect as _detect
import gate as _gate
import gate_env as _gate_env
import orca_client as oc
import quick
import quota
import roles as _roles
import ship
import ship_decide
import verify
from orca_client import OrcaClient, OrcaError

DEFAULT_BASE = "develop"
DEFAULT_MODELS = quota.DEFAULT_MODELS


def run_git(path: str, *args: str) -> tuple:
    """`(returncode, stdout)` of `git -C path args`; `(127, "")` when git cannot run."""
    try:
        proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


FETCH_TIMEOUT = 60


def run_fetch(path: str, base: str) -> tuple:
    """`(returncode, reason)` of `git fetch origin -- base`; never prompts, never hangs.

    A timeout or an unrunnable git comes back as a non-zero code with a reason.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", "-C", path, "fetch", "--quiet", "origin", "--", base],
                              capture_output=True, text=True, env=env, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "tiempo agotado tras %ds" % FETCH_TIMEOUT
    except OSError as error:
        return 127, "git no se pudo ejecutar: %s" % error
    reason = (proc.stderr or "").strip().splitlines()
    return proc.returncode, (reason[-1] if reason else "git fetch terminó con código %d" % proc.returncode)


def _failed(say, error: OrcaError) -> int:
    say("orca failed: %s" % error.message)
    residual = oc.residual_resources_of(error)
    if residual:
        say("left behind by orca: " + ", ".join(residual))
    return 126


def _resolve_repo(repos: list, name: str, cwd: str):
    if name:
        for repo in repos:
            if repo.get("displayName") == name:
                return repo
        return None
    here = os.path.realpath(cwd)
    best = None
    for repo in repos:
        path = os.path.realpath(str(repo.get("path") or "/nonexistent"))
        if here == path or here.startswith(path.rstrip(os.sep) + os.sep):
            if best is None or len(path) > len(best[0]):
                best = (path, repo)
    return best[1] if best else None


def _checkout_of(git: Callable[..., tuple], cwd: str) -> tuple:
    """`(toplevel, main checkout)` of the git checkout holding `cwd` -- a main checkout or a
    linked worktree; `("", "")` when git cannot tell."""
    code, top = git(cwd, "rev-parse", "--show-toplevel")
    code2, common = git(cwd, "rev-parse", "--git-common-dir")
    if code != 0 or code2 != 0 or not top.strip() or not common.strip():
        return "", ""
    common_dir = os.path.realpath(os.path.join(cwd, common.strip()))
    main = os.path.dirname(common_dir) if os.path.basename(common_dir) == ".git" else ""
    return os.path.realpath(top.strip()), main


def _is_dirty(porcelain: str) -> bool:
    for line in porcelain.splitlines():
        path = line[3:].strip().strip('"')
        if path and not path.startswith(".rein/") and path != ".rein":
            return True
    return False


def _taken_names(branches: str) -> set:
    """`quick-*` branch names; a branch Orca prefixed (`user/quick-x`) counts as `quick-x`."""
    taken = set()
    for line in branches.splitlines():
        name = line.strip().lstrip("*+").strip().rsplit("/", 1)[-1]
        if name.startswith("quick-"):
            taken.add(name)
    return taken


def _braked_lines(limits: dict, threshold: float) -> list:
    lines, seen = [], set()
    for agent, _model in quota.candidates(limits, threshold):
        reason = quota.brake_reason(limits, agent)
        if reason and reason not in seen:
            seen.add(reason)
            lines.append(reason)
    return lines


def _exclude_file(git: Callable[..., tuple], workspace: str) -> str:
    """The exclude file git reads for `workspace`; in a linked worktree that is the
    common dir's, not `<workspace>/.git/info/exclude` (there `.git` is a file)."""
    code, out = git(workspace, "rev-parse", "--git-path", "info/exclude")
    found = out.strip() if code == 0 else ""
    if not found:
        return "%s/.git/info/exclude" % workspace
    return os.path.join(workspace, found)  # a relative answer is relative to the workspace


def _write_record(workspace: str, record: dict) -> None:
    folder = os.path.join(workspace, ".rein")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(workspace, quick.RECORD), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")


def _ref_exists(git: Callable[..., tuple], repo_path: str, ref: str) -> bool:
    return git(repo_path, "rev-parse", "--verify", "--quiet", ref)[0] == 0


def _fallback_base(git: Callable[..., tuple], repo_path: str) -> str:
    """The remote's default branch, else `main`; "" when neither can be found."""
    code, out = git(repo_path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    head = out.strip()
    if code == 0 and head:
        return head[len("origin/"):] if head.startswith("origin/") else head
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        if _ref_exists(git, repo_path, ref):
            return "main"
    return ""


def start_quick(
    sentence: str, *, repo: str, base: str = "", here: bool = False,
    dirty_ok: bool = False, agent: str = "", model: str = "", linear: str = "",
    cwd: str = ".", client: OrcaClient, git: Callable[..., tuple] = run_git,
    fetch: Callable[[str, str], tuple] = run_fetch,
    now: Callable[[], float] = time.time, say: Callable[[str], None] = print,
) -> int:
    if not sentence.strip():
        say("rein quick needs a sentence describing the task")
        return 2
    if model and not agent:
        say("--model needs --agent")
        return 2

    # 1. Repo
    try:
        listed = client.repo_list().get("repos")
    except OrcaError as error:
        return _failed(say, error)
    repos = [r for r in listed if isinstance(r, dict)] if isinstance(listed, list) else []
    chosen = _resolve_repo(repos, repo, cwd)
    top = main = ""
    if here or (chosen is None and not repo):
        top, main = _checkout_of(git, cwd)
    if chosen is None and not repo and main:
        # cwd is a linked worktree (an Orca workspace) of a repo Orca knows by its main checkout
        chosen = next((r for r in repos if os.path.realpath(str(r.get("path") or "/nonexistent")) == main), None)
    if chosen is None:
        names = ", ".join(sorted(str(r.get("displayName")) for r in repos)) or "none"
        what = "unknown repo %r" % repo if repo else "%s is not inside a repo Orca knows" % cwd
        say("%s; known repos: %s" % (what, names))
        return 2
    repo_name, repo_id = str(chosen.get("displayName")), str(chosen.get("id"))
    repo_path = str(chosen.get("path"))
    # --here works in the checkout it is run from when that is the chosen repo's main checkout or
    # one of its linked worktrees; otherwise (a --repo elsewhere) in the repo's main checkout.
    here_path = top if (top and main == os.path.realpath(repo_path)) else repo_path

    # 2. Refusals (only --here looks at the checkout's state)
    dirty = False
    if here and not base:
        code, porcelain = git(here_path, "status", "--porcelain")
        if code != 0:
            say("cannot read the state of %s: not a git checkout?" % here_path)
            return 126
        dirty = _is_dirty(porcelain)
    refusal = quick.start_refusal(here, bool(base), dirty, dirty_ok)
    if refusal:
        say(refusal)
        return 2
    if here and os.path.exists(os.path.join(here_path, quick.RECORD)):
        record_path = os.path.join(here_path, quick.RECORD)
        say("%s already has %s: one quick task per workspace; "
            "borra %s cuando la tarea anterior esté commiteada"
            % (here_path, quick.RECORD, record_path))
        return 2

    # 3. Implementer
    if agent:
        model = model or DEFAULT_MODELS.get(agent, "")
        if not model:
            say("--agent %s needs --model" % agent)
            return 2
    else:
        try:
            limits = quota.parse_rate_limits({"result": client.account_list()})
        except OrcaError:
            limits = quota.parse_rate_limits({})  # unknown usage never brakes
        threshold = ship_decide.ShipConfig.from_flow_config(
            _detect.resolve(repo_path)).claude_weekly_threshold
        agent, model = quota.choose_implementer(limits, threshold)
        if quota.brake_reason(limits, agent):
            for line in _braked_lines(limits, threshold):
                say(line)
            say("every provider is braked: nothing was launched")
            return 1

    # gate.env: the literal values when the workspace is known (--here), else a `rein env` the agent
    # evaluates in the worktree Orca is about to create (its name is not known yet).
    env_lines = []
    if here:
        env_lines = _gate_env.export_lines(_gate_env.for_worktree(here_path, repo=repo_name))
    elif _gate_env.for_worktree(repo_path, repo=repo_name):
        env_lines = ['eval "$(python3 %s env --repo %s)"' % (shlex.quote(ship.BIN), shlex.quote(repo_name))]
    brief = quick.build_brief(sentence, linear, quick.looks_like_bug(sentence), ship.BIN, env_lines)

    # 4. Placement
    handle = ""
    if here:
        workspace = here_path  # its current branch is the base, local-only or not: no fetch
        code, sha = git(workspace, "rev-parse", "HEAD")
        code2, branch = git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
        if code != 0 or code2 != 0 or not sha.strip():
            say("cannot read HEAD of %s: not a git checkout?" % workspace)
            return 126
        base_sha, base = sha.strip(), branch.strip()
        try:
            launched = client.agent_launch("path:" + workspace, agent, model, brief)
        except OrcaError as error:
            return _failed(say, error)
        handle = oc.launch_handle_of(launched)
    else:
        explicit = bool(base)
        base = base or DEFAULT_BASE
        _code, branches = git(repo_path, "branch", "--list", "quick-*", "*/quick-*")
        name = "quick-" + quick.slugify(sentence, _taken_names(branches))
        # Branch from the remote tip: the local base may be behind origin.
        fetch_code, reason = fetch(repo_path, base)
        if not explicit and not (
                _ref_exists(git, repo_path, "refs/remotes/origin/" + base)
                or _ref_exists(git, repo_path, "refs/heads/" + base)):
            base = _fallback_base(git, repo_path)
            if not base:
                say("este repo no tiene develop ni una rama por defecto que reconozca; "
                    "dime la base con --base <rama>")
                return 2
            say("este repo no tiene develop; parto de %s" % base)
            fetch_code, reason = fetch(repo_path, base)
        remote_code, _ = git(repo_path, "rev-parse", "--verify", "--quiet",
                             "refs/remotes/origin/" + base)
        start_point = "origin/" + base if remote_code == 0 else base
        if fetch_code != 0:
            using = ("uso origin/%s tal como estaba (puede estar desactualizado)" % base
                     if remote_code == 0 else "uso %s local" % base)
            say("no pude actualizar origin/%s: %s; %s" % (base, reason, using))
        try:
            created = client.worktree_create("id:" + repo_id, name, start_point, agent, model,
                                             brief, linear)
        except OrcaError as error:
            return _failed(say, error)
        workspace = oc.created_worktree_path_of(created)
        if not workspace:
            say("orca did not report the new worktree's path; no record written")
            return 126
        handle = oc.launch_handle_of(created)
        code, sha = git(workspace, "rev-parse", "HEAD")
        if code != 0 or not sha.strip():
            say("cannot read HEAD of %s: not a git checkout?" % workspace)
            return 126
        base_sha = sha.strip()

    # 5. Record, only after Orca succeeded
    record = quick.new_record(repo_name, workspace, base, base_sha, agent, model,
                              sentence, linear, now())
    try:
        _write_record(workspace, record)
    except OSError as error:
        say("the agent started but %s could not be written in %s: %s"
            % (quick.RECORD, workspace, error))
        return 126
    code, _ = git(workspace, "check-ignore", "-q", quick.RECORD)
    if code != 0:
        say('add ".rein/" to %s' % _exclude_file(git, workspace))

    # 6. Report
    say("agent: %s" % agent)
    say("model: %s" % model)
    say("workspace: %s" % workspace)
    if handle:
        say("tab: %s" % handle)
    return 0


# --- close: `rein quick done` ------------------------------------------------

AUDIT_MAX_ROUNDS = 2
AUDIT_WAIT_SECONDS = 20 * 60
_CHECK_MS = 60000


def _load_record(workspace: str):
    """`(record, "")`, or `(None, why)` when it is missing or unusable."""
    path = os.path.join(workspace, quick.RECORD)
    if not os.path.isfile(path):
        return None, ("no quick task here (%s not found); start one with `rein quick`"
                      % quick.RECORD)
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as error:
        return None, "cannot read %s: %s" % (quick.RECORD, error)
    if not isinstance(record, dict) or not isinstance(record.get("base_sha"), str):
        return None, "%s has no base_sha: start the task again with `rein quick`" % quick.RECORD
    return record, ""


def _gate_notes(steps: list, related: list, changed: list, cfg, commands: dict) -> list:
    notes = []
    ran_tests = any(slot == "test" or slot.startswith("testOne:") for slot, _c, _k in steps)
    if not related:
        notes.append("sin tests relacionados")
    elif not ran_tests:
        # Without `test` the suite cannot stand in; `testOne` is missing only when it is not defined.
        missing = "`test`" if commands.get("testOne") else "`test` ni `testOne`"
        notes.append("%d tests relacionados sin ejecutar: el repo no define %s"
                     % (len(related), missing))
    elif any(slot == "test" for slot, _c, _k in steps):
        notes.append("%d tests relacionados: se ejecutó `test` (la suite completa, más lenta) "
                     "en lugar de `testOne` por archivo" % len(related))
    if len(changed) > cfg.large_change_files:
        notes.append("cambio grande (%d archivos, el aviso salta por encima de %d): "
                     "considera hacer un plan la próxima vez" % (len(changed), cfg.large_change_files))
    return notes


def _findings(workspace: str, change: str) -> list:
    return _gate.check_review(workspace, change).get("findings") or []


def _blocking(findings: list) -> list:
    return ["%s: %s" % (f["severity"], f["text"]) for f in findings if f["severity"] == "BLOCKING"]


def _approval_stands(workspace: str, change: str, changed: list, cfg) -> bool:
    """An APPROVED review that still matches its files AND covered every risky changed file.

    `check_review` only re-hashes the files the review listed, so a risky file added after
    the approval would pass it unseen."""
    if not _gate.check_review(workspace, change).get("ok"):
        return False
    episode = _gate.latest_episode(workspace, change) or {}
    reviewed = {os.path.normpath(rel) for rel in (episode.get("reviewed_files") or {})}
    return all(os.path.normpath(path) in reviewed
               for path in changed if ship_decide.touches_risky([path], cfg.risky_paths))


def _last_blocking(workspace: str, record: dict, change: str) -> list:
    """The BLOCKING findings of the last CHANGES_REQUESTED review.

    Kept in the record when the verdict arrives (a later episode must not hide them); a
    record without them falls back to the latest episode."""
    kept = record.get("last_blocking")
    if isinstance(kept, list) and all(isinstance(line, str) for line in kept):
        return kept
    return _blocking(_findings(workspace, change))


def _wait_for_worker(client: OrcaClient, dispatch_id: str, clock: Callable[[], float]):
    """`(kind, value)`: `("done", outcome)`, `("question", body)` or `("timeout", outcome-or-None)`.

    Messages from other dispatches are acked and ignored. `timeout` carries what
    `worker-show` proves after the 20 minutes (a finished worker whose message
    was missed), or None while it still runs."""
    deadline = clock() + AUDIT_WAIT_SECONDS
    while True:
        res = client.check(wait=True, timeout_ms=_CHECK_MS)
        outcome, question = None, None
        for message in oc.messages_of(res):
            payload = oc.payload_of(message)
            if str(payload.get("dispatchId") or "") != dispatch_id:
                continue
            if message.get("type") == "worker_done":
                outcome = str(payload.get("outcome") or "failed")
            elif message.get("type") in ("question", "escalation") and question is None:
                question = str(message.get("body") or "").strip() or "(sin texto)"
        delivery = oc.delivery_id_of(res)
        if delivery:
            client.check(wait=False, ack=delivery)
        if question is not None and outcome is None:
            return "question", question
        if outcome is not None:
            return "done", outcome
        if clock() >= deadline:
            return "timeout", ship.settled_outcome(client.worker_show(dispatch_id))


def _stop_worker(client: OrcaClient, dispatch_id: str, say: Callable[[str], None]) -> None:
    """Stop the audit worker like `ShipDriver.stop_worker`; release it only once Orca proves it ended."""
    try:
        client.worker_stop(dispatch_id)
    except OrcaError:
        pass  # it may already have exited; the look below decides
    try:
        confirmed = ship.settled_outcome(client.worker_show(dispatch_id)) is not None
    except OrcaError:
        confirmed = False
    if not confirmed:
        say("audit worker %s not confirmed stopped: check it in Orca" % dispatch_id)
        return
    try:
        client.worker_release(dispatch_id)
    except OrcaError as error:
        say("could not release %s: %s" % (dispatch_id, error.message))
    say("audit worker %s stopped" % dispatch_id)


def _stop_on_signal(client: OrcaClient, dispatch_id: str, say: Callable[[str], None]):
    """Handler for SIGTERM/SIGINT while the audit runs: stop the worker like a timeout, exit 126.

    A second signal while the first is still stopping the worker only exits."""
    state = {"stopping": False}

    def handler(signum, _frame):
        if not state["stopping"]:
            state["stopping"] = True
            say("interrupted (signal %d): stopping the audit worker" % signum)
            _stop_worker(client, dispatch_id, say)
        raise SystemExit(126)
    return handler


def _install_handler(handler) -> dict:
    """Install `handler` for SIGTERM and SIGINT; the previous handlers, to give back."""
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # not the main thread: there is nothing to hook
    return previous


def _restore_handlers(previous: dict) -> None:
    for sig, old in previous.items():
        signal.signal(sig, signal.SIG_DFL if old is None else old)


def _audit(workspace: str, record: dict, changed: list, limits: dict, client: OrcaClient,
           clock: Callable[[], float], say: Callable[[str], None]) -> int:
    """One audit round over `changed`; the exit code of `done` (0 approved)."""
    name = os.path.basename(workspace)
    change = "quick-" + name
    rounds = int(record.get("audit_rounds") or 0)
    pick = quick.reviewer(limits)
    if pick is None:
        say("every provider is braked: the audit cannot run; try again later "
            "or ask the owner to review the diff")
        return 126
    profile = _roles.profile("reviewer", ship.PLUGIN)
    if not profile["ok"]:
        say(profile["error"])
        return 126
    repo = str(record.get("repo") or "")
    spec = ship.build_audit_spec(
        {"why": record.get("sentence") or ""}, {workspace: changed}, profile["text"], workspace, change,
        env_lines={workspace: _gate_env.export_lines(_gate_env.for_worktree(workspace, repo=repo))},
        gate_commands={workspace: ship.audit_gate_command(workspace, repo)})
    baseline = ship._episode_stamp(_gate.latest_episode(workspace, change))
    client.run_create("rein quick %s" % name)
    task_id = oc.task_id_of(client.task_create(
        spec, "rein quick audit %s round %d" % (name, rounds + 1), []))
    dispatch_id = oc.dispatch_id_of(client.worker_start(
        task_id, worktree="path:" + workspace, agent=pick[0], model=pick[1]))
    say("audit round %d started on %s/%s (%s); waiting up to %d minutes"
        % (rounds + 1, pick[0], pick[1], dispatch_id, AUDIT_WAIT_SECONDS // 60))

    previous = _install_handler(_stop_on_signal(client, dispatch_id, say))
    try:
        kind, value = _wait_for_worker(client, dispatch_id, clock)
    except OrcaError:
        _stop_worker(client, dispatch_id, say)  # never leave the auditor running unattended
        raise
    finally:
        _restore_handlers(previous)
    if kind == "question":
        say("el auditor preguntó: %s" % value)
        _stop_worker(client, dispatch_id, say)
        return 126
    outcome = value
    if outcome is None:  # kind == "timeout" and the worker still runs
        say("the audit did not finish within %d minutes (not a verdict on the code)"
            % (AUDIT_WAIT_SECONDS // 60))
        _stop_worker(client, dispatch_id, say)
        return 126
    try:
        client.worker_release(dispatch_id)
    except OrcaError as error:
        say("could not release %s: %s" % (dispatch_id, error.message))

    episode = _gate.latest_episode(workspace, change)
    if outcome != "succeeded" or episode is None or ship._episode_stamp(episode) == baseline:
        say("the audit worker ended (%s) without recording a review episode in %s"
            % (outcome or "?", workspace))
        return 126
    found = str(episode.get("verdict"))
    if found == "APPROVED":
        say("audit: APPROVED")
        return 0
    if found == "CHANGES_REQUESTED":
        say("audit: CHANGES_REQUESTED")
        blocking = _blocking(_findings(workspace, change))
        for line in blocking:
            say(line)
        # Only a verdict of changes spends a round; the findings stay for the cap message.
        record["audit_rounds"] = rounds + 1
        record["last_blocking"] = blocking
        try:
            _write_record(workspace, record)
        except OSError as error:
            say("cannot update %s in %s: %s" % (quick.RECORD, workspace, error))
        return 1
    say("the audit recorded an unknown verdict %r" % found)
    return 126


def done_quick(*, cwd: str = ".", client: OrcaClient, run=verify.run_one,
               clock: Callable[[], float] = time.time, env: dict = os.environ,
               say: Callable[[str], None] = print) -> int:
    # 1. The workspace and its record
    code, top = run_git(cwd, "rev-parse", "--show-toplevel")
    workspace = top.strip()
    if code != 0 or not workspace:
        say("%s is not inside a git checkout" % os.path.abspath(cwd))
        return 126
    record, problem = _load_record(workspace)
    if record is None:
        say(problem)
        return 2

    # 2. What changed
    try:
        changed = ship.changed_files(workspace, record["base_sha"])
    except RuntimeError as error:
        say("cannot list the changes in %s since %s: %s" % (workspace, record["base_sha"], error))
        return 126
    if not changed:
        say("nothing changed since the task started")
        return 0

    # 3. Light gate
    resolved = _detect.resolve(workspace)
    cfg = ship_decide.ShipConfig.from_flow_config(resolved)
    gate_vars = _gate_env.for_worktree(workspace, repo=str(record.get("repo") or ""), resolved=resolved)
    related = quick.related_tests(changed, lambda rel: os.path.isfile(os.path.join(workspace, rel)))
    steps = quick.gate_steps(resolved.get("commands") or {}, related)
    results, outputs = [], {}
    for slot, command, configured in steps:
        result = ship.call_with(run, slot, command, workspace, cfg.gate_timeout_seconds,
                                configured=configured, env=gate_vars or None)
        results.append(result)
        outputs[slot] = getattr(result, "output", "")
    outcome = quick.verdict(results)
    change = "quick-" + os.path.basename(workspace)
    rounds = int(record.get("audit_rounds") or 0)
    risky = quick.needs_audit(changed, cfg)
    approved = risky and _approval_stands(workspace, change, changed, cfg)
    pending = risky and not approved and rounds < AUDIT_MAX_ROUNDS
    notes = _gate_notes(steps, related, changed, cfg, resolved.get("commands") or {})
    say(quick.summary(results, outputs, notes, audit_pending=pending))
    if outcome == "red":
        return 1
    if outcome == "setup":
        return 126

    # 4. Audit, only for risky changes
    if not risky:
        return 0
    if approved:  # an APPROVED review that still matches the files: nothing to launch
        say("auditoría vigente: aprobada")
        return 0
    if rounds >= AUDIT_MAX_ROUNDS:
        say("dos rondas de auditoría: decide tú. Último resultado:")
        for line in _last_blocking(workspace, record, change):
            say(line)
        return 1
    if not env.get("ORCA_TERMINAL_HANDLE"):
        say("the audit needs an Orca terminal (ORCA_TERMINAL_HANDLE is not set): "
            "run `rein quick done` from the agent's terminal, not an Orca chat")
        return 126
    try:
        limits = quota.parse_rate_limits({"result": client.account_list()})
    except OrcaError:
        limits = quota.parse_rate_limits({})  # unknown usage never brakes
    try:
        return _audit(workspace, record, changed, limits, client, clock, say)
    except OrcaError as error:
        return _failed(say, error)
    except OSError as error:
        say("cannot update %s in %s: %s" % (quick.RECORD, workspace, error))
        return 126
