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

# (the dialog's title, a whole line of its own; what the owner is told, {path} is the worktree)
DIALOGS = (
    (re.compile(r"^hooks?\s+needs?\s+review\.?$", re.I),
     "the agent waits for you to trust its hooks in {path}"),
    (re.compile(r"^(?:do you )?trust (?:the files in )?this (?:folder|directory|workspace|project)\s*\??$", re.I),
     "the agent waits for you to trust the folder {path}"),
)

# A choice the dialog offers: "1. Trust all", "> 1. Yes", "❯ 2. No".
_OPTION = re.compile(r"^(?:[>❯›▶*]\s*)?\d+[.)]\s+\S")
# What a dialog may draw below its options: key hints, nothing the agent says.
_HINT = re.compile(r"\b(?:enter|esc|escape|return|tab|arrow|select|confirm|cancel|navigate)\b|[↑↓←→⏎]", re.I)
_BORDER = "│┃|║╭╮╰╯╔╗╚╝─━═ \t"
MAX_DESCRIPTION = 12  # lines between a dialog's title and its first option

# In accumulated output (no rendered screen), only the last lines are the present.
RECENT_LINES = 25

READ_LIMIT = 80


def _clean(line) -> str:
    return str(line).strip(_BORDER)


def _is_current(lines: list, start: int) -> bool:
    """A dialog titled at `start` is still on screen and unanswered: at least two options follow it
    (after a short description), and below the last one only key hints or borders, never output."""
    options = [i for i in range(start + 1, len(lines)) if _OPTION.match(lines[i])]
    if len(options) < 2 or options[0] - start > MAX_DESCRIPTION + 1:
        return False
    first, last = options[0], options[-1]
    if any(lines[i] and not _OPTION.match(lines[i]) for i in range(first, last + 1)):
        return False  # the option list is one block
    return all(not x or _HINT.search(x) for x in lines[last + 1:])


def detect(lines, path: str, screen: bool = False) -> str:
    """What the agent waits for, from its terminal's lines (oldest first); "" when no dialog shows.

    A dialog counts only as a dialog: its title alone on a line, its options, and nothing the agent
    printed after them. A quote of the title in the agent's own output, or a dialog already
    answered, is not one."""
    lines = [_clean(x) for x in list(lines or [])]
    if not screen:
        lines = lines[-RECENT_LINES:]
    while lines and not lines[-1]:
        lines.pop()
    for i in range(len(lines) - 1, -1, -1):
        for title, text in DIALOGS:
            if title.match(lines[i]) and _is_current(lines, i):
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

