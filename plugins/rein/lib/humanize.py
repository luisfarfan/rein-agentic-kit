#!/usr/bin/env python3
"""Turns raw title/body text into prose for the Plane board (D7).

Humanization is decorative, never load-bearing:

  * it never runs on names or identifiers -- those are sanitised
    mechanically by `plane_client.safe_name()` / `safe_identifier()`, which
    import nothing from this module and cannot be made to call it (D9)
  * every failure mode -- no agent on `PATH`, a non-zero exit, a timeout,
    empty output, or output past the configured cap -- returns the raw
    input verbatim. Nothing here ever raises past `humanize()` (D7)
  * it is never on the path that decides *what* to sync. Whatever computes
    the upsert set does so from `content_hash()` (mechanical) alone; the
    agent call happens strictly after that decision, to produce display
    text nobody's sync depends on

`lang` is read from `plane.json`'s `lang` field, defaulting to `"es"` (D4).
An explicit `lang` argument always wins and reaches the agent unchanged --
the config read only happens when the caller passes `lang=None`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

DEFAULT_LANG = "es"
DEFAULT_TIMEOUT = 30.0
DEFAULT_OUTPUT_CAP = 4000  # characters; a runaway agent must not land on the board

KIND_TITLE = "title"
KIND_BODY = "body"

AGENT_CMD_ENV = "REIN_HUMANIZE_CMD"
DEFAULT_AGENT_CMD = "claude"


def _read_lang(root: str | os.PathLike | None = None) -> str:
    """`plane.json`'s `lang` field, defaulting to `"es"` (D4).

    Any reason the file cannot be read as `{"lang": "<str>"}` -- absent,
    unreadable, not JSON, no `lang` key, an empty or non-string value --
    falls back to the default rather than raising; a malformed config must
    not be able to block humanization any more than a missing agent can.
    """
    base = Path(root) if root is not None else Path.cwd()
    path = base / "plane.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_LANG
    lang = data.get("lang") if isinstance(data, dict) else None
    return lang if isinstance(lang, str) and lang.strip() else DEFAULT_LANG


def content_hash(text: str, kind: str, lang: str) -> str:
    """Identity for caching (D7's "at most once per entity per sync"): the
    triple that fully determines the agent's output, not the object it came
    from -- two entities with the same title in the same run hash equal."""
    payload = f"{kind}\x1f{lang}\x1f{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HumanizeAgentError(Exception):
    """The agent process ran but exited non-zero. Caught by `humanize()`
    just like every other failure mode -- this type exists only so a real
    invocation's stderr is visible in logs before it is discarded (D7)."""


def _build_prompt(text: str, kind: str, lang: str) -> str:
    what = "a short title" if kind == KIND_TITLE else "a short body"
    return (
        f"Rewrite the following {what} as plain, natural prose in language "
        f"'{lang}'. Return only the rewritten text, nothing else.\n\n{text}"
    )


def _default_invoke(text: str, kind: str, lang: str, *, timeout: float) -> str:
    """Shells out to a small agent CLI, configurable via `REIN_HUMANIZE_CMD`
    (default `"claude"`). Never called directly by the test suite -- every
    test injects its own `invoke` -- so this is the one path that is not
    exercised there and that must not open a socket or need credentials
    beyond what the configured command itself requires."""
    cmd = os.environ.get(AGENT_CMD_ENV, DEFAULT_AGENT_CMD)
    proc = subprocess.run(
        [cmd, "-p", _build_prompt(text, kind, lang)],
        capture_output=True,
        timeout=timeout,
        text=True,
    )
    if proc.returncode != 0:
        raise HumanizeAgentError(
            f"{cmd} exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        )
    return proc.stdout


def humanize(
    text: str,
    kind: str,
    lang: str | None = None,
    *,
    root: str | os.PathLike | None = None,
    invoke=None,
    timeout: float = DEFAULT_TIMEOUT,
    cap: int = DEFAULT_OUTPUT_CAP,
) -> str:
    """Prose for `text` (a `kind` of `"title"` or `"body"`) via a small
    agent -- decorative only. Returns `text` unchanged on any failure (D7).

    `lang` defaults to `plane.json`'s `lang` field (itself defaulting to
    `"es"`, D4) but an explicit value always wins and reaches the agent
    unchanged, bypassing the config read entirely.
    """
    resolved_lang = lang if lang is not None else _read_lang(root)
    invoke_fn = invoke or _default_invoke
    try:
        result = invoke_fn(text, kind, resolved_lang, timeout=timeout)
    except Exception:
        # No agent on PATH (FileNotFoundError), a non-zero exit
        # (HumanizeAgentError), a timeout (subprocess.TimeoutExpired) --
        # every failure mode this function does not itself validate below
        # lands here and falls back to the raw input (D7).
        return text
    if not isinstance(result, str):
        return text
    result = result.strip()
    if not result:
        return text
    if len(result) > cap:
        return text
    return result


class HumanizeCache:
    """Caches `humanize()` by `content_hash()` so the agent runs at most
    once per distinct (text, kind, lang) per instance. Callers construct one
    per sync -- never a module-level singleton -- so a cache never outlives
    the run it was built for and never leaks between syncs."""

    def __init__(
        self,
        *,
        root: str | os.PathLike | None = None,
        invoke=None,
        timeout: float = DEFAULT_TIMEOUT,
        cap: int = DEFAULT_OUTPUT_CAP,
    ):
        self._root = root
        self._invoke = invoke
        self._timeout = timeout
        self._cap = cap
        self._seen: dict = {}

    def humanize(self, text: str, kind: str, lang: str | None = None) -> str:
        resolved_lang = lang if lang is not None else _read_lang(self._root)
        key = content_hash(text, kind, resolved_lang)
        if key in self._seen:
            return self._seen[key]
        result = humanize(
            text,
            kind,
            resolved_lang,
            root=self._root,
            invoke=self._invoke,
            timeout=self._timeout,
            cap=self._cap,
        )
        self._seen[key] = result
        return result
