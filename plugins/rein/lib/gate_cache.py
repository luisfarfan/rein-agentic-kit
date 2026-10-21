"""Gate results keyed by what was tested, so the same state is never tested twice.

On one change the same suite ran 20-30 times, often on a commit that had just
passed: the worker's last run, the task gate, the pre-audit gate, the auditor.
A slot that PASSED is stored under

    (worktree, tree state, slot, exact command, gate env)

where the tree state is HEAD plus hashes of `git status --porcelain`, the
tracked diff against HEAD and every untracked file's content. Any edit, commit
or new file changes the key. Rein's own `.rein/` is left out: writing ship state
or a review must not invalidate a gate.

Only passes are stored. A failure, a timeout or a setup problem is a fact about
one moment (a contended database, a missing tool) and must be seen again.

Stored outside the repo (`$REIN_CACHE_DIR`, else `$XDG_CACHE_HOME/rein`, else
`~/.cache/rein`): the gate never writes under the root it checks. A directory
git cannot read has no state, so nothing is cached for it.

The same directory keeps each slot's last passing duration (`history`), which
sizes the next run's timeout.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time

_IGNORED_PREFIX = ".rein/"
MAX_AGE_SECONDS = 14 * 24 * 3600


def cache_dir() -> str:
    explicit = os.environ.get("REIN_CACHE_DIR")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "rein")


def _git(root: str, *args: str):
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _ours(path: str) -> bool:
    return path == ".rein" or path.startswith(_IGNORED_PREFIX)


def tree_state(root: str) -> str:
    """A hash of everything that can change a check's result in `root`, or "" when git cannot say."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return ""
    h = hashlib.sha256()
    head = _git(root, "rev-parse", "HEAD")
    h.update(b"head:" + (head or b"unborn").strip() + b"\n")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status is None:
        return ""
    entries = [e for e in status.split(b"\0") if e]
    kept = [e for e in entries if not _ours(e[3:].decode("utf-8", "replace"))]
    h.update(b"status:" + hashlib.sha256(b"\0".join(kept)).digest())
    diff = _git(root, "diff", "HEAD", "--binary", "--", ".", ":(exclude).rein") if head else \
        _git(root, "diff", "--cached", "--binary")
    if diff is None:
        return ""
    h.update(b"diff:" + hashlib.sha256(diff).digest())
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked is None:
        return ""
    for rel in sorted(p for p in untracked.split(b"\0") if p):
        name = rel.decode("utf-8", "replace")
        if _ours(name):
            continue
        h.update(b"untracked:" + rel + b"\0")
        try:
            with open(os.path.join(root, name), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def key(root: str, state: str, slot: str, command: str, env: dict) -> str:
    canonical = json.dumps([os.path.realpath(root), state, slot, command, sorted((env or {}).items())],
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path(root: str, k: str) -> str:
    folder = hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir(), "gate", folder, k + ".json")


def _write(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(root: str, k: str):
    """The stored passing result for key `k`, or None."""
    path = _path(root, k)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("outcome") != "ok":
        return None
    if time.time() - float(data.get("storedAt") or 0) > MAX_AGE_SECONDS:
        return None
    return data


def put(root: str, k: str, result: dict) -> None:
    """Store a PASS. Anything else is refused: only a pass may stand in for a run."""
    if (result or {}).get("outcome") != "ok":
        return
    try:
        _write(_path(root, k), {**result, "storedAt": time.time()})
    except OSError:
        pass  # a cache that cannot be written only costs a rerun


# -- history: the last passing duration of each slot, per repo ---------------

def _history_path(repo: str, label: str, command: str) -> str:
    digest = hashlib.sha256(json.dumps([repo, label, command]).encode("utf-8")).hexdigest()[:24]
    return os.path.join(cache_dir(), "history", digest + ".json")


def last_pass_seconds(repo: str, label: str, command: str):
    try:
        with open(_history_path(repo, label, command), encoding="utf-8") as fh:
            value = json.load(fh).get("seconds")
    except (OSError, ValueError, AttributeError):
        return None
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def record_pass(repo: str, label: str, command: str, seconds: float) -> None:
    try:
        _write(_history_path(repo, label, command), {"seconds": float(seconds), "at": time.time()})
    except OSError:
        pass
