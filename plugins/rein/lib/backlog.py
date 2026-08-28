#!/usr/bin/env python3
"""The backlog: what `/rein:rein-plan` reads from (T001).

A backlog item is ten words about a problem -- a bug noticed in passing, an
idea. It is not half a task and never becomes one: the real work of
understanding it happens in `/rein:rein-discover` and `/rein:rein-plan`, which
produce a change with criteria and verification. So an item's whole life is
capture, get picked up, disappear.

Two things make this safe rather than just convenient.

**It cannot be executed.** The file lives at `.rein/backlog.md`, which
`plan.read_plan` does not look at. Measured: the same list under
`openspec/changes/backlog/tasks.md` is offered by `rein next` as claimable
work, so the loop would try to implement a ten-word note with no verification
and no acceptance criteria (D2).

**Its tracker state is derived, never stored.** Closing an item when its change
is created is right -- two open cards for one piece of work is double
counting. But this repo measured 71 of 118 changes in one workspace untouched
for one to four months, so an item closed at planning time and then abandoned
would be neither in the backlog nor fixed. `derive_states` recomputes from
scratch every sync: if the change ages out of the window with work
unfinished, the item comes back on its own (D1).
"""

from __future__ import annotations

import os
import re

BACKLOG_RELATIVE_PATH = os.path.join(".rein", "backlog.md")

# `- [B007] the sync should paginate work items too`
ITEM_RE = re.compile(r"^\s*[-*]\s+\[(B\d+)\]\s*(.*)$")

# The high-water mark. Ids are never reused (D4), so the writer records the
# highest one ever assigned -- otherwise deleting the last line would hand the
# next item an id that an existing tracker item already answers to, silently
# pointing it at a different idea.
LAST_ID_RE = re.compile(r"^\s*<!--\s*rein:last-id\s+(B\d+)\s*-->\s*$")

HEADER = "# Backlog\n\nOne line per item. `rein backlog add \"<text>\"` appends.\n"

UNCLAIMED = "backlog"
ABSORBED = "completed"


class BacklogCorrupt(Exception):
    """Two items share an id.

    The high-water marker stops `add` from ever reusing one, but this
    file is hand-edited BY DESIGN -- the docstring says so -- and a
    copy-paste produces a duplicate in two seconds. Two lines with the
    same id build the same external id, so the second silently
    overwrites the first's item in the tracker: one idea disappears
    without a single error. Found on a real repo, not in a test.

    Refusing to sync is the right response. A backlog with duplicate ids
    is corrupt, and syncing it destroys data on the board.
    """

    def __init__(self, duplicates, path):
        self.duplicates = sorted(duplicates)
        self.path = path
        super().__init__(
            f"{path}: duplicate id(s) {', '.join(self.duplicates)} -- two items "
            f"cannot share one id, they would collide on the same tracker item"
        )


def duplicate_ids(root: str = ".") -> list:
    """Ids appearing on more than one line, in file order. `[]` when clean."""
    seen, dupes = set(), []
    for item in items(root):
        if item["id"] in seen and item["id"] not in dupes:
            dupes.append(item["id"])
        seen.add(item["id"])
    return dupes


def check(root: str = ".") -> None:
    """Raise `BacklogCorrupt` when the file cannot be safely projected."""
    dupes = duplicate_ids(root)
    if dupes:
        raise BacklogCorrupt(dupes, backlog_path(root))


def backlog_path(root: str = ".") -> str:
    return os.path.join(os.path.abspath(root), BACKLOG_RELATIVE_PATH)


def _read_lines(root: str) -> list:
    try:
        with open(backlog_path(root), "r", encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def items(root: str = ".") -> list:
    """Every `- [Bnnn] text` line, in file order, as `{"id", "text"}`.

    Lines this does not recognise are simply not items -- they are kept in the
    file untouched by `add()`. The file is meant to be hand-edited, so a note,
    a heading or a blank line between entries has to survive being written by
    a person.
    """
    out = []
    for line in _read_lines(root):
        match = ITEM_RE.match(line)
        if match:
            out.append({"id": match.group(1), "text": match.group(2).strip()})
    return out


def _highest_seen(lines: list) -> int:
    """The largest id number in the file, counting the high-water marker.

    The marker is what makes an id permanent: without it, deleting the last
    line would make the next `add` reuse that id, and a tracker item
    created under it would suddenly describe a different idea (D4).
    """
    highest = 0
    for line in lines:
        marker = LAST_ID_RE.match(line)
        if marker:
            highest = max(highest, int(marker.group(1)[1:]))
            continue
        item = ITEM_RE.match(line)
        if item:
            highest = max(highest, int(item.group(1)[1:]))
    return highest


def next_id(lines: list) -> str:
    return f"B{_highest_seen(lines) + 1:03d}"


def add(root: str, text: str) -> str:
    """Append one item, return its id. Creates the file when absent."""
    text = (text or "").strip()
    if not text:
        raise ValueError("a backlog item needs some text")
    lines = _read_lines(root)
    new_id = next_id(lines)

    body = [line for line in lines if not LAST_ID_RE.match(line)]
    if not body:
        body = HEADER.rstrip("\n").splitlines()
    while body and not body[-1].strip():
        body.pop()
    body.append(f"- [{new_id}] {text}")
    body.append("")
    body.append(f"<!-- rein:last-id {new_id} -->")

    path = backlog_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")
    os.replace(tmp, path)
    return new_id


def derive_states(root: str, changes: list, window_days: int = 30) -> dict:
    """`{item_id: state}` -- recomputed from scratch, storing nothing (D1).

    `changes` is a list of `product_state.state()` records. An item is
    `completed` while some change that absorbed it is still LIVE, and
    `backlog` both when nothing absorbed it and when every change that did has
    aged past `window_days` with at least one unfinished task.

    That last case is the point of the whole module. Closing an item at
    planning time is correct -- it was captured, it was processed -- but this
    repo measured 71 of 118 changes untouched for one to four months. Without
    the return, doing the first step properly is what makes a bug disappear.
    """
    state = {item["id"]: UNCLAIMED for item in items(root)}
    for change in changes or []:
        absorbed = change.get("absorbs") or []
        if not absorbed:
            continue
        age = change.get("lastTouchedDays")
        live = age is None or age <= window_days
        if not live and _has_unfinished(change):
            # Stale AND unfinished: the item returns. A stale change whose work
            # is all done is finished, not abandoned, so it keeps its items
            # closed.
            continue
        for item_id in absorbed:
            if item_id in state:
                state[item_id] = ABSORBED
    return state


def _has_unfinished(change: dict) -> bool:
    for task in change.get("tasks") or []:
        if bool(task.get("checked")):
            continue
        if (task.get("transition") or "planned") in ("verified", "merged"):
            continue
        return True
    return False


def as_change_record(root: str, changes: list, window_days: int = 30) -> dict | None:
    """The backlog shaped as a `product_state.state()` record.

    Returned in `state_all()` so the backlog reads as just another change
    to every consumer of product state -- `rein state`, the dashboard, and
    any future tracker projection -- with no new code. `None` when there
    are no items: an empty change is noise, and a blank name is not a name.
    """
    check(root)
    entries = items(root)
    if not entries:
        return None
    derived = derive_states(root, changes, window_days)
    return {
        "root": os.path.abspath(root),
        "change": "backlog",
        "planPath": backlog_path(root),
        "planExists": True,
        # `lastTouchedDays` is None so a consumer always treats the backlog
        # as live. A backlog does not age out: an idea nobody picked up is
        # still an idea, and collapsing it to closed history would
        # hide exactly the items most in need of attention.
        "lastTouchedDays": None,
        "tasks": [
            {
                "taskId": entry["id"],
                "title": entry["text"],
                "checked": derived.get(entry["id"]) == ABSORBED,
                "transition": (
                    "verified" if derived.get(entry["id"]) == ABSORBED else "planned"
                ),
                "when": "",
                "commit": "",
                "dependsOn": [],
            }
            for entry in entries
        ],
    }
