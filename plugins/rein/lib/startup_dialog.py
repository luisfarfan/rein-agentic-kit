"""An agent stopped on a startup dialog only its owner can answer, read off its terminal.

Codex opens a worktree whose repo defines hooks on "Hooks need review"; Claude Code and others ask
whether to trust a new folder. Until a person answers, the agent never reads its brief, and Orca
may abort the launch. Relaunching meets the same dialog, so Rein names it to the owner instead.
Answering it (trusting the hooks or the folder) is a security decision: Rein never types into the
dialog nor edits the agent's settings.

The patterns are about agents, not projects: they are the same for every repo.
"""

from __future__ import annotations

import re

# (pattern over the terminal's recent lines, what the owner is told; {path} is the worktree)
DIALOGS = (
    (re.compile(r"\bhooks?\s+needs?\s+review\b", re.I),
     "the agent waits for you to trust its hooks in {path}"),
    (re.compile(r"\bdo you trust (?:the files in )?this (?:folder|directory|workspace|project)\b"
                r"|\btrust (?:the files in )?this (?:folder|directory|workspace)\?", re.I),
     "the agent waits for you to trust the folder {path}"),
)

# In accumulated output (no rendered screen), only the last lines are the present: once answered,
# the agent's own output scrolls a dialog away. A rendered screen is the present as a whole.
RECENT_LINES = 25

READ_LIMIT = 80


def detect(lines, path: str, screen: bool = False) -> str:
    """What the agent waits for, from its terminal's lines (oldest first); "" when no dialog shows."""
    lines = list(lines or [])
    recent = "\n".join(str(x) for x in (lines if screen else lines[-RECENT_LINES:]))
    for pattern, text in DIALOGS:
        if pattern.search(recent):
            return text.format(path=path or "its worktree")
    return ""


def read(client, handle: str, path: str) -> str:
    """`detect` over terminal `handle`'s rendered screen; "" when there is no handle or Orca cannot
    read it (a closed terminal is no evidence)."""
    if not handle:
        return ""
    try:
        res = client.terminal_read(handle, limit=READ_LIMIT, screen=True)
    except Exception:  # OrcaError, or an Orca without `terminal read`: nothing is known
        return ""
    term = (res.get("terminal") or {}) if isinstance(res, dict) else {}
    tail = term.get("tail") if isinstance(term, dict) else None
    return detect(tail if isinstance(tail, list) else [], path, screen=term.get("source") == "screen")

