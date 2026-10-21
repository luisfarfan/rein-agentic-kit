"""Quick lane: the pure decisions taken when a quick task starts.

The slug, the bug heuristic, the agent's first message, the start
refusals and the record; and, for `done`, the verdict, the summary and
the audit decisions (which checks run is gate_run's, shared with ship).
Nothing here shells out, touches Orca or reads the filesystem; the
driver built on top owns every side effect.
"""

from __future__ import annotations

import re
import unicodedata

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


def build_brief(sentence: str, linear: str = "", bug: bool = False, bin_path: str = "",
                env_lines: list = ()) -> str:
    """The agent's first message, in Spanish (spec section 4).

    `bin_path` is this checkout's `bin/rein`, named as the fallback when `rein` is not on PATH.
    `env_lines` (the repo's `gate.env`) open it: Orca cannot pass an environment to the agent."""
    lines = []
    if env_lines:
        lines.append("Entorno de tests: ejecuta `%s` antes de cualquier comando de test (en el mismo "
                     "comando si tu shell no conserva variables)." % " && ".join(env_lines))
    lines += [
        sentence,
        "Iteramos juntos: propón, aplica, y espera mi visto bueno antes de seguir.",
    ]
    if bug:
        lines.append("Primero escribe un test que lo reproduzca.")
    lines.append("Corre solo los tests afectados, no la suite completa.")
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
    lines.append("`rein quick close` no es tuyo: lo corre el orquestador desde fuera.")
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

from verify import ERROR_LINES_CAP, error_lines  # noqa: E402,F401  (shared with the gate report)


def verdict(results: list) -> str:
    """`green`, `red` or `setup`; a setup problem outranks a red slot."""
    # `skipped` is not run by design (after a slot that failed, or no affected test).
    outcomes = [r.outcome for r in results if r.outcome != "skipped"]
    # Anything else but ok/failed (not_invocable, timeout, inconclusive,
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
        out.append("%s exit=%s %dms%s" % (r.slot, code, r.elapsed_ms,
                                          " (cached)" if getattr(r, "cached", False) else ""))
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
