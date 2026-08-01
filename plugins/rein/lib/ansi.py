#!/usr/bin/env python3
"""ONE helper for ANSI colour in rein's text output (T003, D4).

D4: colour is for humans only and never changes a byte anyone parses.
`enabled()` is False whenever stdout is not a real TTY, `NO_COLOR` is set, or
`--json` was requested; `paint()` is a no-op whenever `on` is False. `doctor`
and `setup` route every colour through `paint()` here -- never an inline
escape of their own -- so one test can assert that colour has exactly one
source.
"""

from __future__ import annotations

import os
import sys

RESET = "\033[0m"

CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}


def enabled(argv: list[str] | None = None, stream=None) -> bool:
    """Colour turns on only when every one of these holds (D4):
    `--json` was not requested, `NO_COLOR` is unset, and `stream` (stdout by
    default) is a real TTY -- never when piped or redirected.
    """
    if argv and "--json" in argv:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else sys.stdout
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (OSError, ValueError):
        return False


def paint(text: str, *codes: str, on: bool) -> str:
    """Wrap `text` in ANSI `codes`, or return it unchanged when `on` is False."""
    if not on or not text:
        return text
    prefix = "".join(CODES[c] for c in codes)
    return f"{prefix}{text}{RESET}"
