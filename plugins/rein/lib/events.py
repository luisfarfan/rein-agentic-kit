"""Skill-invocation events -- counted separately from runs (D3).

`rein event <name>` appends one line to `~/.claude/rein/events.jsonl`: the
skill's own name, an ISO timestamp, and the project it ran in. Events are the
only trace a `/rein:plan`, `/rein:run`, `/rein:run-auto` or `/rein:review`
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

EVENTS_DIR = os.path.expanduser("~/.claude/rein")
EVENTS_PATH = os.path.join(EVENTS_DIR, "events.jsonl")


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
