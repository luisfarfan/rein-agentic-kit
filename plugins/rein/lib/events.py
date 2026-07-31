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
