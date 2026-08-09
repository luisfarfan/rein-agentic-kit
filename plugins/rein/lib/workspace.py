#!/usr/bin/env python3
"""A workspace is N sibling repos, each with its own `.git` (D5).

`.rein/workspace.json` lives in a container directory (the workspace root)
that is itself NOT a git repo -- it just holds member repos as children:

    {
      "members": [
        {"name": "api", "path": "api"},
        {"name": "web", "path": "web"}
      ]
    }

`discover(start)` walks up from `start` looking for that file, the same
shape as git's own walk-up for `.git`. `members(doc, root)` resolves each
declared member against `root` and reports back which of them are real,
sibling, git-of-their-own repos -- a monorepo (one shared `.git`, several
subdirectories) is not a workspace, and a member pointed at one of those
subdirectories is rejected, named, by this function (D5).

This module reads no network and runs no command other than `git`, scoped
per member with `git -C <path>`.
"""

from __future__ import annotations

import json
import os
import subprocess

GIT_TIMEOUT = 15


def _git(cwd: str, args: list) -> tuple:
    """Run `git -C cwd <args>`, returning (stdout, error). Never raises.

    `error` is `""` on success. Any failure -- missing binary, timeout,
    non-zero exit -- is caught and reported back instead, the same
    never-raise convention as `events.record_event`.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    if proc.returncode != 0:
        return "", (proc.stderr or "").strip() or f"git {' '.join(args)} failed"
    return proc.stdout.strip(), ""


def discover(start: str = ".") -> dict:
    """Walk up from `start` for `.rein/workspace.json`. Never raises.

    Returns `{"found", "root", "path", "doc", "error"}`. `root` is the
    directory the descriptor was found in (member paths resolve against
    it); `doc` is the parsed JSON, or `None` if the file exists but could
    not be read/parsed, in which case `error` names why.
    """
    cur = os.path.realpath(os.path.abspath(start))
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while True:
        candidate = os.path.join(cur, ".rein", "workspace.json")
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                return {"found": True, "root": cur, "path": candidate, "doc": None, "error": str(exc)}
            return {"found": True, "root": cur, "path": candidate, "doc": doc, "error": ""}
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return {"found": False, "root": "", "path": "", "doc": None, "error": ""}


def members(doc: dict, root: str) -> tuple:
    """Resolve a workspace descriptor's members against `root`.

    Returns `(ok, problems)`:
      ok       -- list of `(name, path, branch, head)` tuples, one per member
                  that resolved to a real, sibling, git-of-its-own repo.
      problems -- list of `(name, reason)` tuples for every member that was
                  reported and skipped rather than raised: no path given, a
                  path that escapes `root` via `..`, a path that does not
                  exist, a path with no `.git` of its own (D5), or a `git`
                  lookup that failed.

    Never raises: a broken entry is reported here, not thrown, so one bad
    member in a workspace never hides the rest.
    """
    root_abs = os.path.realpath(os.path.abspath(root))
    ok: list = []
    problems: list = []

    raw_members = doc.get("members") if isinstance(doc, dict) else None
    for entry in raw_members or []:
        if not isinstance(entry, dict):
            problems.append(("<malformed>", "member entry is not an object"))
            continue
        rel = str(entry.get("path") or "").strip()
        name = str(entry.get("name") or "").strip() or rel or "<unnamed>"
        if not rel:
            problems.append((name, "no path given"))
            continue

        joined = os.path.realpath(os.path.join(root_abs, rel))
        try:
            escapes = os.path.commonpath([root_abs, joined]) != root_abs
        except ValueError:
            escapes = True  # different drives on Windows -- definitely not inside root
        if escapes:
            problems.append((name, f"path escapes workspace root: {rel}"))
            continue

        if not os.path.isdir(joined):
            problems.append((name, f"path does not exist: {joined}"))
            continue

        if not os.path.exists(os.path.join(joined, ".git")):
            problems.append((name, f"no .git of its own (part of a monorepo?): {joined}"))
            continue

        branch, berr = _git(joined, ["rev-parse", "--abbrev-ref", "HEAD"])
        if berr:
            problems.append((name, f"git branch lookup failed: {berr}"))
            continue
        head, herr = _git(joined, ["rev-parse", "--short", "HEAD"])
        if herr:
            problems.append((name, f"git head lookup failed: {herr}"))
            continue

        ok.append((name, joined, branch, head))

    return ok, problems
