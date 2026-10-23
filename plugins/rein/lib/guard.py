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

- the worker wrote outside its worktree: `outside_snapshot` notes the git state of every other
  checkout Orca knows when the worker is launched, and `outside_changes` names what changed
  in them by the time its task ends. This is detection, not prevention: a sandbox the agent
  cannot write out of is the agent's (or Orca's) to provide. `gate.outsideIgnore` (globs relative
  to each watched repo, from its own config or the plan root's, read at launch) leaves out what
  the repo's own git hooks rewrite there -- beads re-exports `.beads/` in the main checkout on
  every commit or checkout made in a worktree.

Each problem is one line saying what and why; none means the change is clean.

The guard FAILS CLOSED. Whatever it cannot establish -- git failing, the base's config not
extracted or written, a file that cannot be read, a snapshot it cannot use -- raises
`GuardError` (callers go through `run_guard`, which makes it "guard could not run", exit 126),
never "no change": an incomplete look is not a clean one.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import hashlib
import tempfile

import detect as _detect


class GuardError(RuntimeError):
    """The guard could not establish a fact it needs: the environment's problem, never a pass."""


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
HOOK_TIMEOUT = 120
MAX_COMMITS = 200  # hook runs per check; past it the newest are checked and the rest is reported


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


def protected_patterns(*resolutions: dict, inherited=()) -> list:
    """Repo-relative globs of the configuration Rein gates a change with, under every resolution
    given (the change's base and its current tree), plus `inherited`: the `gate.protected` globs
    the plan root declares for every repo of the change."""
    patterns = ["flow.config.json"]
    patterns += [p.strip() for p in inherited or () if isinstance(p, str) and p.strip()]
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


def declared_protected(resolved) -> list:
    """The `gate.protected` globs a resolved config declares ([] when none or malformed)."""
    declared = ((resolved or {}).get("gate") or {}).get("protected")
    if not isinstance(declared, list):
        return []
    return [p.strip() for p in declared if isinstance(p, str) and p.strip()]


def resolve_at(path: str, base: str):
    """`detect.resolve` of the tree at the merge base of `base` and HEAD: its root files (and the
    nested mise configs and the chosen sub-project's files) written to a temp dir. GuardError when
    any of it cannot be done: a partial base would protect less than the real one."""
    code, sha = _git(path, "merge-base", base, "HEAD")
    sha = sha.strip()
    if code != 0 or not sha:
        raise GuardError("no merge base of %s and HEAD in %s, so the base's config is unknown" % (base, path))
    code, listing = _git(path, "ls-tree", "-r", "--name-only", sha)
    if code != 0:
        raise GuardError("cannot list the base tree %s in %s" % (sha[:12], path))
    # What detect reads to resolve the commands: every root file, and the manifests, lockfiles and
    # runner configs a few levels down -- a sub-project must be visible BEFORE detect picks it.
    names = [n for n in listing.splitlines() if n and ("/" not in n or _resolution_input(n))]
    tmp = tempfile.mkdtemp(prefix="rein-guard-base-")
    try:
        for name in names:
            _extract(path, sha, name, tmp)
        sub = str((_resolve_base(tmp).get("subproject") or "")).strip("/")
        if sub:
            # The sub-project's own files: blobs only (a tree is a folder, never a file to write).
            code, listing = _git(path, "ls-tree", sha, sub + "/")
            if code != 0:
                raise GuardError("cannot list the base's sub-project %s in %s" % (sub, path))
            for line in listing.splitlines():
                meta, _, name = line.partition("\t")
                if name and meta.split()[1:2] == ["blob"]:
                    _extract(path, sha, name, tmp)
        return _resolve_base(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _resolve_base(tmp: str) -> dict:
    """detect, strictly: an extracted input that cannot be read or parsed fails closed (N3)."""
    try:
        return _detect.resolve_strict(tmp)
    except _detect.DetectReadError as exc:
        raise GuardError("cannot read the base's config: %s" % str(exc).replace(tmp + os.sep, ""))


_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Cargo.toml", "go.mod", "package.json",
              "package-lock.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "yarn.lock", "bun.lockb", "bun.lock",
              "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock", "flow.config.json", "angular.json",
              "nx.json", "turbo.json", "deno.json", "Gemfile", "pom.xml", "build.gradle")
_RESOLUTION_DEPTH = 4  # path components: `apps/web/.config/mise.toml`


def _resolution_input(name: str) -> bool:
    """A nested file detect may need to resolve the commands: a manifest, a lockfile or a runner config."""
    parts = name.split("/")
    if len(parts) > _RESOLUTION_DEPTH:
        return False
    base = parts[-1]
    if base in _MANIFESTS:
        return True
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, "*/" + p)
               for files in RUNNER_CONFIG.values() for p in files)


def _extract(path: str, sha: str, name: str, into: str) -> None:
    """Write the base's blob `name` under `into`; GuardError when it cannot be read or written."""
    try:
        proc = subprocess.run(["git", "-C", path, "show", "%s:%s" % (sha, name)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError("cannot read %s from the base %s: %s" % (name, sha[:12], exc))
    if proc.returncode != 0:
        raise GuardError("cannot read %s from the base %s: %s" % (
            name, sha[:12], (proc.stderr or b"").decode("utf-8", "replace").strip()[:200] or "git show failed"))
    target = os.path.join(into, name)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:  # a folder here (never a blob's path) raises too
            fh.write(proc.stdout)
    except OSError as exc:
        raise GuardError("cannot rebuild the base's %s: %s" % (name, exc))


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
    code, configured = _git(path, "config", "core.hooksPath")  # exit 1 = unset, not an error
    if code == 0 and configured.strip():
        folder = os.path.join(path, os.path.expanduser(configured.strip()))
    else:
        code, folder = _git(path, "rev-parse", "--git-path", "hooks")
        if code != 0 or not folder.strip():
            raise GuardError("cannot find the hooks folder of %s" % path)
        folder = os.path.join(path, folder.strip())
    hook = os.path.join(folder, name)
    return os.path.isfile(hook) and os.access(hook, os.X_OK)


def _has_commit_msg_hook(path: str) -> bool:
    """A commit-msg hook is configured: a hook file, or (newer git) a config-defined hook."""
    if _hook_installed(path, "commit-msg"):
        return True
    code, out = _git(path, "hook", "list", "commit-msg")
    return code == 0 and bool(out.strip())


def commit_msg_failures(path: str, base: str, env=None) -> list:
    """One line per commit in `base..HEAD` (merges aside) its repo's `commit-msg` hook rejects.

    Nothing when the repo has no such hook. A git that cannot run hooks (`git hook`, git >= 2.36)
    while one is installed is a problem of its own: the messages cannot be checked."""
    code, out = _git(path, "rev-list", "--no-merges", "%s..HEAD" % base)  # newest first
    if code != 0:
        raise GuardError("cannot list the commits since %s in %s" % (base, path))
    if not _git_runs_hooks(path):
        if out.split() and _hook_installed(path, "commit-msg"):
            return ["the repo has a commit-msg hook but this git cannot run it again (`git hook` needs "
                    "git >= 2.36), so the change's commit messages are unchecked; upgrade git"]
        return []
    every = [c for c in out.split() if c]
    commits = every[:MAX_COMMITS]
    problems = []
    if len(every) > len(commits) and _has_commit_msg_hook(path):
        problems.append("the change has %d commits; only the newest %d were run through the commit-msg hook, "
                        "%d were not checked: squash them, or check them by hand"
                        % (len(every), len(commits), len(every) - len(commits)))
    for sha in commits:
        code, message = _git(path, "log", "-1", "--format=%B", sha)
        if code != 0:
            raise GuardError("cannot read the message of commit %s in %s" % (sha[:8], path))
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
            except OSError as exc:
                raise GuardError("cannot run the commit-msg hook in %s: %s" % (path, exc))
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


MAX_PRE_COMMIT = 25  # pre-commit re-runs per check (each one is a temporary checkout and a full hook run)


def _has_hook(path: str, name: str) -> bool:
    """A `name` hook is configured: a hook file, or (newer git) a config-defined hook."""
    if _hook_installed(path, name):
        return True
    code, out = _git(path, "hook", "list", name)
    return code == 0 and bool(out.strip())


def _clean_git_env(env=None) -> dict:
    out = _hook_env(env)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR"):
        out.pop(name, None)
    return out


def _git_or_raise(path: str, *args: str) -> str:
    code, out = _git(path, *args)
    if code != 0:
        raise GuardError("git %s failed in %s" % (" ".join(args[:2]), path))
    return out


# What a hook prints when it could not run (a missing tool or dependency, the network), as opposed to
# when it ran and rejected the commit. A heuristic, said as such: the exits 126/127 always count.
_ENV_FAILURE = re.compile(
    r"command not found|: not found|No such file or directory|ENOENT|Cannot find (?:module|package)|"
    r"ModuleNotFoundError|No module named|Command \"[^\"]+\" not found|ERR_PNPM_[A-Z_]*(?:NOT_FOUND|EXEC_FIRST_FAIL|FETCH|NO_MATCHING)|"
    r"Failed to (?:download|install|fetch|resolve)|error sending request|Temporary failure in name resolution|"
    r"Connection (?:refused|timed out|reset)|ECONNREFUSED|ETIMEDOUT|getaddrinfo", re.I)
_RELEVANT = re.compile(r"error|fail|\u2717|\u2716|not found|cannot|denied|forbidden|\bE\d{3}\b|^\s*\S+:\d+(?::\d+)?\b", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _relevant_lines(said: str, limit: int = 10, chars: int = 900) -> str:
    """The lines of a hook's output that say why it failed (not its banner and summary), or the last
    few when none does: ` | `-joined."""
    lines = [_ANSI.sub("", l).strip() for l in said.splitlines()]
    lines = [l for l in lines if l]
    picked = [l for l in lines if _RELEVANT.search(l)] or lines[-6:]
    return " | ".join(picked[:limit])[:chars]


def _link_dependencies(source: str, into: str) -> None:
    """Make the temporary checkout see what the worker's worktree has but git does not track -- the
    dependency folders (node_modules, .venv, ...) and other ignored directories -- by symlink, so a hook
    that needs them runs as it does there. Nothing is copied and nothing in `source` is changed by the
    linking; a hook that writes into a linked folder (a cache) writes to the original."""
    code, out = _git(source, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    if code != 0:
        raise GuardError("cannot list the ignored folders of %s" % source)
    for line in out.splitlines():
        if not line.endswith("/"):
            continue
        rel = line.rstrip("/")
        if rel in (".rein", ".git") or rel.startswith((".rein/", ".git/")):
            continue
        target, link = os.path.join(source, rel), os.path.join(into, rel)
        if os.path.lexists(link) or not os.path.isdir(target):
            continue
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(os.path.realpath(target), link)


def _hook_log(said: str) -> str:
    fd, path = tempfile.mkstemp(prefix="rein-pre-commit-log-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(said)
    return path


def _rerun_pre_commit(path: str, sha: str, env=None) -> tuple:
    """Run the repo's `pre-commit` hook as `sha` would have met it: a temporary detached checkout of its
    parent with `sha`'s tree staged on top and the worker's ignored dependency folders linked in (the
    worker's worktree is never modified). `("pass", "")`, `("red", why)` when the hook ran on the tree
    and rejected it or changed it (an autofix: the commit as made did not pass), `("unchecked", why)`
    when it could not be run reliably (no parent, timeout, a tool or dependency missing, the network).
    GuardError when the checkout cannot be set up at all."""
    parts = _git_or_raise(path, "rev-list", "--parents", "-n", "1", sha).split()
    if len(parts) < 2:
        return "unchecked", "it has no parent, so its pre-commit hook cannot be re-run on what it staged"
    tmp = tempfile.mkdtemp(prefix="rein-pre-commit-")
    work = os.path.join(tmp, "wt")
    try:
        # core.hooksPath off for the checkout itself: post-checkout hooks are not what is being checked.
        code, _ = _git(path, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", "--quiet",
                       work, parts[1])
        if code != 0:
            raise GuardError("cannot make a temporary checkout of %s in %s" % (parts[1][:8], path))
        _git_or_raise(work, "read-tree", "-u", "--reset", sha)
        _link_dependencies(path, work)
        try:
            proc = subprocess.run(["git", "-C", work, "hook", "run", "--ignore-missing", "pre-commit"],
                                  capture_output=True, text=True, timeout=HOOK_TIMEOUT,
                                  env=_clean_git_env(env), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return "unchecked", "the pre-commit hook did not finish in %ds" % HOOK_TIMEOUT
        except OSError as exc:
            raise GuardError("cannot run the pre-commit hook in %s: %s" % (path, exc))
        said = ((proc.stderr or "") + (proc.stdout or "")).strip()
        if proc.returncode != 0:
            log = _hook_log(said)
            if proc.returncode in (126, 127) or _ENV_FAILURE.search(said):
                return "unchecked", ("the pre-commit hook could not run in the re-check (exit %d: %s); "
                                     "full output: %s" % (proc.returncode, _relevant_lines(said), log))
            return "red", ("does not pass the repo's pre-commit hook (exit %d): %s; full output: %s"
                           % (proc.returncode, _relevant_lines(said), log))
        # Exit 0 is only the commit's verdict if the hook left the tree as it found it: an autofix
        # (lint-staged, `stage_fixed`) that rewrote it passed its own repair, not the commit.
        _, staged = _git(work, "diff", "--cached", "--name-only", sha)
        _, loose = _git(work, "diff", "--name-only")
        changed = sorted(set(staged.split()) | set(loose.split()))
        if changed:
            _, diff = _git(work, "diff", sha)
            return "red", ("passes its pre-commit hook only after the hook rewrites %s -- the commit as made "
                           "does not; amend it with the hook's fix: %s"
                           % (", ".join(changed[:5]), " ".join(diff.split("\n")[:20])[:700]))
        return "pass", ""
    finally:
        _git(path, "worktree", "remove", "--force", work)
        shutil.rmtree(tmp, ignore_errors=True)
        _git(path, "worktree", "prune")


def pre_commit_failures(path: str, base: str, env=None, accept_hooks=(), unchecked=None, accepted=None) -> list:
    """One line per commit in `base..HEAD` (merges aside) whose `pre-commit` hook rejects it -- or
    rewrites it -- when Rein runs the hook again on what that commit staged: the hook a
    `git commit --no-verify` skips. Nothing when the repo has no such hook.

    What cannot be re-run reliably is not a red: it goes to `unchecked` as `{"sha", "text"}` when the
    caller passes a list (quick and ship then hold for the owner's explicit answer, never green), else it
    is a problem (fail closed). `accept_hooks` (sha prefixes, 7+) is the owner's exit for exactly those
    commits; each use is appended to `accepted` for the log. Never applies to a red."""
    code, out = _git(path, "rev-list", "--no-merges", "%s..HEAD" % base)  # newest first
    if code != 0:
        raise GuardError("cannot list the commits since %s in %s" % (base, path))
    every = [c for c in out.split() if c]
    if not every or not _has_hook(path, "pre-commit"):
        return []
    problems = []
    wanted = [a.lower() for a in accept_hooks or ()]

    def doubt(sha, why):
        if any(sha.startswith(w) for w in wanted):
            if accepted is not None:
                accepted.append({"kind": "hook", "id": sha, "checkout": path, "note": why[:200]})
            return
        text = "commit %s: %s" % (sha[:8], why)
        if unchecked is None:
            problems.append(text + " (unchecked)")
        else:
            unchecked.append({"sha": sha, "text": text})

    if not _git_runs_hooks(path):
        for sha in every:
            doubt(sha, "the repo has a pre-commit hook but this git cannot run it again (`git hook` needs "
                       "git >= 2.36)")
        return problems
    commits = every[:MAX_PRE_COMMIT]
    for sha in every[MAX_PRE_COMMIT:]:
        doubt(sha, "past the newest %d commits its pre-commit hook is not re-run" % MAX_PRE_COMMIT)
    for sha in commits:
        verdict, why = _rerun_pre_commit(path, sha, env)
        if verdict == "red":
            subject = (_git(path, "log", "-1", "--format=%s", sha)[1].strip() or "")[:100]
            problems.append("commit %s %r %s" % (sha[:8], subject, why))
        elif verdict == "unchecked":
            doubt(sha, why)
    return problems


def unmatched_hook_accepts(accept_hooks, accepted) -> list:
    """The `--accept-hook` the owner named that matched no commit whose hook was unchecked."""
    ids = [a.get("id", "") for a in accepted or () if a.get("kind") == "hook"]
    return ["--accept-hook %s is not a commit whose pre-commit hook was unchecked" % h
            for h in accept_hooks or () if not any(i.startswith(h.lower()) for i in ids)]


OUTSIDE_PATH_CAP = 5000  # watched (not ignored) dirty or untracked paths per checkout; past it, GuardError


def _dirty_paths(path: str) -> list:
    """Repo-relative paths git reports changed or untracked (renames as both ends), Rein's `.rein/` aside."""
    code, out = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if code != 0:
        raise GuardError("cannot read the git status of %s" % path)
    found = [entry[3:] for entry in out.split("\0") if len(entry) > 3]
    return [p for p in found if p and not (p == ".rein" or p.startswith(".rein/"))]


def _fingerprint(path: str, rel: str) -> str:
    """What `rel` is now: a content digest, "missing", or "dir"; GuardError when it exists but
    cannot be read (two unreadable states would otherwise compare equal)."""
    full = os.path.join(path, rel)
    if not os.path.lexists(full):
        return "missing"
    if os.path.isdir(full) and not os.path.islink(full):
        return "dir"
    try:
        if os.path.islink(full):
            return "link:" + os.readlink(full)
        digest = hashlib.sha1()
        with open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise GuardError("cannot read %s: %s" % (full, exc))


def outside_ignore_globs(resolved) -> list:
    """`gate.outsideIgnore` of a resolved (or raw) flow config: repo-relative globs, `dir/` for a folder.
    Anything malformed ignores nothing."""
    gate = resolved.get("gate") if isinstance(resolved, dict) else None
    declared = gate.get("outsideIgnore") if isinstance(gate, dict) else None
    if not isinstance(declared, list):
        return []
    return [p.strip() for p in declared if isinstance(p, str) and p.strip()]


def _repo_outside_ignore(path: str) -> list:
    """The checkout's own `gate.outsideIgnore`. A config that cannot be read ignores nothing: stricter."""
    try:
        with open(os.path.join(path, _detect.CONFIG_NAME), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    return outside_ignore_globs(cfg) if isinstance(cfg, dict) else []


def _outside_ignored(rel: str, globs) -> bool:
    """`rel` is exactly what one of `globs` declares, nothing more. `dir/` is what that folder
    holds: its leading components match the folder's, one by one (a `*` there never crosses a
    `/`), and a file named `dir` is not in it. Any other glob matches the whole path, and never a descendant of what it matches."""
    parts = rel.split("/")
    for glob in globs or ():
        if not isinstance(glob, str) or not glob:
            continue
        if glob.endswith("/"):
            folder = glob.rstrip("/").split("/")
            if folder != [""] and len(parts) > len(folder) and all(
                    fnmatch.fnmatch(p, f) for p, f in zip(parts, folder)):
                return True
        elif fnmatch.fnmatch(rel, glob):
            return True
    return False


def _watched_paths(path: str, globs) -> list:
    """The dirty or untracked paths of `path` that `globs` do not ignore. GuardError past
    OUTSIDE_PATH_CAP: a look that cannot cover them all is not a clean one, and ignored paths
    never use the cap up."""
    watched = [rel for rel in _dirty_paths(path) if not _outside_ignored(rel, globs)]
    if len(watched) > OUTSIDE_PATH_CAP:
        raise GuardError("%s has %d changed or untracked paths outside gate.outsideIgnore, more than the %d "
                         "Rein can check" % (path, len(watched), OUTSIDE_PATH_CAP))
    return watched


def _ref_tips(path: str) -> list:
    """The commit every branch, remote-tracking ref and tag points at now: what existed before the
    worker started. A commit reachable from one of them was not made by the worker."""
    code, out = _git(path, "for-each-ref", "--format=%(objectname)")
    if code != 0:
        raise GuardError("cannot list the refs of %s" % path)
    return sorted(set(line.strip() for line in out.splitlines() if line.strip()))


def outside_watch_names(resolved) -> list:
    """`gate.outsideWatch` of a resolved (or raw) flow config: the other repos (an Orca repo name, or a
    directory) whose checkouts a task in this repo also watches. Anything malformed declares none."""
    gate = resolved.get("gate") if isinstance(resolved, dict) else None
    declared = gate.get("outsideWatch") if isinstance(gate, dict) else None
    if not isinstance(declared, list):
        return []
    return [n.strip() for n in declared if isinstance(n, str) and n.strip()]


def git_toplevel(path: str) -> str:
    """The top of the git checkout `path` is in, or "" when it is in none."""
    if not path or not os.path.isdir(path):
        return ""
    code, out = _git(path, "rev-parse", "--show-toplevel")
    return os.path.realpath(out.strip()) if code == 0 and out.strip() else ""


def entry_id(path: str) -> str:
    """The identity of a git entry named by `path`: its directory resolved, its last component not.
    A symlink is its own entry (git tracks the link, not its target): `A -> t` and `B -> t` are two."""
    path = os.path.abspath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _raw_config(directory: str) -> dict:
    try:
        with open(os.path.join(directory, _detect.CONFIG_NAME), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def watched_checkouts(repos, names, config_dirs=()) -> list:
    """The checkouts a task watches: those of the repos `names` (the task's own: a quick's repo; a
    plan's repos) plus the siblings declared by `gate.outsideWatch` in the flow config of each of
    `config_dirs` (read as written, like `gate.outsideIgnore`). `repos` is Orca's repo list (dicts with `displayName`/`path`) or a
    `{name: dict}` map. Every other checkout Orca knows -- another product's, the tool's own -- is
    someone else's work: watching it only blames the task for sessions that never touched it. A
    declared sibling that Orca does not know and is not a directory is left out."""
    listed = list(repos.values()) if isinstance(repos, dict) else list(repos or ())
    by_name = {str(r.get("displayName")): str(r.get("path") or "") for r in listed
               if isinstance(r, dict) and r.get("displayName") and r.get("path")}
    wanted = [n for n in names or () if n]
    for directory in config_dirs or ():
        if directory and os.path.isdir(directory):
            wanted += outside_watch_names(_raw_config(directory))
    paths = []
    for name in wanted:
        path = by_name.get(name) or (name if os.path.isabs(name) and os.path.isdir(name) else "")
        if path and path not in paths:
            paths.append(path)
    return paths


def outside_snapshot(paths, ignore_globs=(), ignore_for=None) -> dict:
    """`{realpath: {head, paths: {rel: fingerprint}, ignore: [glob]}}` of the git checkouts in
    `paths`: HEAD, and the content of every path that is dirty or untracked there (a clean tracked
    path is HEAD's). `ignore` is the checkout's own `gate.outsideIgnore` plus `ignore_globs` (the
    plan root's), fixed now: a worker that edits a config later widens nothing. `ignore_for`
    (`{realpath: globs}`) gives a checkout exactly those globs instead -- the ones frozen when the task
    started -- so a later snapshot never takes one repo's ignores for another's, nor widens them.

    A path that does not exist has nothing to protect and is left out. One that exists but cannot
    be read is kept as `{"error": why}`: taken at launch, the snapshot must not raise (nothing is
    decided yet), and `outside_changes` fails closed on it at the end."""
    snap = {}
    for path in paths or ():
        if not path or not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        try:
            code, head = _git(path, "rev-parse", "HEAD")
            if code != 0:
                raise GuardError("git cannot read %s" % path)
            if ignore_for is not None and real in ignore_for:
                ignore = list(ignore_for[real])
            else:
                ignore = list(dict.fromkeys(_repo_outside_ignore(path) + list(ignore_globs or ())))
            snap[real] = {"head": head.strip(), "refs": _ref_tips(path), "paths": {
                rel: _fingerprint(path, rel) for rel in _watched_paths(path, ignore)}}
            if ignore:
                snap[real]["ignore"] = ignore
        except GuardError as exc:
            snap[real] = {"error": str(exc)}
    return snap


def _own_emails(path: str) -> set:
    """The identity commits made in this checkout carry (`user.email`); empty when unset."""
    code, out = _git(path, "config", "user.email")
    return {out.strip().lower()} if code == 0 and out.strip() else set()


def _created_here(path: str) -> set:
    """The commits made in this checkout with `git commit` (its HEAD reflog), which a fast-forward,
    a pull or a merge from elsewhere does not list."""
    code, out = _git(path, "reflog", "show", "--format=%H%x09%gs", "HEAD")
    if code != 0:
        return set()
    made = set()
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        if sha and subject.startswith("commit"):
            made.add(sha)
    return made


def _accepts_commit(sha: str, accept) -> bool:
    return any(len(c) >= 7 and sha.startswith(c.lower()) for c in (accept or {}).get("commits") or ())


def _head_moves(path: str, was: dict, head: str, accept=None, accepted=None) -> tuple:
    """`(problems, doubts, unresolved)` for HEAD having moved from the launch's to `head`.

    The commits looked at are all those reachable from `head` that did not exist at launch (not
    reachable from the launch HEAD nor any ref the snapshot saw), whether or not HEAD still descends
    from the launch's: an amend or a reset is judged by the commits it made. Each is:
    - the worker's, a failure: made in this checkout (its reflog) under the checkout's own identity;
    - unresolved, which a caller must not turn green: made in this checkout by another identity (it
      may have swept in a pending edit of the worker's, and a commit hides the file from the content
      comparison -- unless every file it touches was dirty at launch and so is compared anyway). The
      owner resolves it explicitly;
    - doubtful, a warning: arrived by a merge, pull or fast-forward from elsewhere with this identity;
    - not the worker's, silent: arrived by a merge, pull or fast-forward, written by someone else.
    HEAD ending on history that does not contain the launch's is a doubt of its own. Work that
    existed at launch is never new, whoever moved HEAD to it."""
    old = str(was.get("head") or "")
    doubts = []
    code, _ = _git(path, "merge-base", "--is-ancestor", old, head)
    if code != 0:
        doubts.append("HEAD moved from %s to %s, which does not contain it (a checkout, reset, amend or "
                      "rebase), so whose it is cannot be told" % (old[:8], head[:8]))
    code, out = _git(path, "rev-list", "--reverse", head, "--not", old, *(was.get("refs") or []))
    if code != 0:
        raise GuardError("cannot list the commits in %s since %s" % (path, old[:8]))
    own, made = _own_emails(path), _created_here(path)
    dirty_at_launch = set((was.get("paths") or {}))
    mine, swept, arrived = [], [], []
    for sha in [c for c in out.split() if c]:
        if _accepts_commit(sha, accept):  # the owner named this very commit
            code, subject = _git(path, "log", "-1", "--format=%s", sha)
            if accepted is not None:
                accepted.append({"kind": "commit", "id": sha, "checkout": path,
                                 "note": subject.strip()[:80] if code == 0 else ""})
            continue
        code, ident = _git(path, "log", "-1", "--format=%ae%n%ce", sha)
        if code != 0:
            raise GuardError("cannot read commit %s in %s" % (sha[:8], path))
        emails = {e.strip().lower() for e in ident.splitlines() if e.strip()}
        foreign = bool(own and emails and not (emails & own))
        if sha in made:
            if not foreign:
                mine.append(sha)
                continue
            code, names = _git(path, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
            if code != 0:
                raise GuardError("cannot read commit %s in %s" % (sha[:8], path))
            if not {n for n in names.splitlines() if n} <= dirty_at_launch:
                swept.append(sha)
        elif not foreign:
            arrived.append(sha)
    problems, unresolved = [], []
    if mine:
        problems.append("new commit(s) (HEAD %s -> %s): %s" % (old[:8], head[:8], " ".join(c[:12] for c in mine[:10])))
    if swept:
        unresolved.append("%d commit(s) by another identity were made in this checkout (HEAD %s -> %s); they "
                          "may include edits the worker left pending there, so whose they are cannot be told"
                          % (len(swept), old[:8], head[:8]))
    if arrived:
        doubts.append("%d commit(s) that did not exist at launch arrived by a merge, pull or fast-forward "
                      "(HEAD %s -> %s), so whose they are cannot be told" % (len(arrived), old[:8], head[:8]))
    return problems, doubts, unresolved


def outside_changes(before: dict, ignore=(), warnings=None, unresolved=None, accept=None, accepted=None) -> list:
    """One problem per checkout of `before` that changed since: every path whose content or
    existence differs from what it was at launch -- edited, created or deleted, untracked ones
    included -- and the commits the worker could have made there (`_head_moves`).

    `ignore`: absolute paths Rein itself writes there (the plan file it ticks); they are left out of
    the comparison itself, so they never make another path look changed. So are the paths matching
    the checkout's `ignore` globs from the snapshot (`gate.outsideIgnore`).

    What cannot be attributed to the worker but could hide its write goes to `unresolved` when the
    caller passes a list for it (ship and quick then hold the task for the owner's explicit answer:
    never green); what is only odd goes to `warnings` (told, not held). With no list for either it is
    a problem: the guard fails closed.

    `accept` is the owner's explicit exit for a change that really happened and is not the task's:
    `{"paths": [absolute path, ...], "commits": [sha (7+ chars), ...]}`. Only a path that differs now
    and a commit that is new now match; each match is left out of the result and appended to `accepted`
    as `{"kind", "id", "checkout", ...}` (a path with its fingerprint) for the log. Anything the owner
    names that matches nothing stays a thing the caller reports (`unmatched_accepts`)."""
    skip = {os.path.realpath(p) for p in ignore or ()}
    problems = []
    if not isinstance(before, dict):
        raise GuardError("the launch snapshot of the other checkouts is unreadable")
    for path, was in sorted(before.items()):
        if not isinstance(was, dict) or (not was.get("error") and not isinstance(was.get("paths"), dict)):
            raise GuardError("the launch snapshot of %s is unreadable" % path)
        if was.get("error"):
            raise GuardError("%s could not be read when the worker was launched (%s), so what it wrote there "
                             "cannot be known" % (path, was["error"]))
        if not os.path.isdir(path):
            problems.append("it wrote outside its worktree: the checkout %s no longer exists" % path)
            continue
        code, head = _git(path, "rev-parse", "HEAD")
        if code != 0:
            raise GuardError("git cannot read %s" % path)
        old = was["paths"]
        globs = was.get("ignore") if isinstance(was.get("ignore"), list) else []
        now = {rel: _fingerprint(path, rel) for rel in _watched_paths(path, globs)}
        for rel in old:  # clean now (committed, or restored): what matters is the content it has
            if rel not in now and not _outside_ignored(rel, globs):
                now[rel] = _fingerprint(path, rel)
        changed = [rel for rel in sorted(set(old) | set(now))
                   if os.path.realpath(os.path.join(path, rel)) not in skip
                   and not _outside_ignored(rel, globs)
                   and old.get(rel, "clean") != now.get(rel, "clean")]
        what, doubts, held = [], [], []
        agreed = {entry_id(p) for p in (accept or {}).get("paths") or ()}
        if agreed:
            for rel in list(changed):
                full = os.path.join(path, rel)  # `path` is the checkout's real path; rel is git's own entry
                if full in agreed:
                    changed.remove(rel)
                    if accepted is not None:
                        accepted.append({"kind": "path", "id": full, "checkout": path,
                                         "fingerprint": now.get(rel, "clean")})
        if head.strip() != was.get("head"):
            if isinstance(was.get("refs"), list):
                moved, doubts, held = _head_moves(path, was, head.strip(), accept, accepted)
                what += moved
            else:  # a snapshot without the refs cannot attribute anything: as strict as it was
                what.append("new commit(s) (HEAD %s -> %s)" % (str(was.get("head"))[:8], head.strip()[:8]))
        if changed:
            what.append("changed: %s" % ", ".join(changed[:10]) + (" ..." if len(changed) > 10 else ""))
        if what:
            problems.append("it wrote outside its worktree, in %s: %s; a worker may only write inside its "
                            "worktree -- revert that (if you changed it yourself meanwhile, retry)"
                            % (path, "; ".join(what)))
        for doubt in held:
            line = "in %s: %s" % (path, doubt)
            if unresolved is None:
                problems.append("it may have written outside its worktree, " + line)
            else:
                unresolved.append(line)
        for doubt in doubts:
            line = "in %s: %s" % (path, doubt)
            if warnings is None:
                problems.append("it may have written outside its worktree, " + line)
            else:
                warnings.append(line)
    return problems


def unmatched_accepts(accept, accepted) -> list:
    """What the owner named in `accept` that `outside_changes` did not find among the changes: an
    acceptance that vouches for nothing is a mistake to say, never a quiet pass."""
    left = []
    ids = {a.get("id", "") for a in accepted or ()}
    for p in (accept or {}).get("paths") or ():
        if entry_id(p) not in ids:
            left.append("--accept-path %s is not a path that changed in the watched checkouts" % p)
    for c in (accept or {}).get("commits") or ():
        if not any(i.startswith(c.lower()) for i in ids):
            left.append("--accept-commit %s is not a new commit in the watched checkouts" % c)
    return left


def accept_file(root: str) -> str:
    return os.path.join(root, ".rein", "outside-accept.json")


def load_accepts(root: str) -> dict:
    """The acceptances the owner recorded for the ship tasks run from `root` (`rein accept-outside`)."""
    try:
        with open(accept_file(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"paths": [], "commits": [], "hooks": []}
    clean = lambda key: [x for x in data.get(key, []) if isinstance(x, str) and x] if isinstance(data, dict) else []  # noqa: E731
    return {"paths": clean("paths"), "commits": clean("commits"), "hooks": clean("hooks")}


def record_accepts(root: str, paths=(), commits=(), hooks=()) -> dict:
    """Add the owner's acceptances to `root`'s file; returns what is recorded now."""
    cur = load_accepts(root)
    cur["paths"] = sorted(set(cur["paths"]) | {entry_id(p) for p in paths})
    cur["commits"] = sorted(set(cur["commits"]) | {c.lower() for c in commits})
    cur["hooks"] = sorted(set(cur["hooks"]) | {c.lower() for c in hooks})
    os.makedirs(os.path.dirname(accept_file(root)), exist_ok=True)
    with open(accept_file(root), "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=1)
    return cur


def run_guard(fn, *args, **kw) -> tuple:
    """`(problems, "")`, or `([], why)` when the guard itself broke: a guard that cannot run
    is the environment's problem (exit 126), never an exception that takes the gate down."""
    try:
        return list(fn(*args, **kw)), ""
    except Exception as exc:  # noqa: BLE001 -- any failure of the check, reported, never raised
        return [], "guard could not run: %s: %s" % (type(exc).__name__, exc)


def check(path: str, base: str, resolved: dict, allowed=(), env=None, inherited=(), accept_hooks=(),
          unchecked=None, hook_accepted=None) -> list:
    """Every rule the change since `base` breaks, one line each; [] when it breaks none. GuardError
    when it cannot tell. `inherited`: the plan root's `gate.protected` globs."""
    if not base:
        raise GuardError("no base to compare the change against")
    try:
        changed = changed_paths(path, base)
    except (RuntimeError, OSError) as exc:
        raise GuardError("cannot list the changes since %s in %s: %s" % (base, path, exc))
    patterns = protected_patterns(resolved, resolve_at(path, base), inherited=inherited)
    problems = ["it changes %s, the configuration Rein gates it with; revert that change (only a task "
                "that declares the file -- a plan task's `Gate config:` field, `rein quick "
                "--allow-config` -- may change it)" % f
                for f in config_changes(changed, patterns, allowed)]
    return problems + commit_msg_failures(path, base, env) + pre_commit_failures(path, base, env, accept_hooks, unchecked, hook_accepted)


def config_snapshot(path: str, base: str, resolved: dict, inherited=()) -> dict:
    """The protected configuration as it is now: `{"patterns": [...], "files": {rel: fingerprint}}`
    for every protected path the change since `base` touches. Taken before an auditor starts, so
    `config_drift` can name what the auditor itself changed (the worker's allowed edits are in it)."""
    patterns = protected_patterns(resolved, resolve_at(path, base), inherited=inherited)
    try:
        changed = changed_paths(path, base)
    except (RuntimeError, OSError) as exc:
        raise GuardError("cannot list the changes since %s in %s: %s" % (base, path, exc))
    return {"patterns": patterns,
            "files": {f: _fingerprint(path, f) for f in config_changes(changed, patterns, ())}}


def config_drift(before: dict, path: str, base: str) -> list:
    """One problem per protected path whose state differs from `before` (a `config_snapshot`):
    edited, created, deleted or reverted since. GuardError when it cannot tell."""
    if not isinstance(before, dict) or not isinstance(before.get("files"), dict):
        raise GuardError("no usable configuration snapshot of %s from before the audit" % path)
    patterns = list(before.get("patterns") or [])
    try:
        changed = changed_paths(path, base)
    except (RuntimeError, OSError) as exc:
        raise GuardError("cannot list the changes since %s in %s: %s" % (base, path, exc))
    now = {f: _fingerprint(path, f) for f in config_changes(changed, patterns, ())}
    return ["the auditor changed %s, the configuration Rein gates the change with (it is a reviewer: "
            "it must not edit the worktree it reviews); revert that change" % f
            for f in sorted(set(now) | set(before["files"])) if now.get(f) != before["files"].get(f)]
