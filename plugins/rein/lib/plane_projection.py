#!/usr/bin/env python3
"""Plane projection: only what is live, and only what changed (T005).

This module decides WHAT to sync, never HOW -- issuing the actual upserts is
T003's `plane_client.py`, wired up by T006's `rein sync --plane`.

  `select(state, window_days, record)` -- pure, no I/O. `state` is a list of
  per-change records shaped like `product_state.state()`'s return value (one
  entry per change: `change`, `tasks`, `lastTouchedDays`, ...). A change
  touched within the window projects as a Module plus one Work Item per task
  (**live**); a change older than the window collapses to a single closed
  Module carrying its task counts in the description, with **no** work items
  emitted at all (D10 -- that omission is the entire saving). Every entity
  (module or work item) carries a content hash; passing the previously
  recorded `{key: hash}` map drops anything unchanged since last time, so a
  no-op run over an unchanged state emits nothing.

  `load_record`/`save_record` -- the on-disk `{key: hash}` map. Applying the
  selected entities and deciding when to write the record back is
  `plane_sync.run_sync()`'s job (T006), not this module's: a sync spans
  several repos and continues past a single entity's failure, reporting it
  and moving on, then writes the record once at the end carrying only the
  entities that actually applied (round-2 review finding 2 -- an earlier
  `sync()` here stopped at the first failure and wrote nothing at all, the
  OPPOSITE of what ships; it was dead code, never called outside its own
  tests, and has been removed rather than left to contradict `run_sync`).

The window defaults to 30 days (D10, measured against the proxima corpus
described in this plan's Why) and is read from `plane.json`'s `window` field
when present.
"""

from __future__ import annotations

import hashlib
import json
import os

DEFAULT_WINDOW_DAYS = 30

# Transitions product_state.state() can report per task (see events.py).
_HISTORY_TRANSITION_ORDER = ("planned", "started", "verified", "blocked", "merged")


def content_hash(payload: dict) -> str:
    """Stable hash of an entity's payload -- independent of key order, so an
    equivalent payload built twice (e.g. across two `select()` calls) always
    hashes the same."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def load_window_days(root: str = ".") -> int:
    """The `window` field of `<root>/plane.json`, defaulting to
    `DEFAULT_WINDOW_DAYS` when the file is absent, unreadable, malformed, or
    simply has no `window` key (D10)."""
    path = os.path.join(root, "plane.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_WINDOW_DAYS
    if not isinstance(config, dict):
        return DEFAULT_WINDOW_DAYS
    value = config.get("window", DEFAULT_WINDOW_DAYS)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS


def _history_description(tasks: list) -> str:
    """Task counts by transition, so the one Module a history change keeps
    still answers "how far did this get" without a single work item (AC3)."""
    counts: dict[str, int] = {}
    for t in tasks:
        transition = t.get("transition") or "planned"
        counts[transition] = counts.get(transition, 0) + 1
    ordered = [t for t in _HISTORY_TRANSITION_ORDER if t in counts]
    ordered += sorted(t for t in counts if t not in _HISTORY_TRANSITION_ORDER)
    breakdown = ", ".join(f"{counts[t]} {t}" for t in ordered)
    total = len(tasks)
    noun = "task" if total == 1 else "tasks"
    return f"{total} {noun} ({breakdown})" if breakdown else f"{total} {noun}"


def _module_entity(change_name: str, tasks: list, live: bool) -> dict | None:
    # `None` when `change_name` is blank: Plane's Module `name` is required
    # and rejects a blank string outright (`400 name may not be blank`), so
    # emitting a payload with `{"name": ""}` is a doomed upsert, not a
    # degraded one (round-2 review finding 1). `plan.read_plan()` now
    # resolves a real name for every flat `tasks.md` plan (its own
    # `# Change:` header, else the repo directory name), so this only ever
    # fires for a plan this module has no way to name -- refusing silently
    # here is honest; sending the blank string to Plane is not.
    if not change_name:
        return None
    # Plane's Module resource carries `status` (backlog/planned/in-progress/
    # paused/completed/cancelled) -- `state` is the Issue field (a state-
    # machine uuid), which is why work items use it and Modules never can.
    # A DRF serializer silently drops an unknown field rather than 400ing,
    # so sending `state` here would create every history Module in
    # whatever Plane defaults a fresh Module to, not `completed` -- the
    # bug would never surface as a failure, only as a wrong board (finding
    # 4, round-1 review). `"active"` was never a legal value either; a live
    # module's status is left unset rather than guessed at.
    if live:
        payload = {"name": change_name}
    else:
        payload = {
            "name": change_name,
            "description": _history_description(tasks),
            "status": "completed",
        }
    key = f"module:{change_name}"
    return {
        "type": "module",
        "change": change_name,
        "scope": "live" if live else "history",
        "key": key,
        "hash": content_hash(payload),
        "payload": payload,
    }


def _work_item_entity(change_name: str, task: dict) -> dict:
    """The hash must cover EVERY field the sync writes, not just the visible
    ones. `dependsOn` reaches Plane as the work item's body (D8/AC4) and is
    the only place a dependency is visible at all, since no relation call is
    ever made. Hashing just name+transition meant editing a dependency in
    tasks.md changed neither, so `select()` emitted nothing and the board kept
    a stale line indefinitely -- worse than an absent one, because it reads as
    current."""
    task_id = task.get("taskId") or ""
    payload = {
        "name": task.get("title") or task_id,
        "transition": task.get("transition") or "planned",
        "dependsOn": list(task.get("dependsOn") or []),
    }
    key = f"item:{change_name}:{task_id}"
    return {
        "type": "work_item",
        "change": change_name,
        "taskId": task_id,
        "key": key,
        "hash": content_hash(payload),
        "payload": payload,
    }


def select(state: list, window_days: int | None = None, record: dict | None = None) -> list:
    """Split `state`'s changes into live and history, project each into
    Plane entities, and drop anything whose content hash already matches
    `record` (the last successfully synced `{key: hash}` map).

    A change with `lastTouchedDays` of exactly `window_days` is **live** --
    `<=`, not `<` -- an off-by-one here would silently drop the change right
    at the edge of the window that nobody would otherwise think to look for
    (AC2). A change with no determinable age (`lastTouchedDays` is `None`,
    e.g. never committed) is treated as live: there is no evidence it is
    stale, and defaulting it to history would be a guess this module refuses
    to make.
    """
    if window_days is None:
        window_days = DEFAULT_WINDOW_DAYS
    record = record or {}

    entities = []
    for change_state in state:
        change_name = change_state.get("change") or ""
        tasks = change_state.get("tasks") or []
        age = change_state.get("lastTouchedDays")
        live = age is None or age <= window_days

        module_entity = _module_entity(change_name, tasks, live)
        if module_entity is not None:
            entities.append(module_entity)
        if live:
            for task in tasks:
                entities.append(_work_item_entity(change_name, task))

    return [e for e in entities if record.get(e["key"]) != e["hash"]]


def load_record(path: str) -> dict:
    """The `{key: hash}` map recorded after the last sync that applied at
    least one entity (`plane_sync.run_sync()`, T006). `{}` when the file is
    absent, unreadable, or malformed -- the first run ever, or a deleted
    record, both just mean "emit everything"."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_record(path: str, record: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, sort_keys=True, indent=2)
    os.replace(tmp_path, path)
