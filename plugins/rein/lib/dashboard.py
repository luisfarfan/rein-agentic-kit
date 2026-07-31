#!/usr/bin/env python3
"""rein dashboard -- the run ledger as a view model, servable locally.

Usage:
    rein dashboard            serve the view model at http://127.0.0.1:8765/
    rein dashboard --port N   serve on a different port (still 127.0.0.1 only)
    rein dashboard --json     print the view model and exit -- no socket opened

The whole data path (ledger -> baseline join -> view model) is a pure function
of two files on disk, so it is testable without a network: `build_view` never
touches a socket, only `serve` does.

Local only, on purpose (see the plan's decisions): no auth, no sharing, no
live streaming -- the ledger is written when a run ends, and that is the only
moment this reads from.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import http.server
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from token_report import (  # noqa: E402
    BASELINE_PATH,
    LEDGER_PATH,
    BaselineCorruptError,
    _pct_change,
    read_baseline,
    read_ledger,
)

DEFAULT_PORT = 8765


# --------------------------------------------------------------- safe reads --


def _read_ledger_safe(ledger_path: str) -> tuple[list[dict], str]:
    """Rows plus an error message, never an exception.

    `read_ledger` already skips individual corrupt JSON lines while keeping the
    rest (that is the "partly corrupt" case -- valid rows must survive it), but
    it only checks that each line parses as JSON, not that it parses as an
    *object* -- a line like `"truncated-but-valid-json"` parses fine and comes
    back as a `str`, which every row-consumer here promptly `.get()`s. Filter
    those out here too, so a merely-truncated line degrades like any other
    corrupt one instead of crashing `build_view` downstream.

    This also guards the outer failure modes a corrupt *file* can still cause
    (not a directory, not readable, not UTF-8) so those degrade to an empty
    ledger with a message instead of a traceback.
    """
    if not os.path.exists(ledger_path):
        return [], ""
    try:
        rows = [r for r in read_ledger(ledger_path) if isinstance(r, dict)]
        return rows, ""
    except OSError as exc:
        return [], f"ledger unreadable at {ledger_path}: {exc}"
    except UnicodeDecodeError as exc:
        return [], f"ledger unreadable at {ledger_path}: {exc}"


def _read_baseline_safe(baseline_path: str) -> dict | None:
    """`None` for both "no baseline" and "baseline file is corrupt" -- a
    dashboard has no good way to surface a second error channel, and a corrupt
    baseline must never take down the run list with it."""
    try:
        return read_baseline(baseline_path)
    except BaselineCorruptError:
        return None


# ------------------------------------------------------------------ view model --


def _run_view(row: dict, project: str, baseline: dict | None) -> dict:
    baseline_wf_id = baseline.get("wf_id") if baseline else None
    baseline_project = baseline.get("project") if baseline else None
    baseline_ts = (baseline.get("ts") or "") if baseline else ""
    # A baseline marked before `project` was tracked cannot be matched to any
    # project safely -- never guess, suppress deltas entirely (mirrors
    # token_report.render_ledger's `baseline_stale`).
    baseline_stale = bool(baseline) and not baseline_project

    is_baseline = bool(baseline_wf_id) and row.get("wf_id") == baseline_wf_id and project == baseline_project

    deltas = None
    if (
        baseline
        and not baseline_stale
        and not is_baseline
        and project == baseline_project
        and row.get("ts", "") > baseline_ts
    ):
        deltas = {
            "turns_per_agent": _pct_change(row.get("turns_per_agent"), baseline.get("turns_per_agent")),
            "ctx_max": _pct_change(row.get("ctx_max"), baseline.get("ctx_max")),
            "opus_share": _pct_change(row.get("opus_share"), baseline.get("opus_share")),
        }

    return {
        "wf_id": row.get("wf_id", ""),
        "ts": row.get("ts", ""),
        "turns": row.get("turns", 0),
        "turns_per_agent": row.get("turns_per_agent", 0),
        "ctx_max": row.get("ctx_max", 0),
        "opus_share": row.get("opus_share", 0),
        "totals": row.get("totals", {}),
        "total": row.get("total", 0),
        "agents": row.get("agents", []),
        "is_baseline": is_baseline,
        "deltas": deltas,
    }


# ------------------------------------------------------ plain-english summary --

# Human-readable name for each metric, plus the word to use on either side of
# its delta ("12.3% fewer turns per agent" / "8.1% more context per turn") --
# never a bare signed number (D2/T001).
_METRIC_WORDS: dict[str, dict[str, str]] = {
    "turns_per_agent": {"noun": "turns per agent", "more": "more", "less": "fewer"},
    "ctx_max": {"noun": "context per turn", "more": "more", "less": "less"},
    "opus_share": {"noun": "of the Opus quota", "more": "more", "less": "less"},
}

# Whether a LOWER value is better, per metric. A lookup table, not a blanket
# rule -- `direction()` reads this instead of assuming "negative is always
# better" for every key, so the day a metric is added where higher is the
# win (a cache-hit rate, say), only this table changes, not every call site
# that currently hardcodes the opposite.
METRIC_DIRECTION: dict[str, bool] = {
    "turns_per_agent": True,
    "ctx_max": True,
    "opus_share": True,
}

_WHY_SENTENCE = (
    "Cost is turns multiplied by the context re-read every turn, so these "
    "three numbers are what predict it."
)

# --------------------------------------------------------------- glossary --

# D3: one glossary, one source. Every metric key the page renders anywhere
# (the per-run table, the per-agent detail table, and each Δ column) must
# have an entry here -- `test_dashboard_glossary.py` asserts that directly
# against this dict, and `_glossary_html` below raises a `KeyError` naming
# the missing key the moment a header tries to use one that isn't. Each
# entry is a one-line `meaning` (what the number is) plus a one-line `why`
# (why it predicts cost) -- never just a definition floating free of the
# thing it is supposed to help someone decide.
GLOSSARY: dict[str, dict[str, str]] = {
    "turns": {
        "meaning": "How many turns this agent took to finish its work.",
        "why": "Every turn is a full model call billed on the context sent with it, so turns is the base unit of cost.",
    },
    "turns_per_agent": {
        "meaning": "Average turns per agent across the whole run.",
        "why": "It is the single biggest lever on total cost: half the turns is roughly half the calls.",
    },
    "ctx_max": {
        "meaning": "The largest context size sent in any single turn.",
        "why": "Every turn resends the full context, so a bigger max multiplies the price of every turn that reaches it.",
    },
    "opus_share": {
        "meaning": "Share of turns that ran on the Opus model instead of a cheaper one.",
        "why": "Opus costs far more per token than Sonnet or Haiku, so this share drives a large part of total spend.",
    },
    "total": {
        "meaning": "Total tokens (input plus output) this agent used.",
        "why": "Tokens are what is actually billed, so this is the cost itself, not a proxy for it.",
    },
    "delta_turns_per_agent": {
        "meaning": "Percent change in turns/agent versus the marked baseline run.",
        "why": "Shows whether a change made the biggest cost lever better or worse, not just what it is now.",
    },
    "delta_ctx_max": {
        "meaning": "Percent change in ctx_max/turn versus the marked baseline run.",
        "why": "Shows whether a change grew or shrank the context every turn re-reads, a direct cost multiplier.",
    },
    "delta_opus_share": {
        "meaning": "Percent change in opus share versus the marked baseline run.",
        "why": "Shows whether a change moved work onto or off of the most expensive model.",
    },
}


def _baseline_identity_text(baseline: dict | None, project: str) -> str:
    """The baseline identified where it is used (D4/T002): which run and
    when it was marked for a project that has one applying to it, or --
    honestly -- what to run to mark one for a project that does not.

    A baseline only "belongs" to a project if it was marked with that
    project recorded on it (mirrors the staleness/mismatch checks
    `_run_view` already applies to individual deltas) -- a baseline for a
    different project, or one predating project tracking, is not this
    project's baseline and must not be presented as if it were.
    """
    applies = bool(baseline) and baseline.get("project") == project and bool(baseline.get("wf_id"))
    if not applies:
        return "No baseline is marked for this project -- run `rein baseline mark` after a change to compare future runs against it."
    wf_id = baseline.get("wf_id", "")
    ts = baseline.get("ts", "")
    return f"Baseline: run {wf_id}, marked {ts}."


def direction(metric_key: str, delta: float | None, table: dict[str, bool] | None = None) -> str | None:
    """"better" / "worse" / "unchanged" for `delta` on `metric_key`, or `None`
    when there is nothing to compare (`delta is None`).

    Pure: the lower-is-better table is an explicit input (defaulting to
    `METRIC_DIRECTION`), not a closed-over assumption -- so a future metric
    where a higher value is the win can be exercised in a test by passing a
    table where that key maps to `False`, without touching module state or
    every other caller.
    """
    if delta is None:
        return None
    lower_is_better = (METRIC_DIRECTION if table is None else table)[metric_key]
    if delta == 0:
        return "unchanged"
    improved = (delta < 0) if lower_is_better else (delta > 0)
    return "better" if improved else "worse"


def _metric_phrase(metric_key: str, pct: float | None) -> str:
    """Plain words for one metric's change, percentage beside the word that
    gives it meaning -- never a bare signed number."""
    words = _METRIC_WORDS[metric_key]
    if pct is None:
        return f"no comparable {words['noun']} recorded"
    if pct == 0:
        return f"the same {words['noun']}"
    word = words["more"] if pct > 0 else words["less"]
    return f"{abs(pct):.1f}% {word} {words['noun']}"


def _project_summary(runs: list[dict], baseline: dict | None) -> dict:
    """The plain-language answer for one project's section (D1): how the
    latest run compares to baseline on the three cost-predicting metrics, or
    -- per D2 -- a stated LIMIT and no comparison when the ledger does not
    actually prove one. `runs` must already be ordered ascending by
    timestamp and carry the `is_baseline`/`deltas` fields `_run_view`
    computes -- this reuses that join instead of re-deriving it, so there is
    exactly one place deciding whether a baseline applies to a run.

    Never raises, never fabricates a comparison.
    """
    if len(runs) < 2:
        limit = "only one run has been recorded for this project -- nothing to compare it to yet"
        return {"limit": limit, "comparison": None, "metrics": [], "text": f"{limit.capitalize()}. {_WHY_SENTENCE}"}

    if not baseline or not baseline.get("wf_id"):
        limit = "no baseline is marked for this project -- run `rein baseline mark` after a change to compare future runs against it"
        return {"limit": limit, "comparison": None, "metrics": [], "text": f"{limit.capitalize()}. {_WHY_SENTENCE}"}

    latest = runs[-1]
    deltas = latest.get("deltas")
    if deltas is None:
        # Covers every reason `_run_view` can decline to compute a delta for
        # the latest run: baseline predates project tracking, belongs to a
        # different project, is the latest run itself, or is not older than
        # it -- any of those means there is no later run to hold the
        # baseline up against.
        limit = (
            "the marked baseline does not apply to the latest run shown here -- "
            "it predates project tracking, belongs to a different project, or is "
            "not older than this run -- so no comparison is shown"
        )
        return {"limit": limit, "comparison": None, "metrics": [], "text": f"{limit.capitalize()}. {_WHY_SENTENCE}"}

    metrics = [
        {"key": key, "pct": deltas.get(key), "direction": direction(key, deltas.get(key)), "phrase": _metric_phrase(key, deltas.get(key))}
        for key in ("turns_per_agent", "ctx_max", "opus_share")
    ]
    phrases = [m["phrase"] if m["direction"] is None else f"{m['phrase']} ({m['direction']})" for m in metrics]
    comparison = "Since the baseline, this run took " + ", ".join(phrases[:-1]) + f", and {phrases[-1]}."
    return {"limit": None, "comparison": comparison, "metrics": metrics, "text": f"{comparison} {_WHY_SENTENCE}"}


def build_view(ledger_path: str = LEDGER_PATH, baseline_path: str = BASELINE_PATH) -> dict:
    """Runs grouped by project, baseline marked, later-same-project runs deltad.

    Never raises: a missing, empty, or unreadable ledger yields
    `{"message": "...", "projects": []}` instead.
    """
    rows, ledger_error = _read_ledger_safe(ledger_path)
    if not rows:
        message = ledger_error or f"no runs recorded yet ({ledger_path})"
        return {"message": message, "projects": []}

    baseline = _read_baseline_safe(baseline_path)

    by_project: dict[str, list[dict]] = {}
    for row in rows:
        by_project.setdefault(row.get("project") or "(unknown)", []).append(row)

    projects = []
    for project, project_rows in sorted(by_project.items()):
        ordered = sorted(project_rows, key=lambda r: r.get("ts", ""))
        root = _unmangle_project(project)
        runs = [_run_view(r, project, baseline) for r in ordered]
        projects.append(
            {
                "project": project,
                "root": root,
                "models": _resolve_models(root),
                "runs": runs,
                "summary": _project_summary(runs, baseline),
                "baseline_info": _baseline_identity_text(baseline, project),
            }
        )

    return {"message": "", "projects": projects}


# ---------------------------------------------------------- per-agent models --

# T003: each project's `models.aux/impl/review` are shown resolved (config or
# default) and can be edited from the page. Two problems this section solves:
#
# 1. The ledger only ever records the OS-mangled project directory name (the
#    same `~/.claude/projects/<mangled>` scheme Claude Code itself uses --
#    every `os.sep` folded into `-`). That mangling is ambiguous to reverse
#    naively (folder names routinely contain `-` themselves, this repo's own
#    checkout included), so `_unmangle_project` walks the real filesystem tree
#    and only ever returns a path that is confirmed to exist and to mangle
#    back to exactly the string it was given -- never a guess.
# 2. A write must never land outside a root the ledger already vouches for
#    (see `_validate_root`): the known-roots set is derived from the current
#    view, not trusted from the request.

CONFIG_NAME = "flow.config.json"
DEFAULT_MODELS = {"aux": "haiku", "impl": "sonnet", "review": "opus"}


def _mangle_path(path: str) -> str:
    """Forward direction of Claude Code's `~/.claude/projects/<name>` naming:
    the real absolute path with the OS separator folded into `-`."""
    return os.path.realpath(path).replace(os.sep, "-")


def _walk_for_dir(current: str, tokens: list[str]) -> str | None:
    """Consume `tokens` (dash-split remainder of a mangled name) against real
    directory entries under `current`, longest match first. Every step is
    confirmed with `os.listdir`/`os.path.isdir`, so this can never return a
    path that does not exist -- but the mangling is genuinely ambiguous (a
    directory name can itself contain `-`), so on a name that mangles more
    than one way this returns the first filesystem-verified candidate,
    longest-segment-first -- not necessarily the one that actually produced
    the mangled name. Only fails to find any match at all returns `None`."""
    if not tokens:
        return current
    try:
        entries = set(os.listdir(current))
    except OSError:
        return None
    for n in range(len(tokens), 0, -1):
        name = "-".join(tokens[:n])
        if name in entries and os.path.isdir(os.path.join(current, name)):
            found = _walk_for_dir(os.path.join(current, name), tokens[n:])
            if found is not None:
                return found
    return None


def _unmangle_project(project: str) -> str | None:
    """Best-effort, filesystem-verified reversal of `_mangle_path`. `None` if
    nothing on disk actually mangles back to this exact string (the project
    moved, the disk isn't the one that recorded it, etc.) -- callers must
    treat that as "no root to resolve config from", never guess one. Note
    this is still a guess among filesystem-verified candidates when the
    mangled name is itself ambiguous -- see `_walk_for_dir`."""
    if not project.startswith("-"):
        return None
    return _walk_for_dir(os.sep, project[1:].split("-"))


def _read_existing_text(root: str) -> str:
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _resolve_models(root: str | None) -> dict[str, dict[str, str]]:
    """`{"aux"/"impl"/"review": {"value": ..., "source": "config"|"default"|"unknown"}}`.
    A missing or corrupt config degrades to every slot reading as default,
    never a crash. No resolvable root at all degrades to "unknown" instead --
    the built-in default value is shown, but it must not be reported with the
    same authority as a value actually read from (or absent from) a real
    config file, since there is no config file this session can even name."""
    cfg_models: dict = {}
    if root:
        try:
            parsed = json.loads(_read_existing_text(root) or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("models"), dict):
            cfg_models = parsed["models"]
    if root is None:
        # No root was resolved at all -- these values are not "the config's
        # default", they are simply unverifiable, so say so rather than
        # implying the same authority a resolved default would have.
        return {key: {"value": DEFAULT_MODELS[key], "source": "unknown"} for key in ("aux", "impl", "review")}
    return {
        key: {
            "value": cfg_models.get(key, DEFAULT_MODELS[key]),
            "source": "config" if key in cfg_models else "default",
        }
        for key in ("aux", "impl", "review")
    }


def _apply_models(existing_text: str, models: dict[str, str]) -> str:
    """The new raw text of the config with only `models.aux/impl/review` set
    from `models` -- every other key, and every unknown key already inside
    `models` (an existing `$comment` included), is carried through untouched.

    Note: every key/value survives byte-for-byte, but the *serialization* is
    always re-emitted with `indent=2` -- a config authored with different
    indentation (or key order Python's dict doesn't preserve from disk in
    some other way) will show a whole-file reformat in the diff, not just the
    `models` hunk. Content is preserved; formatting is not."""
    cfg = json.loads(existing_text) if existing_text.strip() else {}
    if not isinstance(cfg, dict):
        raise ValueError("flow.config.json does not contain a JSON object")
    old_models = cfg.get("models")
    new_models = dict(old_models) if isinstance(old_models, dict) else {}
    new_models.update({k: models[k] for k in ("aux", "impl", "review") if k in models})
    cfg = {**cfg, "models": new_models}
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


class ModelsWriteError(Exception):
    """Refused before touching disk -- callers map this to a non-2xx response."""


def _validate_root(root: str, known_roots: set[str]) -> str:
    if not root:
        raise ModelsWriteError("no project root given")
    real = os.path.realpath(root)
    if real not in known_roots:
        raise ModelsWriteError(f"{root!r} is not a project root already present in the ledger")
    return real


# A `confirmed=True` flag supplied by the *caller* is not evidence that a
# preview ever happened -- the server kept no state linking the two, so one
# request with `confirm: true` could write with nothing ever having been
# shown. `_PreviewTokens` is that missing state: a token is
# `sha256(real_root + normalized models + the file's current text)`, minted
# the moment a preview is computed and required -- present, unexpired, and
# still matching the file's *current* text -- before any confirmed write is
# allowed. Recomputing the hash from the live file at confirm time is what
# catches "the file changed underneath": if it did, the recomputed value
# differs from the token that was actually minted, so the confirm is refused
# as stale rather than trusted.
_PREVIEW_TTL_SECONDS = 300  # a preview is only good for 5 minutes


class _PreviewTokens:
    def __init__(self) -> None:
        self._minted_at: dict[str, float] = {}

    @staticmethod
    def _compute(real_root: str, models: dict[str, str], old_text: str) -> str:
        normalized = json.dumps({k: models.get(k, "") for k in ("aux", "impl", "review")}, sort_keys=True)
        digest_input = "\x1f".join([real_root, normalized, old_text]).encode("utf-8")
        return hashlib.sha256(digest_input).hexdigest()

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [t for t, minted in self._minted_at.items() if now - minted > _PREVIEW_TTL_SECONDS]
        for t in stale:
            del self._minted_at[t]

    def mint(self, real_root: str, models: dict[str, str], old_text: str) -> str:
        self._prune()
        token = self._compute(real_root, models, old_text)
        self._minted_at[token] = time.monotonic()
        return token

    def consume(self, token: str | None, real_root: str, models: dict[str, str], old_text_now: str) -> bool:
        """Single-use: true only for a token that is present, unexpired, and
        whose hash still matches the file's current text -- false (and left
        alone, so a mere typo doesn't burn a good preview) for anything
        absent, unknown, forged, expired, or stale."""
        self._prune()
        if not token or token not in self._minted_at:
            return False
        if token != self._compute(real_root, models, old_text_now):
            return False
        del self._minted_at[token]
        return True


_preview_tokens = _PreviewTokens()


def write_models(
    root: str,
    models: dict[str, str],
    known_roots: set[str],
    confirmed: bool = False,
    token: str | None = None,
) -> dict:
    """Compute the unified diff of writing `models` into `root`'s
    `flow.config.json` (creating it if absent), returned as
    `{"diff": str, "token": str | None, "root": str}`. Only writes to disk
    when `confirmed` is True *and* `token` is exactly what an immediately
    preceding `confirmed=False` call for this same root/models/file-state
    minted (D3): a first call always just previews and mints a fresh token;
    nothing changes on disk until a second, confirming call presents it back.

    Raises `ModelsWriteError` -- before any read or write -- if `root` is not
    one of `known_roots` (a path a run in the ledger actually resolved to),
    or if `confirmed` is True but `token` is absent, unknown, forged, expired,
    or stale because the file changed since the preview it was minted for.
    Raises `ValueError` if `models` is not an object, does not carry all three
    slots, or any slot is not a non-empty string -- the page always edits the
    resolved triple as one unit, never a partial slice of it, and never writes
    a value that isn't the plain model name the CLI expects to dispatch on.
    """
    real_root = _validate_root(root, known_roots)
    if not isinstance(models, dict):
        raise ValueError("models must be an object with aux/impl/review")
    missing = [k for k in ("aux", "impl", "review") if k not in models]
    if missing:
        raise ValueError(f"models.{'/'.join(missing)} missing -- all three of aux/impl/review are required")
    bad = [k for k in ("aux", "impl", "review") if not isinstance(models[k], str) or not models[k]]
    if bad:
        raise ValueError(f"models.{'/'.join(bad)} must be a non-empty string")
    path = os.path.join(real_root, CONFIG_NAME)
    old_text = _read_existing_text(real_root)
    new_text = _apply_models(old_text, models)
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    if confirmed:
        if not _preview_tokens.consume(token, real_root, models, old_text):
            raise ModelsWriteError(
                "no matching preview found for this write -- preview it again before confirming"
            )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        return {"diff": diff, "token": None, "root": real_root}
    new_token = _preview_tokens.mint(real_root, models, old_text)
    return {"diff": diff, "token": new_token, "root": real_root}


# -------------------------------------------------------------------- html --

# The page is entirely self-contained (D2): one inline <style>, no <script>,
# no external stylesheet/font/image, no fetch/XHR. Per-agent rows stay
# reachable without leaving the page via native <details>/<summary> --
# that needs zero JavaScript.

_PAGE_CSS = """
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:2rem;color:#1a1a1a;background:#fff}
h1{font-size:1.4rem}
h2{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{padding:.35rem .6rem;text-align:right;border-bottom:1px solid #eee;vertical-align:top}
th:first-child,td:first-child{text-align:left}
tr.baseline{background:#fffbe6}
.badge{display:inline-block;background:#f5a623;color:#1a1a1a;font-size:.65rem;font-weight:700;padding:.1rem .35rem;border-radius:.2rem}
.delta-better{color:#0a7a2f;font-weight:600}
.delta-worse{color:#b3261e;font-weight:600}
.delta-same,.delta-na{color:#888}
details{text-align:left}
summary{cursor:pointer;color:#555}
table.agents{margin:.4rem 0 0;font-size:.85em}
.empty{color:#666;font-style:italic;padding:1rem 0}
form.models{display:flex;gap:.6rem;align-items:flex-end;margin:.5rem 0 1rem;flex-wrap:wrap}
form.models label{display:flex;flex-direction:column;font-size:.75rem;color:#555;gap:.15rem}
form.models input[type=text]{font:inherit;padding:.2rem .4rem;border:1px solid #ccc;border-radius:.2rem;width:8rem}
form.models button{font:inherit;padding:.3rem .7rem;border:1px solid #888;border-radius:.25rem;background:#f5f5f5;cursor:pointer}
pre.diff{background:#f7f7f7;border:1px solid #ddd;border-radius:.3rem;padding:.75rem;overflow-x:auto;white-space:pre-wrap}
.notice{padding:.5rem;border-radius:.3rem;margin:.5rem 0}
.notice.error{background:#fdecea;color:#611a15}
.notice.ok{background:#e9f7ee;color:#0a4a20}
p.baseline-info{color:#555;font-size:.9em;margin:.25rem 0 .75rem}
th small{font-weight:400;color:#777;display:block}
.glossary-toggle{position:relative;margin-left:.3rem;font:inherit;font-weight:700;font-size:.75em;
  width:1.2em;height:1.2em;line-height:1;border-radius:50%;border:1px solid #888;background:#f5f5f5;
  color:#555;cursor:pointer;padding:0;vertical-align:middle}
.glossary-toggle .glossary-text{position:absolute;right:0;top:120%;z-index:5;display:none;width:16rem;
  max-width:70vw;text-align:left;font-weight:400;font-size:1.1em;background:#fff;color:#1a1a1a;
  border:1px solid #ccc;border-radius:.3rem;padding:.5rem;box-shadow:0 2px 6px rgba(0,0,0,.15);white-space:normal}
.glossary-toggle:hover .glossary-text,.glossary-toggle:focus .glossary-text{display:block}
.glossary-toggle:focus{outline:2px solid #1a73e8}
"""


def _glossary_html(key: str) -> str:
    """The '?' affordance for one metric header (D3/D4): text pulled from
    the single `GLOSSARY` (raises `KeyError` naming `key` if it is missing
    an entry, rather than rendering a header that explains nothing), on a
    native `<button>` so it is reachable by keyboard focus with no
    JavaScript (D5) -- `:focus` alone reveals `.glossary-text` in place, the
    same CSS rule that handles `:hover`. The explanation is also wired in as
    an accessible description via `aria-describedby`, not a hover-only
    `title` attribute, so it reaches people who use a screen reader as
    reliably as people who use a mouse.
    """
    entry = GLOSSARY[key]
    desc_id = f"glossary-{key}"
    text = html.escape(f"{entry['meaning']} {entry['why']}")
    return (
        f'<button type="button" class="glossary-toggle" aria-describedby="{desc_id}" aria-label="What does this mean?">?'
        f'<span id="{desc_id}" class="glossary-text" role="tooltip">{text}</span>'
        "</button>"
    )


def _fmt_num(value, digits: int = 1) -> str:
    """Render a number for the page; `0` (not a crash) for anything missing or
    non-numeric -- the view model already defaults missing fields to 0/"" so
    this is a display-only safety net, not the source of truth."""
    if value is None:
        return "0"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _delta_cell(pct: float | None) -> str:
    """Word the sign explicitly -- for these metrics lower is always better,
    so a bare signed number would not read unambiguously as an improvement.
    The word ("better"/"worse"/"unchanged") is in the visible text itself, not
    just a CSS class, so it survives even plain-text extraction of the page."""
    if pct is None:
        return '<span class="delta-na">n/a</span>'
    if pct < 0:
        cls, word = "delta-better", "better"
    elif pct > 0:
        cls, word = "delta-worse", "worse"
    else:
        cls, word = "delta-same", "unchanged"
    return f'<span class="{cls}">{pct:+.1f}% ({word})</span>'


def _agents_detail(agents: list[dict]) -> str:
    """Per-agent rows, reachable without leaving the page (native disclosure,
    no JS). An empty `agents` list still renders -- never an empty <table>
    that looks broken."""
    if not agents:
        return "<details><summary>0 agents</summary><p>no agents recorded for this run</p></details>"
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(a.get("file", ""))),
            html.escape(str(a.get("model", ""))),
            _fmt_num(a.get("turns"), 0),
            _fmt_num(a.get("total"), 0),
            _fmt_num(a.get("ctx_max"), 0),
        )
        for a in agents
    )
    return (
        f"<details><summary>{len(agents)} agent(s)</summary>"
        '<table class="agents"><thead><tr><th>file</th><th>model</th>'
        f"<th>turns{_glossary_html('turns')}</th>"
        f"<th>total{_glossary_html('total')}</th>"
        f"<th>ctx_max{_glossary_html('ctx_max')}</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></details>"
    )


def _run_row(run: dict, show_deltas: bool) -> str:
    row_class = ' class="baseline"' if run["is_baseline"] else ""
    badge = ' <span class="badge">BASELINE</span>' if run["is_baseline"] else ""
    cells = (
        f"<td>{html.escape(str(run.get('ts', '')))}<br><small>{html.escape(str(run.get('wf_id', '')))}</small>{badge}</td>"
        f"<td>{_fmt_num(run.get('turns_per_agent'))}</td>"
        f"<td>{_fmt_num(run.get('ctx_max'), 0)}</td>"
        f"<td>{_fmt_num(run.get('opus_share'))}%</td>"
    )
    if show_deltas:
        deltas = run.get("deltas")
        cells += "".join(
            f"<td>{_delta_cell(deltas[key]) if deltas else '&mdash;'}</td>"
            for key in ("turns_per_agent", "ctx_max", "opus_share")
        )
    cells += f"<td>{_agents_detail(run.get('agents') or [])}</td>"
    return f"<tr{row_class}>{cells}</tr>"


def _models_summary(models: dict) -> str:
    """Read-only per-agent models line: value and source (config or default)
    for each of aux/impl/review, so editing them can start from what's true."""
    parts = []
    for key in ("aux", "impl", "review"):
        info = models.get(key) or {}
        value = html.escape(str(info.get("value", "")))
        source = html.escape(str(info.get("source", "")))
        parts.append(f"{key}=<b>{value}</b> <small>({source})</small>")
    return '<p class="models">' + " &middot; ".join(parts) + "</p>"


def _models_form(root: str | None, models: dict) -> str:
    """A plain `<form method="post" action="/models">` per project (T003):
    zero JavaScript, so D2 is untouched -- a form POST is a navigation, not a
    fetch. Submitting it always previews first (D3): the response is an HTML
    page with the diff and a second form carrying the server-minted token
    that alone can confirm the write. `root` is `None` when the project could
    not be resolved to a real directory on this disk -- there is nothing to
    write to, so no form is offered."""
    if not root:
        return '<p class="notice error">no resolvable project root -- editing is unavailable here</p>'
    fields = "".join(
        f'<label>{key}<input type="text" name="{key}" value="{html.escape(str((models.get(key) or {}).get("value", "")))}"></label>'
        for key in ("aux", "impl", "review")
    )
    return (
        f'<form class="models" method="post" action="/models">'
        f'<input type="hidden" name="root" value="{html.escape(root)}">'
        f"{fields}"
        '<button type="submit">Preview change</button>'
        "</form>"
    )


def _project_section(project: dict) -> str:
    runs = project["runs"]
    # Deltas (and therefore the whole "savings" column group) only appear for a
    # project that actually has a baseline in it -- with none, this stays a
    # plain trend table, protecting D4 (no baseline -> no savings figure).
    show_deltas = any(r["is_baseline"] or r["deltas"] is not None for r in runs)

    header = (
        "<tr><th>run</th>"
        f"<th>turns/agent{_glossary_html('turns_per_agent')}</th>"
        f"<th>ctx_max/turn{_glossary_html('ctx_max')}</th>"
        f"<th>opus share{_glossary_html('opus_share')}</th>"
    )
    if show_deltas:
        # "negative = better" sits inside the same header cell as the delta
        # column it labels -- the same section as the delta values it
        # describes, not merely somewhere else on the page (T002 #3).
        header += (
            f"<th>Δ turns/agent <small>(negative = better)</small>{_glossary_html('delta_turns_per_agent')}</th>"
            f"<th>Δ ctx_max <small>(negative = better)</small>{_glossary_html('delta_ctx_max')}</th>"
            f"<th>Δ opus share <small>(negative = better)</small>{_glossary_html('delta_opus_share')}</th>"
        )
    header += "<th>agents</th></tr>"

    body = "".join(_run_row(r, show_deltas) for r in runs)
    models = project.get("models") or {}
    models_html = _models_summary(models) + _models_form(project.get("root"), models)
    summary_text = (project.get("summary") or {}).get("text", "")
    summary_html = f'<p class="summary">{html.escape(summary_text)}</p>'
    baseline_html = f'<p class="baseline-info">{html.escape(project.get("baseline_info", ""))}</p>'
    return (
        f"<section><h2>{html.escape(project['project'])}</h2>"
        f"{summary_html}"
        f"{models_html}"
        f"{baseline_html}"
        f"<table><thead>{header}</thead><tbody>{body}</tbody></table></section>"
    )


def render_html(view: dict) -> str:
    """The whole page: one self-contained HTML document, numbers embedded
    server-side (D2) -- there is nothing here for a browser to fetch."""
    if not view.get("projects"):
        message = view.get("message") or "no runs recorded yet"
        body = f'<p class="empty">{html.escape(message)}</p>'
    else:
        body = "".join(_project_section(p) for p in view["projects"])

    return _page(body)


# ---------------------------------------------------------------------- serve --

_MAX_BODY_BYTES = 65536  # 64 KiB -- generous for a models POST, not an
# invitation to buffer an arbitrary-length body into memory.


def _page(body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>rein dashboard</title>"
        f"<style>{_PAGE_CSS}</style></head>"
        f"<body><h1>rein dashboard</h1>{body}</body></html>"
    )


def _render_models_error_page(message: str) -> str:
    return _page(
        f'<p class="notice error">{html.escape(message)}</p>'
        '<p><a href="/">back to dashboard</a></p>'
    )


def _render_models_result_page(root: str, models: dict[str, str], result: dict, confirmed: bool) -> str:
    """The HTML response to a form POST (finding 5): the diff, plus -- when
    still unconfirmed -- a second form carrying the server-minted token that
    alone can confirm the write (no JavaScript involved; the browser's own
    navigation *is* the second request)."""
    diff_html = f'<pre class="diff">{html.escape(result["diff"]) or "(no change)"}</pre>'
    if confirmed:
        body = f'<p class="notice ok">written to {html.escape(root)}/flow.config.json</p>{diff_html}<p><a href="/">back to dashboard</a></p>'
        return _page(body)
    hidden_models = "".join(
        f'<input type="hidden" name="{key}" value="{html.escape(str(models.get(key, "")))}">'
        for key in ("aux", "impl", "review")
    )
    body = (
        diff_html
        + '<form class="models" method="post" action="/models">'
        + f'<input type="hidden" name="root" value="{html.escape(root)}">'
        + hidden_models
        + '<input type="hidden" name="confirm" value="true">'
        + f'<input type="hidden" name="token" value="{html.escape(result["token"] or "")}">'
        + '<button type="submit">Confirm write</button>'
        + "</form>"
        + '<p><a href="/">cancel, back to dashboard</a></p>'
    )
    return _page(body)


def _make_handler(view: dict, port: int) -> type[http.server.BaseHTTPRequestHandler]:
    # Deliberately built once, here, from the ledger snapshot `main()` read at
    # startup -- not re-read per GET. A run that finishes while the page is
    # open (or is open in another tab) will not appear until the process is
    # restarted; only a write this same server performs patches `state["body"]`
    # in place (`_refresh_after_write`, below). Out of scope per the plan: the
    # ledger itself is written once, at run end, so this is a "restart to see
    # a new run" gap, not the live-streaming-during-a-run the plan excludes --
    # narrowing that gap would mean re-reading the ledger/baseline files (and
    # recomputing `known_roots`) on every GET instead of caching `view` here.
    state: dict[str, bytes] = {"body": render_html(view).encode("utf-8")}
    # Derived from the view actually built from the ledger -- never from the
    # request -- so a write can only ever target a root a real run resolved to.
    known_roots = {p["root"] for p in view.get("projects", []) if p.get("root")}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _refresh_after_write(real_root: str) -> None:
        # T003/T002 coherence: a write this server just performed must show up
        # on the very next GET, not only after a restart -- re-resolve just
        # the affected project's models and re-render (still fully embedded
        # server-side, D2 -- the page still never fetches).
        for p in view.get("projects", []):
            if p.get("root") == real_root:
                p["models"] = _resolve_models(real_root)
        state["body"] = render_html(view).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            if self.path != "/":
                self._json(404, {"error": "not found"})
                return
            body = state["body"]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _is_cross_origin(self) -> bool:
            """True if this request did not plausibly come from this
            server's own page -- refused before any filesystem access.
            Binding to 127.0.0.1 keeps a remote attacker out, but not a
            foreign page already open in the user's own browser: that page
            can still aim a POST at this origin, so the state-changing
            request itself must check where it came from."""
            host = self.headers.get("Host", "")
            if host not in allowed_hosts:
                return True
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                return True
            sec_fetch_site = self.headers.get("Sec-Fetch-Site")
            if sec_fetch_site is not None and sec_fetch_site != "same-origin":
                return True
            return False

        def do_POST(self) -> None:  # noqa: N802 (stdlib method name)
            """POST /models -- JSON body: {"root", "models": {aux/impl/review},
            "confirm", "token"}, or a form body (the page's own <form>, same
            fields flat). A first call (no/false `confirm`) only returns the
            diff plus a freshly minted token (D3); a second call must present
            that exact token together with `confirm: true` to perform the
            write -- see `write_models` for what makes a token valid."""
            if self.path != "/models":
                self._json(404, {"error": "not found"})
                return
            if self._is_cross_origin():
                self._json(403, {"error": "cross-origin or foreign-host request refused"})
                return

            content_type = self.headers.get("Content-Type", "")
            mime = content_type.split(";", 1)[0].strip().lower()
            is_form = mime == "application/x-www-form-urlencoded"
            if mime not in ("application/json", "application/x-www-form-urlencoded"):
                self._json(415, {"error": f"unsupported Content-Type {content_type!r}"})
                return

            length_header = self.headers.get("Content-Length")
            try:
                length = int(length_header) if length_header else 0
            except ValueError:
                self._json(400, {"error": "invalid Content-Length"})
                return
            if length < 0 or length > _MAX_BODY_BYTES:
                self._json(400, {"error": "request body missing or too large"})
                return
            raw = self.rfile.read(length) if length else b""

            if is_form:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._json(400, {"error": "invalid form body"})
                    return
                flat = {k: v[0] for k, v in urllib.parse.parse_qs(text).items()}
                root = flat.get("root") or ""
                models = {k: flat[k] for k in ("aux", "impl", "review") if k in flat}
                confirmed = flat.get("confirm") in ("1", "true", "True")
                token = flat.get("token")
            else:
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._json(400, {"error": "invalid JSON body"})
                    return
                if not isinstance(payload, dict):
                    self._json(400, {"error": "JSON body must be an object"})
                    return
                root = payload.get("root") or ""
                models = payload.get("models") or {}
                confirmed = bool(payload.get("confirm"))
                token = payload.get("token")

            try:
                result = write_models(root, models, known_roots, confirmed=confirmed, token=token)
            except ModelsWriteError as exc:
                self._fail(is_form, 403, str(exc))
                return
            except ValueError as exc:
                self._fail(is_form, 400, str(exc))
                return

            if confirmed:
                _refresh_after_write(result["root"])

            if is_form:
                self._html(200, _render_models_result_page(root, models, result, confirmed))
            else:
                self._json(200, {"diff": result["diff"], "token": result["token"], "written": confirmed})

        def _fail(self, is_form: bool, status: int, message: str) -> None:
            if is_form:
                self._html(status, _render_models_error_page(message))
            else:
                self._json(status, {"error": message})

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: int, page: str) -> None:
            data = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # keep test/CLI output quiet -- this is not an access log

    return Handler


def make_server(view: dict, port: int) -> http.server.HTTPServer:
    """Bound to 127.0.0.1 only -- never 0.0.0.0. This is local-only by design."""
    return http.server.HTTPServer(("127.0.0.1", port), _make_handler(view, port))


def serve(view: dict, port: int) -> None:
    httpd = make_server(view, port)
    try:
        print(f"rein dashboard: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# ----------------------------------------------------------------------- cli --


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rein dashboard", description=__doc__)
    p.add_argument("--json", action="store_true", dest="as_json", help="print the view model and exit (no server)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to bind on 127.0.0.1 (default: {DEFAULT_PORT})")
    p.add_argument("--ledger", default=LEDGER_PATH, help=f"ledger path (default: {LEDGER_PATH})")
    p.add_argument("--baseline", default=BASELINE_PATH, help=f"baseline path (default: {BASELINE_PATH})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    view = build_view(args.ledger, args.baseline)

    if args.as_json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0

    serve(view, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
