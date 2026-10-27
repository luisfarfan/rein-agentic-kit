"""The launch profile of a Claude implementer worker: what its session loads before the brief.

A fresh Claude Code session starts with every built-in tool (Agent, Artifact, Workflow ...), the
skill listing and the SessionStart hooks of the user's plugins, none of which a ship worker uses,
and re-reads all of it on every turn. Measured on 459 real workers: a median 68.7k-token first turn,
77% of a worker's tokens. The `lean` profile is a `.claude/settings.local.json` in the worktree that
turns the user's plugins off and denies the tools a worker never needs (21.2k instead of 59.0k in
`claude -p`; docs/research/2026-10-26-arquitectura).

ToolSearch is never denied: with the user's MCP servers still configured, denying it makes Claude
load every deferred MCP tool up front (533k tokens measured). `rein benchmark startup` measures a
profile so a regression like that is caught.

The file is applied only where git ignores it (a worker must never commit it) and is put back as it
was before anything that must not run lean, such as the auditor, starts in that worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

PROFILES = ("default", "lean")
REL_PATH = os.path.join(".claude", "settings.local.json")
USER_SETTINGS = os.path.expanduser("~/.claude/settings.json")

# Built-in tools an implementer worker has no use for. Never ToolSearch (see the module doc).
LEAN_DENY = ["Artifact", "Workflow", "ScheduleWakeup", "ReportFindings", "AskUserQuestion",
             "ListAgents", "SendFeedback", "Agent", "Skill", "advisor"]
NEVER_DENY = ("ToolSearch",)


class ProfileError(Exception):
    """The profile could not be applied or put back; the message says why."""


def enabled_user_plugins(user_settings: str = USER_SETTINGS) -> list:
    """The plugins the user's own Claude settings enable (the worker turns these off)."""
    try:
        with open(user_settings, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    return sorted(name for name, on in plugins.items() if on is True)


def lean_settings(existing: dict, plugins: list) -> dict:
    """`existing` with the lean profile merged in: the plugins off, the tools denied. Whatever else
    the file holds (the owner's own MCP choices, skill overrides) is kept."""
    out = json.loads(json.dumps(existing or {}))
    enabled = out.get("enabledPlugins") if isinstance(out.get("enabledPlugins"), dict) else {}
    out["enabledPlugins"] = dict(enabled, **{p: False for p in plugins})
    perms = out.get("permissions") if isinstance(out.get("permissions"), dict) else {}
    deny = [d for d in perms.get("deny") or [] if isinstance(d, str)]  # the owner's own stay as they are
    deny += [d for d in LEAN_DENY if d not in deny and d not in NEVER_DENY]
    out["permissions"] = dict(perms, deny=deny)
    return out


def is_ignored(worktree: str) -> bool:
    """Whether git ignores the settings file in `worktree` (else a worker could commit it)."""
    proc = subprocess.run(["git", "-C", worktree, "check-ignore", "-q", REL_PATH],
                          capture_output=True, text=True)
    return proc.returncode == 0


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".rein-profile-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def prepare(worktree: str, record: dict = None, plugins: list = None, ignored=None) -> dict:
    """The record of the lean profile for `worktree`, without writing it:
    `{"original": <text before Rein, or None when there was no file>, "written": <text>}`.
    The caller persists it, then `write`s it, so a crash in between never makes Rein take its own
    file for the owner's.

    `record` is this worktree's record from an earlier apply: its `original` is kept unless the
    owner changed the file since. ProfileError when git does not ignore the file or the existing
    file is not a JSON object."""
    if not (ignored or is_ignored)(worktree):
        raise ProfileError("%s is not ignored by git in %s, so a worker could commit it"
                           % (REL_PATH, worktree))
    current = _read(os.path.join(worktree, REL_PATH))
    if record and current == record.get("written"):
        return record  # already in place
    if record and current is None:
        base_text = record.get("original")  # removed since: come back to what the owner had
    else:
        base_text = current  # first apply, or the owner changed it since: that is the file to come back to
    try:
        base = json.loads(base_text) if base_text else {}
    except ValueError as exc:
        raise ProfileError("%s in %s is not valid JSON: %s" % (REL_PATH, worktree, exc))
    if not isinstance(base, dict):
        raise ProfileError("%s in %s is not a JSON object" % (REL_PATH, worktree))
    plugins = enabled_user_plugins() if plugins is None else plugins
    written = json.dumps(lean_settings(base, plugins), indent=2, sort_keys=True) + "\n"
    return {"original": base_text, "written": written}


def write(worktree: str, record: dict) -> None:
    """Write `record`'s profile into `worktree` (a no-op when it is already there)."""
    path = os.path.join(worktree, REL_PATH)
    if _read(path) != record["written"]:
        _write(path, record["written"])


def apply(worktree: str, record: dict = None, plugins: list = None, ignored=None) -> dict:
    """`prepare` then `write`: for callers with nothing to persist in between."""
    rec = prepare(worktree, record, plugins, ignored)
    write(worktree, rec)
    return rec


def restore(worktree: str, record: dict) -> str:
    """Put the file back as it was before Rein. Returns "" when done, else why it was left alone
    (someone changed the file since Rein wrote it)."""
    path = os.path.join(worktree, REL_PATH)
    current = _read(path)
    if current != record.get("written"):
        return "" if current == record.get("original") else (
            "%s in %s changed since Rein wrote it; left as it is" % (REL_PATH, worktree))
    original = record.get("original")
    if original is None:
        os.remove(path)
        try:
            os.rmdir(os.path.dirname(path))  # only when Rein's file was all `.claude/` held
        except OSError:
            pass
    else:
        _write(path, original)
    return ""


# --- measurement (`rein benchmark startup`) --------------------------------------

PROBE_PROMPT = "Reply with exactly: OK"


def first_turn_tokens(usage: dict) -> int:
    """The context of a one-turn `claude -p` answer: everything it read, cached or not."""
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _default_runner(argv: list, cwd: str, timeout: float) -> tuple:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def measure(profile: str, source: str = "", model: str = "haiku", runner=_default_runner,
            claude: str = "claude", timeout: float = 180.0) -> dict:
    """One `claude -p` turn in a scratch git repo under `profile`; the repo gets `source`'s
    CLAUDE.md and AGENTS.md so the project's own instructions count. Returns
    `{"profile", "tokens", "error"}`. It costs one short call on the subscription."""
    if profile not in PROFILES:
        raise ValueError("unknown profile %r" % profile)
    with tempfile.TemporaryDirectory(prefix="rein-startup-") as tmp:
        subprocess.run(["git", "init", "-q", tmp], capture_output=True)
        for name in ("CLAUDE.md", "AGENTS.md"):
            src = os.path.join(source, name) if source else ""
            if src and os.path.isfile(src):
                with open(src, encoding="utf-8", errors="replace") as fh:
                    _write(os.path.join(tmp, name), fh.read())
        if profile == "lean":
            apply(tmp, ignored=lambda _p: True)  # a scratch repo nobody commits from
        argv = [claude, "-p", PROBE_PROMPT, "--model", model, "--output-format", "json",
                "--max-turns", "1", "--no-session-persistence"]
        try:
            code, out, err = runner(argv, tmp, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"profile": profile, "tokens": None, "error": str(exc)}
    try:
        data = json.loads(out)
    except ValueError:
        return {"profile": profile, "tokens": None,
                "error": "claude exited %s without JSON: %s" % (code, (err or out or "").strip()[:300])}
    if data.get("is_error"):
        return {"profile": profile, "tokens": None, "error": str(data.get("result") or "claude reported an error")[:300]}
    return {"profile": profile, "tokens": first_turn_tokens(data.get("usage") or {}), "error": ""}
