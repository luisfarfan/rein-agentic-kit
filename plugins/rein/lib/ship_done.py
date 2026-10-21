"""`rein done <change>` and `rein quick close`: remove what is integrated, keep everything else.

The rule above all: a worktree is removed only when it is clean AND everything it has beyond
`origin/<base>` is already in there: merging its tip into `origin/<base>` changes nothing
(so merge commits, squash merges and rebases are judged by content), or, only when git has no
merge-tree, every commit of a merge-free history is exactly one upstream. Any doubt -- a dirty tree, an unmerged commit, a base that cannot be
fetched -- blocks the WHOLE operation: nothing is removed and the reasons are printed.
"""

from __future__ import annotations

import datetime
import filecmp
import fnmatch
import json
import os
import shlex
import shutil
import subprocess
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


IGNORED_OK = ("node_modules", ".rein", "dist", "build", ".angular", "__pycache__", ".venv", "coverage",
              ".claude", ".codegraph", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cursor", ".turbo",
              ".nx", ".next", ".parcel-cache", ".mcp.json",
              # operating-system junk
              ".DS_Store", ".AppleDouble", ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
              "Thumbs.db", "ehthumbs.db", "Desktop.ini", "desktop.ini", "$RECYCLE.BIN", ".directory")


def _is_junk(name: str) -> bool:
    parts = name.strip("/").split("/")
    return any(part in IGNORED_OK for part in parts) or parts[-1].startswith("._")


def _short_branch(name: str) -> str:
    name = (name or "").strip()
    return name[len("refs/heads/"):] if name.startswith("refs/heads/") else name


def orca_rows(client: OrcaClient) -> dict:
    """`{realpath: row}` of `orca worktree list`; empty when Orca cannot say."""
    try:
        listed = client.worktree_list().get("worktrees")
    except OrcaError:
        return {}
    return {os.path.realpath(str(w["path"])): w for w in listed or [] if isinstance(w, dict) and w.get("path")}


def is_main_worktree(path: str, row: Optional[dict], git: Callable[..., tuple]) -> bool:
    """Orca's `isMainWorktree`, or git's own answer: a main checkout's git dir IS the common dir."""
    if (row or {}).get("isMainWorktree") is True:
        return True
    code1, gd = git(path, "rev-parse", "--git-dir")
    code2, cd = git(path, "rev-parse", "--git-common-dir")
    if code1 != 0 or code2 != 0:
        return True  # cannot tell: never treat it as removable
    return os.path.realpath(os.path.join(path, gd.strip())) == os.path.realpath(os.path.join(path, cd.strip()))


def branch_of(path: str, row: Optional[dict], git: Callable[..., tuple]) -> str:
    named = _short_branch(str((row or {}).get("branch") or ""))
    if named:
        return named
    code, out = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    return out.strip() if code == 0 else ""


def _main_checkout(path: str, git: Callable[..., tuple]) -> str:
    """The repo's main checkout (parent of the common git dir); "" when it cannot be told."""
    code, out = git(path, "rev-parse", "--git-common-dir")
    if code != 0 or not out.strip():
        return ""
    common = os.path.realpath(os.path.join(path, out.strip()))
    return os.path.dirname(common) if os.path.basename(common) == ".git" else ""


def _same_as_main(main: str, path: str, name: str) -> bool:
    """Byte-identical to the same relative path in the main checkout (contents are never printed)."""
    if not main or name.endswith("/") or os.path.realpath(main) == os.path.realpath(path):
        return False
    theirs = os.path.join(main, name)
    try:
        return os.path.isfile(theirs) and filecmp.cmp(os.path.join(path, name), theirs, shallow=False)
    except OSError:
        return False


def regenerable_patterns(*roots: str) -> list:
    """`gate.regenerable` of each root's flow.config.json: gitignored files a setup regenerates."""
    out = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        declared = (_detect.resolve(root).get("gate") or {}).get("regenerable")
        if isinstance(declared, list):
            out += [p.strip() for p in declared if isinstance(p, str) and p.strip()]
    return out


def _regenerable(name: str, patterns: list) -> bool:
    """`name` (repo-relative, a directory ends in "/") matches a pattern; "dir/" covers its files."""
    bare = name.rstrip("/")
    for pattern in patterns:
        pattern = pattern[2:] if pattern.startswith("./") else pattern
        folder = pattern.rstrip("/")
        if fnmatch.fnmatch(bare, folder) or bare.startswith(folder + "/"):
            return True
    return False


def _ignored_files(path: str, git: Callable[..., tuple], regenerable: tuple = ()) -> list:
    # -z: names come back verbatim (no C-quoting of spaces or non-ASCII bytes)
    code, out = git(path, "status", "--porcelain", "-z", "--ignored", "--untracked-files=all")
    main = _main_checkout(path, git)
    patterns = regenerable_patterns(path) + list(regenerable)
    found = []
    for entry in out.split("\0") if code == 0 else []:
        if not entry.startswith("!! "):
            continue
        name = entry[3:]
        if _is_junk(name) or _same_as_main(main, path, name) or _regenerable(name, patterns):
            continue
        found.append(name)
    return found


def _resolve_base(path: str, base: str, git: Callable[..., tuple], fetch: Callable[[str, str], tuple],
                  fallback: bool, notes: list) -> tuple:
    """`(base to compare against, "")` or `("", why)`. With `fallback`, a base that is not on the
    remote (a local ship branch) is replaced by develop, then main, and the notes say so."""
    code, why = fetch(path, base)
    if code == 0 and qd._ref_exists(git, path, "refs/remotes/origin/" + base):
        return base, ""
    if fallback:
        for other in (DEFAULT_BASE, "main"):
            if other != base and fetch(path, other)[0] == 0 and \
                    qd._ref_exists(git, path, "refs/remotes/origin/" + other):
                notes.append("origin/%s is not on the remote; checked against origin/%s instead" % (base, other))
                return other, ""
    return "", "cannot fetch origin/%s to verify it: %s" % (base, why or "no such remote branch")


def check_worktree(path: str, base: str, git: Callable[..., tuple], fetch: Callable[[str, str], tuple],
                   base_sha: str = "", branch: str = "", warnings: Optional[list] = None,
                   fallback: bool = False, notes: Optional[list] = None, regenerable: tuple = ()) -> str:
    """"" when `path` is clean and integrated into `origin/<base>`, else the reason it is not.

    Both HEAD and `branch` (the one Orca deletes with the worktree) must be integrated; a
    detached HEAD with no known branch cannot be vouched for. Gitignored files that would be
    lost (`.env` ...) are appended to `warnings` -- only for an integrated worktree, since a
    kept one loses nothing; pass `warnings=None` when the checkout will not be removed. Files
    matching `gate.regenerable` (the worktree's own, plus `regenerable`) are not a loss.
    """
    code, porcelain = git(path, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        return "cannot read its git status"
    if qd._is_dirty(porcelain):
        return "uncommitted changes"
    if not branch:
        return "detached HEAD and no known branch: cannot tell what would be lost"
    base, why = _resolve_base(path, base, git, fetch, fallback, notes if notes is not None else [])
    if not base:
        return why
    if base_sha:  # a quick record with no commits beyond its base has nothing to keep
        tips = {git(path, "rev-parse", "--verify", "--quiet", ref)[1].strip()
                for ref in ("HEAD", "refs/heads/" + branch)}
        if tips == {base_sha}:
            return _integrated(path, git, warnings, regenerable)
    for ref in ("HEAD", "refs/heads/" + branch):
        why = integration_gap(path, "origin/" + base, ref, git)
        if why:
            return why
    return _integrated(path, git, warnings, regenerable)


def _integrated(path: str, git: Callable[..., tuple], warnings: Optional[list], regenerable: tuple = ()) -> str:
    if warnings is not None:
        warnings.extend((path, name) for name in _ignored_files(path, git, regenerable))
    return ""


def integration_gap(path: str, upstream: str, ref: str, git: Callable[..., tuple]) -> str:
    """"" when everything `ref` holds is already in `upstream`, else why not.

    The rule is about content, so merge commits (which `git cherry` never looks at), squash
    merges and a base merged into the branch are all judged alike: `ref` is integrated when
    merging it into `upstream` would leave `upstream`'s tree exactly as it is
    (`git merge-tree --write-tree`, git >= 2.38). When merge-tree can answer, its answer is
    final. Without it, a history with merge commits blocks, and a plain history is accepted only
    when every commit is EXACTLY (`patch-id --verbatim`, whitespace included) one upstream.
    """
    code, out = git(path, "rev-list", "--merges", "--count", "%s..%s" % (upstream, ref))
    if code != 0:
        return "cannot compare %s with %s" % (ref, upstream)
    merges = int(out.strip() or "0")
    code, out = git(path, "merge-tree", "--write-tree", "--no-messages", upstream, ref)
    if code in (0, 1):
        tree = out.split()[0] if code == 0 and out.split() else ""
        code2, base_tree = git(path, "rev-parse", "--verify", "--quiet", upstream + "^{tree}")
        if code2 == 0 and tree and tree == base_tree.strip():
            return ""
        return _content_differs(path, upstream, ref, tree, git)
    if merges:
        return ("cannot verify the %d merge commit(s) of %s against %s: `git merge-tree "
                "--write-tree` failed (it needs git >= 2.38)" % (merges, ref, upstream))
    code, out = git(path, "cherry", upstream, ref)
    if code != 0:
        return "cannot compare %s with %s" % (ref, upstream)
    unmerged = [ln for ln in out.splitlines() if ln.startswith("+")]
    if unmerged:
        return "not integrated: %d commit(s) of %s are not in %s" % (len(unmerged), ref, upstream)
    equivalent = [ln.split()[1] for ln in out.splitlines() if ln.startswith("-") and len(ln.split()) > 1]
    if equivalent and not _verbatim_in(path, upstream, ref, equivalent, git):
        return ("not integrated: %d commit(s) of %s match %s only up to whitespace (or that cannot be "
                "checked exactly on this git)" % (len(equivalent), ref, upstream))
    return ""


DIFFSTAT_LINES = 12


def _content_differs(path: str, upstream: str, ref: str, merged_tree: str, git: Callable[..., tuple]) -> str:
    """The merge-tree verdict, with what differs: a squash merge edited upstream afterwards lands
    here too, and only the owner can tell that apart from lost work."""
    if merged_tree:  # what merging would change in upstream
        code, stat = git(path, "diff", "--stat", upstream + "^{tree}", merged_tree)
    else:  # the merge conflicts: what the branch changed since it forked
        code, stat = git(path, "diff", "--stat", "%s...%s" % (upstream, ref))
    lines = [ln.rstrip() for ln in stat.splitlines() if ln.strip()] if code == 0 else []
    if len(lines) > DIFFSTAT_LINES:
        lines = lines[:DIFFSTAT_LINES - 1] + ["... %d more" % (len(lines) - DIFFSTAT_LINES + 1), lines[-1]]
    why = "not integrated: content differs from %s: merging %s would change it%s; verify by hand: if that work already landed another way (a squash edited later), remove the worktree yourself" % (
        upstream, ref, " (merge conflicts)" if not merged_tree else "")
    return why + "".join("\n      " + ln for ln in lines)


def _verbatim_ids(path: str, log_text: str) -> dict:
    """`{commit: verbatim patch id}`; empty when this git cannot say (`--verbatim` is git >= 2.39)."""
    try:
        proc = subprocess.run(["git", "-C", path, "patch-id", "--verbatim"], input=log_text,
                              capture_output=True, text=True)
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    return {parts[1]: parts[0] for parts in (ln.split() for ln in proc.stdout.splitlines()) if len(parts) == 2}


def _verbatim_in(path: str, upstream: str, ref: str, commits: list, git: Callable[..., tuple]) -> bool:
    code1, mine = git(path, "log", "-p", "--no-merges", "--no-color", "--no-ext-diff", "%s..%s" % (upstream, ref))
    code2, theirs = git(path, "log", "-p", "--no-merges", "--no-color", "--no-ext-diff", "%s..%s" % (ref, upstream))
    if code1 != 0 or code2 != 0:
        return False
    mine_ids, their_ids = _verbatim_ids(path, mine), set(_verbatim_ids(path, theirs).values())
    return all(mine_ids.get(c) in their_ids for c in commits) if mine_ids else False


def _warn(warnings: list, say: Callable[[str], None]) -> None:
    say("WARNING: these gitignored files would be lost:")
    for path, name in warnings:
        say("  %s: %s" % (path, name))


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


_IN_PROGRESS = (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase or am"),
                ("CHERRY_PICK_HEAD", "a cherry-pick"), ("REVERT_HEAD", "a revert"))


def _archive_blockers(root: str, src: str, dst: str, git: Callable[..., tuple]) -> list:
    """The archive commits on whatever branch the plan repo has checked out (develop, a feature
    worktree ...); only a dirty plan folder or an operation in progress stops it."""
    out = []
    if os.path.exists(dst):
        out.append("archive folder already exists: %s" % dst)
    if git(root, "ls-files", "--error-unmatch", "--", src)[0] != 0:
        out.append("the plan %s is not tracked by git, so it cannot be moved with git mv" % src)
    if git(root, "symbolic-ref", "--quiet", "--short", "HEAD")[0] != 0:
        out.append("the plan repo is on a detached HEAD (no branch to commit the archive on)")
    code, porcelain = git(root, "status", "--porcelain", "--untracked-files=all", "--", src)
    if code != 0 or porcelain.strip():
        out.append("the plan folder %s has uncommitted changes; commit or discard them first" % src)
    for marker, what in _IN_PROGRESS:
        code, rel = git(root, "rev-parse", "--git-path", marker)
        if code == 0 and rel.strip() and os.path.exists(os.path.join(root, rel.strip())):
            out.append("the plan repo is in the middle of %s; finish or abort it first" % what)
    return out


def _archive_plan(root: str, change: str, src: str, dst: str, git: Callable[..., tuple]) -> bool:
    """git mv + commit; a rejected commit (hooks) puts the plan back exactly as it was."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if git(root, "mv", src, dst)[0] != 0:
        return False
    code, _ = git(root, "commit", "-q", "-m", "docs(openspec): archive %s" % change, "--only", "--", src, dst)
    if code == 0:
        return True
    git(root, "reset", "-q", "HEAD", "--", src, dst)
    if os.path.isdir(dst) and not os.path.exists(src):
        os.rename(dst, src)
    git(root, "reset", "-q", "HEAD", "--", src, dst)
    try:
        os.rmdir(os.path.dirname(dst))  # only if the archive folder we made is empty
    except OSError:
        pass
    return False


def _already_archived(root: str, change: str) -> bool:
    folder = os.path.join(root, "openspec", "changes", "archive")
    try:
        return any(n.endswith("-" + change) for n in os.listdir(folder))
    except OSError:
        return False


def run_done(root: str, change: str, client: OrcaClient, *, dry_run: bool = False, yes: bool = False,
             git: Callable[..., tuple] = qd.run_git, fetch: Callable[[str, str], tuple] = qd.run_fetch,
             driver_alive: Optional[Callable[[str], bool]] = None, today: str = "",
             say: Callable[[str], None] = print, env: Optional[dict] = None) -> int:
    root = os.path.abspath(root)
    env = os.environ if env is None else env
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
    if (driver_alive or (lambda c: sw.driver_alive(c, root=root)))(change):
        say("rein done: `rein ship %s` is still running; nothing removed" % change)
        return 1

    repos, rows = _repos_by_name(client), orca_rows(client)
    blockers, present, gone, warnings = [], [], [], []
    regenerable = tuple(regenerable_patterns(root))  # the plan root's declaration covers every repo
    for repo, path in _worktree_paths(state).items():
        if not os.path.isdir(path):
            gone.append((repo, path))
            continue
        row = rows.get(os.path.realpath(path))
        if is_main_worktree(path, row, git):
            blockers.append((repo, path, "it is a main checkout; Rein never removes those"))
            continue
        base = _base_name(repos.get(repo), git, path)
        why = check_worktree(path, base, git, fetch, branch=branch_of(path, row, git), warnings=warnings,
                             regenerable=regenerable)
        if why:
            blockers.append((repo, path, why))
        else:
            present.append((repo, path))
    archive = _archive_target(root, change, today)
    if archive:
        blockers += [("plan", root, why) for why in _archive_blockers(root, archive[0], archive[1], git)]
    if blockers:
        say("rein done: %s is NOT ready to close; nothing was removed:" % change)
        for repo, path, why in blockers:
            say("  blocker: %s (%s): %s" % (repo, path, why))
        return 1
    if warnings:
        _warn(warnings, say)
        if not yes:
            say("nothing was removed; re-run with --yes only if losing them is fine")
            return 1

    prefix = "would " if dry_run else ""
    for repo, path in present:
        say("%sremove worktree %s (%s)" % (prefix, repo, path))
    for repo, path in gone:
        say("already gone: %s (%s)" % (repo, path))
    if archive:
        on = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")[1].strip()
        say("%sarchive plan %s -> %s (commit on %s)" % (prefix, os.path.relpath(archive[0], root),
                                                         os.path.relpath(archive[1], root), on))
    elif _already_archived(root, change):
        say("plan already archived")
    say("%sclose tab 'rein ship %s', archive the state and drop the mailbox" % (prefix, change))
    if dry_run:
        return 0

    failed = 0
    for repo, path in present:
        try:
            client.worktree_rm("path:" + path)
        except OrcaError as exc:
            failed += 1
            say("orca could not remove %s (%s): %s" % (repo, path, exc.message))
    if failed:
        say("state and mailbox kept: fix the above and run `rein done %s` again" % change)
        return 126

    errors = 0
    title = "rein ship %s" % change
    own = str(env.get("ORCA_TERMINAL_HANDLE") or "")
    try:
        for t in oc.terminals_of(client.terminal_list("path:" + root)):
            if t.get("title") == title and t.get("handle"):
                if own and str(t["handle"]) == own:
                    say("left the tab '%s' open: this `rein done` runs in it; close it yourself" % title)
                    continue
                client.terminal_close(str(t["handle"]))
                say("closed tab '%s'" % title)
    except OrcaError as exc:
        errors += 1
        say("orca could not close the tab '%s': %s" % (title, exc.message))

    if archive:
        if not _archive_plan(root, change, archive[0], archive[1], git):
            say("could not archive the plan (git rejected it); the move was undone and the state kept; "
                "run `rein done %s` again" % change)
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


def _toplevel(cwd: str, git: Callable[..., tuple]) -> str:
    code, out = git(cwd, "rev-parse", "--show-toplevel")
    return out.strip() if code == 0 else ""


def _inside(cwd: str, path: str) -> bool:
    here, target = os.path.realpath(cwd or "."), os.path.realpath(path)
    return here == target or here.startswith(target.rstrip(os.sep) + os.sep)


def _restore(record_path: str, data: bytes, say: Callable[[str], None]) -> None:
    """Put the record back after a failed remove: the task is still open."""
    try:
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        say("could not restore %s: %s" % (record_path, exc))


def run_quick_close(arg: str, client: OrcaClient, *, cwd: str = ".", dry_run: bool = False, yes: bool = False,
                    git: Callable[..., tuple] = qd.run_git, fetch: Callable[[str, str], tuple] = qd.run_fetch,
                    say: Callable[[str], None] = print) -> int:
    """Worktrees are removed only when linked AND on a `quick-*` branch; any other location with a
    record (a `--here` checkout, a main worktree) only loses its record, and only when integrated."""
    rows = orca_rows(client)
    if not rows and not (arg and os.path.isdir(arg)):
        try:
            client.worktree_list()
        except OrcaError as exc:
            say("orca failed: %s" % exc.message)
            return 126
    listed = [str(r["path"]) for r in rows.values()]
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
    plan, warned = [], []
    for path in targets:
        record, why = qd._load_record(path)
        if record is None:
            blocked += 1
            say("blocker: %s: %s" % (path, why))
            continue
        row = rows.get(os.path.realpath(path))
        base = str(record.get("base") or DEFAULT_BASE)
        branch, notes = branch_of(path, row, git), []
        # only a removed worktree can lose gitignored files; a kept checkout just loses its record
        removable = not is_main_worktree(path, row, git) and "quick-" in branch
        warnings: Optional[list] = [] if removable else None
        reason = check_worktree(path, base, git, fetch, base_sha=str(record.get("base_sha") or ""),
                                branch=branch, warnings=warnings,
                                fallback=True, notes=notes)
        for note in notes:
            say("%s: %s" % (path, note))
        if reason:
            blocked += 1
            say("kept: %s: %s" % (path, reason))
            continue
        if warnings and not yes:
            blocked += 1
            warned += warnings
            say("kept: %s: gitignored files would be lost (listed below)" % path)
            continue
        if removable and _inside(cwd, path):
            blocked += 1
            say("kept: %s: this `rein quick close` runs inside the worktree it would remove; the "
                "orchestrator closes it from outside: cd %s && rein quick close %s"
                % (path, shlex.quote(_main_checkout(path, git) or os.path.dirname(path)),
                   shlex.quote(os.path.basename(path.rstrip("/")))))
            continue
        if warnings:
            _warn(warnings, say)
        plan.append((path, removable))
    if warned:
        _warn(warned, say)
        say("those were kept; re-run with --yes only if losing them is fine")
    for path, removable in plan:
        if dry_run:
            say("would %s" % ("remove worktree %s" % path if removable else "delete the record in %s" % path))
            closed += 1
            continue
        # The record first: Orca refuses a worktree with an untracked file, and the record is one
        # wherever `.rein/` is not ignored. Only that file: `.rein/` may hold tracked files.
        record_path = os.path.join(path, quick.RECORD)
        try:
            with open(record_path, "rb") as fh:
                kept = fh.read()
            os.unlink(record_path)
        except OSError:
            kept = None
        if removable:
            try:
                client.worktree_rm("path:" + path)
            except OrcaError as exc:
                errors += 1
                say("orca could not remove %s: %s" % (path, exc.message))
                if kept is not None:
                    _restore(record_path, kept, say)
                continue
            say("removed worktree %s" % path)
        else:
            say("kept the checkout %s (main checkout or not a quick-* branch); deleted its quick record" % path)
        closed += 1
    say("closed %d, kept %d" % (closed, blocked))
    return 126 if errors else (1 if blocked else 0)
