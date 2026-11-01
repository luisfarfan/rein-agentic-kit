"""Quota: pick the implementer and the brake from live Orca rate limits.

Pure functions over the JSON of `orca account list --json`; nothing here
shells out. Unknown usage (provider down, block missing, no number) is
`None`. Legacy normalized inputs remain compatible; execution events use
checked_limits so unknown or stale observations cannot authorize dispatch.
"""

from __future__ import annotations

import math
import time

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
MAX_OBSERVATION_AGE_SECONDS = 15 * 60


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if not isinstance(value, float) or math.isfinite(value) else None


def _percent(value):
    number = _number(value)
    return number if number is not None and 0 <= number <= 100 else None


def _window(block, name):
    win = block.get(name)
    return _percent(win.get("usedPercent")) if isinstance(win, dict) else None


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


def checked_limits(account_list: dict, now=None, max_age_seconds=MAX_OBSERVATION_AGE_SECONDS) -> dict:
    """Execution-event view of Orca observations. No query, cache, or daemon.

    Orca updatedAt is milliseconds. Missing/invalid/future/old timestamps and
    unknown numeric windows require a new usable observation, never an override.
    Keep known limited signals even if the observation is unusable.
    """
    now = time.time() if now is None else now
    if _number(now) is None or _number(max_age_seconds) is None or max_age_seconds <= 0:
        raise ValueError('quota time and positive max age must be finite numbers')
    out = parse_rate_limits(account_list)
    result = account_list.get('result', {}) if isinstance(account_list, dict) else {}
    raw = result.get('rateLimits', {}) if isinstance(result, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    for provider, usage in out.items():
        block = raw.get(provider)
        block = block if isinstance(block, dict) else {}
        stamp = _number(block.get('updatedAt'))
        try:
            age = now - stamp / 1000 if stamp is not None else None
        except OverflowError:
            age = None
        reason = ''
        if age is None:
            reason = 'observation timestamp is missing or invalid'
        elif age < -60:
            reason = 'observation timestamp is in the future'
        elif age > max_age_seconds:
            reason = 'observation is stale (older than %g seconds)' % max_age_seconds
        elif not any(usage.get(w) is not None for w in WINDOWS):
            reason = 'usage is unknown'
        usage['_quota_checked'] = True
        usage['_observation_age_seconds'] = age
        if reason:
            usage['_quota_unusable'] = reason
        epoch = {'plan': block.get('planType')}
        for w in WINDOWS:
            win = block.get(w)
            win = win if isinstance(win, dict) else {}
            epoch[w] = [win.get('windowMinutes'), win.get('resetsAt')]
        usage['_quota_epoch'] = epoch
    return out


def _unusable(limits, provider):
    block = limits.get(provider) or {}
    if block.get('_quota_unusable'):
        return block['_quota_unusable']
    if not block and any(isinstance(v, dict) and v.get('_quota_checked') for v in limits.values()):
        return 'provider observation is missing'
    return ''


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
    unusable = _unusable(limits, provider)
    if unusable:
        return '%s quota %s; refresh Orca before dispatch' % (provider, unusable)
    return ""


def usage_delta(before: dict, after: dict) -> dict:
    """Observable nonnegative deltas; unknown after a reset or missing observation."""
    out = {}
    for provider in sorted(set(before) | set(after)):
        b = before.get(provider) or {}
        a = after.get(provider) or {}
        same_epoch = a.get('_quota_epoch') == b.get('_quota_epoch')
        usable = not a.get('_quota_unusable') and not b.get('_quota_unusable')
        out[provider] = {
            w: (a[w] - b[w] if same_epoch and usable and a.get(w) is not None and b.get(w) is not None and a[w] >= b[w] else None)
            for w in WINDOWS
        }
    return out


def availability(limits: dict, provider: str, max_pct: float = 90.0) -> str:
    """One known window can establish available usage; absent windows stay unknown."""
    block = limits.get(provider) or {}
    if block.get('_limited') or any(block.get(w) is not None and block[w] >= max_pct for w in WINDOWS):
        return 'limited'
    if _unusable(limits, provider):
        return 'unknown'
    return "available" if any(_percent(block.get(w)) is not None for w in WINDOWS) else "unknown"


def snapshot(account_list: dict, plans=None) -> dict:
    """Sanitized metadata for evidence, with explicit plans when not reported.

    No account IDs, auth, prices, inferred token balances, or private endpoints.
    Re-read each event; this function holds no plan cache across runs.
    """
    limits = checked_limits(account_list)
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
        out[provider]['observation_age_seconds'] = usage.get('_observation_age_seconds')
        out[provider]['unusable_reason'] = usage.get('_quota_unusable', '')
    return out
