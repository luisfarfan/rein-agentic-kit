"""What Rein checks about a finished task besides its tests: the rules an agent must not break.

A worker in a real run rewrote the `flow.config.json` it was being gated with (its test
commands and `gate.affected`), and another skipped the repo's commit-msg hook with
`LEFTHOOK_EXCLUDE`. The briefs forbid both; this module is how Rein notices anyway:

- the change touches the configuration Rein judges it with: `flow.config.json` always,
  the task runner's config when the commands come from one (`mise.toml`), and any glob in
  `gate.protected`. A task whose own text names the file may change it;
- a commit of the change does not pass the repo's own `commit-msg` hook, run again by Rein
  (`git hook run`, so `core.hooksPath` and lefthook/husky installs are honoured) with the
  usual bypass variables removed from its environment.

Each problem is one line saying what and why; none means the change is clean. A checkout
git cannot read is not this module's verdict to give (the gate's is): it yields nothing.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import tempfile

import affected as _affected

# Environment variables that switch a repo's hooks off (lefthook, husky, pre-commit).
BYPASS_ENV = ("LEFTHOOK", "LEFTHOOK_EXCLUDE", "LEFTHOOK_SKIP", "HUSKY", "HUSKY_SKIP_HOOKS", "SKIP",
              "PRE_COMMIT", "PRE_COMMIT_ALLOW_NO_CONFIG", "GIT_HOOKS_PATH")
RUNNER_CONFIG = {"mise": ("mise.toml", ".mise.toml", "mise.*.toml", ".mise.*.toml", ".mise/config.toml",
                          ".config/mise.toml", ".config/mise/config.toml")}
HOOK_TIMEOUT = 120
MAX_COMMITS = 50


def protected_patterns(resolved: dict) -> list:
    """Repo-relative globs of the configuration Rein gates a change with."""
    patterns = ["flow.config.json"]
    patterns += list(RUNNER_CONFIG.get(str((resolved or {}).get("taskRunner") or ""), ()))
    declared = ((resolved or {}).get("gate") or {}).get("protected")
    if isinstance(declared, list):
        patterns += [p.strip() for p in declared if isinstance(p, str) and p.strip()]
    return patterns


def config_changes(changed: list, patterns: list, allowed_text: str = "") -> list:
    """The changed files that are gate configuration, minus those the task's text names."""
    out = []
    for path in changed:
        if not any(fnmatch.fnmatch(path, p) for p in patterns):
            continue
        if allowed_text and (path in allowed_text or os.path.basename(path) in allowed_text):
            continue
        out.append(path)
    return out


def _git(path: str, *args: str) -> tuple:
    try:
        proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout


def _hook_env(env=None) -> dict:
    out = dict(os.environ if env is None else env)
    for name in BYPASS_ENV:
        out.pop(name, None)
    return out


def commit_msg_failures(path: str, base: str, env=None) -> list:
    """One line per commit in `base..HEAD` (merges aside) its repo's `commit-msg` hook rejects.

    Nothing when the repo has no such hook, or when this git cannot run one (`git hook`,
    git >= 2.36)."""
    code, out = _git(path, "rev-list", "--no-merges", "--reverse", "%s..HEAD" % base)
    if code != 0:
        return []
    commits = [c for c in out.split() if c][:MAX_COMMITS]
    problems = []
    for sha in commits:
        code, message = _git(path, "log", "-1", "--format=%B", sha)
        if code != 0:
            continue
        fd, msg_file = tempfile.mkstemp(prefix="rein-commit-msg-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(message)
            try:
                proc = subprocess.run(["git", "-C", path, "hook", "run", "--ignore-missing", "commit-msg",
                                       "--", msg_file], capture_output=True, text=True,
                                      timeout=HOOK_TIMEOUT, env=_hook_env(env), stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                problems.append("commit %s: the repo's commit-msg hook did not finish in %ds"
                                % (sha[:8], HOOK_TIMEOUT))
                continue
            except OSError:
                return problems
        finally:
            try:
                os.unlink(msg_file)
            except OSError:
                pass
        said = ((proc.stderr or "") + (proc.stdout or "")).strip()
        if proc.returncode == 129 or "is not a git command" in said:
            return problems  # this git has no `git hook`: nothing can be checked
        if proc.returncode != 0:
            subject = (message.strip().splitlines() or [""])[0][:100]
            tail = " | ".join(line.strip() for line in said.splitlines()[-3:] if line.strip())
            problems.append("commit %s %r does not pass the repo's commit-msg hook (exit %d)%s; amend its "
                            "message, never bypass the hook" % (sha[:8], subject, proc.returncode,
                                                                ": " + tail[:300] if tail else ""))
    return problems


def check(path: str, base: str, resolved: dict, allowed_text: str = "", env=None) -> list:
    """Every rule the change since `base` breaks, one line each; [] when it breaks none."""
    if not base:
        return []
    try:
        changed = _affected.changed_files(path, base)
    except (RuntimeError, OSError):
        return []
    problems = ["it changes %s, the configuration Rein gates it with; revert that change (only a task "
                "that names the file may change it)" % f
                for f in config_changes(changed, protected_patterns(resolved), allowed_text)]
    return problems + commit_msg_failures(path, base, env)
