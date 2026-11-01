"""Quota: pick the implementer and the brake from live Orca rate limits.

Pure functions over the JSON of `orca account list --json`; nothing here
shells out. Unknown usage (provider down, block missing, no number) is
`None`, and `None` never brakes and never routes work onto Claude.
"""

from __future__ import annotations

import math

CODEX = ("codex", "gpt-6.1-sol")
SONNET = ("claude", "sonnet")
OPUS = ("claude", "opus")
ANTIGRAVITY = ("antigravity", "gemini-4-argon-medium")

# The model used when the owner names an agent but no model (rein quick, rein ship).
DEFAULT_MODELS = {SONNET[0]: SONNET[1], CODEX[0]: CODEX[1], ANTIGRAVITY[0]: ANTIGRAVITY[1]}


def default_model(agent: str) -> str:
    """The default model of a known agent, "" when the agent is unknown."""
    return DEFAULT_MODELS.get(agent, "")


WINDOWS = ("session", "weekly")
_ALWAYS = ("claude", "codex")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 <= value <= 100 and math.isfinite(value) else None


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
        limited = block.get("status") in ("limited", "exhausted", "rate_limited", "limit_reached")
        ok = block.get("status") == "ok"
        out[provider] = {w: (_window(block, w) if ok else None) for w in WINDOWS}
        if limited or any(isinstance(block.get(w), dict) and block[w].get("limited") is True for w in WINDOWS):
            out[provider]["_limited"] = True
    return out


def choose_implementer(limits: dict, threshold: float, override=None, max_pct: float = 90.0) -> tuple:
    """The explicit override, else the first candidate the brake would not
    stop: Sonnet (only while Claude's weekly usage is known and below
    `threshold`), then Codex, then Antigravity (only when its usage is
    known). When every candidate is braked, the first one, so the brake opens."""
    if override is not None:
        return override
    return first_unbraked(candidates(limits, threshold), limits, max_pct)


def candidates(limits: dict, threshold: float) -> list:
    """The implementers worth trying, in order of preference."""
    out = []
    weekly = (limits.get("claude") or {}).get("weekly")
    if weekly is not None and weekly < threshold:
        out.append(SONNET)
    out.append(CODEX)
    if (limits.get("antigravity") or {}).get("weekly") is not None:
        out.append(ANTIGRAVITY)
    return out


def first_unbraked(choices: list, limits: dict, max_pct: float = 90.0) -> tuple:
    """Prefer measured capacity, then a legacy unknown fallback, else a braked choice."""
    # A missing percentage must not outrank measured spare capacity.
    for choice in choices:
        if availability(limits, choice[0], max_pct) == "available":
            return choice
    for choice in choices:
        if not brake_reason(limits, choice[0], max_pct):
            return choice
    return choices[0]


def brake_reason(limits: dict, agent: str, max_pct: float = 90.0) -> str:
    """`""` when the agent's provider is fine; otherwise a sentence naming
    the provider and the window at or above `max_pct`."""
    provider = {"claude": "claude", "codex": "codex"}.get(agent, agent)
    block = limits.get(provider) or {}
    if block.get("_limited") is True:
        return "%s is marked limited by Orca" % provider
    for window in WINDOWS:
        used = block.get(window)
        if used is not None and used >= max_pct:
            return "%s %s usage is at %g%% (limit %g%%)" % (provider, window, used, max_pct)
    return ""


def usage_delta(before: dict, after: dict) -> dict:
    """Observable nonnegative deltas; unknown after a reset or missing observation."""
    out = {}
    for provider in sorted(set(before) | set(after)):
        b = before.get(provider) or {}
        a = after.get(provider) or {}
        out[provider] = {
            w: (a[w] - b[w] if a.get(w) is not None and b.get(w) is not None and a[w] >= b[w] else None)
            for w in WINDOWS
        }
    return out


def availability(limits: dict, provider: str, max_pct: float = 90.0) -> str:
    """One known window can establish available usage; absent windows stay unknown."""
    if brake_reason(limits, provider, max_pct):
        return "limited"
    block = limits.get(provider) or {}
    return "available" if any(_number(block.get(w)) is not None for w in WINDOWS) else "unknown"


def snapshot(account_list: dict, plans=None) -> dict:
    """Sanitized metadata for evidence, with explicit plans when not reported.

    No account IDs, auth, prices, inferred token balances, or private endpoints.
    Re-read each event; this function holds no plan cache across runs.
    """
    limits = parse_rate_limits(account_list)
    result = account_list.get("result", {}) if isinstance(account_list, dict) else {}
    raw = result.get("rateLimits", {}) if isinstance(result, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    plans = plans or {}
    out = {}
    for provider, usage in limits.items():
        block = raw.get(provider)
        block = block if isinstance(block, dict) else {}
        windows = {}
        for w in WINDOWS:
            win = block.get(w)
            win = win if isinstance(win, dict) else {}
            windows[w] = {"usedPercent": usage[w], "resetsAt": win.get("resetsAt"),
                          "windowMinutes": win.get("windowMinutes")}
        out[provider] = {"status": availability(limits, provider), "windows": windows,
                         "plan": block.get("planType") or plans.get(provider),
                         "plan_source": "provider" if block.get("planType") else "configured" if plans.get(provider) else "unknown",
                         "updatedAt": block.get("updatedAt"), "source": "orca account list"}
    return out
