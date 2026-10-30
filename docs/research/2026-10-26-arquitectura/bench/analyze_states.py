#!/usr/bin/env python3
"""Aggregate real `rein ship` state files (<root>/.rein/ship/**/*.json). Read-only."""
import collections, glob, json, os, re, sys

ROOTS = [os.path.expanduser(p) for p in ("~/Developer", "~/orca/workspaces")]

CATS = [
    ("verification_failed", r"Verification|verification command|exited \d|exit code"),
    ("test_failed", r"FAILED|AssertionError|failing test|tests? failed|\bfailed\b.*test|Error: expect"),
    ("lint", r"\blint|ruff|eslint|prettier"),
    ("typecheck", r"typecheck|tsc|mypy|pyright|TS\d{4}"),
    ("guard_outside", r"outside (its|the) worktree|wrote outside|outside its worktree"),
    ("guard_config", r"gate config|protected|flow\.config|Gate config"),
    ("no_commit", r"no commit|nothing committed|did not commit|uncommitted|not committed"),
    ("hook", r"commit-msg|hook"),
    ("timeout", r"timed? ?out|timeout"),
    ("audit_blocking", r"^BLOCKING|BLOCKING:"),
    ("setup_env", r"126|not invocable|command not found|No such file|ENOENT|setup"),
]


def cat_of(text):
    out = []
    for name, rx in CATS:
        if re.search(rx, text or "", re.I | re.M):
            out.append(name)
    return out or ["other"]


def main(out):
    files = []
    for r in ROOTS:
        files += glob.glob(os.path.join(r, "**/.rein/ship/**/*.json"), recursive=True)
    files = sorted(set(f for f in files if "node_modules" not in f))
    runs, tasks = [], []
    unique = {}  # an archived copy and the live file of one run are the same run
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if isinstance(d, dict) and "tasks" in d:
            unique[(d.get("change"), d.get("run_id"))] = (f, d)
    for f, d in unique.values():
        ts = d["tasks"]
        plan_tasks = [t for t in ts.values() if not re.match(r"^(AUDIT|GATE)\d", t["id"])]
        audit_fix = [t for t in ts.values() if t["id"].startswith("AUDIT")]
        gate_fix = [t for t in ts.values() if t["id"].startswith("GATE")]
        runs.append(dict(file=f, change=d.get("change"), phase=d.get("phase"), audit=d.get("audit_result"),
                         audit_rounds=d.get("audit_rounds"), extra_rounds=d.get("extra_audit_rounds"),
                         repos=len(set(t["repo"] for t in ts.values())),
                         n_plan=len(plan_tasks), n_audit_fix=len(audit_fix), n_gate_fix=len(gate_fix),
                         dispatches=sum(t.get("dispatches", 0) for t in ts.values()),
                         agents=collections.Counter("%s:%s" % (t.get("agent"), t.get("model")) for t in ts.values()),
                         changed_files=len(d.get("changed_files") or []),
                         decisions=len(d.get("owner_decisions") or [])))
        for t in ts.values():
            tasks.append(dict(change=d.get("change"), id=t["id"], repo=t["repo"], status=t["status"],
                              dispatches=t.get("dispatches", 0), attempts=t.get("attempts", 0),
                              agent=t.get("agent"), model=t.get("model"),
                              kind="audit_fix" if t["id"].startswith("AUDIT") else "gate_fix" if t["id"].startswith("GATE") else "plan",
                              fail=cat_of(t.get("last_failure")) if t.get("last_failure") else [],
                              touched=len(t.get("touched") or [])))
    s = {}
    s["state_files"] = len(runs)
    s["phase"] = collections.Counter(r["phase"] for r in runs)
    s["audit_result"] = collections.Counter(str(r["audit"]) for r in runs)
    s["audit_rounds"] = collections.Counter(str(r["audit_rounds"]) for r in runs)
    s["multi_repo_runs"] = sum(1 for r in runs if r["repos"] > 1)
    s["plan_tasks"] = sum(r["n_plan"] for r in runs)
    s["audit_fix_tasks"] = sum(r["n_audit_fix"] for r in runs)
    s["gate_fix_tasks"] = sum(r["n_gate_fix"] for r in runs)
    s["runs_with_audit_fix"] = sum(1 for r in runs if r["n_audit_fix"])
    s["runs_with_gate_fix"] = sum(1 for r in runs if r["n_gate_fix"])
    s["dispatches_total"] = sum(r["dispatches"] for r in runs)
    pt = [t for t in tasks if t["kind"] == "plan"]
    s["plan_task_dispatches"] = collections.Counter(min(t["dispatches"], 5) for t in pt)
    s["plan_task_first_try_share"] = sum(1 for t in pt if t["dispatches"] <= 1) / max(1, len(pt))
    s["status"] = collections.Counter("%s/%s" % (t["kind"], t["status"]) for t in tasks)
    s["agents"] = collections.Counter("%s/%s:%s" % (t["kind"], t["agent"], t["model"]) for t in tasks)
    s["fail_categories_retried_plan_tasks"] = collections.Counter(c for t in pt if t["dispatches"] > 1 for c in t["fail"])
    s["fail_categories_all"] = collections.Counter(c for t in tasks for c in t["fail"])
    os.makedirs(out, exist_ok=True)
    json.dump(dict(summary=s, runs=runs, tasks=tasks), open(os.path.join(out, "states.json"), "w"), indent=1, default=str)
    print(json.dumps(s, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
