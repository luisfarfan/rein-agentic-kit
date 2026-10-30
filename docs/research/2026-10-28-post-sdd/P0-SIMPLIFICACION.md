# P0: REIN interviene cuando aporta, no porque el workflow lo obliga

Propuesta. **No hay nada implementado todavía**; los diffs son los esperados. Cada cambio es un
commit propio en la rama `p0-simplify` del repo de REIN.

Ningún cambio añade agentes, un selector de rutas ni un framework. Cuatro de los siete son solo
texto (skills, README y briefs).

## Qué causó el overhead medido y dónde vive

| Evidencia de la fase 2 | Origen en REIN |
|---|---|
| new-errors A: 39 de 65 min en workers «arreglando» la suite. Commiteó cambios en `tests/db_setup.py` y en 2 tests de seguridad ajenos | `ship_decide._on_repo_gate_verdict`: ante un gate completo rojo crea una tarea `GATE<n>` **sin clasificar el fallo**. Su brief (`ship.build_task_spec`) dice *«the failing tests may be outside what the change touched»* |
| P2: «gate rojo» registrado como BLOCKING | `skills/rein-audit/SKILL.md`, paso 2: *«a red gate is by definition a blocker»* |
| Corrección de 35 min y $9 que no arregló el defecto señalado | El brief de las tareas AUDIT: *«fix them»*, sin exigir reproducir antes |
| La auditoría no vio el defecto de permisos (0 de 2), y el plan lo escribió como decisión | Ni la skill de auditoría ni la de plan piden revisar permisos, contratos o concurrencia de forma explícita |
| Workers tocando infraestructura de tests | `guard.protected_patterns` solo protege `flow.config.json` y la config de los runners |

## Cambios

### C1. Rutas de uso, solo texto (README y descripciones de skills)

**Antes:**
- Las skills `rein-role` y `rein-discover` se disparan solas (*«or when you are about to plan,
  implement or review…»*).
- Nada dice cuándo **no** usar REIN.

**Después:** se añade una sección corta al README, y la misma regla en una línea en la descripción
de cada skill.

| Situación | Ruta |
|---|---|
| Cambio pequeño en un repo, bajo riesgo | **Claude Code o Codex nativo.** Sin REIN |
| Incertidumbre real, varios requisitos o varios repos | `/rein:rein-plan`, implementación nativa |
| Toca permisos, seguridad, concurrencia, estado durable o contratos entre servicios | Añadir `/rein:rein-audit` |
| Varias tareas con dependencias o coordinación entre repos | `/ship` |

**Ejemplo de diff en la descripción de `ship`:**
```diff
-description: "Implement a planned change: opens a detached Orca tab ... Use when the user says ship, lánzalo, implementa el plan, or invokes /ship."
+description: "Implement a planned change ... Use only when the plan has several dependent tasks or spans repos, and the user says ship, lánzalo, implementa el plan, or invokes /ship. A single-repo change is done natively."
```

**En `rein-role` y `rein-discover`:** se borra la cláusula de autodisparo. Solo corren si se
invocan.

### C2. Clasificar un gate completo rojo antes de lanzar un worker (código, unas 40 líneas)

**Antes:** gate completo rojo → tarea `GATE1`. Si vuelve a salir rojo → `GATE2`. Hasta
`max_retries`, y después pregunta al dueño.

**Después:** primero se clasifica el fallo. Ningún worker toca un rojo de infraestructura ni uno
intermitente.

```diff
 # lib/failure_triage.py
+_INFRA = re.compile(r"rejected SSL upgrade|TimeoutError|ConnectionRefused|Connection refused|"
+                    r"connection (?:reset|was closed)|too many clients|could not connect to server|"
+                    r"Cannot connect to the Docker daemon|address already in use|ECONNREFUSED|ETIMEDOUT")
 def kind(text: str) -> str:
-    """outside | hook | test | other: which part of Rein said no."""
+    """outside | hook | infra | test | other: which part of Rein said no."""
     t = clean(text)
     ...
+    if _INFRA.search(t):
+        return "infra"
     if re.search(r"^(?:test|testIntegration|lint|typecheck|build) \[", t, re.M):
```

```diff
 # lib/ship_decide.py, _on_repo_gate_verdict, red branch
-        fails = state.repo_gate_fails.get(repo, 0) + 1
+        sig, why = triage.signature(event.reason), triage.kind(event.reason)
+        prev = state.repo_gate_sigs.get(repo)
+        state.repo_gate_sigs[repo] = sig
+        if why == "infra" or (prev and prev != sig):
+            # Not the change's fault, or not the same failure twice: no worker, the owner decides.
+            state.repo_gates[repo] = "setup"
+            _open(state, actions, None, "The full gate of %s is red for a reason no worker should fix "
+                  "(%s): %s. Retry the gate, or skip it (the audit starts without it)?"
+                  % (repo, "infrastructure" if why == "infra" else "intermittent: a different failure "
+                  "each run", event.reason[:QUESTION_DETAIL]), ["retry", "skip"], "repo_gate")
+            state.pending_gates[-1]["repo"] = repo
+            return
+        if not prev:  # first red: run it once more before spending a worker on it
+            actions.append(RunRepoGate(repo))
+            return
+        fails = state.repo_gate_fails.get(repo, 0) + 1
```

**Lo que se mantiene:**
- `ship_state.ShipState` gana `repo_gate_sigs: dict` (por defecto `{}`, compatible con los
  estados guardados).
- La pregunta reutiliza el tipo `repo_gate`, que ya existe: *retry* vuelve a correr el gate y
  *skip* sigue adelante. No hay acción nueva.
- **Nada se oculta.** El rojo se muestra al dueño con su texto y queda en el log del gate. El
  gate de cada tarea (lint, typecheck y tests afectados) no cambia, y CI tampoco.

**El brief de `GATE<n>` en `lib/ship.py`, cuando de verdad se lanza:**
```diff
-        lines += ["", "The full test suite of %s (run once every task was done, before the audit) failed; "
-                  "fix it -- the failing tests may be outside what the change touched:" % task.repo,
+        lines += ["", "The full test suite of %s failed the same way twice. First run each failing test "
+                  "alone (the testOne command). Fix only a failure your change causes. If a test passes "
+                  "alone, or fails the same way on the base, do not edit tests, fixtures or test setup: "
+                  "ask the owner with `orca orchestration ask`." % task.repo,
                   task.last_failure]
```

### C3. Alcance: la infraestructura de tests y la configuración global quedan protegidas

**Antes:**
- El guard solo rechaza cambios en `flow.config.json`, en la config de los runners y en
  `gate.protected`.
- `tests/db_setup.py` o un `conftest.py` pasaban sin problema.

**Después:** la infraestructura de tests queda protegida por defecto. Un plan la habilita
declarándola en el campo `Gate config:` de la tarea, el mismo mecanismo que ya existe.

```diff
 # lib/guard.py
+# Test infrastructure: what every test runs through. A task changes it only when it declares it.
+TEST_INFRA = ("conftest.py", "*/conftest.py", "pytest.ini", "tox.ini", "*/db_setup.py",
+              "vitest.config.*", "jest.config.*", "playwright.config.*", "karma.conf.*")
 def protected_patterns(*resolutions: dict, inherited=()) -> list:
-    patterns = ["flow.config.json"]
+    patterns = ["flow.config.json", *TEST_INFRA]
```

**Los briefs** (`GUARDRAILS` en `lib/ship.py`) añaden una línea:
```diff
+    "Never edit global or user configuration (~/.claude, ~/.codex, shell, git config) or a repository "
+    "other than your task's.",
```

**`skills/rein-plan/SKILL.md`** añade una línea: *«si una tarea necesita tocar infraestructura de
tests, decláralo en `Gate config:`»*.

### C4. Auditoría: un BLOCKING tiene que ser reproducible (texto, más una validación de 6 líneas)

**`skills/rein-audit/SKILL.md`:**
```diff
-   record `CHANGES_REQUESTED` with the failing commands as `BLOCKING` findings (a red gate is
-   by definition a blocker; see the severity vocabulary in step 7). A `SETUP` result is not a
+   do not record a red slot as a finding by itself. Run each failing test alone: a failure your
+   change causes is a `BLOCKING` with that test as its reproduction; one that passes alone or
+   fails on the base is infrastructure -- say so in the report, not as a finding. A `SETUP` result is not a
```
```diff
+   **Always check, and say for each "checked" or "not applicable":** permissions and scopes
+   (who can see or do what, tenant boundaries), security, concurrency and idempotency,
+   contracts between services or repos. A plan *Decision* is not exempt: one that grants more
+   access than the criteria need is a finding.
```
```diff
-   Every finding must be prefixed with one of the three severities — `BLOCKING` (must fix,
+   Every finding must be prefixed with one of the three severities. A `BLOCKING` must name
+   `file:line` and a reproduction (a test, a command, or a concrete input and the wrong result);
+   without one it is `IMPORTANT`. `BLOCKING` (must fix,
```

**`lib/gate.py`, en `record_review`:** valida lo único que puede validar sin juicio.
```diff
+_CITES_CODE = re.compile(r"[\w./-]+\.\w+:\d+")
+    for f in parsed_findings:
+        if f["severity"] == "BLOCKING" and not _CITES_CODE.search(f["text"]):
+            raise ValueError("a BLOCKING must name file:line and how to reproduce it; a red gate "
+                             "alone is not a finding (record it as IMPORTANT or in the report)")
```

**El brief de las tareas AUDIT** (`lib/ship.py`):
```diff
-        lines += ["", "The audit found these blocking problems; fix them (a finding that is not about "
+        lines += ["", "The audit found these blocking problems. For each: reproduce it first (a failing "
+                  "test), then make the smallest change that fixes its cause. Add no new table, service "
+                  "or abstraction the finding does not require; if you cannot reproduce it, say so with "
+                  "`orca orchestration ask` instead of changing code. (A finding that is not about "
```

## Qué se elimina

**Ahora:**
- la regla «un gate rojo es por definición un bloqueante»;
- el lanzamiento automático de `GATE<n>` en el primer rojo;
- la frase del brief que invita a tocar tests fuera del cambio;
- el autodisparo de `rein-role` y `rein-discover`.

**Candidatos para después del P0 (no ahora):**
- `rein-role` (lo cubren los subagentes y estilos nativos);
- `rein-discover` (lo cubre el modo plan nativo).

Se decide cuando el P0 lleve un tiempo en uso real.

## Riesgos y rollback

| Riesgo | Mitigación |
|---|---|
| Un fallo real con texto «de infraestructura» (por ejemplo un `TimeoutError` que causa el propio código) deja de recibir un worker | No se salta: pasa al dueño con el texto completo, y el dueño puede pedir una tarea de corrección |
| La segunda pasada del gate completo cuesta unos 7 min en la API cuando el rojo es real | Sigue saliendo mucho más barato que un worker perdido (28 min medidos) |
| Proteger `conftest.py` bloquea tareas que sí lo necesitan | El plan lo declara en `Gate config:`, mecanismo ya existente. El guard dice exactamente qué declarar |
| Un auditor (Codex) intenta registrar un BLOCKING sin `file:line` | `record_review` lo rechaza con el motivo, y el auditor lo vuelve a registrar bien. Los 12 BLOCKING sobre código de la fase 2 ya citaban `file:line`; solo se habría rechazado el «gate rojo» |

**Rollback:**
- 5 commits independientes en `p0-simplify`; cualquiera se revierte con `git revert`.
- La herramienta instalada sigue en `2.1.0` hasta que reinstales desde la rama.
- Para volver atrás: `uv tool install --force rein-agentic-kit==2.1.0` o reinstalar desde `main`.

## Verificación (determinista, sin agentes ni benchmark)

1. **Suite actual de REIN:** `python3 -m unittest discover -s tests -q`. Hoy da **1923 tests, OK
   (2 skipped)**, y tiene que seguir igual.
2. **Tests nuevos**, puros y sin I/O de red:
   - `test_failure_triage.py`: los textos reales del piloto («rejected SSL upgrade», `TimeoutError`
     de asyncpg) dan `infra`; un `AssertionError` de pytest da `test`.
   - `test_ship_decide.py`, secuencias de eventos:
     - rojo de infraestructura → pregunta `repo_gate`, sin tarea `GATE`;
     - rojo de test y luego verde → `passed`;
     - rojo de test dos veces con la misma firma → `GATE1`;
     - dos rojos con firmas distintas → pregunta, sin tarea.
   - `test_guard.py`:
     - un cambio en `tests/conftest.py` sin declararlo → problema;
     - declarado en `Gate config:` → limpio.
   - `test_review_fixes.py`:
     - un BLOCKING sin `file:line` → `ValueError`;
     - `"BLOCKING: app/x.py:12 ..."` → se acepta.
   - Brief: el de `GATE1` contiene «run each failing test alone» y ya no contiene «may be outside
     what the change touched».
3. **Humo manual opcional:**
   - `rein ship --dry-run` sobre un plan de ejemplo del repo, para comprobar que el estado nuevo se
     serializa;
   - `rein review record` con un BLOCKING sin `file:line`, para ver el mensaje.
