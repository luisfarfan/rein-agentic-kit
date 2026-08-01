#!/usr/bin/env python3
"""Mechanical, pre-agent checks over a drafted plan (T001: the-plan-checks-itself).

D3: these four BLOCKING classes are the SAME four the loop's PlanCheck prompt
(`buildPlanCheckPrompt` in workflows/loop.js) names, verbatim, in its own four
lenses -- kept identical in both places on purpose, and
`tests/test_plan_self_check.py` fails the moment either one drifts from the
other.

Only two of the four are decided here, deterministically. The other two -- an
uncheckable criterion, a criterion that contradicts a stated Decision -- are
semantic calls no regex can make honestly; they are the critique agent's job,
described in `plugins/rein/skills/plan/SKILL.md`, the same way the loop's own
PlanCheck lenses are entirely agent-judged, never computed. What IS mechanical
is decided here so the concrete, measured defect -- T003's Verification naming
T002's own test module, 81k tokens to catch inside the loop -- is never left to
an agent's mood again.
"""

from __future__ import annotations

import re

import plan as _plan

# D3: verbatim-identical to the four phrases embedded in loop.js's
# buildPlanCheckPrompt lenses. Do not reword one without rewording the other.
BLOCKING_CLASSES = (
    "a verification that cannot mechanically confirm the criteria it is attached to",
    "a criterion no command can check",
    "a dependency that is circular or names a task that does not exist",
    "a criterion that contradicts a stated decision",
)

# D3 asymmetry, recorded deliberately rather than fixed by adding a fifth
# mechanical class (round-1 review finding 4): the loop's PlanCheck prompt
# carries one lens this module does not decide. Unlike class 1 (a reused
# Verification string) or class 3 (a missing/circular dependency), "the
# whole suite where one file would prove it" has no fixed syntactic shape a
# regex can honestly tell apart from a legitimately whole-suite-only
# project's own test entry point -- it stays an agent judgement, on the
# loop's side of the gate, on purpose. Pinned verbatim so a drift in either
# copy is caught the same way BLOCKING_CLASSES already is.
UNBOUNDED_VERIFICATION_LENS = (
    "Verifications that are unbounded — a command that runs the whole suite "
    "where one test or one file would prove the criterion, burning far more "
    "context than the task needs."
)
LOOP_ONLY_LENSES = {
    UNBOUNDED_VERIFICATION_LENS: (
        "no regex can honestly tell a project whose only test entry point IS "
        "the whole suite from a task that should have scoped its command -- "
        "stays a semantic call for the loop's own PlanCheck agent"
    ),
}

# The one shape this repo's own convention (SKILL.md's own example: "one
# test file or id, never 'the suite'") treats as inherently task-scoped: a
# bare `python3 -m unittest <dotted.module.path>` naming exactly one module
# -- the shape of the real T003/T002 defect this module exists to catch. A
# bare runner (`pytest`, `npm test`, `cargo test`), `unittest discover`, or a
# runner invoked with a filter (`npm test -- <suite>`, `cargo test <mod>`)
# can honestly be reused across tasks that legitimately verify through the
# same suite, and reusing one is never BLOCKING on that shape alone (round-1
# review finding 5).
_SCOPED_UNITTEST_MODULE = re.compile(
    r"^python3?\s+-m\s+unittest\s+(?!discover\b)[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$"
)


def _is_scoped_command(cmd: str) -> bool:
    return bool(_SCOPED_UNITTEST_MODULE.match(cmd.strip()))


def _confirm_findings(tasks: list[dict]) -> list[dict]:
    """Class 1: a Verification command reused verbatim from an earlier task is
    confirming THAT task's criteria, not this one's -- the exact, measured
    shape of the defect this change exists to catch (T003's Verification
    named tests.test_verify_commands, which was T002's own test module).

    Severity depends on the command's own shape (finding 5): a reused,
    per-module `python3 -m unittest <module>` command is BLOCKING -- this
    project's own convention makes that shape task-scoped, so reusing one is
    always the T003/T002 defect. Any other reused shape -- a bare or
    filtered whole-suite runner -- can be an honestly shared verification
    and is reported as IMPORTANT instead: visible, but not unfixable."""
    findings: list[dict] = []
    owner: dict[str, str] = {}
    for t in tasks:
        v = (t.get("verification") or "").strip()
        if not v:
            continue
        if v in owner and owner[v] != t["id"]:
            criteria = t.get("acceptance") or []
            listed = "; ".join(criteria) if criteria else "its acceptance criteria"
            severity = "BLOCKING" if _is_scoped_command(v) else "IMPORTANT"
            findings.append({
                "taskId": t["id"],
                "severity": severity,
                "classId": BLOCKING_CLASSES[0],
                "text": (
                    f"{t['id']}'s verification (`{v}`) is {owner[v]}'s verification, not "
                    f"{t['id']}'s -- it cannot mechanically confirm: {listed}"
                ),
            })
        else:
            owner.setdefault(v, t["id"])
    return findings


def _dependency_findings(tasks: list[dict]) -> list[dict]:
    """Class 3: a dependency that names a task absent from the plan, or a
    cycle among tasks that ARE in the plan."""
    findings: list[dict] = []
    known = {t["id"] for t in tasks}
    missing_reported: set[str] = set()
    for t in tasks:
        for d in t.get("dependsOn", []):
            if d not in known:
                findings.append({
                    "taskId": t["id"],
                    "severity": "BLOCKING",
                    "classId": BLOCKING_CLASSES[2],
                    "text": f"{t['id']} depends on {d}, which is not a task in this plan",
                })
                missing_reported.add(t["id"])
    # order_by_dependencies silently drops a dep on an unknown id (treats it
    # as satisfied) -- caught above instead. What it DOES report in `stuck`
    # is a genuine cycle among ids that all exist.
    _, stuck = _plan.order_by_dependencies(tasks)
    for tid in stuck:
        if tid in missing_reported:
            continue  # already explained by the missing-task finding above
        findings.append({
            "taskId": tid,
            "severity": "BLOCKING",
            "classId": BLOCKING_CLASSES[2],
            "text": f"{tid} is part of a dependency cycle and can never become ready",
        })
    return findings


def mechanical_findings(text: str) -> list[dict]:
    """Everything about a drafted plan's own text that a regex can honestly
    decide -- classes 1 and 3 of BLOCKING_CLASSES. Never raises: a plan that
    fails to parse simply yields no findings, the same "never raises"
    contract `plan.parse_tasks_md` already carries."""
    tasks = _plan.parse_tasks_md(text)
    return _confirm_findings(tasks) + _dependency_findings(tasks)


if __name__ == "__main__":
    import json
    import sys

    _path = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(_path, encoding="utf-8", errors="replace") as _fh:
        print(json.dumps(mechanical_findings(_fh.read()), indent=2))
