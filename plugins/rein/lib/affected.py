"""Which tests a change can break: the per-task gate runs these, not the whole suite.

The full suite runs once per repo before the audit (the authoritative gate); a
task's gate only needs the tests its own change reaches. The strategy comes
from flow.config.json, per language, or is detected:

    "gate": {"affected": {"python": "pytest", "node": "vitest"}, "affectedMax": 40}

- `pytest`: changed test files, plus tests mapped from each changed module by
  name (`tests/**/test_<module>.py`, preferring a directory that mirrors the
  module's), plus tests that import the module (a grep of its dotted path).
  They run through `commands.testOne`, `{target}` receiving every file.
- `vitest` / `jest`: the runner's own dependency graph (`vitest related --run`,
  `jest --findRelatedTests`) over the changed source files.
- a command template with `{tests}` (mapped test files, e.g. sibling
  `*.spec.ts`) or `{files}` (changed source files).
- `testOne` (detected when only `commands.testOne` is known): mapped test files
  (sibling `*.spec.*`/`*.test.*`, and the python mapping) through `testOne`.
- `full` (the default for anything else): the `test` slot.

Nothing selected runs nothing at task level (lint and typecheck still run).
More than `affectedMax` files, a change to dependencies or test configuration,
or a strategy that cannot be applied runs the full `test` slot instead.
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
import subprocess

DEFAULT_MAX = 40

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".rein", "dist", "build",
              ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".next",
              "coverage", ".angular", ".turbo", ".cache"}
_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
# A change to any of these can break any test: run the whole suite.
_PY_GLOBAL = ("pyproject.toml", "setup.cfg", "setup.py", "uv.lock", "poetry.lock", "Pipfile.lock",
              "pytest.ini", "tox.ini", "noxfile.py")
_PY_GLOBAL_PREFIX = ("requirements",)
_JS_GLOBAL_RE = re.compile(r"^(package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|"
                           r"tsconfig.*\.json|(vitest|vite|jest)\.config\..*|\.babelrc.*|babel\.config\..*)$")
_ALIASES = {"node": ("node", "typescript", "javascript", "ts", "js"), "python": ("python", "py")}
_EXEC = {"pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx", "npm": "npx"}

STRATEGIES = ("pytest", "vitest", "jest", "testOne", "full")


def settings(resolved: dict) -> dict:
    block = (resolved or {}).get("gate")
    block = block if isinstance(block, dict) else {}
    cap = block.get("affectedMax")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        cap = DEFAULT_MAX
    return {"affected": block.get("affected"), "max": cap}


def _language(resolved: dict) -> str:
    stack = str((resolved or {}).get("stack") or "")
    for lang, names in _ALIASES.items():
        if stack in names:
            return lang
    return stack


def strategy(resolved: dict) -> str:
    """The strategy name or command template for this repo; "full" when nothing applies."""
    cfg = settings(resolved)["affected"]
    lang = _language(resolved)
    value = ""
    if isinstance(cfg, str):
        value = cfg
    elif isinstance(cfg, dict):
        for name in _ALIASES.get(lang, (lang,)):
            if isinstance(cfg.get(name), str) and cfg[name].strip():
                value = cfg[name]
                break
    value = (value or "auto").strip()
    if value != "auto":
        return value
    commands = (resolved or {}).get("commands") or {}
    test = "%s %s" % (commands.get("test") or "", commands.get("testOne") or "")
    if lang == "python" and "pytest" in test:
        return "pytest"
    if re.search(r"\bvitest\b", test):
        return "vitest"
    if re.search(r"\bjest\b", test):
        return "jest"
    if "{target}" in (commands.get("testOne") or ""):
        return "testOne"  # any runner: the mapped test files through its own testOne
    return "full"


# -- file helpers -------------------------------------------------------------

def _walk_tests(base: str, is_test) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if is_test(name):
                out.append(os.path.relpath(os.path.join(dirpath, name), base).replace(os.sep, "/"))
    return sorted(out)


def _is_py_test(name: str) -> bool:
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _is_js_test(name: str) -> bool:
    return any(name.endswith(".spec" + e) or name.endswith(".test" + e) for e in _JS_EXT)


def _dotted(path: str) -> str:
    stem = path[:-3] if path.endswith(".py") else path
    parts = [p for p in stem.split("/") if p]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(text: str, dotted: str) -> bool:
    if not dotted:
        return False
    esc = re.escape(dotted)
    if re.search(r"^\s*(?:from|import)\s+%s(?:[\s.,]|$)" % esc, text, re.M):
        return True
    if "." not in dotted:
        return False
    parent, name = dotted.rsplit(".", 1)
    for m in re.finditer(r"^\s*from\s+%s\s+import\s+(\([^)]*\)|[^\n]*)" % re.escape(parent), text, re.M):
        if re.search(r"\b%s\b" % re.escape(name), m.group(1)):
            return True
    return False


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# -- strategies ---------------------------------------------------------------

def _pytest_targets(base: str, changed: list) -> tuple:
    """`(targets, full_reason)` for python: full_reason is set when only the full suite will do."""
    tests = _walk_tests(base, _is_py_test)
    test_set = set(tests)
    found = set()
    modules = []
    for path in changed:
        name = posixpath.basename(path)
        if name in _PY_GLOBAL or name.startswith(_PY_GLOBAL_PREFIX):
            return [], "%s changed: it can affect every test" % path
        if not path.endswith(".py"):
            continue
        if name == "conftest.py":
            folder = posixpath.dirname(path)
            found.update(t for t in tests if not folder or t.startswith(folder + "/"))
            continue
        if _is_py_test(name):
            if path in test_set:
                found.add(path)
            continue
        modules.append(path)
    for path in modules:
        stem = posixpath.basename(path)[:-3]
        if stem == "__init__":
            stem = posixpath.basename(posixpath.dirname(path))
        named = [t for t in tests if posixpath.basename(t) in ("test_%s.py" % stem, "%s_test.py" % stem)]
        if named:
            src_dirs = {d for d in posixpath.dirname(path).split("/") if d and d != "src"}

            def score(t):
                return len(src_dirs & set(posixpath.dirname(t).split("/")))
            best = max(score(t) for t in named)
            found.update(t for t in named if score(t) == best)
    dotted = [d for d in (_dotted(p) for p in modules) if d]
    if dotted:
        for t in tests:
            if t in found:
                continue
            text = _read(os.path.join(base, t))
            if any(_imports(text, d) for d in dotted):
                found.add(t)
    return sorted(found), ""


def _sibling_tests(base: str, changed: list) -> list:
    found = set()
    for path in changed:
        name = posixpath.basename(path)
        if _is_js_test(name) or _is_py_test(name):
            if os.path.isfile(os.path.join(base, path)):
                found.add(path)
            continue
        directory, _, _ = path.rpartition("/")
        stem, dot, ext = name.rpartition(".")
        if not dot:
            continue
        for candidate in ("%s.spec.%s" % (stem, ext), "%s.test.%s" % (stem, ext), "test_%s.%s" % (stem, ext)):
            rel = posixpath.join(directory, candidate) if directory else candidate
            if os.path.isfile(os.path.join(base, rel)):
                found.add(rel)
    return sorted(found)


def _js_sources(base: str, changed: list) -> tuple:
    """`(existing changed JS/TS files, full_reason)`."""
    out = []
    for path in changed:
        name = posixpath.basename(path)
        if _JS_GLOBAL_RE.match(name):
            return [], "%s changed: it can affect every test" % path
        if not path.endswith(_JS_EXT):
            continue
        if not os.path.isfile(os.path.join(base, path)):
            return [], "%s was deleted: its importers are not known" % path
        out.append(path)
    return sorted(out), ""


def _quoted(paths: list) -> str:
    return " ".join(shlex.quote(p) for p in paths)


def _relative(changed: list, sub: str) -> list:
    if not sub:
        return [posixpath.normpath(p) for p in changed]
    prefix = sub.rstrip("/") + "/"
    return [posixpath.normpath(p[len(prefix):]) for p in changed if p.startswith(prefix)]


def compute(root: str, resolved: dict, changed: list) -> dict:
    """What the task-level test step runs for `changed` (paths relative to the repo root).

    `mode`: "none" (nothing selected: run no tests), "run" (`command` selects the
    affected tests), or "full" (the whole `test` slot, `reason` says why)."""
    resolved = resolved or {}
    commands = resolved.get("commands") or {}
    how = strategy(resolved)
    cap = settings(resolved)["max"]
    sub = str(resolved.get("subproject") or "")
    base = os.path.join(root, sub) if sub else root
    prefix = "cd %s && " % shlex.quote(sub) if sub else ""
    rel = _relative(changed, sub)
    out = {"strategy": how, "mode": "full", "command": "", "configured": "", "targets": [], "reason": ""}

    def full(reason):
        out.update(mode="full", reason=reason)
        return out

    def run(targets, command, configured):
        out.update(targets=targets)
        if not targets:
            out.update(mode="none", reason="no test is affected by the changed files")
            return out
        if len(targets) > cap:
            return full("%d affected files, more than gate.affectedMax (%d)" % (len(targets), cap))
        out.update(mode="run", command=command, configured=configured)
        return out

    if how == "full":
        return full("no affected-test strategy for this repo (gate.affected)")
    if how in ("pytest", "testOne"):
        one = commands.get("testOne") or ""
        if "{target}" not in one:
            return full("commands.testOne with {target} is not configured")
        if how == "pytest":
            targets, why = _pytest_targets(base, rel)
        else:
            targets, why = _sibling_tests(base, rel), ""
            if any(p.endswith(".py") for p in rel):
                py, why = _pytest_targets(base, rel)
                targets = sorted(set(targets) | set(py))
        if why:
            return full(why)
        return run(targets, one.replace("{target}", _quoted(targets)), one)
    if how in ("vitest", "jest"):
        sources, why = _js_sources(base, rel)
        if why:
            return full(why)
        runner = _EXEC.get(str(resolved.get("packageManager") or ""), "npx")
        if how == "vitest":
            template = "%s%s vitest related --run {files}" % (prefix, runner)
        else:
            template = "%s%s jest --findRelatedTests {files}" % (prefix, runner)
        return run(sources, template.replace("{files}", _quoted(sources)), template)
    if "{tests}" in how or "{files}" in how:
        if "{tests}" in how:
            targets = _sibling_tests(base, rel)
            if any(p.endswith(".py") for p in rel):
                py, why = _pytest_targets(base, rel)
                if why:
                    return full(why)
                targets = sorted(set(targets) | set(py))
            return run(targets, how.replace("{tests}", _quoted(targets)), how)
        sources = [p for p in rel if os.path.isfile(os.path.join(base, p))]
        return run(sources, how.replace("{files}", _quoted(sources)), how)
    return full("unknown gate.affected strategy %r" % how)


def _git_lines(root: str, *args: str) -> list:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError("git could not run: %s" % exc)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:500] or "git %s failed" % args[0])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def changed_files(root: str, base: str) -> list:
    """Everything changed in `root` since `base`: commits (`base...HEAD`), uncommitted edits and
    new files, repo-relative and sorted. Rein's own `.rein/` is not a change. RuntimeError when
    git cannot say (an unknown base, not a checkout)."""
    found = set(_git_lines(root, "diff", "--name-only", "%s...HEAD" % base))
    found.update(_git_lines(root, "diff", "--name-only", "HEAD"))
    found.update(_git_lines(root, "ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in found if not f.startswith(".rein/"))
