"""Quota: pick the implementer and the brake from live Orca rate limits.

Pure functions over the JSON of `orca account list --json`; nothing here
shells out. Unknown usage (provider down, block missing, no number) is
`None`, and `None` never brakes and never routes work onto Claude.
"""

from __future__ import annotations

CODEX = ("codex", "gpt-6.1-sol")
SONNET = ("claude", "sonnet")
OPUS = ("claude", "opus")

WINDOWS = ("session", "weekly")
_ALWAYS = ("claude", "codex")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _window(block, name):
    win = block.get(name)
    return _number(win.get("usedPercent")) if isinstance(win, dict) else None


def parse_rate_limits(account_list: dict) -> dict:
    """`{provider: {"session": pct|None, "weekly": pct|None}}`.

    `claude` and `codex` are always present. A provider whose status is not
    "ok", or whose block is missing or malformed, gets `None` values.
    """
    result = account_list.get("result") if isinstance(account_list, dict) else None
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    if not isinstance(limits, dict):
        limits = {}
    out = {}
    for provider in _ALWAYS:
        out[provider] = {w: None for w in WINDOWS}
    for provider, block in limits.items():
        if not isinstance(block, dict) or "status" not in block:
            continue  # configuration flags and targets share this object
        ok = block.get("status") == "ok"
        out[provider] = {w: (_window(block, w) if ok else None) for w in WINDOWS}
    return out


def choose_implementer(limits: dict, threshold: float, override=None) -> tuple:
    """The explicit override, else Sonnet while Claude's weekly usage is
    known and below `threshold`, else Codex."""
    if override is not None:
        return override
    weekly = (limits.get("claude") or {}).get("weekly")
    if weekly is None or weekly >= threshold:
        return CODEX
    return SONNET


def brake_reason(limits: dict, agent: str, max_pct: float = 90.0) -> str:
    """`""` when the agent's provider is fine; otherwise a sentence naming
    the provider and the window at or above `max_pct`."""
    provider = {"claude": "claude", "codex": "codex"}.get(agent, agent)
    block = limits.get(provider) or {}
    for window in WINDOWS:
        used = block.get(window)
        if used is not None and used >= max_pct:
            return "%s %s usage is at %g%% (limit %g%%)" % (provider, window, used, max_pct)
    return ""


def usage_delta(before: dict, after: dict) -> dict:
    """Per provider and window, `after - before`; `None` when either side is."""
    out = {}
    for provider in sorted(set(before) | set(after)):
        b = before.get(provider) or {}
        a = after.get(provider) or {}
        out[provider] = {
            w: (a[w] - b[w] if a.get(w) is not None and b.get(w) is not None else None)
            for w in WINDOWS
        }
    return out
