"""Quick lane driver: `rein quick "<sentence>"`.

Resolves the repo, picks the implementer by quota, checks the workspace,
launches the agent through Orca and writes `.rein/quick.json`. Every
decision comes from `quick.py` and `quota.py`; this module owns the side
effects: Orca calls, git and the record. `done_quick` joins it later.

Exit codes: 0 started, 1 every provider braked, 2 usage or refusal,
126 Orca or git could not do it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable

import detect as _detect
import orca_client as oc
import quick
import quota
import ship_decide
from orca_client import OrcaClient, OrcaError

DEFAULT_BASE = "develop"
# The model used when the owner names an agent but no model.
DEFAULT_MODELS = {
    quota.SONNET[0]: quota.SONNET[1],
    quota.CODEX[0]: quota.CODEX[1],
    quota.ANTIGRAVITY[0]: quota.ANTIGRAVITY[1],
}


def run_git(path: str, *args: str) -> tuple:
    """`(returncode, stdout)` of `git -C path args`; `(127, "")` when git cannot run."""
    try:
        proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


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


def _write_record(workspace: str, record: dict) -> None:
    folder = os.path.join(workspace, ".rein")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(workspace, quick.RECORD), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")


def start_quick(
    sentence: str, *, repo: str, base: str = "", here: bool = False,
    dirty_ok: bool = False, agent: str = "", model: str = "", linear: str = "",
    cwd: str = ".", client: OrcaClient, git: Callable[..., tuple] = run_git,
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
    if chosen is None:
        names = ", ".join(sorted(str(r.get("displayName")) for r in repos)) or "none"
        what = "unknown repo %r" % repo if repo else "%s is not inside a repo Orca knows" % cwd
        say("%s; known repos: %s" % (what, names))
        return 2
    repo_name, repo_id = str(chosen.get("displayName")), str(chosen.get("id"))
    repo_path = str(chosen.get("path"))

    # 2. Refusals (only --here looks at the checkout's state)
    dirty = False
    if here and not base:
        code, porcelain = git(repo_path, "status", "--porcelain")
        if code != 0:
            say("cannot read the state of %s: not a git checkout?" % repo_path)
            return 126
        dirty = _is_dirty(porcelain)
    refusal = quick.start_refusal(here, bool(base), dirty, dirty_ok)
    if refusal:
        say(refusal)
        return 2
    if here and os.path.exists(os.path.join(repo_path, quick.RECORD)):
        say("%s already has %s: one quick task per workspace" % (repo_path, quick.RECORD))
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

    brief = quick.build_brief(sentence, linear, quick.looks_like_bug(sentence))

    # 4. Placement
    handle = ""
    if here:
        workspace = repo_path
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
        base = base or DEFAULT_BASE
        _code, branches = git(repo_path, "branch", "--list", "quick-*", "*/quick-*")
        name = "quick-" + quick.slugify(sentence, _taken_names(branches))
        try:
            created = client.worktree_create("id:" + repo_id, name, base, agent, model,
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
        say('add ".rein/" to %s/.git/info/exclude' % workspace)

    # 6. Report
    say("agent: %s" % agent)
    say("model: %s" % model)
    say("workspace: %s" % workspace)
    if handle:
        say("tab: %s" % handle)
    return 0
