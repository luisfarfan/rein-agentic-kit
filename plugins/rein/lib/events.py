"""Skill-invocation events -- counted separately from runs (D3).

`rein event <name>` appends one line to `~/.claude/rein/events.jsonl`: the
skill's own name, an ISO timestamp, and the project it ran in. Events are the
only trace a `/rein:rein-plan`, `/rein:rein-step`, `/rein:rein-steps` or `/rein:rein-audit`
invocation leaves (hooks are out of scope for this project) -- each shipped
SKILL.md records its own invocation as its first step.

This module writes nothing outside `~/.claude/rein/` (D5): no network call,
no other file. Recording never fails the caller (D4) -- every failure mode
here is caught and reported back as an error string, never raised, so a
metrics write can never break the flow it is measuring.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess

EVENTS_DIR = os.path.expanduser("~/.claude/rein")
EVENTS_PATH = os.path.join(EVENTS_DIR, "events.jsonl")

# T002/AC1: the ONLY transitions a task may emit. Anything else is a caller
# bug (a typo, a made-up state) and is rejected BY NAME, before anything is
# written -- accepting an unknown word here would let a state.py fold over
# it silently and report a task "at" a stage that was never real.
TASK_TRANSITIONS = ("started", "verified", "blocked", "merged")


def record_event(name: str, root: str = ".", events_path: str = EVENTS_PATH) -> tuple[bool, str]:
    """Append one event line for skill `name` invoked from `root`.

    Returns `(ok, error)` -- `error` is `""` on success. Never raises: any
    `OSError` (missing/unwritable directory, disk full, permission denied)
    is caught and reported back instead, with `ok=False` -- callers still
    exit 0 (D4).
    """
    record = {
        "name": name,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "project": os.path.realpath(root),
    }
    try:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return False, str(exc)
    return True, ""


def _git_head(repo: str) -> str:
    """The repo's current commit, or "" if it cannot be determined.

    Never raises (same convention as `workspace._git`): a missing `git`
    binary, a repo with no commits yet, or any other failure degrades to an
    empty commit rather than blocking the event it is attached to.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def canonical_repo(root: str) -> str:
    """The MAIN repo for `root`, so a worktree and its parent agree on identity.

    This is the whole reason transitions survive a run. The loop executes each
    task inside a worktree and emits with `--root <worktree>`; the fold in
    `product_state.state()` runs against the main repo. Keying events on the
    literal path made those two never match, and the worktree is deleted when
    the run ends -- so every transition was written to a name nobody would ever
    look up again, and `rein state` reported `planned` forever. Measured: emit
    `verified` from a real `git worktree add` directory, then fold; the main
    repo saw `planned` and only the worktree saw `verified`.

    `--git-common-dir` is the shared `.git` for every worktree of a repo, so
    its parent is the one path both sides can compute. Anything that is not a
    git repo -- or a bare one, whose common dir has no working tree -- falls
    back to the literal path, which is exactly right for a non-worktree root.
    """
    real = os.path.realpath(root)

    def git(*args):
        try:
            proc = subprocess.run(
                ["git", "-C", real, *args],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    # A bare repo has no working tree, and `--git-common-dir` answers "." for
    # it -- whose parent is the CONTAINING directory, so two sibling bare
    # repos collapsed onto one key. Measured: bare/a.git and bare/b.git both
    # resolved to `bare`.
    if (git("rev-parse", "--is-bare-repository") or "").lower() == "true":
        return real

    common = git("rev-parse", "--git-common-dir")
    if not common:
        return real
    if not os.path.isabs(common):
        common = os.path.join(real, common)
    common = os.path.realpath(common)

    # Only a common dir literally named `.git` sits directly above a working
    # tree. A submodule's is `<parent>/.git/modules/<name>`, and taking its
    # parent mapped every submodule of one repo to `<parent>/.git/modules` --
    # so a workspace of submodules folded all of its members together.
    if os.path.basename(common) == ".git":
        parent = os.path.dirname(common)
        if os.path.isdir(parent):
            return parent

    toplevel = git("rev-parse", "--show-toplevel")
    return os.path.realpath(toplevel) if toplevel else real


def resolve_change(root: str, change: str = "") -> str:
    """The change an event belongs to, resolved the SAME way the reader
    resolves it.

    Symmetry is the whole point, exactly as with `canonical_repo`. A flat
    `tasks.md` takes its change name from the `# Change:` heading, so a
    plan reads as `product-observability` while an event emitted without
    `--change` recorded `""` -- and a fold that compares the two found
    nothing. Deriving it here instead of trusting the caller keeps both
    halves in agreement whether or not the loop passed the flag.

    An explicit `change` always wins; anything unresolvable degrades to
    `""`, never raises.
    """
    if change:
        return change
    try:
        import plan as _plan  # local: plan.py must not import this module
        # Resolved against the CANONICAL repo, not the literal root. This is
        # the same normalisation `canonical_repo` performs, and for the same
        # reason -- but it took a second review to notice the change axis
        # needed it too.
        #
        # `read_plan` falls back to `basename(root)` when a flat `tasks.md`
        # carries no `# Change:` heading (plan.py documents such a plan as
        # valid). In a worktree that basename is `rein-wt-<label>`, a sibling
        # directory, while the reader in the main repo computes the repo's own
        # name. So a header-less plan emitted `change="wt-feature"` and folded
        # against `change="mainrepo"`, and every task read `planned` forever --
        # byte for byte the failure canonical_repo was written to remove, moved
        # one column over, and strictly worse than not filtering on change at
        # all. Measured on a real `git worktree add`.
        return _plan.read_plan(canonical_repo(root)).get("change") or ""
    except Exception:  # noqa: BLE001 -- identity is best-effort, never fatal
        return ""


def record_task_event(
    task_id: str,
    transition: str,
    change: str = "",
    root: str = ".",
    events_path: str = EVENTS_PATH,
) -> tuple[bool, str]:
    """Append one TASK-TRANSITION event -- emitted while it happens (T002),
    not reconstructed afterwards from a checkbox.

    Rejects any `transition` not in `TASK_TRANSITIONS` BY NAME, before doing
    anything else -- nothing is written for an unknown transition (AC1).
    On a valid transition, appends one line to the SAME `events.jsonl` this
    module already writes skill-invocation events to, carrying `task_id`,
    `transition`, `change`, the resolved `repo`, and the repo's current git
    commit (best-effort -- "" when it cannot be read, never fatal).

    Returns `(ok, error)`, same never-raise convention as `record_event`:
    an unknown transition and an `OSError` while writing both come back as
    `ok=False` with `error` explaining why, never a raised exception.
    """
    if transition not in TASK_TRANSITIONS:
        return False, (
            f"unknown transition {transition!r} -- must be one of "
            f"{', '.join(TASK_TRANSITIONS)}"
        )
    # `repo` is the MAIN repo so the fold can find this again after the
    # worktree is removed; `commit` still reads the worktree, because the
    # commit the work was actually on is the useful one.
    worktree = os.path.realpath(root)
    repo = canonical_repo(worktree)
    record = {
        "kind": "task",
        "task_id": task_id,
        "transition": transition,
        "change": resolve_change(worktree, change),
        "repo": repo,
        "worktree": worktree if worktree != repo else "",
        "commit": _git_head(worktree),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return False, str(exc)
    return True, ""


def read_events(events_path: str = EVENTS_PATH) -> list[dict]:
    """Read every event, skipping any corrupt or truncated line.

    `events.jsonl` is append-only from concurrent sessions -- a reader that
    raises on one bad line would lose the whole history, so a line that is
    not valid JSON, or not a JSON object, is silently skipped. A line with
    invalid UTF-8 bytes (e.g. truncated mid multi-byte character by a
    concurrent/killed append) is decoded with replacement characters so it
    simply fails the JSON parse instead of raising `UnicodeDecodeError` out
    of the file iterator. An unreadable file (missing, permission denied)
    degrades to no events rather than a traceback (D4).
    """
    rows: list[dict] = []
    try:
        if not os.path.exists(events_path):
            return []
        with open(events_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                rows.append(row)
    except OSError:
        return rows
    return rows


def count_by_project(rows: list[dict]) -> dict[str, int]:
    """Invocation count per project -- never mixed into any run total (D3)."""
    counts: dict[str, int] = {}
    for r in rows:
        project = r.get("project") or "(unknown)"
        counts[project] = counts.get(project, 0) + 1
    return counts


# `events.jsonl` is append-only and grows without bound as sessions run (D6:
# files, not a database, so a bounded read is the fix for growth rather than
# a query engine). The dashboard only ever needs recent activity, so cap how
# much of the file a render will look at -- large enough to cover a project's
# actual recent usage, small enough that a file with years of history never
# turns a page load into a full-file scan.
MAX_EVENTS_READ = 500

_TAIL_BLOCK_SIZE = 65536  # 64 KiB read chunks while scanning backward from EOF


def read_recent_events(events_path: str = EVENTS_PATH, limit: int = MAX_EVENTS_READ) -> list[dict]:
    """The most recent `limit` valid events, newest-last, without reading the
    whole file (D6): scans backward from the end in fixed-size blocks and
    stops as soon as `limit` lines have been collected, so cost is bounded by
    `limit` (plus one block), not by how large `events_path` has grown to.

    Same corrupt-line tolerance as `read_events`: a line that is not valid
    JSON, or not a JSON object, is skipped rather than raised, and invalid
    UTF-8 bytes are replaced rather than raising `UnicodeDecodeError`. An
    absent or unreadable file degrades to no events, never a traceback (D4).
    """
    try:
        if not os.path.exists(events_path):
            return []
        with open(events_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            block = b""
            lines: list[bytes] = []
            while pos > 0 and len(lines) <= limit:
                read_size = min(_TAIL_BLOCK_SIZE, pos)
                pos -= read_size
                fh.seek(pos)
                block = fh.read(read_size) + block
                lines = block.split(b"\n")
            # The first element may be a partial line where the block
            # boundary landed mid-line -- drop it, unless `pos` reached 0, in
            # which case it genuinely is the first line of the file.
            if pos > 0 and lines:
                lines = lines[1:]
    except OSError:
        return []

    # Parse every collected line first, *then* cap to `limit` -- capping
    # before parsing would let a blank trailing line (the file's own final
    # "\n", or a skipped corrupt line) steal one of the `limit` slots and
    # silently under-return by that many.
    rows: list[dict] = []
    for raw in lines:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        rows.append(row)
    return rows[-limit:]
