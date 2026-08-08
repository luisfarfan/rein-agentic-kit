#!/usr/bin/env python3
"""Plane projection: only what is live, and only what changed (T005).

This module decides WHAT to sync, never HOW -- issuing the actual upserts is
T003's `plane_client.py`, wired up by T006's `rein sync --plane`. Two halves:

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

  `sync(state, window_days, apply_fn, record_path)` -- the only place this
  module touches disk. It loads the record, calls `select()`, and applies
  each emitted entity through the caller's `apply_fn`. The updated record is
  written only once every entity in this batch applied cleanly: a failure
  partway leaves the ON-DISK record exactly as it was, so nothing already
  recorded gets un-recorded, and nothing this run touched but didn't finish
  gets mistaken for synced. The remainder (the failed entity and everything
  queued after it) comes back as the retry set for the next run.

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


def _module_entity(change_name: str, tasks: list, live: bool) -> dict:
    if live:
        payload = {"name": change_name, "state": "active"}
    else:
        payload = {
            "name": change_name,
            "description": _history_description(tasks),
            "state": "completed",
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
    task_id = task.get("taskId") or ""
    payload = {
        "name": task.get("title") or task_id,
        "transition": task.get("transition") or "planned",
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

        entities.append(_module_entity(change_name, tasks, live))
        if live:
            for task in tasks:
                entities.append(_work_item_entity(change_name, task))

    return [e for e in entities if record.get(e["key"]) != e["hash"]]


def load_record(path: str) -> dict:
    """The `{key: hash}` map recorded after the last successful `sync()`.
    `{}` when the file is absent, unreadable, or malformed -- the first run
    ever, or a deleted record, both just mean "emit everything"."""
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


def sync(state: list, window_days: int | None, apply_fn, record_path: str):
    """Select the changed entities and apply each through `apply_fn(entity)`
    (which raises on failure). The on-disk record is written **once**, only
    after every selected entity applied cleanly (AC4) -- never partially, so
    a failure partway leaves the previous record intact and the unsent
    remainder -- starting with the entity that failed -- comes back as the
    retry set for the next run (AC5).

    Returns `(applied, retry)`.
    """
    record = load_record(record_path)
    entities = select(state, window_days, record=record)

    applied = []
    for index, entity in enumerate(entities):
        try:
            apply_fn(entity)
        except Exception:
            return applied, entities[index:]
        applied.append(entity)

    if applied:
        new_record = dict(record)
        for entity in applied:
            new_record[entity["key"]] = entity["hash"]
        save_record(record_path, new_record)

    return applied, []
