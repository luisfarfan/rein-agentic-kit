"""What a red task's failure says before Rein spends another worker on it. Pure: no I/O.

Measured on 53 real `rein ship` runs (docs/research/2026-10-26-arquitectura): a fresh worker met
exactly the failure its predecessor had met 26 times in a row -- mostly the guard's "wrote outside
its worktree" about a commit in another checkout, which no new worker can undo -- and one task took
18 dispatches. The failure text a retry was given carried ANSI colour codes in 74% of test failures.

`clean` is the text a worker or the owner reads; `signature` is what two failures are compared by
(the same failure, whatever the run-specific ids, shas, timings and temp paths in it); `hint` is
the one line that tells the owner what a fresh worker cannot do about it.
"""

from __future__ import annotations

import hashlib
import re

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")
# What changes between two runs of the same failure and says nothing about it.
_VOLATILE = [
    (re.compile(r"(?:/private)?/var/folders/\S+|/tmp/\S+"), "<tmp>"),
    (re.compile(r"\b(?:ctx|task|run|msg|delivery|term|gate)_[0-9a-f-]{6,}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<sha>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?\b"), "<time>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|min)\b"), "<dur>"),
    (re.compile(r"\(\d+:\d{2}:\d{2}\)"), "<dur>"),
]


def clean(text: str) -> str:
    """`text` without terminal escape sequences or carriage-return redraws."""
    out = _ANSI.sub("", text or "")
    # A progress line redrawn with \r keeps only what it last showed.
    return "\n".join(line.rsplit("\r", 1)[-1] for line in out.split("\n"))


def normalize(text: str) -> str:
    out = clean(text)
    for rx, token in _VOLATILE:
        out = rx.sub(token, out)
    return "\n".join(" ".join(line.split()) for line in out.splitlines() if line.strip())


def signature(text: str) -> str:
    """Equal for two failures that only differ in ids, shas, timings or temp paths; "" for none."""
    norm = normalize(text)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16] if norm else ""


def kind(text: str) -> str:
    """outside | hook | test | other: which part of Rein said no."""
    t = clean(text)
    if "wrote outside its worktree" in t:
        return "outside"
    if "pre-commit hook only after the hook rewrites" in t or "commit-msg hook" in t:
        return "hook"
    if re.search(r"^(?:test|testIntegration|lint|typecheck|build) \[", t, re.M):
        return "test"
    return "other"


def hint(text: str) -> str:
    """What the owner can do that a fresh worker cannot, for the kind of `text`."""
    k = kind(text)
    if k == "outside":
        return ("A new worker cannot undo a change in another checkout. If it is your own or another "
                "session's work, `rein accept-outside --path <abs path>` or `--commit <sha>` records that, "
                "then retry; if a worker wrote it, revert it there yourself, then retry.")
    if k == "hook":
        return ("The repo's own git hook rewrites files on commit; check that hook, or let the next worker "
                "amend its commit with the hook's fix (retry).")
    if k == "test":
        return "Two workers in a row could not make this check pass the same way; look at the failure before retrying."
    return "Look at the failure before retrying."
