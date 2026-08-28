#!/usr/bin/env python3
"""One Linear issue -> the fields an intake needs. Pure, no I/O.

**Linear is the source. Nothing here reads a file from another repo.** That
is a deliberate constraint and it turned out to be the sturdier design:
most of what an intake needs is a STRUCTURED API field, not markdown.
`title`, `priority`, `state`, `labels`, `parent` and `branchName` all
arrive typed, and no regex can get them wrong. Only two facts live in the
body -- who found it, and which flows it touches.

The body was measured over the 50 bug issues of the `pproxima` board, and
it comes in two shapes that both have to parse:

  * **shape A (33 of 50)** -- `**Impacto:** <line>`, a metadata table, a
    `---`, then the prose.
  * **shape B (17 of 50)** -- the same, with a full copy of the
    `proxima-qa` document appended below it: a second `# title`, a second
    `> Encontrado por ...`, and a SECOND `**Impacto:**`.

Reading the frame from the FIRST `**Impacto:**` therefore returns the
metadata table as the bug's prose on 17 issues. The body is taken from the
LAST one instead, which lands after the duplicated header in shape B and
is a no-op in shape A -- one rule, both shapes, no shape detection.

Two more things the board does to text, both measured:

  * it autolinks bare dotted words. `notification.email` arrives as
    `[notification.email](<http://notification.email>)`, three times in one
    issue (PPR-89, PPR-123, PPR-79). Those are identifiers an implementer
    greps for, so they are repaired here rather than passed on broken.
  * a table row is `| key | value |`, and the PROSE contains rows too
    ("| Producto -> Add or remove units | ..."). Only the three known keys
    are read; matching any row would have picked up a UI path as the repo
    to route the work to.

Nothing raises. What could not be read is named in `missing`, so a caller
refuses with a list rather than proceeding on a half-read bug -- the same
never-raise convention as `events.record_event` and `workspace._git`.
"""

from __future__ import annotations

import re

# Repo labels are the routing fact, and they are API data rather than
# markdown. Measured: 50/50 bug issues carry exactly one.
REPO_LABEL_PREFIX = "proxima-"

# The label that marks a grouper. 15 of the 64 issues are these: they hold
# no work of their own, and an intake that takes one produces nothing.
FLOW_LABEL = "Flujo"

_IMPACT = re.compile(r"^\*\*Impacto:\*\*\s*(?P<impact>.+?)\s*$", re.M)
_TABLE_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>[^|]*?)\s*\|\s*$", re.M)
_TABLE_KEYS = {
    "repo dueño": "repo",
    "repo dueno": "repo",
    "encontrado por": "foundBy",
    "flujos afectados": "flows",
}

# `[notification.email](<http://notification.email>)` -> `notification.email`
_AUTOLINK = re.compile(r"\[([^\]]+)\]\(<?https?://(?:www\.)?\1/?>?\)")

# `*Repro completa:* `proxima-qa/docs/bugs/<id>.md`` -- kept as a reference
# to where the long form lives. NOT read: the whole point of this module is
# that a second repo never has to be cloned or current for an intake to run.
_DOC_PATH = re.compile(r"`(?P<path>[\w./-]*docs/bugs/[\w.-]+\.md)`")

# The block every bug ends with: a `---` rule, the pointer to the long
# form, and a provenance line. All 50 carry it, it says the same thing on
# every issue, and it is the last thing an implementer reads before
# starting. `docPath` already keeps the one fact in it worth keeping.
_FOOTER_LINE = re.compile(r"^\*(?:Repro completa|Reportado por)\b")

FRAME_FIELDS = ("identifier", "title", "repo", "impact", "body")


def repair_autolinks(text: str) -> str:
    """Undo Linear's autolinking of bare dotted identifiers."""
    return _AUTOLINK.sub(r"\1", text or "")


def is_grouper(issue: dict) -> bool:
    """A parent "flujo" issue: an index, not work.

    Identified by BOTH signals, because either alone is wrong. 15 issues
    carry the `Flujo` label; a real bug can also have no parent (PPR-84
    does). Requiring the label AND the absence of a parent is what
    separates "this is a heading" from "this bug was filed standalone".
    """
    labels = _label_names(issue)
    return FLOW_LABEL in labels and not issue.get("parent")


def _label_names(issue: dict) -> list:
    nodes = ((issue.get("labels") or {}).get("nodes")) or []
    return [n.get("name", "") for n in nodes]


def repo_from_labels(issue: dict) -> str:
    """The owning repo, from the API's own labels rather than the body.

    Preferred over the `Repo dueño` table row because a label is typed data
    the board validates, while the row is text an agent rewrites. When both
    are present they agree on all 50; `parse` reports it when they ever
    stop agreeing instead of silently picking one.
    """
    for name in _label_names(issue):
        if name.startswith(REPO_LABEL_PREFIX):
            return name
    return ""


def _table(description: str) -> dict:
    """The metadata table, restricted to the three keys it is allowed to
    carry. The prose contains `| ... | ... |` rows of its own."""
    out: dict = {}
    for match in _TABLE_ROW.finditer(description or ""):
        key = _TABLE_KEYS.get(match.group("key").strip().lower())
        if key and key not in out:
            out[key] = match.group("value").strip().strip("`")
    return out


def body_of(description: str) -> str:
    """The bug's prose, from the LAST `**Impacto:**` line onward.

    One rule for both body shapes: in shape B it lands past the duplicated
    header, in shape A there is only one match and it is a no-op. A leading
    metadata table and its `---` rule are then dropped, because in shape A
    they sit between that line and the prose.
    """
    description = repair_autolinks(description or "")
    matches = list(_IMPACT.finditer(description))
    rest = description[matches[-1].end():] if matches else description

    lines = rest.splitlines()
    start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            start = index + 1
            continue
        # Table rows, the table's own `| -- | -- |` rule, and the `---`
        # that closes the header block. Anything else is the prose.
        if stripped.startswith("|") or set(stripped) <= set("-"):
            start = index + 1
            continue
        break

    kept = lines[start:]
    # Drop the trailing footer the same way: from the end, past blanks,
    # the `---` rule and the provenance lines. Trimming from the END means
    # a bug whose PROSE happens to contain a horizontal rule keeps it.
    end = len(kept)
    while end > 0:
        stripped = kept[end - 1].strip()
        if not stripped or set(stripped) <= set("-") or _FOOTER_LINE.match(stripped):
            end -= 1
            continue
        break
    return "\n".join(kept[:end]).strip()


def parse(issue: dict) -> dict:
    """One issue as returned by `linear_client`, flattened.

    `flows` is not a frame field: one bug of the 50 names no flow, and that
    is a bug that touches no catalogued journey, not a malformed record.
    """
    issue = issue or {}
    description = issue.get("description") or ""
    table = _table(description)
    impact = _IMPACT.search(description)
    label_repo = repo_from_labels(issue)
    table_repo = table.get("repo", "")

    doc = _DOC_PATH.search(description)
    result = {
        "identifier": issue.get("identifier") or "",
        "title": (issue.get("title") or "").strip(),
        # Straight from the API: `1` is Urgent and `0` is "no priority",
        # so this is never inferred from the body's own P-scale.
        "priority": issue.get("priority"),
        "state": ((issue.get("state") or {}).get("name")) or "",
        "stateType": ((issue.get("state") or {}).get("type")) or "",
        "labels": _label_names(issue),
        "repo": label_repo or table_repo,
        "foundBy": table.get("foundBy", ""),
        "flows": [f.strip() for f in table.get("flows", "").split(",") if f.strip()],
        "parent": ((issue.get("parent") or {}).get("identifier")) or "",
        "branchName": issue.get("branchName") or "",
        "url": issue.get("url") or "",
        "impact": repair_autolinks(impact.group("impact")).strip() if impact else "",
        "body": body_of(description),
        "docPath": doc.group("path") if doc else "",
        "isGrouper": is_grouper(issue),
        "warnings": [],
        "missing": [],
    }

    # Two independent recordings of the same fact. They agree on all 50
    # today; a disagreement means the board and the body have drifted, and
    # routing the work on the loser silently lands a fix in the wrong repo.
    if label_repo and table_repo and label_repo != table_repo:
        result["warnings"].append(
            f"repo disagrees: label {label_repo!r} vs table {table_repo!r}"
        )

    # A grouper legitimately has none of the frame -- it is a heading. It is
    # reported through `isGrouper`, and calling it malformed on top of that
    # would make 15 ordinary issues look broken.
    if not result["isGrouper"]:
        result["missing"] = [f for f in FRAME_FIELDS if not result.get(f)]
    return result
