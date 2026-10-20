"""`rein done <change>` and `rein quick close`: remove what is integrated, keep everything else.

The rule above all: a worktree is removed only when it is clean AND every commit it has
beyond `origin/<base>` is already in there (patch-equivalent, so rebases and squash-free
cherry-picks count). Any doubt -- a dirty tree, an unmerged commit, a base that cannot be
fetched -- blocks the WHOLE operation: nothing is removed and the reasons are printed.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
from typing import Callable, Optional

import detect as _detect
import orca_client as oc
import quick
import quick_driver as qd
import ship_state as ss
import ship_watch as sw
from orca_client import OrcaClient, OrcaError

DEFAULT_BASE = "develop"


def _repos_by_name(client: OrcaClient) -> dict:
    try:
        listed = client.repo_list().get("repos")
    except OrcaError:
        return {}
    return {str(r.get("displayName")): r for r in listed or [] if isinstance(r, dict)}


def _base_name(repo: Optional[dict], git: Callable[..., tuple], path: str) -> str:
    ref = str((repo or {}).get("worktreeBaseRef") or "")
    if ref:
        return ref[len("origin/"):] if ref.startswith("origin/") else ref
    if qd._ref_exists(git, path, "refs/remotes/origin/" + DEFAULT_BASE):
        return DEFAULT_BASE
    if qd._ref_exists(git, path, "refs/remotes/origin/main"):
        return "main"
    return DEFAULT_BASE


def check_worktree(path: str, base: str, git: Callable[..., tuple], fetch: Callable[[str, str], tuple],
                   base_sha: str = "") -> str:
    """"" when `path` is clean and integrated into `origin/<base>`, else the reason it is not."""
    code, porcelain = git(path, "status", "--porcelain")
    if code != 0:
        return "cannot read its git status"
    if qd._is_dirty(porcelain):
        return "uncommitted changes"
    fetch_code, why = fetch(path, base)
    if fetch_code != 0:
        return "cannot fetch origin/%s to verify it: %s" % (base, why)
    if base_sha:  # a quick record with no commits beyond its base has nothing to keep
        code, head = git(path, "rev-parse", "HEAD")
        if code == 0 and head.strip() == base_sha:
            return ""
    code, out = git(path, "cherry", "origin/" + base, "HEAD")
    if code != 0:
        return "cannot compare it with origin/%s" % base
    unmerged = [ln for ln in out.splitlines() if ln.startswith("+")]
    if unmerged:
        return "not integrated: %d commit(s) are not in origin/%s" % (len(unmerged), base)
    return ""


def _load_state(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _worktree_paths(state: dict) -> dict:
    paths = state.get("worktree_paths") if isinstance(state.get("worktree_paths"), dict) else {}
    wts = state.get("worktrees") if isinstance(state.get("worktrees"), dict) else {}
    out = {}
    for repo in sorted(set(paths) | set(wts)):
        path = paths.get(repo) or oc.worktree_path_of(wts.get(repo))
        if path:
            out[repo] = str(path)
    return out


def _archive_target(root: str, change: str, today: str) -> Optional[tuple]:
    src = os.path.join(root, "openspec", "changes", change)
    if _detect._detect_plan_source(root) != "openspec" or not os.path.isdir(src):
        return None
    return src, os.path.join(root, "openspec", "changes", "archive", "%s-%s" % (today, change))


def run_done(root: str, change: str, client: OrcaClient, *, dry_run: bool = False,
             git: Callable[..., tuple] = qd.run_git, fetch: Callable[[str, str], tuple] = qd.run_fetch,
             driver_alive: Callable[[str], bool] = sw.driver_alive, today: str = "",
             say: Callable[[str], None] = print) -> int:
    root = os.path.abspath(root)
    today = today or datetime.date.today().isoformat()
    state_file = ss.state_path(root, change)
    state = _load_state(state_file)
    if state is None:
        say("rein done: no ship state for %s in %s" % (change, root))
        return 1
    phase = str(state.get("phase") or "none")
    if phase != "done":
        say("rein done: %s is in phase %s, not done; nothing removed" % (change, phase))
        return 1
    if driver_alive(change):
        say("rein done: `rein ship %s` is still running; nothing removed" % change)
        return 1

    repos = _repos_by_name(client)
    blockers, present, gone = [], [], []
    for repo, path in _worktree_paths(state).items():
        if not os.path.isdir(path):
            gone.append((repo, path))
            continue
        base = _base_name(repos.get(repo), git, path)
        why = check_worktree(path, base, git, fetch)
        if why:
            blockers.append((repo, path, why))
        else:
            present.append((repo, path))
    archive = _archive_target(root, change, today)
    if archive and os.path.exists(archive[1]):
        blockers.append(("plan", archive[1], "archive folder already exists"))
    if blockers:
        say("rein done: %s is NOT ready to close; nothing was removed:" % change)
        for repo, path, why in blockers:
            say("  blocker: %s (%s): %s" % (repo, path, why))
        return 1

    prefix = "would " if dry_run else ""
    for repo, path in present:
        say("%sremove worktree %s (%s)" % (prefix, repo, path))
    for repo, path in gone:
        say("already gone: %s (%s)" % (repo, path))
    if archive:
        say("%sarchive plan %s -> %s" % (prefix, os.path.relpath(archive[0], root), os.path.relpath(archive[1], root)))
    say("%sclose tab 'rein ship %s', archive the state and drop the mailbox" % (prefix, change))
    if dry_run:
        return 0

    errors = 0
    for repo, path in present:
        try:
            client.worktree_rm("path:" + path)
        except OrcaError as exc:
            errors += 1
            say("orca could not remove %s (%s): %s" % (repo, path, exc.message))
    title = "rein ship %s" % change
    try:
        for t in oc.terminals_of(client.terminal_list("path:" + root)):
            if t.get("title") == title and t.get("handle"):
                client.terminal_close(str(t["handle"]))
                say("closed tab '%s'" % title)
    except OrcaError as exc:
        errors += 1
        say("orca could not close the tab '%s': %s" % (title, exc.message))

    if archive:
        src, dst = archive
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        code1, _ = git(root, "mv", src, dst)
        code2 = 1
        if code1 == 0:
            code2, _ = git(root, "commit", "-q", "-m", "docs(openspec): archive %s" % change,
                              "--only", "--", src, dst)
        if code1 != 0 or code2 != 0:
            errors += 1
            say("could not archive the plan with git (is it tracked?); state kept so you can retry")
            return 126
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1].strip() or "the plan branch"
        say("archived plan: committed on %s (no push); push it when ready" % branch)

    try:
        dest = os.path.join(root, ".rein", "ship", "archive", os.path.basename(state_file))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(state_file, dest)
        shutil.rmtree(ss.answers_dir(root, change), ignore_errors=True)
    except OSError as exc:
        say("could not archive the state file: %s" % exc)
        return 126
    say("done: %s closed" % change)
    return 126 if errors else 0
