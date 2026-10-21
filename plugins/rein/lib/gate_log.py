"""Every gate Rein runs, kept where it can be read afterwards.

A run's per-task gates and per-repo full gates were only lines in the driver's tab: which
tier ran, which slots, how long each took, which were reused from the cache and which
`gate.env` names were applied could not be audited once the tab was gone. Each gate now
appends one JSON line:

- the ship driver, next to the change's state: `<root>/.rein/ship/<change>.gates.jsonl`
  (archived with it by `rein done`);
- `rein gate` and `rein quick done`, per checkout: `~/.claude/rein/verify/log/<name>-<hash>.jsonl`
  (`$REIN_GATE_LOG_DIR` moves it).

`rein gate-log` prints either. Values of the environment are never written, only names.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import verify as _verify

_LOCK = threading.Lock()
SLOT_FIELDS = ("outcome", "exitCode", "elapsedMs", "budgetSeconds", "command")


def ship_log_path(state_file: str) -> str:
    """The change's gate log, next to its state file."""
    return (state_file[:-len(".json")] if state_file.endswith(".json") else state_file) + ".gates.jsonl"


def root_log_path(root: str) -> str:
    folder = os.environ.get("REIN_GATE_LOG_DIR") or os.path.join(_verify.STATE_DIR, "log")
    abs_root = os.path.abspath(root)
    digest = hashlib.sha256(abs_root.encode("utf-8")).hexdigest()[:16]
    base = "".join(c if c.isalnum() or c in "-_." else "-" for c in os.path.basename(abs_root.rstrip(os.sep)))
    return os.path.join(folder, "%s-%s.jsonl" % (base or "root", digest))


def summarize(report) -> dict:
    """What a gate report says about itself: tier, slots (with time, cache and budget), env names,
    affected tests and the decision. `report` is `rein gate --json` output (text or parsed), or a
    bare `gate_run` report; anything unreadable gives no slots."""
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            return {"slots": []}
    if not isinstance(report, dict):
        return {"slots": []}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    inner = report.get("report") if isinstance(report.get("report"), dict) else report
    results = inner.get("results") if isinstance(inner.get("results"), dict) else {}
    slots = []
    for name, res in results.items():
        if not isinstance(res, dict):
            continue
        slot = {"slot": res.get("slot") or name, "cached": bool(res.get("cached"))}
        slot.update({k: res.get(k) for k in SLOT_FIELDS if k in res})
        slots.append(slot)
    out = {"slots": slots}
    for key in ("tier", "env", "affected"):
        if key in inner:
            out[key] = inner[key]
    if decision:
        out["decision"] = decision.get("decision")
        out["reason"] = str(decision.get("reason") or "")[:300]
    return out


def append(path: str, entry: dict) -> None:
    """Add one line; a log that cannot be written never fails the gate it describes."""
    line = json.dumps(dict(entry, at=entry.get("at") or time.time()), sort_keys=True)
    try:
        with _LOCK:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        pass


def read(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    out.append(data)
    except OSError:
        return []
    return out


def render(entries: list) -> str:
    """One block per gate: when, what (task or repo), exit, tier, env names, then a line per slot."""
    lines = []
    for e in entries:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(e.get("at") or 0)))
        what = " ".join(str(x) for x in (e.get("kind"), e.get("task") or "", e.get("repo") or "") if x)
        head = "%s  %s  exit=%s  tier=%s" % (when, what or "gate", e.get("exit"), e.get("tier") or "default")
        if e.get("env"):
            head += "  env=%s" % ",".join(map(str, e["env"]))
        lines.append(head)
        affected = e.get("affected") if isinstance(e.get("affected"), dict) else None
        if affected:
            lines.append("    affected tests: %s (%s file(s))" % (affected.get("mode"), affected.get("count")))
        for s in e.get("slots") or []:
            lines.append("    %-15s %-13s exit=%s %6sms%s%s" % (
                s.get("slot"), s.get("outcome"), "-" if s.get("exitCode") is None else s.get("exitCode"),
                s.get("elapsedMs", "-"), " cached" if s.get("cached") else "",
                "  budget %gs" % s["budgetSeconds"] if isinstance(s.get("budgetSeconds"), (int, float)) else ""))
        for extra in ("verification", "guard", "error"):
            if e.get(extra):
                lines.append("    %s: %s" % (extra, e[extra]))
    return "\n".join(lines)
