#!/usr/bin/env python3
"""Which issues to work on, and in what order. Pure, no I/O.

This module decides WHAT, never HOW: fetching is `linear_client`, reading
one issue is `linear_issue`, and printing is `bin/rein`. The split is the
same one the retired Plane layer used, and for the same reason -- a
selection with no network in it can be tested against the real corpus
exhaustively, which is what pins the two traps below.

**Trap 1: priority 0 is not urgent, it is absent.** Linear numbers
priorities `1 Urgent, 2 High, 3 Medium, 4 Low`, and reserves `0` for "no
priority". Sorting on the raw number therefore puts every unprioritised
issue *ahead of every urgent one*. On this board all 14 groupers sit at 0,
so a naive sort hands back a page of headings before the first real bug.
`urgency_rank` moves 0 to the back; nothing else re-orders.

**Trap 2: filtering to nothing is not the same as matching nothing.**
`--repo proxima-app` (a repo that does not exist) and `--repo proxima-api`
with everything already closed both return an empty list, and they need
different answers from a human. `select` reports which filters were
applied and which values were never seen in the corpus, so the CLI can say
"no such repo" instead of "nothing to do".
"""

from __future__ import annotations

import linear_issue as _issue

# Linear's own numbering. `0` means "no priority", which is why it cannot
# be compared numerically against the rest.
NO_PRIORITY = 0
PRIORITY_NAMES = {0: "none", 1: "urgent", 2: "high", 3: "medium", 4: "low"}


def urgency_rank(priority) -> tuple:
    """Sort key: urgent first, unprioritised last.

    Returns a tuple so `None` (an issue the API gave no priority field at
    all) and `0` both land behind every real priority without either of
    them comparing as more urgent than `1`.
    """
    if priority is None or priority == NO_PRIORITY:
        return (1, 0)
    return (0, priority)


def priority_name(priority) -> str:
    return PRIORITY_NAMES.get(priority, "?")


def _matches(parsed: dict, filters: dict) -> bool:
    repo = filters.get("repo")
    if repo and parsed["repo"] != repo:
        return False

    state = filters.get("state")
    if state and parsed["state"].lower() != state.lower():
        return False

    flow = filters.get("flow")
    if flow and flow.upper() not in [f.upper() for f in parsed["flows"]]:
        return False

    label = filters.get("label")
    if label and label not in parsed["labels"]:
        return False

    parent = filters.get("parent")
    if parent and parsed["parent"].upper() != parent.upper():
        return False

    priority = filters.get("priority")
    if priority is not None and parsed["priority"] != priority:
        return False

    # "At least this urgent". Lower is more urgent, so this is `<=` on the
    # number -- and an unprioritised issue never satisfies it, because
    # "at least medium" cannot include "nobody said".
    max_priority = filters.get("maxPriority")
    if max_priority is not None:
        if parsed["priority"] is None or parsed["priority"] == NO_PRIORITY:
            return False
        if parsed["priority"] > max_priority:
            return False

    return True


def select(issues: list, *, repo: str = "", state: str = "", flow: str = "",
           label: str = "", parent: str = "", priority=None, max_priority=None,
           include_groupers: bool = False) -> dict:
    """Parse, filter and order.

    Groupers are excluded by default: 14 of the 64 issues on this board are
    index cards holding no work of their own, and an intake handed one
    produces nothing. `include_groupers` is there for listing the flows
    themselves, which is a different question.

    Returns `{"issues": [...], "filters": {...}, "unknown": [...]}`.
    `unknown` names each filter whose value appears nowhere in the corpus,
    so an empty result can be explained rather than just reported.
    """
    parsed_all = [_issue.parse(i) for i in (issues or [])]
    work = [p for p in parsed_all if include_groupers or not p["isGrouper"]]

    filters = {
        "repo": repo, "state": state, "flow": flow, "label": label,
        "parent": parent, "priority": priority, "maxPriority": max_priority,
    }
    active = {k: v for k, v in filters.items() if v not in ("", None)}

    # Vocabulary is taken from the pool being filtered, so "no such repo"
    # is judged against what the board actually holds -- not against a
    # hardcoded list that would go stale the day a fourth repo appears.
    seen = {
        "repo": {p["repo"] for p in work if p["repo"]},
        "state": {p["state"].lower() for p in work if p["state"]},
        "flow": {f.upper() for p in work for f in p["flows"]},
        "label": {l for p in work for l in p["labels"]},
        "parent": {p["parent"].upper() for p in work if p["parent"]},
    }
    unknown = []
    for key in ("repo", "state", "flow", "label", "parent"):
        value = active.get(key)
        if not value:
            continue
        candidate = value.lower() if key == "state" else (
            value.upper() if key in ("flow", "parent") else value
        )
        if candidate not in seen[key]:
            unknown.append({"filter": key, "value": value, "known": sorted(seen[key])})

    matched = [p for p in work if _matches(p, filters)]
    # Urgent first, then by identifier so two runs over an unchanged board
    # print the same order -- a list that shuffles is a list nobody can
    # compare against yesterday's.
    matched.sort(key=lambda p: (urgency_rank(p["priority"]), _ident_key(p["identifier"])))
    return {"issues": matched, "filters": active, "unknown": unknown}


def _ident_key(identifier: str) -> tuple:
    """`PPR-86` sorts before `PPR-119`. Splitting the number off is the
    whole point: as text, `PPR-119` sorts first and the list reads as if it
    were ordered by something nobody can name."""
    prefix, _, number = (identifier or "").rpartition("-")
    try:
        return (prefix, int(number))
    except ValueError:
        return (identifier or "", 0)
