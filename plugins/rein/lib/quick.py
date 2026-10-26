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
REPORT_FILE = ".rein/quick-report.md"  # the agent's report as a file (rein quick report)
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


# Lo que el agente no puede hacer; `rein quick done` comprueba las dos últimas (guard.py).
GUARDRAILS = [
    "Solo dentro de este worktree: no ejecutes tú scripts de fuera (repo hermano, harness, update) "
    "ni escribas fuera.",
    "Los git hooks del repo SÍ deben correr al commitear, aunque llamen scripts de otros repos: "
    "déjalos correr, sin preguntar.",
    "No te saltes hooks: nada de `--no-verify`, LEFTHOOK=0, LEFTHOOK_EXCLUDE, SKIP ni HUSKY=0; "
    "si uno falla, arréglalo o pregúntame.",
    "No toques la config de los gates (flow.config.json, mise.toml, Makefile) salvo que la tarea "
    "lo autorice con --allow-config.",
]


def build_brief(sentence: str, linear: str = "", bug: bool = False, bin_path: str = "",
                env_lines: list = (), allowed_config: list = ()) -> str:
    """The agent's first message, in Spanish (spec section 4).

    `bin_path` is this checkout's `bin/rein`, named as the fallback when `rein` is not on PATH.
    `env_lines` (the repo's `gate.env`) open it: Orca cannot pass an environment to the agent.
    `allowed_config` (--allow-config) is named: the agent must not ask about files its task may change."""
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
    lines.append("Si tienes una duda para mí, no la dejes solo en esta terminal: ejecuta "
                 "`rein quick ask \"<pregunta>\"` y espera; mi respuesta llegará aquí.")
    lines.append("Cuando diga *listo*, ejecuta `rein quick done` y muéstrame el resultado.")
    lines.append("Tu informe final viaja como texto, no por la pantalla: cuando termines, ejecuta "
                 "`rein quick report \"<texto o ruta de un archivo>\"` con lo que cambiaste y el resultado de "
                 "los tests; el orquestador lo lee de ahí.")
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
    lines += GUARDRAILS
    if allowed_config:
        lines.append("Esta tarea AUTORIZA expresamente cambiar estos archivos de config: %s "
                     "(--allow-config); no preguntes por ellos. Cualquier otro archivo de config "
                     "sigue prohibido." % ", ".join(allowed_config))
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
               model: str, sentence: str, linear: str, now: float, allow_config=()) -> dict:
    record = {
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
    if allow_config:
        record["allow_config"] = list(allow_config)  # the gate config this task may change (guard.py)
    return record


def owner_decisions(record: dict) -> list:
    """The questions the owner answered (`rein quick answer`), in ship's `owner_decisions` shape:
    a quick audit's brief lists them as binding."""
    found = record.get("questions") if isinstance(record.get("questions"), list) else []
    return [{"gate": q.get("id"), "kind": "question", "task_id": None, "question": str(q.get("text") or ""),
             "answer": q["answer"]} for q in found if isinstance(q, dict) and q.get("answer") is not None]


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


def reviewer(limits: dict, brake_percent: float = 90.0):
    """Opus, else the first unbraked of Codex then Sonnet, else None."""
    if not quota.brake_reason(limits, quota.OPUS[0], brake_percent):
        return quota.OPUS
    pick = quota.first_unbraked([quota.CODEX, quota.SONNET], limits, brake_percent)
    return None if quota.brake_reason(limits, pick[0], brake_percent) else pick
