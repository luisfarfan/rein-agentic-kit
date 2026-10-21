"""Per-worktree environment for the checks Rein runs and the agents it starts.

Worktrees of one repo share whatever their test suite reaches outside the tree:
a test database is the usual one. Two suites dropping the same schema at once
fail each other, and every false red costs a retry. A repo's flow.config.json
(or the plan root's, for every repo of a change) declares the variables:

    "gate": {"env": {"APP_TEST_DB": "app_test_{worktree}"}}

`{worktree}` is a slug of the worktree's directory name (lowercase, [a-z0-9_],
at most 40 characters, the same every time) and `{repo}` the repo's name.
Rein expands them for every gate and verification it runs in that worktree and
names them to the worker and the auditor. Nothing configured, nothing changes.

`gate.cleanup` is the other half: a command that drops what those variables named
(`dropdb --if-exists app_test_{worktree}`), run by `rein done` and `rein quick close`
once the worktree is removed. `{path}` is the removed worktree's path, shell-quoted.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess

import detect as _detect

SLUG_MAX = 40
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def worktree_slug(path: str) -> str:
    """`[a-z0-9_]{1,40}` from the worktree's basename; long names keep a hash so two stay apart."""
    base = os.path.basename(os.path.normpath(path or "")) if path else ""
    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    if not slug:
        return "wt"
    if len(slug) > SLUG_MAX:
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        slug = slug[:SLUG_MAX - 9].rstrip("_") + "_" + digest
    return slug


def repo_name_of(root: str) -> str:
    """The repo's name: its main checkout's directory, which a linked worktree shares; else the basename."""
    try:
        proc = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"],
                              capture_output=True, text=True)
        common = proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        common = ""
    if common:
        common = os.path.realpath(os.path.join(root, common))
        if os.path.basename(common) == ".git":
            return os.path.basename(os.path.dirname(common))
    return os.path.basename(os.path.normpath(os.path.abspath(root)))


def _declared(resolved: dict) -> dict:
    block = (resolved or {}).get("gate")
    env = block.get("env") if isinstance(block, dict) else None
    return env if isinstance(env, dict) else {}


def expand(declared, worktree: str, repo: str) -> dict:
    """`{name: value}` with the placeholders filled; malformed names and values are dropped."""
    if not isinstance(declared, dict):
        return {}
    slug = worktree_slug(worktree)
    out = {}
    for name, value in declared.items():
        if not isinstance(name, str) or not _NAME_RE.match(name):
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        out[name] = str(value).replace("{worktree}", slug).replace("{repo}", repo or "")
    return out


def for_worktree(root: str, repo: str = "", env_root: str = "", resolved: dict = None) -> dict:
    """The expanded env for a worktree: the plan root's declaration, then the repo's own on top."""
    root = os.path.abspath(root)
    repo = repo or repo_name_of(root)
    declared = {}
    if env_root and os.path.abspath(env_root) != root:
        declared.update(_declared(_detect.resolve(env_root)))
    declared.update(_declared(resolved if resolved is not None else _detect.resolve(root)))
    return expand(declared, root, repo)


def export_lines(env: dict) -> list:
    return ["export %s=%s" % (k, shlex.quote(v)) for k, v in sorted((env or {}).items())]


def merged(env: dict) -> dict:
    """This process's environment with `env` on top: what a subprocess is given."""
    out = dict(os.environ)
    out.update(env or {})
    return out


CLEANUP_TIMEOUT = 300


def expand_command(command: str, worktree: str, repo: str) -> str:
    """`gate.cleanup` with `{worktree}` (the slug), `{repo}` and `{path}` (shell-quoted) filled."""
    return (command.replace("{worktree}", worktree_slug(worktree)).replace("{repo}", repo or "")
            .replace("{path}", shlex.quote(os.path.abspath(worktree))))


def cleanup_command(root: str, repo: str = "", env_root: str = "") -> str:
    """The worktree's expanded `gate.cleanup`: the repo's own, else the plan root's; "" when none."""
    root = os.path.abspath(root)
    repo = repo or repo_name_of(root)
    for where in (root, env_root):
        if not where:
            continue
        block = _detect.resolve(where).get("gate")
        command = block.get("cleanup") if isinstance(block, dict) else None
        if isinstance(command, str) and command.strip():
            return expand_command(command.strip(), root, repo)
    return ""


def run_cleanup(command: str, cwd: str, env: dict = None, timeout: float = CLEANUP_TIMEOUT) -> tuple:
    """`(exit code, last output line)` of the cleanup command; it never raises."""
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd if os.path.isdir(cwd or "") else None,
                              capture_output=True, text=True, timeout=timeout, env=merged(env),
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %gs" % timeout
    except OSError as exc:
        return 127, str(exc)
    lines = [ln for ln in ((proc.stdout or "") + (proc.stderr or "")).splitlines() if ln.strip()]
    return proc.returncode, (lines[-1].strip()[:300] if lines else "")
