#!/usr/bin/env python3
"""Deterministic plan parsing.

Why this is code and not an agent: reading a task list is a parse, not a
judgement. An agent doing it spends turns re-deriving something a regex settles,
and every one of those turns re-reads the whole accumulated context. The loop's
first agent runs one command and gets the plan already structured.

Two plan sources, same format:
  tasks-md   <root>/tasks.md              -- the default; works in a bare repo
  openspec   openspec/changes/<c>/tasks.md -- plus proposal.md / design.md / specs/

## Format

Standard markdown checkboxes. Everything except the checkbox line is optional:

    - [ ] T001 Parse the config file
      - Type: implementation
      - Depends on: none
      - Human review: false
      - Verification: `python3 -m unittest tests.test_config`
      - Acceptance:
        - reads flow.config.json when present
        - falls back to autodetect otherwise

A bare `- [ ] do the thing` also parses: it gets an auto id, no dependencies, and
the loop falls back to the project's configured `test` command for verification.
Being forgiving matters more than being strict -- a plan the parser rejects is a
plan the loop cannot run.
"""

from __future__ import annotations

import json
import os
import re

# The header sections above the tasks. They exist because of a measured failure:
# a plan whose criteria have NOTHING above them gives the reviewer nothing to
# check intent against, so "satisfied in letter while the guarantee fails" stays
# invisible. Three short sections fix that -- and only three, because everything
# here is read by agents and length is paid for on every turn.
SECTION_RE = re.compile(r"^#{1,3}\s+(Why|Scope|Decisions)\s*$", re.I | re.M)
DECISION_RE = re.compile(r"^\s*[-*]\s+(D\d+)\b[.:) ]*\s*(.*)$", re.I)
SCOPE_RE = re.compile(r"^\s*[-*]\s+(in|out)\s*:\s*(.*)$", re.I)

# `# Change: <name>` -- the convention every plan in this repo already
# follows (tests/test_plan.py's FULL fixture). Matched separately from
# `parse_header()`, whose exact
# key set is pinned by `test_a_plan_with_no_header_still_parses_exactly_
# as_before`; adding a key there would break that test for every plan that
# names itself, which is nearly all of them.
CHANGE_HEADER_RE = re.compile(r"^#\s+Change:\s*(.*)$", re.I)

# `Absorbs: B001, B002` -- which backlog items this change took. Matched
# separately from `parse_header()` for the same reason `CHANGE_HEADER_RE`
# is: that function's exact key set is pinned by
# `test_a_plan_with_no_header_still_parses_exactly_as_before`, so adding a
# key there breaks every plan that does not use this field -- which is all
# of them today.
ABSORBS_RE = re.compile(r"^\s*(?:absorbs|closes\s+backlog)\s*:\s*(.*)$", re.I | re.M)

TASK_RE = re.compile(r"^(\s*)[-*]\s+\[([ xX])\]\s+(.*)$")
FIELD_RE = re.compile(r"^(\s*)[-*]\s+([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
ID_RE = re.compile(r"^(T\d{1,4})\b[.:) ]*\s*(.*)$", re.IGNORECASE)

# Canonical field name -> accepted spellings.
FIELDS = {
    "type": ("type",),
    "dependsOn": ("depends on", "dependson", "depends"),
    "humanReview": ("human review", "humanreview", "supervised"),
    "verification": ("verification", "verify"),
    "area": ("area",),
}
_LOOKUP = {spelling: canon for canon, spellings in FIELDS.items() for spelling in spellings}

TRUE_WORDS = {"true", "yes", "y", "1", "required"}
NONE_WORDS = {"", "none", "n/a", "-", "nothing"}


def _clean(value: str) -> str:
    """Strip markdown emphasis and inline code fences from a field value."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == "`":
        v = v[1:-1].strip()
    return v.strip("*_ ").strip()


def _split_ids(value: str) -> list[str]:
    v = _clean(value).lower()
    if v in NONE_WORDS:
        return []
    return [p.strip().upper() for p in re.split(r"[,;/]| and ", value) if p.strip() and p.strip().lower() not in NONE_WORDS]


def parse_tasks_md(text: str) -> list[dict]:
    """Parse a tasks.md body into structured tasks. Never raises."""
    tasks: list[dict] = []
    current: dict | None = None
    collecting: str = ""  # field currently absorbing sub-bullets ("acceptance" or "")
    auto = 0

    for raw in text.splitlines():
        if not raw.strip():
            continue

        m = TASK_RE.match(raw)
        if m:
            indent, mark, rest = len(m.group(1)), m.group(2), m.group(3).strip()
            ident = ID_RE.match(rest)
            if ident:
                task_id, title = ident.group(1).upper(), ident.group(2).strip()
            else:
                auto += 1
                task_id, title = f"T{auto:03d}", rest
            current = {
                "id": task_id,
                "title": title or task_id,
                "checked": mark.lower() == "x",
                "type": "implementation",
                "dependsOn": [],
                "humanReview": False,
                "verification": "",
                "area": "",
                "acceptance": [],
                "_indent": indent,
            }
            tasks.append(current)
            collecting = ""
            continue

        if current is None:
            continue

        field = FIELD_RE.match(raw)
        if field and len(field.group(1)) > current["_indent"]:
            name = field.group(2).strip().lower()
            key = _LOOKUP.get(name)
            value = field.group(3)
            if name in ("acceptance", "acceptance criteria", "criteria"):
                collecting = "acceptance"
                if value.strip():
                    current["acceptance"].append(_clean(value))
                continue
            if key is None and collecting == "acceptance":
                # A criterion like "the prompt carries four lenses: a, b, c" is
                # field-SHAPED (plain words, then a colon) but is not a field.
                # Only a KNOWN field name may end the collection; swallowing
                # unknown ones silently dropped criteria from real plans.
                current["acceptance"].append(_clean(field.group(2) + ": " + value))
                continue
            collecting = ""
            if key == "dependsOn":
                current["dependsOn"] = _split_ids(value)
            elif key == "humanReview":
                current["humanReview"] = _clean(value).lower() in TRUE_WORDS
            elif key:
                current[key] = _clean(value)
            continue

        if collecting == "acceptance":
            bullet = BULLET_RE.match(raw)
            if bullet and len(bullet.group(1)) > current["_indent"]:
                current["acceptance"].append(_clean(bullet.group(2)))
            elif current["acceptance"] and len(raw) - len(raw.lstrip()) > current["_indent"]:
                # A criterion wrapped across lines. Without this, every
                # continuation was dropped in silence -- measured at 2% of the
                # criteria text across this repo's 63 historical plans, because
                # those were written as single long lines. A plan hard-wrapped
                # for readability loses far more, and loses it invisibly: the
                # implementer prompt is built from this list (loop.js).
                current["acceptance"][-1] += " " + _clean(raw)

    for t in tasks:
        t.pop("_indent", None)
    return tasks


# ---------------------------------------------------------------- locating --


def absorbs_from_text(text: str) -> list:
    """The backlog ids a change declares it took. `[]` when absent.

    Read from anywhere above the first task, so it can sit in the Why
    where it reads naturally. Tolerates `Absorbs:` and `Closes backlog:`,
    any case, and a `none` value.
    """
    head = text.split("\n- [", 1)[0]
    found = []
    for match in ABSORBS_RE.finditer(head):
        for part in re.split(r"[,;/]| and ", match.group(1)):
            token = _clean(part).upper()
            if token and token.lower() not in NONE_WORDS and re.fullmatch(r"B\d+", token):
                found.append(token)
    return found


def absorbs_for(plan_path: str) -> list:
    """`absorbs_from_text` over a plan FILE. `[]` when unreadable.

    A separate reader rather than a new key on `read_plan`, whose return
    shape several callers already destructure -- and the parse is cheap
    enough that a second read costs nothing next to the git calls
    `product_state.state()` already makes.
    """
    try:
        with open(plan_path, "r", encoding="utf-8") as fh:
            return absorbs_from_text(fh.read())
    except OSError:
        return []


def parse_header(text: str) -> dict:
    """Why / Scope / Decisions, from above the first task. Never raises.

    All three are optional: a plan that is only a task list still parses, exactly
    as before. Ceremony that blocks a small change is worse than no ceremony.
    """
    header = {"why": "", "scopeIn": [], "scopeOut": [], "decisions": []}
    current = ""
    why_lines: list[str] = []

    # Everything before the FIRST checkbox is the header. Scanned line by line
    # rather than with a regex search: TASK_RE is anchored and not MULTILINE
    # (it is matched per-line everywhere else), so searching the whole blob finds
    # nothing and the header would silently swallow the entire task list.
    for raw in text.splitlines():
        if TASK_RE.match(raw):
            break
        section = SECTION_RE.match(raw)
        if section:
            current = section.group(1).lower()
            continue
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        if current == "why":
            if not line.startswith("#"):
                why_lines.append(line)
        elif current == "scope":
            m = SCOPE_RE.match(raw)
            if m:
                key = "scopeIn" if m.group(1).lower() == "in" else "scopeOut"
                value = _clean(m.group(2))
                if value:
                    header[key].append(value)
            else:
                bullet = BULLET_RE.match(raw)
                # A bare bullet under Scope with no in/out prefix is ambiguous;
                # treat it as in-scope rather than dropping it silently.
                if bullet:
                    header["scopeIn"].append(_clean(bullet.group(2)))
        elif current == "decisions":
            m = DECISION_RE.match(raw)
            if m:
                title, _, rationale = m.group(2).partition("—")
                if not rationale:
                    title, _, rationale = m.group(2).partition(" - ")
                header["decisions"].append({
                    "id": m.group(1).upper(),
                    "title": _clean(title) or _clean(m.group(2)),
                    "rationale": _clean(rationale),
                })
    header["why"] = " ".join(why_lines).strip()
    return header


def change_name_from_text(text: str) -> str:
    """The name after a `# Change: <name>` line above the first task, or ""
    when the plan carries no such line.

    This is what a flat `tasks.md` plan (no `openspec/changes` directory)
    names itself -- `read_plan()` falls back to it when no `change` argument
    was given, so a flat repo gets a real change name instead of the blank
    string every downstream consumer chokes on (round-2 review finding 1).
    """
    for raw in text.splitlines():
        if TASK_RE.match(raw):
            break
        m = CHANGE_HEADER_RE.match(raw.strip())
        if m:
            return _clean(m.group(1))
    return ""


def _openspec_changes(root: str) -> list[str]:
    """Names of the change directories under `openspec/changes`, sorted.

    Each one carries its own `tasks.md` (that is the whole point of the
    `openspec` plan source) -- a bare directory entry without one is not a
    real change and is left out, the same way a manifest-less directory is
    never counted as a sub-project (D2).
    """
    changes_dir = os.path.join(root, "openspec", "changes")
    if not os.path.isdir(changes_dir):
        return []
    try:
        entries = os.listdir(changes_dir)
    except OSError:
        return []
    return sorted(
        name for name in entries
        if not name.startswith(".")
        and os.path.isfile(os.path.join(changes_dir, name, "tasks.md"))
    )


def plan_path(root: str, source: str, change: str = "", configured: str = "") -> str:
    """The tasks.md this plan resolves to, or "" when none can be named yet.

    D3: never a join that produces a path nobody could have created. For
    `source == "openspec"` with no `change`, `openspec/changes/<change>/
    tasks.md` collapses to `openspec/changes/tasks.md` if `change` is simply
    interpolated -- a location that can never exist, because `tasks.md` is
    the child of a NAMED change directory, never of `changes/` itself. `""`
    signals "no change was named" to `read_plan`, which reports the changes
    that do exist instead of a NOT FOUND at an impossible path.
    """
    root = os.path.abspath(root)
    if configured:
        return configured if os.path.isabs(configured) else os.path.join(root, configured)
    if source == "openspec":
        if not change:
            return ""
        return os.path.join(root, "openspec", "changes", change, "tasks.md")
    for name in ("tasks.md", "TASKS.md"):
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(root, "tasks.md")


def read_plan(root: str = ".", source: str = "", change: str = "", configured: str = "") -> dict:
    """Structured plan + the context artifacts the implementers should read."""
    root = os.path.abspath(root)
    if not source:
        source = "openspec" if os.path.isdir(os.path.join(root, "openspec", "changes")) else "tasks-md"
    path = plan_path(root, source, change, configured)

    if source == "openspec" and not path:
        # No change was named, and the impossible join is exactly what D3
        # forbids reporting -- name what actually exists instead, and say
        # distinctly when nothing does (an empty `openspec/changes` is a
        # different problem than "you did not name one").
        available = _openspec_changes(root)
        error = (
            f"no change named -- choose one of: {', '.join(available)}"
            if available
            else "openspec/changes has no changes yet"
        )
        return {
            "source": source,
            "change": change,
            "path": path,
            "exists": False,
            "tasks": [],
            "pending": [],
            "artifacts": [],
            "why": "",
            "scopeIn": [],
            "scopeOut": [],
            "decisions": [],
            "availableChanges": available,
            "error": error,
        }

    if not os.path.exists(path):
        return {
            "source": source,
            "change": change,
            "path": path,
            "exists": False,
            # Present on EVERY branch, empty when it does not apply: `rein
            # doctor --json` and `rein context` splat this dict verbatim, so a
            # key that appears and disappears is a shape a machine consumer
            # cannot rely on.
            "availableChanges": [],
            "tasks": [],
            "pending": [],
            "artifacts": [],
            "why": "",
            "scopeIn": [],
            "scopeOut": [],
            "decisions": [],
            "error": f"no plan at {path}",
        }

    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    tasks = parse_tasks_md(raw)
    header = parse_header(raw)

    # `change` arrives "" for a flat tasks-md plan (there is no directory to
    # name it from, unlike openspec's `<change>/tasks.md`). Fall back to the
    # plan's own `# Change: <name>` header, then to the repo directory name,
    # so `product_state.state()["change"]` -- and everything keyed on it --
    # is never silently blank.
    resolved_change = change or change_name_from_text(raw) or (os.path.basename(root) or "")

    artifacts = []
    if source == "openspec":
        base = os.path.dirname(path)
        for name in ("proposal.md", "design.md"):
            if os.path.exists(os.path.join(base, name)):
                artifacts.append(os.path.relpath(os.path.join(base, name), root))
        if os.path.isdir(os.path.join(base, "specs")):
            artifacts.append(os.path.relpath(os.path.join(base, "specs"), root))

    return {
        "source": source,
        "change": resolved_change,
        "path": path,
        "exists": True,
        "availableChanges": [],
        **header,
        "tasks": tasks,
        "pending": [t for t in tasks if not t["checked"]],
        "artifacts": artifacts,
        "error": "",
    }


# ----------------------------------------------------------------- closing --


def close_task(path: str, task_id: str) -> bool:
    """Flip `- [ ]` to `- [x]` for one task. Deterministic on purpose.

    An agent hand-editing a checkbox is a chance to corrupt the plan, and the
    plan is the state when tracker is "none".
    """
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    wanted = task_id.upper()
    auto = 0
    changed = False
    for i, line in enumerate(lines):
        m = TASK_RE.match(line.rstrip("\n"))
        if not m:
            continue
        rest = m.group(3).strip()
        ident = ID_RE.match(rest)
        if ident:
            found = ident.group(1).upper()
        else:
            auto += 1
            found = f"T{auto:03d}"
        if found == wanted:
            if m.group(2).lower() != "x":
                lines[i] = re.sub(r"\[\s\]", "[x]", line, count=1)
                changed = True
            break

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
    return changed


# ----------------------------------------------------------------- ordering --


def order_by_dependencies(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Topological order. Deterministic, and tolerant of cycles and broken deps.

    Never hangs and never drops a task: whatever cannot be resolved goes last and
    is reported, because a plan the loop silently truncates is worse than one it
    runs in an imperfect order.
    """
    known = {t["id"] for t in tasks}
    placed: set[str] = set()
    ordered: list[dict] = []
    progress = True
    while progress and len(ordered) < len(tasks):
        progress = False
        for t in tasks:
            if t["id"] in placed:
                continue
            deps = [d for d in t.get("dependsOn", []) if d in known]
            if all(d in placed for d in deps):
                ordered.append(t)
                placed.add(t["id"])
                progress = True
    stuck = [t for t in tasks if t["id"] not in placed]
    return ordered + stuck, [t["id"] for t in stuck]


if __name__ == "__main__":
    import sys

    print(json.dumps(read_plan(sys.argv[1] if len(sys.argv) > 1 else "."), indent=2))
