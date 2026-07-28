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
FRONTEND_MARKERS = ("next", "vite", "astro", "@sveltejs/kit", "nuxt", "@remix-run", "react-scripts")
INFRA_FILES = ("serverless.yml", "serverless.ts", "sst.config.ts", "template.yaml", "Dockerfile")

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

    subtypes = sorted({m for m in FRONTEND_MARKERS if any(m in d for d in deps)})
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
        return {"stack": "unknown", "packageManager": "", "commands": {}, "subtypes": []}

    # A python repo can still have a package.json driving a frontend, and vice
    # versa. Detect the secondary stack rather than pretending it is not there.
    if base["stack"] != "node" and _exists(root, "package.json"):
        node = _detect_node(root)
        base["subtypes"] = sorted(set(base["subtypes"]) | set(node["subtypes"]) | {"node"})

    infra = [f for f in INFRA_FILES if _exists(root, f)]
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


def _browser_tools(root: str) -> list[str]:
    """Which rendered-verification tools this PROJECT can actually reach.

    Deliberately project-local only (never the operator's machine or home
    directory): the same `rein detect` call must give the same answer for
    everyone who clones the project, and it must be reproducible in a throwaway
    temp tree for tests. Naming a tool nobody here can use is worse than
    naming none.
    """
    tools: list[str] = []

    pkg = _read_json(os.path.join(root, "package.json"))
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    pyproject = _read_text(os.path.join(root, "pyproject.toml")).lower()
    requirements = _read_text(os.path.join(root, "requirements.txt")).lower()
    has_playwright = (
        "playwright" in deps
        or "@playwright/test" in deps
        or "playwright" in pyproject
        or "playwright" in requirements
        or os.path.exists(os.path.join(root, "node_modules", ".bin", "playwright"))
    )
    if has_playwright:
        tools.append("playwright")

    mcp_cfg = _read_json(os.path.join(root, ".mcp.json"))
    servers = mcp_cfg.get("mcpServers") or {}
    if any("chrome" in k.lower() for k in servers):
        tools.append("claude-in-chrome")

    skills_dir = os.path.join(root, ".claude", "skills")
    browser_skill_names = ("browser-testing", "browser-testing-with-devtools")
    if any(os.path.isdir(os.path.join(skills_dir, n)) for n in browser_skill_names):
        tools.append("browser-testing")

    return tools


_PORT_FLAG_RE = re.compile(r"(?:--port|-p)[=\s]+(\d+)")


def _serve(root: str, subtypes: list[str], cfg: dict) -> dict | None:
    """How to run a frontend project so a real page can be rendered and checked.

    Returns None for non-frontend projects: there is nothing to serve, so no
    `serve` block should appear at all rather than an empty placeholder.
    """
    if "frontend" not in subtypes:
        return None

    pkg = _read_json(os.path.join(root, "package.json"))
    scripts = pkg.get("scripts") or {}
    runner = _pm_runner(_package_manager(root))

    script_body = ""
    if "dev" in scripts:
        script_body = scripts["dev"]
        command = f"{runner} dev"
    elif "start" in scripts:
        script_body = scripts["start"]
        command = f"{runner} start"
    else:
        command = ""

    cfg_url = ((cfg.get("verify") or {}).get("url") or "").strip()
    if cfg_url:
        url = cfg_url
    else:
        port_match = _PORT_FLAG_RE.search(script_body) if script_body else None
        url = f"http://localhost:{port_match.group(1) if port_match else 3000}"

    return {"command": command, "url": url}


def _verify_policy(root: str, subtypes: list[str], commands: dict[str, str], cfg: dict) -> dict:
    cfg_verify = cfg.get("verify") or {}
    if "mode" in cfg_verify:
        mode = cfg_verify["mode"]
    elif "frontend" in subtypes:
        mode = "rendered"
    elif "infra" in subtypes and "test" not in commands:
        mode = "plan-only"
    else:
        mode = "unit"

    requires: list[str] = []
    forbids: list[str] = []
    tools: list[str] = []

    if mode == "rendered":
        requires = ["a real browser render must be observed, not just a passing test suite"]
        tools = _browser_tools(root)
    elif mode == "plan-only":
        forbids = list(DESTRUCTIVE_OPS)

    return {"mode": mode, "requires": requires, "forbids": forbids, "tools": tools}


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
    serve = _serve(root, subtypes, cfg)

    missing_commands = [s for s in ("test", "testOne", "lint", "typecheck") if s not in commands]
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
        "verifyPolicy": _verify_policy(root, subtypes, commands, cfg),
    }
    if serve is not None:
        result["serve"] = serve
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(resolve(sys.argv[1] if len(sys.argv) > 1 else "."), indent=2))
