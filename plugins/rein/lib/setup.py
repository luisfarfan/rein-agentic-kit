#!/usr/bin/env python3
"""Provision the retrieval tools this kit recommends, for whatever repo it runs in.

Why this exists: the kit's third lever is retrieval discipline, and its prompts
tell agents to orient with a code graph before opening files. In a repo with no
such tool installed that instruction is inert -- measured: zero graphify
invocations across seven runs of this repo, because no index existed here.
A recommendation nothing provisions is decoration.

The discipline is fixed, and it is the same one the whole kit follows:

  PROBE FIRST      never install what is already there
  REPORT ALWAYS    say what is missing and the exact command that would fix it
  ASK BEFORE WRITE `--install` is opt-in; a bare run changes nothing
  NEVER BREAK      a failed install is reported and the rest continues

What it does NOT do: pick tools per language. Serena covers 40+ languages
through one install, so there is no per-stack language-server matrix to build --
a conclusion that only survived checking, since this kit's own memory claimed a
Python language server was a prerequisite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

# Each tool: what proves it is present, how to install it, and — the field that
# keeps this honest — what it actually buys, in terms this kit can measure.
TOOLS = {
    "serena": {
        "why": "LSP symbol retrieval: find_symbol / find_declaration / "
               "find_referencing_symbols / get_symbols_overview. Attacks the "
               "measured 41 turns of orientation before an agent's first edit.",
        "probe": ["serena"],
        "install": ["uv", "tool", "install", "-p", "3.13", "serena-agent"],
        "needs": ["uv"],
        "post": [
            ["serena", "init"],
            ["serena", "setup", "claude-code"],
        ],
        # MCP servers are enumerated when a session starts, so a freshly
        # registered one is invisible until the next session. Saying so is the
        # difference between "installed" and "usable".
        "caveat": "registers an MCP server — its tools appear in the NEXT session, not this one",
        "gitignore": ".serena/",
        # The binary being on PATH and THIS repo being usable with it are
        # different facts -- the marker `serena project create` writes.
        "activation_marker": ".serena/project.yml",
        "inertReason": "installed but this repo is not activated for serena — "
                        "the kit's symbol-first retrieval prompts will not fire here",
    },
    "graphify": {
        "why": "Precomputed code graph: `graphify explain \"<symbol>\"` returns its "
               "definition plus direct callers/callees, `graphify path \"<A>\" \"<B>\"` "
               "returns the call/reference chain between two symbols -- bounded "
               "orientation instead of a raw grep. The kit's CTX already teaches "
               "these two commands (never `graphify query`, which is BFS from a "
               "literal token match and returns confident noise), but only when "
               "an index exists.",
        "probe": ["graphify"],
        "install": None,  # distributed as a skill, not a package this can fetch
        "manual": "install the graphify skill, then run "
                  "`graphify update . --no-cluster` in this repo once -- no LLM, "
                  "no API key, ~1-2s. Without an index every graph command "
                  "answers 'graph file not found', which is how this capability "
                  "stayed inert across seven runs.",
        "index": "graphify-out/graph.json",
        "gitignore": "graphify-out/",
    },
    "openspec": {
        "why": "Richer plan source than tasks.md: proposal / specs / design "
               "artifacts the loop reads as intent. Optional — tasks-md is the default.",
        "probe": ["openspec"],
        "install": ["npm", "install", "-g", "openspec"],
        "needs": ["npm"],
    },
}


def _which(name: str) -> str:
    """shutil.which plus ~/.local/bin, where `uv tool install` puts things and
    a non-login shell may not look."""
    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.expanduser(f"~/.local/bin/{name}")
    return candidate if os.access(candidate, os.X_OK) else ""


def probe(root: str = ".") -> dict:
    """What is present, what is missing, and why each one matters. Read-only."""
    root = os.path.abspath(root)
    out = {"root": root, "tools": {}}
    for name, spec in TOOLS.items():
        path = _which(spec["probe"][0])
        entry = {
            "present": bool(path),
            "path": path,
            "why": spec["why"],
            "installable": bool(spec.get("install")) and bool(_which((spec.get("needs") or [""])[0])),
            "caveat": spec.get("caveat", ""),
        }
        if spec.get("install") and not entry["installable"] and spec.get("needs"):
            entry["blockedBy"] = f"needs {spec['needs'][0]}, which is not on PATH"
        if spec.get("manual") and not spec.get("install"):
            entry["manual"] = spec["manual"]
        # A tool can be installed and still inert: graphify without an index is
        # the case this whole module exists because of.
        if spec.get("index"):
            entry["indexPath"] = os.path.join(root, spec["index"])
            entry["indexed"] = os.path.exists(entry["indexPath"])
            if entry["present"] and not entry["indexed"]:
                entry["inert"] = "installed but this repo has no index — the kit's graph-first prompts will not fire here"
        if spec.get("activation_marker"):
            entry["activationPath"] = os.path.join(root, spec["activation_marker"])
            entry["activated"] = os.path.exists(entry["activationPath"])
            if entry["present"] and not entry["activated"]:
                entry["inert"] = spec.get("inertReason", "installed but not activated for this repo")
        out["tools"][name] = entry
    out["missing"] = [n for n, e in out["tools"].items() if not e["present"]]
    out["inert"] = [n for n, e in out["tools"].items() if e.get("inert")]
    return out


def _run(cmd: list[str], timeout: int = 900, input_text: str | None = None) -> tuple[bool, str]:
    """D4: unattended means unattended -- a child must never be able to wait
    on an operator who is not there. Two ways that plays out, both handled
    here:

    * no prompt expected: `input_text=None` closes stdin (DEVNULL) so any
      surprise prompt hits EOF and fails fast instead of hanging.
    * a bounded prompt IS expected: `input_text` feeds it a fixed stream of
      answers. The pipe still closes once that's consumed -- there is no
      operator on the other end either way, just a scripted one instead of
      none, because some CLIs (see `activate_serena`) abort on EOF rather
      than taking the prompt's own default, which would fail cleanly but
      would not deliver "activates serena for the current repo".
    """
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")
    try:
        if input_text is None:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                   stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                   input=input_text)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    return proc.returncode == 0, "\n".join(tail)


def install(names: list[str] | None = None, root: str = ".") -> dict:
    """Install only what is missing. One failure never stops the others."""
    state = probe(root)
    targets = names or state["missing"]
    results = {}
    for name in targets:
        spec = TOOLS.get(name)
        if not spec:
            results[name] = {"ok": False, "reason": "unknown tool"}
            continue
        if state["tools"][name]["present"]:
            results[name] = {"ok": True, "reason": "already present — nothing done"}
            continue
        if not spec.get("install"):
            results[name] = {"ok": False, "reason": spec.get("manual", "no automatic install")}
            continue
        need = (spec.get("needs") or [""])[0]
        if need and not _which(need):
            results[name] = {"ok": False, "reason": f"needs {need}, which is not on PATH"}
            continue

        ok, out = _run(spec["install"])
        steps = [{"cmd": " ".join(spec["install"]), "ok": ok, "output": out}]
        # Post-steps only run if the install itself worked -- initialising a
        # package that is not there produces a confusing error, not a fix.
        if ok:
            for post in spec.get("post", []):
                p_ok, p_out = _run(post)
                steps.append({"cmd": " ".join(post), "ok": p_ok, "output": p_out})
                if not p_ok:
                    ok = False
                    break
        results[name] = {"ok": ok, "steps": steps, "caveat": spec.get("caveat", "")}
    # Installing the binary and activating it FOR THIS REPO are different
    # facts -- the whole distinction this module exists to enforce, one
    # level down. A machine that already had serena would otherwise never
    # get this repo's `.serena/project.yml`, because "missing" alone never
    # surfaces that. Runs on every --install, not gated behind `targets`,
    # and a failed activation never stops any other tool's install.
    results["serena-activate"] = activate_serena(root)
    return {"root": state["root"], "results": results}


def activate_serena(root: str = ".") -> dict:
    """Activate serena for THIS repo by creating `.serena/project.yml`.

    A serena binary on PATH buys nothing here until this runs -- see
    `activation_marker` in TOOLS and the installed-vs-usable split `probe`
    already draws for graphify. Idempotent: an already-activated repo is
    reported as such and left untouched, never re-created.

    Unattended (D4): no `--language` is passed, so serena infers the
    project's languages from its own files -- its own dominant-language
    detection is unconditional and needs no prompt. But for any repo that
    isn't single-language, `serena project create` ALSO asks, once per
    additional minority language it finds, "Enable <lang>? [y/N]" -- and
    with stdin simply closed, that prompt hits EOF and ABORTS THE WHOLE
    ACTIVATION, main language included. Feeding it a bounded run of "n"
    answers takes the bracketed default for each -- the same choice an
    operator hitting Enter would make, so still no language beyond what the
    repo's own files already carry gets enabled -- while still closing the
    pipe once consumed, so this never blocks on an operator who is not there.
    """
    root = os.path.abspath(root)
    marker = os.path.join(root, ".serena", "project.yml")
    if os.path.exists(marker):
        return {"ok": True, "attempted": False, "reason": "already activated — nothing done"}
    path = _which("serena")
    if not path:
        return {"ok": False, "attempted": False,
                "reason": "missing prerequisite: serena binary not found"}
    # 64 is a generous cap on how many "any other language?" prompts a
    # single repo could trigger; unused answers are simply never read.
    ok, out = _run(["serena", "project", "create", root], input_text="n\n" * 64)
    result = {"ok": ok, "attempted": True, "cmd": f"serena project create {root}"}
    if not ok:
        result["reason"] = out
    return result


def gitignore_lines(root: str = ".") -> list[str]:
    """Entries these tools write into the repo that must not be committed.

    Local, machine-specific state: a language-server cache or a graph index
    travelling with the repo is noise at best and a stale answer at worst.
    """
    missing = []
    path = os.path.join(os.path.abspath(root), ".gitignore")
    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        body = ""
    for spec in TOOLS.values():
        entry = spec.get("gitignore")
        if entry and entry.rstrip("/") not in body:
            missing.append(entry)
    return missing


def render(state: dict) -> str:
    lines = [f"retrieval tools for {state['root']}", ""]
    for name, e in state["tools"].items():
        mark = "ok     " if e["present"] else "MISSING"
        lines.append(f"  [{mark}] {name}")
        lines.append(f"            {e['why']}")
        if e["present"]:
            lines.append(f"            at {e['path']}")
        if e.get("inert"):
            lines.append(f"            ⚠ {e['inert']}")
        if e.get("blockedBy"):
            lines.append(f"            ⚠ {e['blockedBy']}")
        if e.get("manual"):
            lines.append(f"            → {e['manual']}")
        if e.get("caveat"):
            lines.append(f"            note: {e['caveat']}")
        lines.append("")
    if state["missing"]:
        lines.append(f"  missing: {', '.join(state['missing'])} — install with `rein setup --install`")
    else:
        lines.append("  nothing to install")
    if state["inert"]:
        lines.append(f"  present but inert: {', '.join(state['inert'])}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(json.dumps(probe(sys.argv[1] if len(sys.argv) > 1 else "."), indent=2))
