#!/usr/bin/env python3
"""Arm A supervisor: polls `rein ship`, answers its gates with a FIXED script (each answer is one
human intervention), records run_end when the run settles. Stops the run past the wall budget.

Usage: watch_a.py <run-id> <change> [--wall-min 90]
"""
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402

SCRIPT_TEXT = ("Decide within the acceptance criteria and the plan's decisions; the owner has no "
               "further input for this pilot.")


def choose(kind, options, seen):
    """The scripted owner: one retry/resend per gate kind, then let Rein go on; never approve on
    the owner's behalf something only a human could judge (no human_review in these plans)."""
    if not options:
        return SCRIPT_TEXT
    n = seen.get(kind, 0)
    for pick in (("retry", "resend") if n < 1 else ()) + ("approve", "continue", "accept", "wait", "skip"):
        if pick in options:
            return pick
    return options[0]


def main():
    run_id, change = sys.argv[1], sys.argv[2]
    wall = float(sys.argv[sys.argv.index("--wall-min") + 1]) if "--wall-min" in sys.argv else 90.0
    task, base, repo, meta = h.paths(run_id)
    state_file = os.path.join(repo, ".rein", "ship", change + ".json")
    t0 = time.time()
    answered, seen = set(), {}
    while True:
        st = json.load(open(state_file)) if os.path.exists(state_file) else {}
        phase = st.get("phase")
        if phase in ("done", "failed", "in-review", "rejected"):
            break
        if time.time() - t0 > wall * 60 or os.path.exists(h.STOP):
            h.ev(meta, "budget_stop", phase=phase)
            pid = st.get("driver_pid")
            if pid:
                subprocess.run(["kill", str(pid)])
            for t in (st.get("tasks") or {}).values():
                if t.get("dispatch_id") and t.get("status") == "running":
                    subprocess.run(["orca", "orchestration", "worker-stop", "--dispatch", t["dispatch_id"], "--json"],
                                   capture_output=True)
            break
        out = subprocess.run(["rein", "watch", change, "--root", repo, "--once"], capture_output=True, text=True,
                             timeout=120).stdout
        for m in re.finditer(r"PENDING (\S+) \((\w+)\) task (\S+)\n(?:question: (.*)\n)?(?:options: (.*))?", out):
            gate, kind, tid, question, opts = m.groups()
            if gate in answered:
                continue
            options = [o.strip() for o in (opts or "").split(",") if o.strip() and not o.strip().startswith("(")]
            answer = choose(kind, options, seen)
            seen[kind] = seen.get(kind, 0) + 1
            r = subprocess.run(["rein", "answer", change, answer, "--gate", gate, "--root", repo],
                               capture_output=True, text=True)
            answered.add(gate)
            h.ev(meta, "owner_answer", gate=gate, gate_kind=kind, task=tid, question=(question or "")[:400],
                 options=options, answer=answer, rc=r.returncode)
            print("answered", gate, kind, "->", answer, flush=True)
        time.sleep(30)
    st = json.load(open(state_file)) if os.path.exists(state_file) else {}
    wt = list((st.get("worktree_paths") or {}).values())
    tree = wt[0] if wt else ""
    head = h.sh(["git", "rev-parse", "HEAD"], cwd=tree).stdout.strip() if tree else ""
    h.ev(meta, "run_end", phase=st.get("phase"), audit=st.get("audit_result"), audit_rounds=st.get("audit_rounds"),
         tree=tree, head=head, quota=h.quota(),
         dispatches=sum(t.get("dispatches", 0) for t in (st.get("tasks") or {}).values()),
         fix_rounds=st.get("audit_rounds"))
    print("A run settled:", st.get("phase"), st.get("audit_result"), tree, flush=True)


if __name__ == "__main__":
    main()
