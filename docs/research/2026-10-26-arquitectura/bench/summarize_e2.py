#!/usr/bin/env python3
"""Per (question, strategy): median and [min, max] over reps. Usage: summarize_e2.py <results.jsonl>..."""
import collections, json, statistics, subprocess, sys, os


def rng(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    m = statistics.median(xs)
    fmt = (lambda v: "%.2f" % v) if any(isinstance(x, float) and x < 10 for x in xs) else (lambda v: "%d" % round(v))
    return "%s [%s–%s]" % (fmt(m), fmt(min(xs)), fmt(max(xs)))


rows = []
for p in sys.argv[1:]:
    base = os.path.dirname(p)
    for line in open(p):
        r = json.loads(line)
        if r["q"] == "Q4":  # rescore from the kept worktree (full-suite scoring was dropped mid-run)
            wt = os.path.join(base, "wt-%s-%s-%d" % (r["q"], r["strategy"], r["rep"]))
            if os.path.isdir(wt):
                diff = subprocess.run(["git", "diff", "--name-only"], cwd=wt, capture_output=True, text=True).stdout.split()
                r["score"]["exact_revert"] = diff == []
                r["score"]["minimal"] = diff in ([], ["plugins/rein/lib/quota.py"])
        r["tokens"] = sum((r.get(k) or 0) for k in ("input", "cache_write", "cache_read", "output"))
        rows.append(r)

g = collections.defaultdict(list)
for r in rows:
    g[(r["q"], r["strategy"])].append(r)
print("| Q | Strat | n | quality | turns | tool calls | cg calls | gf calls | tokens (k) | wall s | api-equiv $ |")
print("|---|---|---:|---|---|---|---:|---:|---|---|---|")
for (q, s), rs in sorted(g.items()):
    if q == "Q4":
        qual = "%d/%d tests ok, %d/%d minimal" % (sum(r["score"]["target_tests_pass"] for r in rs), len(rs),
                                                  sum(r["score"]["minimal"] for r in rs), len(rs))
    else:
        qual = "F1 " + rng([r["score"]["f1"] for r in rs]) + " R " + rng([r["score"]["recall"] for r in rs])
    print("| %s | %s | %d | %s | %s | %s | %d | %d | %s | %s | %s |" % (
        q, s, len(rs), qual, rng([r["turns"] for r in rs]), rng([r["tool_calls"] for r in rs]),
        sum(r["codegraph_calls"] for r in rs), sum(r["graphify_calls"] for r in rs),
        rng([r["tokens"] / 1000 for r in rs]), rng([r["wall_s"] for r in rs]),
        rng([r["api_equiv_usd"] or 0 for r in rs])))
