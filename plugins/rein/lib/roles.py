#!/usr/bin/env python3
"""The three role profiles, readable without Claude Code.

The profiles live in `skills/rein-role/SKILL.md`, which is a Claude Code skill:
frontmatter, a `/rein:rein-role` heading, and a bash line that hunts for the
binary in `~/.claude/plugins/cache/`. All of that is fine for Claude Code and
useless to Codex, OpenCode, or anything else that might implement or review.

The fix is NOT a second copy. This kit already tests that one definition has
two consumers by substring-matching both shipped files against each other,
because two copies of one rule drift apart with nothing failing. So this reads
the SAME file the skill does and prints one section of it. One source, two
consumers, and drift is not merely detected -- it is impossible.

`rein role reviewer` is therefore how any agent gets the reviewer's operating
profile, including the rules it is expected to be bound by.
"""

from __future__ import annotations

import os
import re

ROLES = ("planner", "implementer", "reviewer")

# The section heading as the skill writes it: `## \`planner\``.
_HEADING = re.compile(r"^##\s+`(?P<name>[a-z-]+)`\s*$", re.MULTILINE)


def skill_path(plugin_root: str) -> str:
    return os.path.join(plugin_root, "skills", "rein-role", "SKILL.md")


def _sections(text: str) -> dict:
    """Every `## \\`name\\`` section, by name, body text only."""
    out = {}
    matches = list(_HEADING.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        # The file closes with a `---` rule and a confirmation sentence aimed at
        # a Claude Code session; it belongs to the skill, not to the role.
        body = re.split(r"^---\s*$", body, maxsplit=1, flags=re.MULTILINE)[0]
        out[m.group("name")] = body.strip()
    return out


def available(plugin_root: str) -> list[str]:
    """Roles this installation can actually produce -- not the constant.

    A wheel built without `skills/*/SKILL.md` in package-data would leave every
    profile unreachable while `ROLES` still listed three of them, which is the
    kind of confident-and-wrong answer this kit exists to avoid.
    """
    try:
        with open(skill_path(plugin_root), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    # Read ONCE, then filter. Calling fh.read() inside the comprehension runs
    # it per role: the first gets the file, the rest get "" from an exhausted
    # handle, so this reported one role out of three and looked plausible.
    found = _sections(text)
    return [r for r in ROLES if r in found]


def profile(name: str, plugin_root: str) -> dict:
    """One role's operating profile as plain markdown.

    Returns `{"ok", "role", "text", "error"}` rather than raising: this is read
    by a CLI whose contract is an exit code, and a missing skill file is a setup
    problem, not a crash.
    """
    wanted = (name or "").strip().lower()
    if wanted not in ROLES:
        return {"ok": False, "role": wanted, "text": "",
                "error": f"unknown role {name!r} -- expected one of: {', '.join(ROLES)}"}

    path = skill_path(plugin_root)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"ok": False, "role": wanted, "text": "",
                "error": f"cannot read {path}: {exc}"}

    section = _sections(text).get(wanted)
    if not section:
        return {"ok": False, "role": wanted, "text": "",
                "error": f"{path} has no `{wanted}` section -- the skill's headings changed"}

    return {"ok": True, "role": wanted, "text": section, "error": ""}
