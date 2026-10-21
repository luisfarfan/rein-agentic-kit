"""What Rein checks about a finished task besides its tests: the rules an agent must not break.

A worker in a real run rewrote the `flow.config.json` it was being gated with (its test
commands and `gate.affected`), and another skipped the repo's commit-msg hook with
`LEFTHOOK_EXCLUDE`. The briefs forbid both; this module is how Rein notices anyway:

- the change touches the configuration Rein judges it with: `flow.config.json` always, the
  config of every task runner (mise, make, just, task) a gate command comes from or calls --
  as resolved at the change's base AND now, so deleting or swapping the runner is caught --
  and any glob in `gate.protected`. Only a path the task explicitly declares (`allowed`: a
  plan task's `Gate config:` field, `rein quick --allow-config`) may change; a mention in
  the task's text is not a declaration;
- a commit of the change does not pass the repo's own `commit-msg` hook, run again by Rein
  (`git hook run`, so `core.hooksPath` and lefthook/husky installs are honoured) with the
  usual bypass variables removed from its environment.

Each problem is one line saying what and why; none means the change is clean. A checkout
git cannot read is not this module's verdict to give (the gate's is): it yields nothing.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import tempfile

import detect as _detect


# Environment variables that switch a repo's hooks off (lefthook, husky, pre-commit).
BYPASS_ENV = ("LEFTHOOK", "LEFTHOOK_EXCLUDE", "LEFTHOOK_SKIP", "HUSKY", "HUSKY_SKIP_HOOKS", "SKIP",
              "PRE_COMMIT", "PRE_COMMIT_ALLOW_NO_CONFIG", "GIT_HOOKS_PATH")
# Each task runner detect knows, and the files that define its tasks.
RUNNER_CONFIG = {
    "mise": ("mise.toml", ".mise.toml", "mise.*.toml", ".mise.*.toml", ".mise/config.toml",
             ".config/mise.toml", ".config/mise/config.toml"),
    "make": ("Makefile", "makefile", "GNUmakefile", "*.mk"),
    "just": ("justfile", "Justfile", ".justfile"),
    "task": ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml", "taskfile.yaml", "Taskfile.dist.yml",
             "Taskfile.dist.yaml"),
}
GATE_COMMANDS = ("lint", "typecheck", "build", "test", "testOne", "testIntegration")
_RUNNER_CALL = re.compile(r"(?:^|[\s;&|()])(%s)(?=\s|$)" % "|".join(RUNNER_CONFIG))
# Where a runner's files can sit beyond the root: the nested mise config folders.
_NESTED_CONFIG = (".config/mise.toml", ".config/mise/config.toml", ".mise/config.toml")
HOOK_TIMEOUT = 120
MAX_COMMITS = 50


def gate_runners(resolved: dict) -> set:
    """The task runners a gate command comes from (its source) or calls (`make test`, `mise run x`)."""
    resolved = resolved or {}
    commands, sources = resolved.get("commands") or {}, resolved.get("commandSources") or {}
    found = set()
    for slot in GATE_COMMANDS:
        command = str(commands.get(slot) or "")
        if not command.strip():
            continue
        if sources.get(slot) in RUNNER_CONFIG:
            found.add(sources[slot])
        found.update(m.group(1) for m in _RUNNER_CALL.finditer(command))
    return found


def protected_patterns(*resolutions: dict) -> list:
    """Repo-relative globs of the configuration Rein gates a change with, under every resolution
    given (the change's base and its current tree)."""
    patterns = ["flow.config.json"]
    for resolved in resolutions:
        resolved = resolved or {}
        sub = str(resolved.get("subproject") or "").strip("/")
        for runner in sorted(gate_runners(resolved)):
            for name in RUNNER_CONFIG[runner]:
                patterns += [name] + (["%s/%s" % (sub, name)] if sub else [])
        declared = (resolved.get("gate") or {}).get("protected")
        if isinstance(declared, list):
            patterns += [p.strip() for p in declared if isinstance(p, str) and p.strip()]
    return list(dict.fromkeys(patterns))


def resolve_at(path: str, base: str):
    """`detect.resolve` of the tree at the merge base of `base` and HEAD: its root files (and the
    nested mise configs and the chosen sub-project's files) written to a temp dir. None when git
    cannot say."""
    code, sha = _git(path, "merge-base", base, "HEAD")
    sha = sha.strip()
    if code != 0 or not sha:
        return None
    code, listing = _git(path, "ls-tree", sha)
    if code != 0:
        return None
    names = [line.split("\t", 1)[1] for line in listing.splitlines()
             if "\t" in line and line.split()[1] == "blob"]
    names += list(_NESTED_CONFIG)
    tmp = tempfile.mkdtemp(prefix="rein-guard-base-")
    try:
        for name in names:
            _extract(path, sha, name, tmp)
        sub = str((_detect.resolve(tmp).get("subproject") or "")).strip("/")
        if sub:
            code, listing = _git(path, "ls-tree", "--name-only", sha, sub + "/")
            for name in listing.splitlines() if code == 0 else []:
                _extract(path, sha, name, tmp)
        return _detect.resolve(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _extract(path: str, sha: str, name: str, into: str) -> None:
    try:
        proc = subprocess.run(["git", "-C", path, "show", "%s:%s" % (sha, name)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return
    if proc.returncode != 0:
        return
    target = os.path.join(into, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(proc.stdout)


def config_changes(changed: list, patterns: list, allowed=()) -> list:
    """The changed files that are gate configuration, minus those `allowed` declares: whole
    repo-relative paths or globs, never a substring of a sentence."""
    out = []
    for path in changed:
        if not any(fnmatch.fnmatch(path, p) for p in patterns):
            continue
        if any(a and fnmatch.fnmatch(path, a) for a in allowed or ()):
            continue
        out.append(path)
    return out


def declared_paths(value) -> list:
    """`flow.config.json, mise.toml` (a `Gate config:` field, an --allow-config value) -> paths."""
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = re.split(r"[,\s]+", str(value or ""))
    out = []
    for item in items:
        item = item.strip().strip("`")
        item = item[2:] if item.startswith("./") else item
        if item:
            out.append(item)
    return out


def _git(path: str, *args: str) -> tuple:
    try:
        proc = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout


def changed_paths(path: str, base: str) -> list:
    """Every path the change since `base` touches -- commits (`base...HEAD`), staged and unstaged
    edits, new files -- with rename detection OFF, so a rename lists both its ends: renaming
    flow.config.json away is a change to flow.config.json. RuntimeError when git cannot say."""
    found = set()
    for args in (("diff", "--name-only", "--no-renames", "%s...HEAD" % base),
                 ("diff", "--name-only", "--no-renames", "HEAD"),
                 ("ls-files", "--others", "--exclude-standard")):
        code, out = _git(path, *args)
        if code != 0:
            raise RuntimeError("git %s failed in %s" % (args[0], path))
        found.update(line for line in out.splitlines() if line.strip())
    return sorted(f for f in found if not f.startswith(".rein/"))


def _hook_env(env=None) -> dict:
    out = dict(os.environ if env is None else env)
    for name in BYPASS_ENV:
        out.pop(name, None)
    return out


def _git_runs_hooks(path: str) -> bool:
    """This git has `git hook run` (git >= 2.36): asked on its own, never read off a hook's exit."""
    try:
        proc = subprocess.run(["git", "-C", path, "hook", "-h"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return "git hook run" in (proc.stdout or "") + (proc.stderr or "")  # usage, not "not a git command"


def _hook_installed(path: str, name: str) -> bool:
    """A `name` hook exists for the checkout (core.hooksPath, else the git dir's hooks)."""
    code, configured = _git(path, "config", "core.hooksPath")
    if code == 0 and configured.strip():
        folder = os.path.join(path, os.path.expanduser(configured.strip()))
    else:
        code, folder = _git(path, "rev-parse", "--git-path", "hooks")
        folder = os.path.join(path, folder.strip()) if code == 0 else ""
    hook = os.path.join(folder, name) if folder else ""
    return bool(hook) and os.path.isfile(hook) and os.access(hook, os.X_OK)


def commit_msg_failures(path: str, base: str, env=None) -> list:
    """One line per commit in `base..HEAD` (merges aside) its repo's `commit-msg` hook rejects.

    Nothing when the repo has no such hook. A git that cannot run hooks (`git hook`, git >= 2.36)
    while one is installed is a problem of its own: the messages cannot be checked."""
    code, out = _git(path, "rev-list", "--no-merges", "--reverse", "%s..HEAD" % base)
    if code != 0:
        return []
    if not _git_runs_hooks(path):
        if out.split() and _hook_installed(path, "commit-msg"):
            return ["the repo has a commit-msg hook but this git cannot run it again (`git hook` needs "
                    "git >= 2.36), so the change's commit messages are unchecked; upgrade git"]
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
        if proc.returncode != 0:  # the hook ran (git can run hooks): every non-zero exit rejects
            subject = (message.strip().splitlines() or [""])[0][:100]
            tail = " | ".join(line.strip() for line in said.splitlines()[-3:] if line.strip())
            problems.append("commit %s %r does not pass the repo's commit-msg hook (exit %d)%s; amend its "
                            "message, never bypass the hook" % (sha[:8], subject, proc.returncode,
                                                                ": " + tail[:300] if tail else ""))
    return problems


def check(path: str, base: str, resolved: dict, allowed=(), env=None) -> list:
    """Every rule the change since `base` breaks, one line each; [] when it breaks none."""
    if not base:
        return []
    try:
        changed = changed_paths(path, base)
    except (RuntimeError, OSError):
        return []
    patterns = protected_patterns(resolved, resolve_at(path, base) or {})
    problems = ["it changes %s, the configuration Rein gates it with; revert that change (only a task "
                "that declares the file -- a plan task's `Gate config:` field, `rein quick "
                "--allow-config` -- may change it)" % f
                for f in config_changes(changed, patterns, allowed)]
    return problems + commit_msg_failures(path, base, env)
