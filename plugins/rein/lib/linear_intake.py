#!/usr/bin/env python3
"""Take an issue off the board, and put it back when it lands.

Two moves, and everything between them is the ordinary flow (`rein-plan`,
`rein-apply`, `rein-audit`):

  * `intake` -- Backlog -> In Progress, a Beads issue in the OWNING repo, a
    branch named the way Linear already named it, and a comment saying
    where the work went.
  * `land` -- Beads closed, Linear -> Done, a comment carrying the merge
    commit. Refuses when the work is not actually merged.

**The link between a Linear issue and its Beads issue lives in a record
file**, `<repo>/.rein/linear_record.json`. Beads was measured first and
cannot carry it:

  * `--external-ref` exists and its own help names Linear, but the value
    comes back from neither `bd show --json` nor `bd export`. It is
    write-only from here, so it is set for a human reading the Beads UI
    and never relied on.
  * text search cannot find it either. `bd query 'description=PPR'`
    returns the issue; `description=PPR-86` returns nothing, and so does
    `description=Reportado` for a description that contains the word. The
    hyphen alone loses it.

So the record is the link, and a missing record is not fatal: `land`
refuses by name and takes `bead=` instead of guessing. Guessing which
issue to close is worse than stopping.

**"Merged" is checked against the base branch log, not the commit graph.**
A squash merge writes a NEW commit, so `git branch --contains <sha>` says
"not merged" about work that shipped -- it reported exactly that for the
first issue this ran on. What survives a squash is the identifier in the
commit subject, so that is what is looked for.

Nothing here deletes anything, in either system.
"""

from __future__ import annotations

import json
import os
import subprocess

import linear_issue as _issue

RECORD_RELATIVE_PATH = os.path.join(".rein", "linear_record.json")
GIT_TIMEOUT = 30
BD_TIMEOUT = 60

IN_PROGRESS = "In Progress"
DONE = "Done"


class IntakeError(Exception):
    """Something a person has to decide about. Always names what and why."""


def _run(cmd: list, cwd: str, timeout: int) -> tuple:
    """(stdout, error). Never raises -- the caller decides what a failure
    means, the same convention as `workspace._git`."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    if proc.returncode != 0:
        return proc.stdout.strip(), (proc.stderr or proc.stdout or "").strip() or f"{cmd[0]} failed"
    return proc.stdout.strip(), ""


# --------------------------------------------------------------- the record

def record_path(repo_root: str) -> str:
    return os.path.join(repo_root, RECORD_RELATIVE_PATH)


def load_record(repo_root: str) -> dict:
    try:
        with open(record_path(repo_root), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_record(repo_root: str, record: dict) -> None:
    path = record_path(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------ repo resolving

def resolve_repo(root: str, repo_name: str) -> str:
    """`<root>/<repo_name>`, if it is a git repository of its own.

    The label on the issue is the directory name -- that is the whole
    convention, and it holds for all 50 bugs on this board. A missing
    directory is named rather than guessed at: creating a branch in the
    wrong repository is expensive to notice.
    """
    if not repo_name:
        raise IntakeError("the issue names no owning repo -- nothing to route to")
    candidate = os.path.join(os.path.abspath(os.path.expanduser(root)), repo_name)
    if not os.path.isdir(candidate):
        raise IntakeError(f"no directory {candidate!r} -- pass --root <workspace> to say where the repos live")
    if not os.path.isdir(os.path.join(candidate, ".git")):
        raise IntakeError(f"{candidate!r} is not a git repository of its own")
    return candidate


def base_branch(repo_root: str) -> str:
    """The repo's own `flow.config.json` decides, defaulting to `main`.

    Read here rather than assumed: this workspace's repos build off
    `develop`, and branching a fix off `main` would put it on top of code
    that is not what ships.
    """
    try:
        with open(os.path.join(repo_root, "flow.config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "main"
    if not isinstance(cfg, dict):
        return "main"
    return ((cfg.get("worktree") or {}).get("baseBranch")) or "main"


# ------------------------------------------------------------------- intake

def intake(identifier: str, *, root: str, client, run=_run, dry_run: bool = False) -> dict:
    """Take `identifier`. Returns a report; raises `IntakeError` on a
    refusal a person has to resolve."""
    parsed = _issue.parse(client.issue(identifier))

    if parsed["isGrouper"]:
        raise IntakeError(
            f"{identifier} is a flow index, not work -- it holds no bug of its own. "
            f"Take one of its children instead."
        )
    if parsed["missing"]:
        raise IntakeError(
            f"{identifier} is missing {', '.join(parsed['missing'])} -- "
            f"the board entry is malformed, so there is nothing reliable to implement"
        )
    if parsed["warnings"]:
        raise IntakeError(
            f"{identifier}: {'; '.join(parsed['warnings'])} -- "
            f"routing on the wrong one lands the fix in the wrong repository"
        )
    if parsed["stateType"] == "completed":
        raise IntakeError(f"{identifier} is already {parsed['state']} -- nothing to take")

    repo_root = resolve_repo(root, parsed["repo"])
    base = base_branch(repo_root)
    branch = parsed["branchName"] or f"rein/{identifier.lower()}"
    record = load_record(repo_root)
    existing = record.get(identifier) or {}

    report = {
        "identifier": identifier, "repo": parsed["repo"], "repoRoot": repo_root,
        "branch": branch, "base": base, "url": parsed["url"],
        "bead": existing.get("bead", ""), "resumed": bool(existing), "dryRun": dry_run,
        "steps": [],
    }
    if dry_run:
        report["steps"].append("dry run -- nothing was written to git, Beads or Linear")
        return report

    # Beads first: it is the only step that cannot be undone by re-running,
    # so a resumed intake must not file a second issue for one bug.
    if not report["bead"]:
        out, err = run(
            ["bd", "create", parsed["title"], "-t", "bug",
             "-p", str(parsed["priority"] if parsed["priority"] is not None else 2),
             "-d", f"{parsed['impact']}\n\n{parsed['body']}",
             "--external-ref", parsed["url"] or f"linear:{identifier}"],
            repo_root, BD_TIMEOUT,
        )
        if err:
            raise IntakeError(f"bd create failed in {parsed['repo']}: {err}")
        report["bead"] = _bead_id_from(out)
        report["steps"].append(f"bd create -> {report['bead'] or '(id not reported)'}")

    _, err = run(["git", "checkout", base], repo_root, GIT_TIMEOUT)
    if err:
        raise IntakeError(f"cannot check out {base} in {parsed['repo']}: {err}")
    _, err = run(["git", "checkout", "-b", branch], repo_root, GIT_TIMEOUT)
    if err:
        # Already there is fine on a resumed intake; anything else is not.
        _, switch_err = run(["git", "checkout", branch], repo_root, GIT_TIMEOUT)
        if switch_err:
            raise IntakeError(f"cannot create or switch to {branch}: {err}")
        report["steps"].append(f"branch {branch} already existed -- switched to it")
    else:
        report["steps"].append(f"branch {branch} from {base}")

    record[identifier] = {
        "bead": report["bead"], "branch": branch, "repo": parsed["repo"],
        "base": base, "url": parsed["url"], "title": parsed["title"],
    }
    save_record(repo_root, record)
    report["steps"].append(f"recorded in {RECORD_RELATIVE_PATH}")

    if parsed["state"] != IN_PROGRESS:
        client.set_state(identifier, IN_PROGRESS)
        report["steps"].append(f"{parsed['state']} -> {IN_PROGRESS}")
    client.comment(identifier, _intake_comment(report))
    report["steps"].append("commented")
    return report


def _bead_id_from(output: str) -> str:
    """`bd create` prints `✓ Created issue: <id> — <title>`."""
    for line in (output or "").splitlines():
        if "Created issue:" in line:
            return line.split("Created issue:", 1)[1].strip().split()[0].strip(":")
    return ""


def _intake_comment(report: dict) -> str:
    return (
        "**rein** tomó este issue.\n\n"
        f"| | |\n| --- | --- |\n"
        f"| repo | `{report['repo']}` |\n"
        f"| rama | `{report['branch']}` (base `{report['base']}`) |\n"
        f"| bead | `{report['bead'] or '—'}` |\n\n"
        "Se cierra solo cuando el trabajo esté mergeado."
    )


# --------------------------------------------------------------------- land

def is_merged(repo_root: str, identifier: str, base: str, run=_run) -> bool:
    """Does `base`'s history mention `identifier`?

    NOT `git branch --contains`: a squash merge writes a new commit, so the
    branch's own sha is nowhere in `base` even though the work shipped.
    The identifier in the commit subject is what survives a squash, and it
    is there because the commit convention puts it there.
    """
    out, err = run(["git", "log", base, "--grep", identifier, "--oneline", "-5"],
                   repo_root, GIT_TIMEOUT)
    return bool(out.strip()) and not err


def land(identifier: str, *, root: str, client, run=_run, bead: str = "",
         force: bool = False, dry_run: bool = False) -> dict:
    """Close it out: Beads closed, Linear Done, both saying where it landed."""
    parsed = _issue.parse(client.issue(identifier))
    if parsed["stateType"] == "completed" and not force:
        raise IntakeError(f"{identifier} is already {parsed['state']} -- nothing to land")

    repo_root = resolve_repo(root, parsed["repo"])
    record = load_record(repo_root)
    entry = record.get(identifier) or {}
    bead_id = bead or entry.get("bead", "")
    base = entry.get("base") or base_branch(repo_root)

    if not bead_id:
        raise IntakeError(
            f"no Beads issue recorded for {identifier} in {RECORD_RELATIVE_PATH} -- "
            f"pass --bead <id> to say which one to close. "
            f"Beads cannot be searched for the link (see this module's header), "
            f"so guessing which issue to close is not an option."
        )

    merged = is_merged(repo_root, identifier, base, run=run)
    if not merged and not force:
        raise IntakeError(
            f"{base} carries no commit mentioning {identifier} in {parsed['repo']} -- "
            f"marking it Done would put a claim on the board that the code does not support. "
            f"Merge it first, or pass --force if the commit does not name the issue."
        )

    subject, _ = run(["git", "log", base, "--grep", identifier, "--format=%h %s", "-1"],
                     repo_root, GIT_TIMEOUT)
    report = {
        "identifier": identifier, "repo": parsed["repo"], "bead": bead_id,
        "base": base, "mergeCommit": subject.split()[0] if subject else "",
        "merged": merged, "dryRun": dry_run, "steps": [],
    }
    if dry_run:
        report["steps"].append("dry run -- nothing was written to Beads or Linear")
        return report

    reason = (f"Mergeado en {base}"
              + (f" como {report['mergeCommit']}" if report["mergeCommit"] else "")
              + f". Reportado como {identifier} en Linear.")
    _, err = run(["bd", "close", bead_id, "--reason", reason], repo_root, BD_TIMEOUT)
    if err:
        raise IntakeError(f"bd close {bead_id} failed: {err}")
    report["steps"].append(f"bd close {bead_id}")

    client.comment(identifier, _land_comment(report, subject))
    client.set_state(identifier, DONE)
    report["steps"].append(f"{parsed['state']} -> {DONE}")
    return report


def _land_comment(report: dict, subject: str) -> str:
    line = f"| commit | `{subject}` |\n" if subject else ""
    return (
        f"**Cerrado.** Mergeado en `{report['base']}`.\n\n"
        f"| | |\n| --- | --- |\n"
        f"| repo | `{report['repo']}` |\n{line}"
        f"| bead | `{report['bead']}` — cerrado |\n"
    )
