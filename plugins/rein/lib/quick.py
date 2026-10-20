"""Quick lane: the pure decisions taken when a quick task starts.

The slug, the bug heuristic, the agent's first message, the start
refusals and the record; and, for `done`, the related tests, the gate
steps, the verdict, the summary and the audit decisions. Nothing here
shells out, touches Orca or reads the filesystem (`related_tests` gets
an `exists` callable); the driver built on top owns every side effect.
"""

from __future__ import annotations

import posixpath
import re
import shlex
import unicodedata
from typing import Callable

import quota
import ship_decide

RECORD = ".rein/quick.json"
SLUG_MAX = 30
VERSION = 1

_BUG_WORDS = (
    "bug", "error", "falla", "fallo", "no funciona", "roto", "rompe",
    "crash", "excepción", "exception", "500", "no carga", "no guarda",
    "sale vacío", "sale vacia",
)


def slugify(sentence: str, taken: set) -> str:
    """Worktree slug for `sentence`; unique against `quick-<slug>` names in `taken`."""
    folded = unicodedata.normalize("NFKD", sentence).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-") or "task"
    candidate, n = slug, 2
    while "quick-" + candidate in taken:
        candidate = "%s-%d" % (slug, n)
        n += 1
    return candidate


def looks_like_bug(sentence: str) -> bool:
    low = sentence.lower()
    return any(word in low for word in _BUG_WORDS)


def build_brief(sentence: str, linear: str = "", bug: bool = False, bin_path: str = "") -> str:
    """The agent's first message, in Spanish (spec section 4).

    `bin_path` is this checkout's `bin/rein`, named as the fallback when `rein` is not on PATH."""
    lines = [
        sentence,
        "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir.",
    ]
    if bug:
        lines.append("Primero escribe un test que lo reproduzca.")
    lines.append("Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado.")
    if bin_path:
        lines.append("Si `rein` no está en el PATH, usa `python3 %s quick done`." % bin_path)
    lines.append(
        "`rein quick done` puede tardar hasta 30 minutos si hay auditoría: ejecútalo con un "
        "timeout de 35 minutos o en segundo plano y espera su resultado; no hagas commit "
        "hasta que termine en verde."
    )
    lines.append(
        "No hagas push, merge ni PR salvo que yo lo pida; "
        "si la rama destino es main, pide confirmación antes."
    )
    if linear:
        lines.append("Incluye %s en el mensaje de commit." % linear)
    else:
        lines.append("Commit convencional sin ID de issue: no inventes uno ni lo preguntes.")
    lines.append("No incluyas `.rein/` en ningún commit.")
    lines.append("No leas `.env*` ni credenciales.")
    return "\n".join(lines)


def start_refusal(here: bool, base_given: bool, dirty: bool, dirty_ok: bool) -> str:
    """Why the start must be refused, or "" when it may go ahead."""
    if here and base_given:
        return ("--here cannot be combined with --base: the main checkout "
                "stays on its current branch")
    if here and dirty and not dirty_ok:
        return ("the checkout has uncommitted changes and the task's diff would mix "
                "with them; commit or stash them, or pass --dirty-ok")
    return ""


def new_record(repo: str, workspace: str, base: str, base_sha: str, agent: str,
               model: str, sentence: str, linear: str, now: float) -> dict:
    return {
        "version": VERSION,
        "repo": repo,
        "workspace": workspace,
        "base": base,
        "base_sha": base_sha,
        "agent": agent,
        "model": model,
        "sentence": sentence,
        "linear": linear,
        "started_at": now,
        "audit_rounds": 0,
    }


# --- close: `rein quick done` ------------------------------------------------

MAX_RELATED_TESTS = 10
from verify import ERROR_LINES_CAP, error_lines  # noqa: E402,F401  (shared with the gate report)


def _is_test_file(path: str) -> bool:
    name = posixpath.basename(path)
    stem, _, ext = name.rpartition(".")
    return (
        ".spec." in name
        or ".test." in name
        or (ext == "py" and (stem.startswith("test_") or stem.endswith("_test")))
    )


def _sibling_candidates(path: str) -> list:
    directory, name = posixpath.split(path)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return []
    out = [
        posixpath.join(directory, "%s.spec.%s" % (stem, ext)),
        posixpath.join(directory, "%s.test.%s" % (stem, ext)),
    ]
    if ext == "py":
        out.append(posixpath.join(directory, "test_%s.py" % stem))
        out.append(posixpath.join("tests", directory, "test_%s.py" % stem))
    return out


def related_tests(changed: list, exists: Callable[[str], bool]) -> list:
    """Existing tests worth running for `changed` (repo-relative, sorted, unique).

    A changed test file counts when it still exists; a changed source file
    contributes the existing sibling tests named after it. Deleted files
    never reach the result."""
    found = set()
    for raw in changed:
        path = posixpath.normpath(raw)
        if _is_test_file(path):
            if exists(path):
                found.add(path)
            continue
        for candidate in _sibling_candidates(path):
            if exists(candidate):
                found.add(candidate)
    return sorted(found)


def gate_steps(commands: dict, related: list) -> list:
    """The light gate as `(slot, command, configured)` steps, in run order."""
    steps = []
    for slot in ("lint", "typecheck"):
        cmd = commands.get(slot)
        if cmd:
            steps.append((slot, cmd, cmd))
    if not related:
        return steps
    one, whole = commands.get("testOne"), commands.get("test")
    if one and len(related) <= MAX_RELATED_TESTS:
        for path in related:
            steps.append(("testOne:" + path, one.replace("{target}", shlex.quote(path)), one))
    elif whole:
        steps.append(("test", whole, whole))
    return steps


def verdict(results: list) -> str:
    """`green`, `red` or `setup`; a setup problem outranks a red slot."""
    outcomes = [r.outcome for r in results]
    # Anything but ok/failed (not_invocable, timeout, inconclusive, skipped,
    # proves_nothing, or an outcome this code does not know) is not a verdict
    # on the code.
    if any(o not in ("ok", "failed") for o in outcomes):
        return "setup"
    return "red" if "failed" in outcomes else "green"


def summary(results: list, outputs: dict, notes: list, audit_pending: bool = False) -> str:
    """Verdict, one line per slot, error lines for the red slots only, then the notes.

    A green gate that an audit still has to confirm is not a verdict yet, so the first
    line says so (`audit_pending`)."""
    found = verdict(results)
    if audit_pending and found == "green":
        out = ["gate: green \u2014 auditoría pendiente"]
    else:
        out = ["verdict: " + found]
    for r in results:
        code = "-" if r.exit_code is None else r.exit_code
        out.append("%s exit=%s %dms" % (r.slot, code, r.elapsed_ms))
        if r.outcome not in ("ok", "failed") and getattr(r, "error", ""):
            out.append("  %s: %s" % (r.outcome, r.error))  # a setup slot: say why
        if r.outcome == "failed":
            out.extend("  " + line for line in error_lines(outputs.get(r.slot, "")))
    out.extend(notes)
    return "\n".join(out)


def needs_audit(changed: list, cfg: ship_decide.ShipConfig) -> bool:
    """Only risky paths trigger the audit; a large change alone does not."""
    return ship_decide.touches_risky(changed, cfg.risky_paths)


def reviewer(limits: dict):
    """Opus, else the first unbraked of Codex then Sonnet, else None."""
    if not quota.brake_reason(limits, quota.OPUS[0]):
        return quota.OPUS
    pick = quota.first_unbraked([quota.CODEX, quota.SONNET], limits)
    return None if quota.brake_reason(limits, pick[0]) else pick
