#!/usr/bin/env python3
"""Real token accounting for Claude Code agent runs, broken down PER MODEL.

Why this exists (measured, not assumed): the spend of a multi-agent workflow is
~90% `cache_read` -- every turn re-reads the whole accumulated context, so
cost ~= turns x context-size. Output tokens are ~0.3%, which is why "make the
model write less" optimizations do nothing. The workflow runtime's own
`budget.spent()` counts ONLY output, so it cannot be used to optimize.

This tool reads the `usage` field of Claude Code's JSONL transcripts (which
includes `cache_read_input_tokens`) and shows where the tokens actually go.

On a subscription plan the number that matters is not the grand total but the
tokens spent on OPUS -- that is the scarce quota you hit first. Hence the
per-model breakdown.

Usage:
    rein token-report                    # most recent workflow run
    rein token-report <dir|file.jsonl>   # a specific run
    rein token-report --json             # machine-readable
    rein token-report --record           # also append to the ledger
    rein token-report --no-record        # never touch the ledger
    rein token-report --record --change measure-itself  # label the row (D2)

The ledger (~/.claude/rein/runs.jsonl) is what makes cross-project, cross-session
history possible: raw transcripts get rotated away, a summarized record does not.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
from collections import defaultdict

PROJECTS = os.path.expanduser("~/.claude/projects")
LEDGER_DIR = os.path.expanduser("~/.claude/rein")
LEDGER_PATH = os.path.join(LEDGER_DIR, "runs.jsonl")
BASELINE_PATH = os.path.join(LEDGER_DIR, "baseline.json")

# Metrics that actually predict cost. Anything else is decoration.
# - turns_per_agent : the runaway signal (a 200-turn agent is the whole bill)
# - ctx_max         : how big the re-read context got, per turn
# - opus_share      : the scarce quota on a subscription plan
KEY_METRICS = ("turns_per_agent", "ctx_max", "opus_share")


# ---------------------------------------------------------------- discovery --


def latest_workflow_dir() -> str | None:
    """Most recent `wf_*` dir across ALL projects.

    A run can be launched from any session or window (including a worktree), so
    the default is not scoped to the current project.
    """
    dirs = glob.glob(os.path.join(PROJECTS, "*", "*", "subagents", "workflows", "wf_*"))
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def _iter_files(target: str) -> list[str]:
    if os.path.isfile(target):
        return [target]
    return sorted(
        glob.glob(os.path.join(target, "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )


def _provenance(target: str) -> dict[str, str]:
    """Pull project / session / workflow id out of the transcript path.

    Layout: ~/.claude/projects/<project>/<session>/subagents/workflows/<wf_id>/
    Anything that does not match degrades to empty strings rather than failing --
    the report still works on an arbitrary folder of transcripts.
    """
    real = os.path.realpath(target)
    parts = real.split(os.sep)
    out = {"project": "", "session": "", "wf_id": "", "path": real}
    if "workflows" in parts:
        i = parts.index("workflows")
        if i + 1 < len(parts) and parts[i + 1].startswith("wf_"):
            out["wf_id"] = parts[i + 1]
        # <project>/<session>/subagents/workflows
        if i >= 3:
            out["session"] = parts[i - 2]
            out["project"] = parts[i - 3]
    return out


def _short_model(model: str) -> str:
    """'claude-opus-5' -> 'opus-5'; 'claude-haiku-4-5-20251001' -> 'haiku-4-5'."""
    m = model.replace("claude-", "")
    kept: list[str] = []
    for part in m.split("-"):
        # Drop the trailing date stamp so families group together.
        if len(part) >= 8 and part.isdigit():
            break
        kept.append(part)
    return "-".join(kept) or model


# ------------------------------------------------------------------ analysis --


def _parse_iso_ts(raw) -> datetime.datetime | None:
    """Parse a transcript record's `timestamp` field (e.g.
    '2026-07-31T00:00:00.037Z') into an aware `datetime`, or `None` if it is
    missing or not a valid ISO-8601 string (D2: unparseable is absent, never
    a crash and never a fabricated time)."""
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def analyze(path: str) -> dict:
    """Token accounting for a single transcript (one agent)."""
    turns = 0
    totals: dict[str, int] = defaultdict(int)
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ctx_max = 0
    # First and last record IN FILE ORDER that carry a parseable `timestamp`
    # -- deliberately not min()/max(), so a transcript whose timestamps are
    # genuinely out of order (last-seen earlier than first-seen) surfaces as
    # exactly that, rather than being silently corrected into a valid span.
    start_ts: datetime.datetime | None = None
    end_ts: datetime.datetime | None = None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso_ts(rec.get("timestamp"))
            if ts is not None:
                if start_ts is None:
                    start_ts = ts
                end_ts = ts
            msg = rec.get("message") or {}
            usage = msg.get("usage") or rec.get("usage")
            if not usage:
                continue
            turns += 1
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            fresh_in = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)

            totals["cache_read"] += cache_read
            totals["cache_write"] += cache_write
            totals["input"] += fresh_in
            totals["output"] += out
            # Context actually carried into this turn (everything but the output).
            ctx_max = max(ctx_max, cache_read + cache_write + fresh_in)

            model = _short_model(msg.get("model") or "unknown")
            by_model[model]["turns"] += 1
            by_model[model]["total"] += cache_read + cache_write + fresh_in + out
            by_model[model]["cache_read"] += cache_read

    # An agent can technically switch models mid-run; attribute it to the model
    # that did most of its turns.
    dominant = max(by_model.items(), key=lambda kv: kv[1]["turns"])[0] if by_model else "unknown"

    return {
        "file": os.path.basename(path),
        "turns": turns,
        "totals": dict(totals),
        "total": sum(totals.values()),
        "ctx_max": ctx_max,
        "model": dominant,
        "by_model": {m: dict(v) for m, v in by_model.items()},
        # ISO strings (not datetimes) so this dict stays JSON-safe; `summarize`
        # re-parses them. `None` when no line in this transcript carried a
        # parseable `timestamp` (D2).
        "start_ts": start_ts.isoformat() if start_ts is not None else None,
        "end_ts": end_ts.isoformat() if end_ts is not None else None,
    }


def summarize(target: str, limit: int | None = None) -> dict:
    """Aggregate every transcript under `target` into one run summary."""
    files = _iter_files(target)
    if limit:
        files = files[:limit]

    agents: list[dict] = []
    grand: dict[str, int] = defaultdict(int)
    grand_by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    turns = 0
    ctx_max = 0
    # D1/D2: overlap is measured from each agent's own transcript timestamps,
    # never claimed from a status flag. `agent_min` is a SUM of per-agent
    # spans, so one agent with no usable span (missing, unparseable, or
    # last-seen-before-first-seen timestamps) invalidates the whole run's
    # duration figures -- summing a fabricated 0 alongside real spans would
    # understate the total and poison the overlap ratio computed from it.
    agent_spans_min: list[float] = []
    run_start: datetime.datetime | None = None
    run_end: datetime.datetime | None = None
    spans_valid = True

    for path in files:
        a = analyze(path)
        if not a["turns"]:
            continue
        agents.append(
            {
                "file": a["file"],
                "model": a["model"],
                "turns": a["turns"],
                "total": a["total"],
                "cache_read": a["totals"].get("cache_read", 0),
                "ctx_max": a["ctx_max"],
            }
        )
        turns += a["turns"]
        ctx_max = max(ctx_max, a["ctx_max"])
        for key, val in a["totals"].items():
            grand[key] += val
        for model, mv in a["by_model"].items():
            for key, val in mv.items():
                grand_by_model[model][key] += val

        start_dt = _parse_iso_ts(a["start_ts"])
        end_dt = _parse_iso_ts(a["end_ts"])
        if start_dt is None or end_dt is None or end_dt < start_dt:
            spans_valid = False
        else:
            agent_spans_min.append((end_dt - start_dt).total_seconds() / 60.0)
            if run_start is None or start_dt < run_start:
                run_start = start_dt
            if run_end is None or end_dt > run_end:
                run_end = end_dt

    total = sum(grand.values())
    opus_total = sum(v["total"] for m, v in grand_by_model.items() if "opus" in m)

    # Sort loudest agent first: the runaway is what you are hunting.
    agents.sort(key=lambda a: -a["total"])

    prov = _provenance(target)
    summary = {
        "schema": 1,
        "project": prov["project"],
        "session": prov["session"],
        "wf_id": prov["wf_id"],
        "path": prov["path"],
        # File mtime, not wall clock: keeps the record stable if you re-analyze.
        "ts": _mtime_iso(target),
        "agents_counted": len(agents),
        "turns": turns,
        "totals": dict(grand),
        "total": total,
        "ctx_max": ctx_max,
        "turns_per_agent": round(turns / len(agents), 1) if agents else 0,
        "by_model": {m: dict(v) for m, v in grand_by_model.items()},
        "opus_tokens": opus_total,
        "opus_share": round(100 * opus_total / total, 2) if total else 0.0,
        "agents": agents,
    }

    # D2: absent is absent. Only set when EVERY counted agent yielded a real,
    # non-negative span, run_start/run_end were seen, and the wall clock is
    # actually positive -- otherwise these three keys are omitted entirely
    # rather than persisting a fabricated 0m/1.00x that would read as an
    # instant run and poison every average derived from the ledger.
    if spans_valid and agent_spans_min and run_start is not None and run_end is not None:
        wall_clock_min = (run_end - run_start).total_seconds() / 60.0
        if wall_clock_min > 0:
            agent_min = sum(agent_spans_min)
            summary["wall_clock_min"] = round(wall_clock_min, 2)
            summary["agent_min"] = round(agent_min, 2)
            summary["overlap"] = round(agent_min / wall_clock_min, 2)

    return summary


def _mtime_iso(target: str) -> str:
    try:
        ts = os.path.getmtime(target)
    except OSError:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------------- ledger --


def append_to_ledger(summary: dict, ledger_path: str = LEDGER_PATH) -> str:
    """Append (or replace) this run's record in the ledger.

    Keyed by `wf_id` so re-running the report does not duplicate a run. Runs with
    no `wf_id` (an ad-hoc folder of transcripts) are not recorded -- there is no
    stable identity to dedupe on.
    """
    wf_id = summary.get("wf_id")
    if not wf_id:
        return ""

    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)

    record = {k: v for k, v in summary.items() if k != "agents"}
    # Keep a trimmed agent list: enough to spot a runaway, small enough to keep
    # the ledger cheap to read for the whole history.
    record["agents"] = [
        {k: a[k] for k in ("file", "model", "turns", "total", "ctx_max")} for a in summary.get("agents", [])
    ]

    existing: list[dict] = []
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("wf_id") != wf_id:
                    existing.append(row)

    existing.append(record)
    tmp = ledger_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in existing:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, ledger_path)
    return ledger_path


def read_ledger(ledger_path: str = LEDGER_PATH) -> list[dict]:
    if not os.path.exists(ledger_path):
        return []
    rows = []
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ------------------------------------------------------------------ baseline --

# The metrics that matter, snapshotted at mark-time so `show` (and later, the
# delta in `rein ledger`) never has to re-open the ledger.
BASELINE_FIELDS = ("wf_id", "ts", "project", "turns", "turns_per_agent", "ctx_max", "opus_share")


def mark_baseline(wf_id: str | None, ledger_path: str = LEDGER_PATH, baseline_path: str = BASELINE_PATH) -> dict:
    """Snapshot one ledger row as the baseline. Leaves the ledger itself untouched.

    `wf_id=None` picks the most recent row (by `ts`). Raises `ValueError` naming
    the id if an explicit `wf_id` is not in the ledger -- never stores a dangling
    reference.
    """
    rows = read_ledger(ledger_path)
    if not rows:
        raise ValueError(f"ledger is empty ({ledger_path}) -- nothing to mark as baseline")

    if wf_id:
        match = next((r for r in rows if r.get("wf_id") == wf_id), None)
        if match is None:
            raise ValueError(f"no run {wf_id!r} in the ledger ({ledger_path})")
    else:
        match = max(rows, key=lambda r: r.get("ts", ""))

    record = {k: match.get(k) for k in BASELINE_FIELDS}

    os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
    tmp = baseline_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, baseline_path)
    return record


class BaselineCorruptError(Exception):
    """The baseline file exists but is not valid JSON.

    Deliberately distinct from "no baseline marked" (a missing file, returned as
    `None`) -- a user who did mark a baseline should be told their file is
    broken, not that nothing was ever marked.
    """


def read_baseline(baseline_path: str = BASELINE_PATH) -> dict | None:
    """Return the baseline record, `None` if none was ever marked.

    Raises `BaselineCorruptError` if the file exists but cannot be parsed --
    callers must not treat that the same as "no baseline".
    """
    if not os.path.exists(baseline_path):
        return None
    with open(baseline_path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise BaselineCorruptError(f"baseline file unreadable: {baseline_path} -- re-run `rein baseline mark`") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise BaselineCorruptError(
            f"baseline file has an unexpected shape ({type(data).__name__}): {baseline_path} -- re-run `rein baseline mark`"
        )
    return data


def clear_baseline(baseline_path: str = BASELINE_PATH) -> bool:
    """Remove the marker. Returns whether anything was actually removed."""
    if not os.path.exists(baseline_path):
        return False
    os.remove(baseline_path)
    return True


def _pct_change(value: float | None, base: float | None) -> float | None:
    """Signed % change of `value` relative to `base`. None if `base` or `value` is missing, or `base` is zero."""
    if base in (None, 0) or value is None:
        return None
    return round(100 * (value - base) / base, 1)


def _fmt_delta(pct: float | None, base: float | None = None, value: float | None = 0) -> str:
    """Label the direction explicitly -- for these metrics lower is always better,
    so a bare signed number is not enough to read at a glance.

    `base` is only used to explain a `None` pct: missing baseline data reads
    differently from a baseline metric that was legitimately zero. `value` is
    the row's own metric -- if the row never recorded it (schema drift), there
    is no real number to compare and reporting one would be fabricated.
    """
    if value is None:
        return "n/a (row missing metric)"
    if pct is None:
        if base == 0:
            return "n/a (baseline 0)"
        return "n/a"
    tag = "better" if pct < 0 else "worse" if pct > 0 else "same"
    return f"{pct:+.1f}% {tag}"


def render_ledger(rows: list[dict], ledger_path: str = LEDGER_PATH, baseline: dict | None = None) -> str:
    lines: list[str] = [f"{len(rows)} run(s) in {ledger_path}\n"]

    baseline_wf_id = baseline.get("wf_id") if baseline else None
    baseline_ts = baseline.get("ts") or "" if baseline else ""
    baseline_project = baseline.get("project") if baseline else None
    # A baseline snapshotted before `project` was tracked has no way to say which
    # project it belongs to. The ledger is GLOBAL (all projects in one file), so
    # without a project to key on we cannot tell whether a delta is a real
    # improvement or a comparison against a completely unrelated project's run --
    # suppress deltas entirely rather than guess.
    baseline_stale = bool(baseline) and not baseline_project

    by_project: dict[str, list[dict]] = {}
    for r in rows:
        by_project.setdefault(r.get("project") or "(unknown)", []).append(r)

    for project, runs in sorted(by_project.items()):
        runs = sorted(runs, key=lambda r: r.get("ts", ""))
        total = sum(r.get("total", 0) for r in runs)
        opus = sum(r.get("opus_tokens", 0) for r in runs)
        lines.append(f"{project}   {len(runs)} run(s)   total={total:,}   opus={opus:,} ({100*opus/total if total else 0:.1f}%)")

        window = runs[-5:]
        # The baseline row must stay visible even if it has aged out of the last
        # 5 -- otherwise every later row gets a "vs baseline" delta while the
        # `[baseline]` tag itself is nowhere on screen.
        baseline_in_window = any(r.get("wf_id") == baseline_wf_id for r in window)
        show_baseline_here = (
            baseline_wf_id and project == baseline_project and not baseline_in_window
            and any(r.get("wf_id") == baseline_wf_id for r in runs)
        )
        if show_baseline_here:
            baseline_row = next(r for r in runs if r.get("wf_id") == baseline_wf_id)
            window = sorted([baseline_row, *window], key=lambda r: r.get("ts", ""))

        for r in window:
            # D2: a row recorded without --change (all 13 pre-existing rows,
            # and anyone who still runs token-report bare) has no "change" key
            # at all -- `.get` returns None, the tag is simply omitted, and the
            # row reads back exactly as it always did.
            change_tag = f"  [{r['change']}]" if r.get("change") else ""
            # D2/D3: a row recorded before this task (or one whose transcripts
            # had no usable timestamps) has no "wall_clock_min" key at all --
            # `.get` reads back `None`, and the duration/overlap tag is simply
            # omitted, exactly like the "change" tag above.
            duration_tag = (
                f"  wall={r['wall_clock_min']}m  agent={r['agent_min']}m  overlap={r['overlap']}x"
                if r.get("wall_clock_min") is not None else ""
            )
            line = (
                f"    {r.get('ts',''):<21} {r.get('wf_id','')[:14]:<14}{change_tag} "
                f"turns={r.get('turns',0):>4}  turns/agent={r.get('turns_per_agent',0):>5}  "
                f"ctx_max={r.get('ctx_max',0):>8,}  opus={r.get('opus_share',0):>5}%{duration_tag}"
            )
            is_baseline_row = bool(baseline_wf_id) and r.get("wf_id") == baseline_wf_id and project == baseline_project
            if is_baseline_row:
                line += "  [baseline]"
            elif (
                baseline
                and not baseline_stale
                and project == baseline_project
                and r.get("ts", "") > baseline_ts
            ):
                # Only runs recorded AFTER the baseline, in the SAME project, get a
                # delta -- comparing across projects or backwards in time would
                # fabricate a number that means nothing.
                d_turns = _fmt_delta(_pct_change(r.get("turns_per_agent"), baseline.get("turns_per_agent")), baseline.get("turns_per_agent"), r.get("turns_per_agent"))
                d_ctx = _fmt_delta(_pct_change(r.get("ctx_max"), baseline.get("ctx_max")), baseline.get("ctx_max"), r.get("ctx_max"))
                d_opus = _fmt_delta(_pct_change(r.get("opus_share"), baseline.get("opus_share")), baseline.get("opus_share"), r.get("opus_share"))
                line += f"\n        vs baseline: turns/agent {d_turns}  ctx_max {d_ctx}  opus_share {d_opus}"
            lines.append(line)
        if show_baseline_here:
            lines.append("        (baseline older than the runs shown above)")
        lines.append("")

    lines.append("The three numbers that predict cost: turns/agent, ctx_max/turn, opus share.")
    if baseline_stale:
        lines.append(
            f"Baseline: {baseline_wf_id} ({baseline_ts}, project unknown -- marked before project tracking was added). "
            "Deltas suppressed -- re-run `rein baseline mark` to fix."
        )
    elif baseline:
        lines.append(f"Baseline: {baseline_wf_id} ({baseline_project}, {baseline_ts}). Deltas above are vs this run -- negative = better.")
    else:
        lines.append("Savings require a marked baseline -- without one this is a trend, not a saving.")
    return "\n".join(lines)


def _fmt_metric(value, fmt: str = "") -> str:
    """Render a baseline metric, or `n/a` when the marked row never had it
    (schema drift -- `mark_baseline` snapshots the key with value `None` rather
    than omitting it, so a plain `.get(k, default)` would never catch this)."""
    if value is None:
        return "n/a"
    return format(value, fmt) if fmt else str(value)


def render_baseline(baseline: dict | None) -> str:
    if not baseline:
        return "no baseline marked (run `rein baseline mark` after recording a run)"
    project = baseline.get("project") or "unknown project"
    return (
        f"baseline: {baseline.get('wf_id')}  ({project}, {baseline.get('ts', '')})\n"
        f"  turns={_fmt_metric(baseline.get('turns'))}  turns_per_agent={_fmt_metric(baseline.get('turns_per_agent'))}  "
        f"ctx_max={_fmt_metric(baseline.get('ctx_max'), ',')}  opus_share={_fmt_metric(baseline.get('opus_share'))}%"
    )


# ------------------------------------------------------------------ rendering --


def render_text(summary: dict, recorded: str = "") -> str:
    lines: list[str] = []
    total = summary["total"]
    lines.append(f"run: {summary['wf_id'] or summary['path']}")
    if summary["project"]:
        lines.append(f"project: {summary['project']}")
    lines.append(f"agents: {summary['agents_counted']}   turns: {summary['turns']}")
    lines.append("")

    for a in summary["agents"]:
        cr_pct = 100 * a["cache_read"] / a["total"] if a["total"] else 0
        lines.append(
            f"  {a['file'][:14]:<14} {a['model']:<12} turns={a['turns']:>4}  "
            f"total={a['total']:>12,}  cache_read={cr_pct:>3.0f}%  ctx_max/turn={a['ctx_max']:>8,}"
        )

    lines.append("")
    lines.append(f"=== aggregate ({summary['agents_counted']} agents, {summary['turns']} turns) ===")
    for key, val in sorted(summary["totals"].items(), key=lambda kv: -kv[1]):
        pct = 100 * val / total if total else 0
        lines.append(f"  {key:<12} {val:>14,}  ({pct:>4.1f}%)")
    lines.append(f"  {'TOTAL':<12} {total:>14,}")

    if summary["turns"]:
        per_turn = total // summary["turns"]
        cr_turn = summary["totals"].get("cache_read", 0) // summary["turns"]
        lines.append("")
        lines.append(
            f"  per turn: {per_turn:,} tok (cache_read {cr_turn:,}). "
            f"Lever: fewer turns x smaller context."
        )

    lines.append("")
    lines.append("=== PER MODEL (this is what your subscription limits are made of) ===")
    for model, mv in sorted(summary["by_model"].items(), key=lambda kv: -kv[1]["total"]):
        share = 100 * mv["total"] / total if total else 0
        flag = "  <- SCARCE" if "opus" in model else ""
        lines.append(
            f"  {model:<14} turns={mv['turns']:>4}  total={mv['total']:>13,}  "
            f"({share:>4.1f}%)  cache_read={mv['cache_read']:>13,}{flag}"
        )

    lines.append("")
    lines.append(
        f"  -> OPUS tokens (the expensive quota): {summary['opus_tokens']:,} "
        f"({summary['opus_share']}% of total)"
    )
    lines.append(f"  -> turns/agent: {summary['turns_per_agent']}   ctx_max/turn: {summary['ctx_max']:,}")

    if recorded:
        lines.append("")
        lines.append(f"  recorded in ledger: {recorded}")

    return "\n".join(lines)


# ----------------------------------------------------------------------- cli --


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rein token-report", description=__doc__)
    p.add_argument(
        "target",
        nargs="?",
        default=None,
        help="transcript folder or .jsonl (default: most recent workflow run)",
    )
    p.add_argument("--limit", type=int, default=None, help="max transcripts to read")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    p.add_argument("--record", action="store_true", help="force append to the ledger")
    p.add_argument("--no-record", action="store_true", help="never touch the ledger")
    p.add_argument("--ledger", default=LEDGER_PATH, help=f"ledger path (default: {LEDGER_PATH})")
    p.add_argument("--change", default="", help="label this run with a change name (D2) -- printed next to wf_id by `rein ledger`")
    p.add_argument(
        "--groups", default="",
        help="JSON-encoded task grouping for this run (e.g. '[[\"T001\"],[\"T002\"]]'), as the loop decided it -- "
             "recorded verbatim, never re-derived",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    target = args.target or latest_workflow_dir()
    if not target:
        print("no workflow runs found under ~/.claude/projects (pass a folder or .jsonl)")
        return 1
    if not os.path.exists(target):
        print(f"not found: {target}")
        return 1

    summary = summarize(target, limit=args.limit)
    if not summary["agents_counted"]:
        print(f"no transcripts with usage data in {target}")
        return 1

    # D2: a ledger row names its change, or the history is unreadable. Absent
    # entirely (not even an empty string) when --change is not passed, so the
    # 13 existing rows -- and any row recorded without a label -- stay exactly
    # as they were: `.get("change")` reads back `None` on them, never "".
    if args.change:
        summary["change"] = args.change

    # AC4: the run's task grouping rides in from the loop's own decision
    # (parallelSummary.parallelGroups) rather than being re-derived here --
    # this module has no way to know how tasks were grouped, only that they
    # were. Malformed JSON is treated like an absent flag (D2): the row reads
    # back with no "groups" key rather than a garbage value.
    if args.groups:
        try:
            summary["groups"] = json.loads(args.groups)
        except json.JSONDecodeError:
            pass

    # Recording is the default: the ledger is only useful if it is complete.
    should_record = not args.no_record
    recorded = append_to_ledger(summary, args.ledger) if should_record else ""

    if args.as_json:
        print(json.dumps({**summary, "recorded": recorded}, indent=2, ensure_ascii=False))
    else:
        print(render_text(summary, recorded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
