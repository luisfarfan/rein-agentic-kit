#!/usr/bin/env python3
"""Deterministic gates: what is next, and whether a review still stands.

Both exist for the same reason, taken straight from the origin project's
`change-run-auto`:

    Stop conditions are read from a verifiable signal -- never from the model's
    impression that "this is probably enough".

A loop whose stop condition is an agent setting `done: true` on itself has no
gate at all; it has a suggestion. `next_task()` answers "is there a task to do,
and may it be claimed" from the plan alone, and `check_review()` answers "does
the approval still apply to this code" from content hashes alone. Neither asks a
model anything.

The review episode is the second half of the origin project's rule that an
APPROVED verdict is only valid for the exact state reviewed: if the code changes
after approval, the review is STALE and must be re-run. Without that, an agent
can be approved and then quietly keep editing.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import plan as _plan

EPISODE_DIR = ".rein/reviews"
VERDICTS = ("APPROVED", "CHANGES_REQUESTED")
IMPLEMENTER = "implementer"
SEVERITIES = ("BLOCKING", "IMPORTANT", "SUGGESTION")
DEFAULT_SEVERITY = "IMPORTANT"


def _parse_finding(raw) -> dict:
    """Split a finding string into {"severity", "text"} on a known prefix.

    Only the fixed severity words are recognized as prefixes -- a finding
    whose own text contains a colon (e.g. "BLOCKING: SQL query: no params")
    must not be mis-split, so this never does a naive partition on the first
    colon it finds. Untagged text defaults to IMPORTANT (D1: never silently
    to the mildest level, SUGGESTION).
    """
    if isinstance(raw, dict):
        severity = str(raw.get("severity", DEFAULT_SEVERITY)).strip().upper()
        if severity not in SEVERITIES:
            severity = DEFAULT_SEVERITY
        # str() so a machine-written episode with a numeric text fails later as
        # a validation error, not as an AttributeError mid-record.
        return {"severity": severity, "text": str(raw.get("text", ""))}
    # Strip FIRST: the CLI splits --findings on '|', which leaves the natural
    # spelling "IMPORTANT: a | BLOCKING: b" with a leading space on every
    # finding after the first. Matching the prefix against the raw string made
    # " BLOCKING: x" read as IMPORTANT — and D2's APPROVED-with-blocker
    # refusal silently failed open on exactly the documented input shape.
    text = str(raw).strip()
    for severity in SEVERITIES:
        prefix = f"{severity}:"
        if text[: len(prefix)].upper() == prefix:
            return {"severity": severity, "text": text[len(prefix):].strip()}
    return {"severity": DEFAULT_SEVERITY, "text": text}


def _normalize_findings(raw_findings) -> list[dict]:
    """Read findings of either shape (old plain strings, new tagged dicts)."""
    return [_parse_finding(f) for f in (raw_findings or [])]


# ------------------------------------------------------------- next task --


def next_task(root: str = ".", source: str = "", change: str = "", configured: str = "") -> dict:
    """The deterministic answer to "what may be worked on now".

    `ready` is the signal a bounded loop stops on. It is false for a *reason*,
    always named, so a caller never has to infer why nothing happened.
    """
    plan = _plan.read_plan(root, source=source, change=change, configured=configured)
    if not plan["exists"]:
        # `error` already names the real problem (D3) -- for an openspec
        # source with no change, `path` is "" and there is no location to
        # report a NOT FOUND at.
        reason = plan.get("error") or f"no plan at {plan['path']}"
        return {"ready": False, "reason": reason, "planPath": plan["path"],
                "taskId": "", "title": "", "humanReview": False, "verification": "",
                "remaining": 0, "blockedBy": [], "unresolvableDeps": []}

    pending = plan["pending"]
    ordered, stuck = _plan.order_by_dependencies(pending)
    done_ids = {t["id"] for t in plan["tasks"] if t["checked"]}
    pending_ids = {t["id"] for t in pending}

    base = {
        "planPath": plan["path"],
        "remaining": len(pending),
        "unresolvableDeps": stuck,
        "blockedBy": [],
        "taskId": "",
        "title": "",
        "humanReview": False,
        "verification": "",
    }

    if not pending:
        return {**base, "ready": False, "reason": "no pending tasks"}

    for task in ordered:
        # A dependency that is neither done nor still pending does not exist:
        # treat it as satisfied rather than deadlocking on a typo'd id.
        unmet = [d for d in task.get("dependsOn", []) if d in pending_ids and d not in done_ids]
        if unmet:
            continue
        return {
            **base,
            "ready": True,
            "reason": "",
            "taskId": task["id"],
            "title": task["title"],
            "humanReview": bool(task.get("humanReview")),
            "verification": task.get("verification", ""),
        }

    first = ordered[0]
    return {
        **base,
        "ready": False,
        "reason": f"every pending task is blocked by an unfinished dependency (e.g. {first['id']})",
        "blockedBy": [d for d in first.get("dependsOn", []) if d not in done_ids],
    }


# ---------------------------------------------------------- review episode --


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def state_hash(reviewed_files: dict[str, str]) -> str:
    """sha256 over the sorted JSON of {path: content-hash}.

    Sorted and canonical so the same reviewed set always yields the same hash on
    any machine -- the gate compares hashes, so an unstable definition would make
    every review look stale.
    """
    canonical = json.dumps(reviewed_files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_review(
    root: str,
    change: str,
    verdict: str,
    files: list[str],
    findings: list[str],
    reviewer: str,
    agent: str = "",
) -> dict:
    """Write a review episode. Raises ValueError on anything that would make it a lie."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if not reviewer.strip():
        raise ValueError("reviewer must be named -- an anonymous verdict cannot be attributed")
    if reviewer.strip() == IMPLEMENTER:
        raise ValueError(f"reviewer cannot be {IMPLEMENTER!r}: no agent approves its own implementation")
    if not files:
        raise ValueError("reviewed_files cannot be empty -- declare what you actually inspected")

    parsed_findings = _normalize_findings(findings)
    if any(not f["text"].strip() for f in parsed_findings):
        raise ValueError("a finding must carry non-empty text -- an empty finding informs no one")
    has_blocking = any(f["severity"] == "BLOCKING" for f in parsed_findings)
    if verdict == "CHANGES_REQUESTED" and not has_blocking:
        raise ValueError(
            "D2: CHANGES_REQUESTED requires at least one BLOCKING finding"
        )
    if verdict == "APPROVED" and has_blocking:
        raise ValueError(
            "D2: APPROVED tolerates no BLOCKING findings"
        )

    root = os.path.abspath(root)
    reviewed: dict[str, str] = {}
    missing: list[str] = []
    for rel in files:
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.isfile(path):
            missing.append(rel)
            continue
        reviewed[os.path.relpath(path, root)] = _sha256_file(path)
    if missing:
        raise ValueError(f"declared files that do not exist: {', '.join(missing)}")

    episode = {
        "schema": 1,
        "change": change,
        "verdict": verdict,
        "reviewer_actor": reviewer.strip(),
        "agent": agent or reviewer.strip(),
        "reviewed_files": reviewed,
        "reviewed_state_hash": state_hash(reviewed),
        "findings": parsed_findings,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = os.path.join(root, EPISODE_DIR, f"{stamp}-{change or 'change'}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "episode.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(episode, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return {**episode, "path": path}


def latest_episode(root: str, change: str = "") -> dict | None:
    base = os.path.join(os.path.abspath(root), EPISODE_DIR)
    if not os.path.isdir(base):
        return None
    best = None
    for entry in sorted(os.listdir(base)):
        path = os.path.join(base, entry, "episode.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                episode = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if change and episode.get("change") != change:
            continue
        episode["path"] = path
        best = episode  # sorted by timestamped dir name: the last one wins
    return best


def check_review(root: str, change: str = "") -> dict:
    """Does an APPROVED review still apply to the code as it is right now?

    Three ways to fail, each reported explicitly rather than as a bare False:
    no review, not approved, or approved for a state the code has since left.
    """
    episode = latest_episode(root, change)
    if episode is None:
        return {"ok": False, "reason": "no review episode recorded", "verdict": "", "stale": False, "changed": []}

    problems = []
    if episode.get("verdict") != "APPROVED":
        problems.append(f"verdict is {episode.get('verdict')!r}")
    actor = (episode.get("reviewer_actor") or "").strip()
    if not actor or actor == IMPLEMENTER:
        problems.append(f"reviewer_actor is {actor!r}")

    root = os.path.abspath(root)
    changed: list[str] = []
    current: dict[str, str] = {}
    for rel, recorded in (episode.get("reviewed_files") or {}).items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            changed.append(f"{rel} (deleted)")
            continue
        now = _sha256_file(path)
        current[rel] = now
        if now != recorded:
            changed.append(rel)

    stale = bool(changed) or state_hash(current) != episode.get("reviewed_state_hash")
    if stale:
        problems.append("the reviewed state has changed since approval")

    return {
        "ok": not problems,
        "reason": "; ".join(problems),
        "verdict": episode.get("verdict", ""),
        "reviewer": actor,
        "stale": stale,
        "changed": changed,
        "episode": episode.get("path", ""),
        "findings": _normalize_findings(episode.get("findings", [])),
    }


# ------------------------------------------------------------------- gate --
# `rein verify` answers "could these commands be INVOKED at all" -- a precheck,
# so nobody is paid to work toward a gate that cannot pass. It returns 0 when
# every command was invocable, which means a test suite that ran and FAILED
# exits 0 there. That is right for a precheck and useless as a gate.
#
# This answers the other question: did they PASS. Same report, different
# verdict, and the two exit codes stay distinct so a dead environment can never
# be reported as bad code.

GATE_GREEN = "green"
GATE_RED = "red"
GATE_SETUP = "setup"

EXIT_GREEN = 0
EXIT_RED = 1
EXIT_SETUP = 126

# What a gate is allowed to be. `testOne` runs against a synthetic target that
# no suite owns (verify's OUTCOME_INCONCLUSIVE exists for exactly that), and
# `serve` is a dev server that never runs to completion -- neither proves
# anything about a change, so neither can hold the gate open or shut.
GATE_SLOTS = ("test", "lint", "typecheck", "build")

# Outcomes that mean the environment failed, not the code. Kept as names rather
# than imported from verify so this module stays free of that dependency (it is
# imported by the CLI alongside verify, never beneath it).
_SETUP_OUTCOMES = ("not_invocable", "timeout")
_PASS_OUTCOMES = ("ok",)


def decide_gate(report: dict, review: dict = None, require_review: bool = False) -> dict:
    """Did the configured gate commands pass? Pure -- runs nothing.

    Setup beats red, always. A run where typecheck could not be invoked and
    the tests failed reports SETUP, because an environment that cannot run
    half its checks has not earned the right to call the code wrong.

    This is deliberately stricter than `decideGatePrecheck`, which treats an
    uninvocable lint or typecheck as a warning. That function runs BEFORE the
    work, deciding whether to start at all, and you can still write code with
    a broken linter. This one runs AFTER, deciding whether the work is done --
    and a check that never ran is not a check that passed. Same fact, opposite
    consequence, because the question is not the same.
    """
    results = (report or {}).get("results") or {}

    setup, failed, passed, ignored = [], [], [], []
    for slot in sorted(results):
        res = results[slot] or {}
        outcome = res.get("outcome", "")
        if slot not in GATE_SLOTS:
            ignored.append(f"{slot} [{outcome}]")
            continue
        if outcome in _SETUP_OUTCOMES or not res.get("invocable", True):
            setup.append(f"{slot} [{outcome or 'unknown'}]")
        elif outcome in _PASS_OUTCOMES:
            passed.append(slot)
        elif outcome == "skipped":
            # Configured but deliberately not run this time; it proves nothing
            # either way, so it neither passes nor blocks.
            ignored.append(f"{slot} [skipped]")
        else:
            failed.append(f"{slot} [{outcome or 'unknown'}] exit={res.get('exitCode')}")

    if setup:
        return _verdict(GATE_SETUP, EXIT_SETUP,
                        "could not be invoked: " + ", ".join(setup) +
                        " -- a setup problem, not a code problem",
                        setup, failed, passed, ignored)

    if failed:
        return _verdict(GATE_RED, EXIT_RED, "failed: " + ", ".join(failed),
                        setup, failed, passed, ignored)

    if not passed:
        # Nothing ran. Green here would be the loudest possible lie: it is the
        # shape of every silent pass this kit exists to prevent -- a gate that
        # reports success because it checked nothing at all.
        return _verdict(GATE_SETUP, EXIT_SETUP,
                        "no gate command ran -- nothing was verified, so nothing is proven",
                        setup, failed, passed, ignored)

    if require_review:
        rev = review or {}
        if not rev.get("ok"):
            return _verdict(GATE_RED, EXIT_RED,
                            "review gate not satisfied: " + (rev.get("reason") or "no review recorded"),
                            setup, failed, passed, ignored)

    return _verdict(GATE_GREEN, EXIT_GREEN, "passed: " + ", ".join(passed),
                    setup, failed, passed, ignored)


def _verdict(decision, exit_code, reason, setup, failed, passed, ignored) -> dict:
    return {
        "decision": decision,
        "exit": exit_code,
        "reason": reason,
        "setup": setup,
        "failed": failed,
        "passed": passed,
        "ignored": ignored,
    }
