"""Quick lane: the pure decisions taken when a quick task starts.

The slug, the bug heuristic, the agent's first message, the start
refusals and the record. Nothing here shells out or touches Orca; the
driver built on top owns every side effect.
"""

from __future__ import annotations

import re
import unicodedata

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


def build_brief(sentence: str, linear: str = "", bug: bool = False) -> str:
    """The agent's first message, in Spanish (spec section 4)."""
    lines = [
        sentence,
        "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir.",
    ]
    if bug:
        lines.append("Primero escribe un test que lo reproduzca.")
    lines.append("Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado.")
    lines.append(
        "No hagas push, merge ni PR salvo que yo lo pida; "
        "si la rama destino es main, pide confirmación antes."
    )
    if linear:
        lines.append("Incluye %s en el mensaje de commit." % linear)
    else:
        lines.append("Commit convencional sin ID de issue: no inventes uno ni lo preguntes.")
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
