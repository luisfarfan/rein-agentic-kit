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


# --- `rein quick close` -------------------------------------------------------

def _has_record(path: str) -> bool:
    return os.path.isfile(os.path.join(path, quick.RECORD))


def _is_main_checkout(path: str) -> bool:
    """A `--here` workspace is a repo's main checkout: its `.git` is a directory, not a linked file."""
    return os.path.isdir(os.path.join(path, ".git"))


def _toplevel(cwd: str, git: Callable[..., tuple]) -> str:
    code, out = git(cwd, "rev-parse", "--show-toplevel")
    return out.strip() if code == 0 else ""


def _orca_worktree_paths(client: OrcaClient) -> list:
    listed = client.worktree_list().get("worktrees")
    return [str(w["path"]) for w in listed or [] if isinstance(w, dict) and w.get("path")]


def run_quick_close(arg: str, client: OrcaClient, *, cwd: str = ".", dry_run: bool = False,
                    git: Callable[..., tuple] = qd.run_git, fetch: Callable[[str, str], tuple] = qd.run_fetch,
                    say: Callable[[str], None] = print) -> int:
    try:
        listed = _orca_worktree_paths(client)
    except OrcaError as exc:
        if arg and os.path.isdir(arg):
            listed = []  # an explicit path does not need Orca to be found
        else:
            say("orca failed: %s" % exc.message)
            return 126
    if arg:
        if os.path.isdir(arg) and _has_record(arg):
            targets = [os.path.abspath(arg)]
        else:
            targets = [p for p in listed if os.path.basename(p.rstrip("/")) == arg and _has_record(p)]
        if not targets:
            say("rein quick close: no quick task found for %r" % arg)
            return 1
    else:
        candidates = list(listed)
        top = _toplevel(cwd, git)
        if top:
            candidates.append(top)
        targets, seen = [], set()
        for path in candidates:
            real = os.path.realpath(path)
            if real not in seen and _has_record(path):
                seen.add(real)
                targets.append(path)
        if not targets:
            say("no quick tasks to close")
            return 0

    blocked = errors = closed = 0
    for path in targets:
        record, why = qd._load_record(path)
        if record is None:
            blocked += 1
            say("blocker: %s: %s" % (path, why))
            continue
        base = str(record.get("base") or DEFAULT_BASE)
        if not os.path.isdir(path):
            continue
        reason = check_worktree(path, base, git, fetch, base_sha=str(record.get("base_sha") or ""))
        if reason:
            blocked += 1
            say("kept: %s: %s" % (path, reason))
            continue
        here = _is_main_checkout(path)
        if dry_run:
            say("would %s" % ("delete the record in %s" % path if here else "remove worktree %s" % path))
            closed += 1
            continue
        if not here:
            try:
                client.worktree_rm("path:" + path)
            except OrcaError as exc:
                errors += 1
                say("orca could not remove %s: %s" % (path, exc.message))
                continue
            say("removed worktree %s" % path)
        else:
            say("kept the checkout %s (it is a main checkout); deleted its quick record" % path)
        try:
            os.unlink(os.path.join(path, quick.RECORD))
        except OSError:
            pass
        closed += 1
    say("closed %d, kept %d" % (closed, blocked))
    return 126 if errors else (1 if blocked else 0)
