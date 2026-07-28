#!/usr/bin/env python3
"""Stack detection + command resolution for a project.

Why a CLI and not agent exploration: the whole point of this kit is that context
re-reads are the bill. An agent that greps around to figure out "is this pnpm or
npm, vitest or jest" burns turns to rediscover something deterministic. One
`rein detect` call answers it in a single bash round-trip.

Precedence (highest wins):
  1. flow.config.json          -- explicit intent, always wins
  2. task runner               -- justfile / Makefile / Taskfile: the project
                                  already declared how it is built
  3. autodetect                -- manifest files + dependencies

Nothing here fails hard: an unknown stack yields empty commands and `stack:
"unknown"`, and the caller degrades to asking the user or exploring.
"""

from __future__ import annotations

import json
import os
import re

CONFIG_NAME = "flow.config.json"

# Frontend frameworks are a SUBTYPE of node, not a stack of their own -- they
# share node's commands but additionally require real rendered verification
# ("the tests pass but the UI is broken" is the failure this kit exists to catch).
FRONTEND_MARKERS = ("next", "vite", "astro", "@sveltejs/kit", "nuxt", "nuxt3", "@remix-run", "react-scripts")
INFRA_FILES = ("serverless.yml", "serverless.ts", "sst.config.ts", "template.yaml", "Dockerfile")
# Terraform is the canonical plan-only case and DESTRUCTIVE_OPS names its verbs,
# so matching it by extension is not optional: detecting the ops but not the repo
# would leave the guarantee unreachable exactly where it matters most.
INFRA_GLOBS = ("*.tf", "*.tfvars")

# An infra task "verified" by actually mutating real infrastructure is not
# verified, it is an incident. These are always forbidden in plan-only mode.
DESTRUCTIVE_OPS = ("deploy", "apply", "destroy")


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _exists(root: str, *names: str) -> str:
    for n in names:
        p = os.path.join(root, n)
        if os.path.exists(p):
            return n
    return ""


def _is_set(commands: dict, slot: str) -> bool:
    """Whether a command slot is genuinely configured.

    An EMPTY STRING means "not set" everywhere else in this system -- it is the
    documented idiom in flow.config.example.json, and loop.js treats a falsy
    command as absent. Key-presence checks disagreed with that and silently
    dropped the plan-only prohibition for an infra project that had `"test": ""`.
    """
    return bool((commands.get(slot) or "").strip())


_INFRA_MAX_DEPTH = 2
_INFRA_SKIP_DIRS = (".git", "node_modules", "vendor", ".venv", "dist", "build", ".terraform")


def _infra_files(root: str) -> list[str]:
    """Infra markers by exact name and by extension, a bounded depth down.

    Root-only matching is not enough here: `terraform/`, `infra/` and
    `envs/prod/` are the ORDINARY layouts, so a root-only probe would leave the
    plan-only guarantee unreachable for most real infra repos while looking
    fixed. Depth is bounded at two levels on purpose: it reaches `envs/prod/` and
    `deploy/terraform/` -- the layouts named above -- while a full walk would make
    every `rein detect` pay for a recursive scan of the whole tree. Deeper than
    that, set `subtypes` in flow.config.json.
    """
    import glob as _glob

    found = [n for n in INFRA_FILES if _exists(root, n)]
    for pattern in INFRA_GLOBS:
        if _glob.glob(os.path.join(root, pattern)):
            found.append(pattern)

    def scan(rel: str, depth: int):
        path = os.path.join(root, rel) if rel else root
        for name in INFRA_FILES:
            if rel and os.path.exists(os.path.join(path, name)):
                found.append(f"{rel}/{name}")
        for pattern in INFRA_GLOBS:
            if rel and _glob.glob(os.path.join(path, pattern)):
                found.append(f"{rel}/{pattern}")
        if depth <= 0:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        for entry in entries:
            if entry in _INFRA_SKIP_DIRS or entry.startswith("."):
                continue
            child = os.path.join(path, entry)
            if os.path.isdir(child):
                scan(f"{rel}/{entry}" if rel else entry, depth - 1)

    scan("", _INFRA_MAX_DEPTH)
    return found


# ------------------------------------------------------------- task runners --


def _just_targets(root: str) -> list[str]:
    text = _read_text(os.path.join(root, "justfile")) or _read_text(os.path.join(root, "Justfile"))
    if not text:
        return []
    # `name:` or `name arg:` at column 0, skipping comments and assignments.
    return re.findall(r"^([a-zA-Z][\w-]*)(?:\s+[^:\n]*)?:(?!=)", text, re.MULTILINE)


def _make_targets(root: str) -> list[str]:
    text = _read_text(os.path.join(root, "Makefile")) or _read_text(os.path.join(root, "makefile"))
    if not text:
        return []
    return re.findall(r"^([a-zA-Z][\w-]*):(?!=)", text, re.MULTILINE)


def _task_runner(root: str) -> tuple[str, list[str]]:
    """(runner_prefix, available_targets). Empty prefix means no runner."""
    if _exists(root, "justfile", "Justfile"):
        return "just", _just_targets(root)
    if _exists(root, "Taskfile.yml", "Taskfile.yaml"):
        text = _read_text(os.path.join(root, _exists(root, "Taskfile.yml", "Taskfile.yaml")))
        return "task", re.findall(r"^\s{2}([a-zA-Z][\w-]*):", text, re.MULTILINE)
    if _exists(root, "Makefile", "makefile"):
        return "make", _make_targets(root)
    return "", []


# Command slot -> target names a project might plausibly use for it.
_SLOT_ALIASES = {
    "test": ("test", "tests", "test-all", "check-test", "pytest", "test-python", "test-unit"),
    "lint": ("lint", "fmt-check", "ruff", "eslint", "check-lint"),
    "typecheck": ("typecheck", "types", "mypy", "tsc", "check-types"),
    "build": ("build", "compile", "dist"),
}


def _from_task_runner(runner: str, targets: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    lowered = {t.lower(): t for t in targets}
    for slot, aliases in _SLOT_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                found[slot] = f"{runner} {lowered[alias]}"
                break
    return found


# --------------------------------------------------------------- autodetect --


def _package_manager(root: str) -> str:
    if _exists(root, "pnpm-lock.yaml"):
        return "pnpm"
    if _exists(root, "yarn.lock"):
        return "yarn"
    if _exists(root, "bun.lockb", "bun.lock"):
        return "bun"
    return "npm"


def _pm_runner(pm: str) -> str:
    """The CLI prefix used to invoke a package.json script for this package manager."""
    return {"pnpm": "pnpm", "yarn": "yarn", "bun": "bun run"}.get(pm, "npm run")


def _dep_matches(dep: str, marker: str) -> bool:
    """Exact dependency match, or a scoped package under it.

    NOT a substring test: "vite" is a substring of "vitest", so every Node
    project using the most popular test runner was classified frontend, and once
    phase 2 turned subtypes into a POLICY that meant a CLI library got an
    unsatisfiable rendered contract. "next" likewise matches next-auth and
    nextra. The prefix form is only for scoped families like @remix-run/react.
    """
    return dep == marker or dep.startswith(marker + "/")


def _detect_node(root: str) -> dict:
    pkg = _read_json(os.path.join(root, "package.json"))
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}

    pm = _package_manager(root)
    runner = _pm_runner(pm)

    commands: dict[str, str] = {}
    for slot in ("test", "lint", "typecheck", "build"):
        if slot in scripts:
            commands[slot] = f"{runner} {slot}"
    if "typecheck" not in commands and "tsc" in deps:
        commands["typecheck"] = "npx tsc --noEmit"

    # The bounded verification: ONE test file, never the suite.
    if "vitest" in deps:
        commands.setdefault("testOne", "npx vitest run {target}")
    elif "jest" in deps:
        commands.setdefault("testOne", "npx jest {target}")
    elif "test" in commands:
        commands.setdefault("testOne", commands["test"] + " {target}")

    subtypes = sorted({m for m in FRONTEND_MARKERS if any(_dep_matches(d, m) for d in deps)})
    return {
        "stack": "node",
        "packageManager": pm,
        "commands": commands,
        "subtypes": ["frontend"] + subtypes if subtypes else [],
    }


def _detect_python(root: str) -> dict:
    text = _read_text(os.path.join(root, "pyproject.toml"))
    if _exists(root, "uv.lock"):
        pm, run = "uv", "uv run "
    elif _exists(root, "poetry.lock"):
        pm, run = "poetry", "poetry run "
    else:
        pm, run = "pip", ""

    commands = {"test": f"{run}pytest -q", "testOne": f"{run}pytest -q {{target}}"}
    if "ruff" in text:
        commands["lint"] = f"{run}ruff check ."
    if "mypy" in text:
        commands["typecheck"] = f"{run}mypy ."
    elif "pyright" in text:
        commands["typecheck"] = f"{run}pyright"
    return {"stack": "python", "packageManager": pm, "commands": commands, "subtypes": []}


def _detect_rust(_root: str) -> dict:
    return {
        "stack": "rust",
        "packageManager": "cargo",
        "commands": {
            "test": "cargo test",
            "testOne": "cargo test {target}",
            "lint": "cargo clippy -- -D warnings",
            "typecheck": "cargo check",
            "build": "cargo build",
        },
        "subtypes": [],
    }


def _detect_go(_root: str) -> dict:
    return {
        "stack": "go",
        "packageManager": "go",
        "commands": {
            "test": "go test ./...",
            "testOne": "go test {target}",
            "lint": "go vet ./...",
            "build": "go build ./...",
        },
        "subtypes": [],
    }


def _autodetect(root: str) -> dict:
    if _exists(root, "pyproject.toml", "setup.py", "requirements.txt"):
        base = _detect_python(root)
    elif _exists(root, "Cargo.toml"):
        base = _detect_rust(root)
    elif _exists(root, "go.mod"):
        base = _detect_go(root)
    elif _exists(root, "package.json"):
        base = _detect_node(root)
    else:
        # No language manifest is NOT "nothing to detect". A terraform or
        # serverless repo is exactly this shape, and it is the one that most
        # needs the plan-only prohibition -- so fall through to the infra probe
        # instead of returning early. Returning here made auto-detected
        # plan-only unreachable for the archetypal infra repo.
        base = {"stack": "unknown", "packageManager": "", "commands": {}, "subtypes": []}

    # A python repo can still have a package.json driving a frontend, and vice
    # versa. Detect the secondary stack rather than pretending it is not there.
    if base["stack"] not in ("node", "unknown") and _exists(root, "package.json"):
        node = _detect_node(root)
        base["subtypes"] = sorted(set(base["subtypes"]) | set(node["subtypes"]) | {"node"})

    infra = _infra_files(root)
    if infra:
        base["subtypes"] = sorted(set(base["subtypes"]) | {"infra"})
        base["infraFiles"] = infra
    return base


# ------------------------------------------------------------------ resolve --


def _detect_plan_source(root: str) -> str:
    if os.path.isdir(os.path.join(root, "openspec", "changes")):
        return "openspec"
    if _exists(root, "tasks.md", "TASKS.md"):
        return "tasks-md"
    return "tasks-md"


def _capabilities(root: str) -> list[str]:
    import shutil

    caps = [c for c in ("graphify", "openspec", "bd", "just", "git", "node", "python3") if shutil.which(c)]
    if os.path.isdir(os.path.join(root, "graphify-out")):
        caps.append("graphify-index")
    return caps


# ------------------------------------------------------------- verify policy --


def _has_playwright_dependency(text: str) -> bool:
    """Line-based dependency match: a `#` comment mentioning playwright (in a
    pyproject.toml or requirements.txt) must not name a tool nobody installed.
    """
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"\bplaywright\b", code, re.IGNORECASE):
            return True
    return False


def _mcp_chrome_configured(root: str) -> bool:
    # `.mcp.json` is the project-committed MCP config; `.claude/settings.json`
    # and `.claude/settings.local.json` are the more common real-world places
    # a project configures the claude-in-chrome MCP (settings.local.json is
    # typically gitignored, but still project-local and reproducible for
    # whoever generated it, unlike the operator's home-directory `~/.claude.json`).
    for rel in (".mcp.json", os.path.join(".claude", "settings.json"), os.path.join(".claude", "settings.local.json")):
        cfg = _read_json(os.path.join(root, rel))
        servers = cfg.get("mcpServers") or {}
        if any("chrome" in k.lower() for k in servers):
            return True
    return False


def _browser_tools(root: str) -> list[str]:
    """Which rendered-verification tools this PROJECT can actually reach.

    Deliberately project-local only (never the operator's machine or home
    directory): the same `rein detect` call must give the same answer for
    everyone who clones the project, and it must be reproducible in a throwaway
    temp tree for tests. Naming a tool nobody here can use is worse than
    naming none.

    Plugin-provided skills (e.g. this kit's own `browser-testing-with-devtools`,
    shipped from a plugin rather than `.claude/skills/`) are deliberately NOT
    probed here: plugin installation state lives outside the repo (typically
    `~/.claude/plugins`), so checking for it would break the "same answer for
    everyone who clones the project" invariant this function exists to uphold.
    """
    tools: list[str] = []

    pkg = _read_json(os.path.join(root, "package.json"))
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    pyproject = _read_text(os.path.join(root, "pyproject.toml"))
    requirements = _read_text(os.path.join(root, "requirements.txt"))
    has_playwright = (
        "playwright" in deps
        or "@playwright/test" in deps
        or _has_playwright_dependency(pyproject)
        or _has_playwright_dependency(requirements)
        or os.path.exists(os.path.join(root, "node_modules", ".bin", "playwright"))
    )
    if has_playwright:
        tools.append("playwright")

    if _mcp_chrome_configured(root):
        tools.append("claude-in-chrome")

    skills_dir = os.path.join(root, ".claude", "skills")
    browser_skill_names = ("browser-testing", "browser-testing-with-devtools")
    if any(os.path.isdir(os.path.join(skills_dir, n)) for n in browser_skill_names):
        tools.append("browser-testing")

    return tools


_PORT_FLAG_RE = re.compile(r"(?:--port|-p)[=\s]+(\d+)")


def _serve(root: str, subtypes: list[str], commands: dict[str, str], cfg: dict) -> tuple[dict | None, list[str]]:
    """How to run a frontend project so a real page can be rendered and checked.

    Returns None for non-frontend projects: there is nothing to serve, so no
    `serve` block should appear at all rather than an empty placeholder.

    `commands["serve"]` (set via flow.config.json's `commands.serve`) is the
    highest-precedence source -- same precedence rule as every other command
    slot ("flow.config.json -- explicit intent, always wins"). Without this,
    a frontend whose dev server is not an npm script (static site, a Django/
    Rails-served front end, docker compose) could never be configured: the
    `serve` slot would stay in `missingCommands` with no way to satisfy it.
    """
    if "frontend" not in subtypes:
        return None, []

    pkg = _read_json(os.path.join(root, "package.json"))
    scripts = pkg.get("scripts") or {}
    runner = _pm_runner(_package_manager(root))

    cfg_command = (commands.get("serve") or "").strip()

    script_body = ""
    if cfg_command:
        script_body = cfg_command
        command = cfg_command
    elif "dev" in scripts:
        script_body = scripts["dev"]
        command = f"{runner} dev"
    elif "start" in scripts:
        script_body = scripts["start"]
        command = f"{runner} start"
    else:
        command = ""

    cfg_url = ((cfg.get("verify") or {}).get("url") or "").strip()
    warnings: list[str] = []
    if cfg_url:
        url = cfg_url
    else:
        inspected = script_body or ""
        port, provenance = _port_from(inspected) if inspected else ("", "")
        fallback, known_framework = _framework_port(subtypes)
        # The framework default only applies when the framework's own dev server
        # is what runs: commands.serve means it deliberately is not.
        framework_applies = known_framework and not cfg_command

        if provenance in ("env", "flag"):
            chosen, guessed = port, False
        elif provenance == "flag-compound" and framework_applies:
            # A sibling process's port is worse than the framework's own default.
            chosen, guessed = str(fallback), False
        elif port:
            chosen, guessed = port, True
        else:
            chosen, guessed = str(fallback), not framework_applies
        url = f"http://localhost:{chosen}"
        # A port is a GUESS when it came from the bare-number heuristic, or when
        # nothing in the command gave one and no framework default legitimately
        # applies. `npm run dev` on Vite needs no warning -- 5173 IS that
        # framework's default. But a config-supplied `commands.serve` means the
        # server is deliberately NOT the framework's dev server, so the
        # framework's port stops being a safe assumption.

        # `commands.serve` exists precisely for servers that are NOT npm scripts,
        # so there is no framework default to fall back on and no port to read.
        # Handing the implementer a URL nothing listens on is worse than admitting
        # the guess: it and the reviewer would both be told to render a dead link.
        # Deliberately NOT warned for package.json scripts -- `npm run dev` never
        # carries a port flag, and a warning on every ordinary frontend project is
        # noise that teaches people to ignore warnings.
        if command and guessed:
            why = (
                f"port {chosen} was inferred from a bare number in {inspected!r}"
                if port
                else f"no port could be read from {inspected!r}" if inspected else "there is no serve command to read a port from"
            )
            warnings.append(
                f"serve.url {url!r} is a guess: {why}. "
                f"Set verify.url in flow.config.json if the server does not listen there."
            )

    return {"command": command, "url": url}, warnings


# Dev-server defaults, so an ordinary frontend project gets the RIGHT url without
# any configuration. 3000 for everything was simply wrong for half of these.
_FRAMEWORK_PORTS = {
    "vite": 5173,
    "@sveltejs/kit": 5173,
    "astro": 4321,
    "next": 3000,
    "nuxt": 3000,
    "nuxt3": 3000,
    "@remix-run": 3000,
    "react-scripts": 3000,
}


def _framework_port(subtypes: list[str]) -> tuple[int, bool]:
    """(port, known). `known` is False when 3000 is a bare last resort.

    Reachable via config-supplied `subtypes: ["frontend"]`, which names no
    framework -- detection alone always yields a marker we have a default for.
    """
    for marker in subtypes:
        if marker in _FRAMEWORK_PORTS:
            return _FRAMEWORK_PORTS[marker], True
    return 3000, False


# `PORT=4000 next dev` / `cross-env PORT=3001 react-scripts start` is the
# mainstream way a package.json script moves off the default port. It is an
# assignment, so a blanket "assignment values are never ports" rule silently
# broke exactly the case that matters -- match it explicitly, first.
# Bare PORT=, or the two framework-public prefixes -- NOT any *_PORT=. A
# wildcard prefix read DB_PORT=5432 and REDIS_PORT=6379 as the dev server's
# port: the same "a non-server number became the server port" defect as
# NODE_OPTIONS=--max-old-space-size=4096, reintroduced through the prefix.
_PORT_ENV_RE = re.compile(r"(?:^|\s)(?:NEXT_PUBLIC_|VITE_)?PORT=(\d{2,5})(?!\S)")
# In a PARALLEL script the first --port may belong to a SIBLING process: with
# `concurrently "json-server --port 3001" "vite"` something really does listen
# on 3001, so a render check there passes against the wrong process.
# Sequential forms (`&&`, `||`, a pipe) have no sibling -- the last command is
# the only long-running process, so its flag IS the server's own port. Treating
# them as ambiguous silently discarded `tsc && vite --port 4000`.
_COMPOUND_RE = re.compile(r"(?<!&)&(?!&)|concurrently|npm-run-all|run-p|run-s")


def _port_from(text: str) -> tuple[str, str]:
    """(port, provenance) -- how much the port can be trusted, not just its value.

    provenance is one of:
      "env"           PORT=<n> assignment            -- certain
      "flag"          --port/-p <n>                  -- certain
      "flag-compound" a flag inside a compound script -- may be a sibling's port
      "bare"          a lone number on the line      -- a guess
      ""              nothing found
    Other assignment values are excluded outright: NODE_OPTIONS=--max-old-space-size=4096
    is never a port, and reading it as one sent both agents to a dead URL.
    """
    env = _PORT_ENV_RE.search(text)
    if env:
        return env.group(1), "env"
    flag = _PORT_FLAG_RE.search(text)
    if flag:
        return flag.group(1), "flag-compound" if _COMPOUND_RE.search(text) else "flag"
    for candidate in re.findall(r"(?<![\w.:=-])(\d{2,5})(?![\w.=])", text):
        if 1 <= int(candidate) <= 65535:
            return candidate, "bare"
    return "", ""


_VALID_VERIFY_MODES = {"rendered", "plan-only", "unit"}


def _verify_policy(root: str, subtypes: list[str], commands: dict[str, str], cfg: dict) -> tuple[dict, list[str]]:
    cfg_verify = cfg.get("verify") or {}
    raw_mode = str(cfg_verify.get("mode") or "").strip()

    # Absent, empty, or the documented sentinel "auto" all mean "not set --
    # fall through to detection". This is what flow.config.example.json
    # ships (`"mode": "auto"`), and treating any-truthy-value-in-cfg as an
    # override previously made that shipped example silently resolve to an
    # empty verifyPolicy for frontend projects (loop.js's policy blocks only
    # branch on 'rendered'/'plan-only', so 'auto' produced neither).
    bad_mode = ""
    mode = ""
    if raw_mode and raw_mode != "auto":
        if raw_mode in _VALID_VERIFY_MODES:
            mode = raw_mode
        else:
            bad_mode = raw_mode  # surfaced below instead of failing open

    if not mode:
        if "frontend" in subtypes:
            mode = "rendered"
        elif "infra" in subtypes and not _is_set(commands, "test"):
            mode = "plan-only"
        else:
            mode = "unit"

    requires: list[str] = []
    forbids: list[str] = []
    tools: list[str] = []
    warnings: list[str] = []

    if bad_mode:
        warnings.append(
            f"verify.mode {bad_mode!r} is not one of {sorted(_VALID_VERIFY_MODES)} "
            f"(or 'auto') -- falling back to detected mode {mode!r}"
        )

    if mode == "rendered":
        requires = ["a real browser render must be observed, not just a passing test suite"]
        tools = _browser_tools(root)
        if not tools:
            # Rendered mode with nothing to render WITH is an unsatisfiable
            # instruction: the implementer burns its step budget, or writes an
            # unobserved "renders correctly" that the reviewer then accepts as
            # the very evidence it was told to demand. Say so instead.
            warnings.append(
                "verify.mode is 'rendered' but no browser tool was found in this project "
                "(Playwright dependency, chrome MCP server, or a project-local browser skill). "
                "Install one, or set verify.mode to 'unit' in flow.config.json to opt out."
            )
    elif mode == "plan-only":
        forbids = list(DESTRUCTIVE_OPS)

    # warnings are returned as a sibling of the policy dict (see resolve()),
    # never merged into it: loop.js's CONTEXT_SCHEMA declares verifyPolicy
    # with additionalProperties: false and exactly {mode, requires, forbids,
    # tools} because the Prepare agent copies config.verifyPolicy LITERALLY
    # into ctx.verifyPolicy. A fifth key here would make that literal copy
    # violate the schema on any project with a typo'd verify.mode.
    result = {"mode": mode, "requires": requires, "forbids": forbids, "tools": tools}
    return result, warnings


def resolve(root: str = ".") -> dict:
    """Full resolution with precedence applied. Never raises."""
    root = os.path.abspath(root)
    auto = _autodetect(root)
    runner, targets = _task_runner(root)
    runner_cmds = _from_task_runner(runner, targets) if runner else {}
    cfg = _read_json(os.path.join(root, CONFIG_NAME))
    cfg_cmds = cfg.get("commands") or {}

    # Precedence: config > task runner > autodetect.
    commands = {**auto.get("commands", {}), **runner_cmds, **cfg_cmds}
    sources = {}
    for slot in commands:
        if slot in cfg_cmds:
            sources[slot] = "flow.config.json"
        elif slot in runner_cmds:
            sources[slot] = runner or "task-runner"
        else:
            sources[slot] = "autodetect"

    models = {"aux": "haiku", "impl": "sonnet", "review": "opus", **(cfg.get("models") or {})}
    limits = {"maxTaskSteps": 8, "maxReviewRounds": 3, **(cfg.get("limits") or {})}
    worktree = {"enabled": True, "prefix": "rein-wt", **(cfg.get("worktree") or {})}
    plan = {"source": _detect_plan_source(root), "path": "", **(cfg.get("plan") or {})}
    tracker = {"kind": "none", **(cfg.get("tracker") or {})}
    subtypes = cfg.get("subtypes") or auto.get("subtypes", [])
    serve, serve_warnings = _serve(root, subtypes, commands, cfg)
    verify_policy, verify_warnings = _verify_policy(root, subtypes, commands, cfg)
    verify_warnings = serve_warnings + verify_warnings

    missing_commands = [s for s in ("test", "testOne", "lint", "typecheck") if not _is_set(commands, s)]
    if serve is not None and not serve["command"]:
        missing_commands.append("serve")

    result = {
        "schema": 1,
        "root": root,
        "configFound": bool(cfg),
        "configPath": os.path.join(root, CONFIG_NAME) if cfg else "",
        "stack": cfg.get("stack") or auto["stack"],
        "subtypes": subtypes,
        "packageManager": auto.get("packageManager", ""),
        "taskRunner": runner,
        "commands": commands,
        "commandSources": sources,
        "missingCommands": missing_commands,
        "plan": plan,
        "tracker": tracker,
        "models": models,
        "limits": limits,
        "worktree": worktree,
        "capabilities": _capabilities(root),
        "verifyPolicy": verify_policy,
    }
    if serve is not None:
        result["serve"] = serve
    if verify_warnings:
        result["verifyWarnings"] = verify_warnings
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(resolve(sys.argv[1] if len(sys.argv) > 1 else "."), indent=2))
