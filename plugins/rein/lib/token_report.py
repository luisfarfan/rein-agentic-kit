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

The ledger (~/.claude/rein/runs.jsonl) is what makes cross-project, cross-session
history possible: raw transcripts get rotated away, a summarized record does not.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict

PROJECTS = os.path.expanduser("~/.claude/projects")
LEDGER_DIR = os.path.expanduser("~/.claude/rein")
LEDGER_PATH = os.path.join(LEDGER_DIR, "runs.jsonl")

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


def analyze(path: str) -> dict:
    """Token accounting for a single transcript (one agent)."""
    turns = 0
    totals: dict[str, int] = defaultdict(int)
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ctx_max = 0

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
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

    total = sum(grand.values())
    opus_total = sum(v["total"] for m, v in grand_by_model.items() if "opus" in m)

    # Sort loudest agent first: the runaway is what you are hunting.
    agents.sort(key=lambda a: -a["total"])

    prov = _provenance(target)
    return {
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


def _mtime_iso(target: str) -> str:
    import datetime

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
