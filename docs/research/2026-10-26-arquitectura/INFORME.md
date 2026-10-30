# REIN — Investigación arquitectónica y optimización experimental

Fecha: 2026-10-26 · REIN `1c0f9ff` · Orca 1.4.240 · Claude Code 2.1.311 · codex-cli 0.164.0

Convención usada en todo el documento:

- **[H] Hecho observado**: medido en este trabajo o verificado en el código, con su referencia.
- **[I] Hipótesis o inferencia**: razonable pero no demostrada.
- **[R] Recomendación**: lo que propongo hacer, con la evidencia que la respalda.

Fuentes de evidencia, de la más a la menos fuerte:

1. **Corpus real de REIN**: 53 ejecuciones únicas de `rein ship` (51 changes, 172 tareas de plan y 63 de fix de auditoría) y
   657 transcripts de workers reales (459 de Claude y 198 de Codex), leídos en local sin coste de
   cuota (§4.1).
2. **Experimentos controlados de bajo consumo**: E1 (contexto de arranque), E2 (estrategias de
   contexto) y E3 (handoff) (§4.2–4.4).
3. **Lectura de código** de REIN, de la CLI y los skills de Orca, y de Gas Town y Agetor a un SHA
   fijo (§2). No se instaló ni ejecutó ningún sistema externo.

---

## 0. Respuestas cortas

**¿REIN aprovecha bien Orca y las capacidades nativas de los proveedores?**
En la orquestación sí: worktrees, Run/Task/Dispatch, `worker_done`, decision gates y cuotas, siguiendo
el contrato de seguridad de Orca al pie de la letra. En el contexto y la capacidad de los
proveedores, no. Hay cuatro huecos medidos:

- **(a)** Cada worker Claude arranca con unos 68,7k tokens de contexto fijo, y el 77% (mediana) de los
  tokens de un worker son relecturas de ese arranque. Unos 46k de ese arranque se pueden eliminar con
  flags que Claude Code ya ofrece (E1).
- **(b)** REIN descarta el resumen que cada worker envía en `worker_done`.
- **(c)** No usa `orca search`, que ya indexa 1.104 sesiones de los tres proveedores.
- **(d)** Infrautiliza Antigravity. Lo enruta solo a Gemini y lo frena por el máximo de sus buckets
  (53%), cuando el mismo plan de $20 ofrece Claude Sonnet/Opus 5.5 en otro bucket que está al 2%.

**¿Qué hacen mejor Gas Town, Agetor y otros?**
Cuatro cosas concretas y baratas:

- Un **contrato de resultado del worker** con un único recordatorio antes de declarar rojo (Agetor).
- La **deduplicación de reintentos por firma de fallo** (agent-orchestrator).
- Un **circuit breaker con escalado** y la **reclasificación "cuota ≠ rojo"** (Gas Town).
- Un **cercado con nonce** del texto que pasa de un agente a otro (Agetor).

Su arquitectura de conjunto no conviene. Gas Town tiene 2+2R agentes LLM siempre encendidos, Dolt,
tmux, 249k LOC de Go y falsos positivos de liveness que matan agentes sanos. Agetor no tiene gates
deterministas y su bus factor es 1.

**¿Conviene usar Graphify, CodeGraph, Context Packs o memoria compartida?**

- **CodeGraph y Graphify: no como parte del flujo de los workers.**
  - En 657 workers reales, CodeGraph se usó en 11 y Graphify en 5.
  - En E2 (proxima-api, 632k LOC; Haiku y una confirmación con Sonnet), todas las estrategias dieron
    F1 = 1,0.
  - Con la pista en el system prompt, el agente no usó el grafo ni una vez. Cuando se le obliga a
    usarlo, sigue verificando con grep y los turnos no bajan.
  - La única señal es un −22% de tokens con Sonnet en el impacto a dos niveles, con n=2: no
    concluyente (§4.3).
- **Context Packs deterministas, sí, pero pequeños**, generados por Python a partir de lo que el plan ya
  sabe (§5).
- **La memoria compartida no compensa como base nueva.**
  - El solapamiento real de archivos entre workers sucesivos de un change es bajo: mediana del 8% en
    tareas de implementación.
  - En E3, pasar hechos verificados que se pueden volver a obtener **no** ahorró turnos: el agente los
    re-verifica (§4.4).
  - Lo que merece la pena conservar es lo que el código no dice: qué se intentó, por qué falló, qué
    decidió el dueño y qué trampas tiene el entorno.

**¿Cuál es la arquitectura más simple y eficiente?**
La actual, con menos cosas, no con más:

1. Workers lanzados con un perfil de contexto mínimo.
2. Handoff estructurado y verificado entre tareas.
3. Clasificación determinista de los fallos antes de gastar un reintento: entorno, cuota, flaky o código.
4. Routing por bucket de cuota real y modelos descubiertos, no hardcodeados.

Sin orquestador LLM, sin base de grafos y sin MCP nuevo (§7).

---

## 1. AS-IS: la arquitectura real de REIN

### 1.1 Reparto de responsabilidades REIN ↔ Orca [H]

| Responsabilidad | Quién | Dónde |
|---|---|---|
| Worktrees, setup hooks, terminales, lanzamiento de claude/codex/antigravity | Orca | `orca orchestration worker-start --worktree new-top-level --setup run` (`ship.py`, `orca_client.py:127`) |
| Run, Task DAG, Dispatch, mailbox (`worker_done`, `question`, `escalation`), decision gates | Orca | `orca_client.py:114-212` |
| Lectura de cuotas por proveedor | Orca | `orca account list --json` → `quota.parse_rate_limits` (`quota.py:41`) |
| Circuit breaker de arranque (3 fallos de `worker-start` → task failed) | Orca | skill `orchestration` («After three consecutive failures…») |
| Indexado full-text de sesiones de agentes | Orca (no usado por REIN) | `orca search`, 1.104 sesiones y 252k mensajes indexados |
| Plan → tareas con criterios, `Verification`, `Repo:`, decisiones | REIN | `plan.py`, `plan_check.py` |
| Brief de cada worker | REIN | `ship.build_task_spec` (`ship.py:153`) |
| Gates deterministas (lint, typecheck, tests afectados, suite completa por repo) | REIN | `gate*.py`, `verify.py`, `affected.py`, `detect.py` (3.978 LOC) |
| Guardas (escritura fuera del worktree, config del gate protegida, commit-msg hooks) | REIN | `guard.py` (1.066 LOC) |
| Decisiones de reintento, auditoría, rondas de fix, freno por cuota | REIN | `ship_decide.py` (función pura de 1.276 LOC) |
| Estado reanudable | REIN | `<root>/.rein/ship/<change>.json` (`ship_state.py`) |
| Medición de tokens | REIN | `token_report.py` → `~/.claude/rein/runs.jsonl` (solo transcripts de Claude) |

**Valoración [H].** REIN no duplica a Orca en lo esencial. El driver sigue el contrato de seguridad
de Orca:

- direcciona por dispatch id;
- tras 3 esperas vacías consulta `worker-list` y su `nextAction`;
- solo reintenta con prueba positiva de que el worker salió.

La duplicación que queda es menor y justificada. Un ejemplo es `close_idle_setup_tabs`
(`orca_client.py:456`), que cierra las pestañas de setup que Orca deja abiertas.

**Tamaño [H].** 18.057 LOC en `plugins/rein/` y 25.521 LOC de tests (1.852 tests):

| Componente | LOC |
|---|---:|
| Pila de `ship` (ship, decide, state, watch, answer, done) | 5.070 |
| gate/verify/affected/detect/serve | 3.978 |
| guard | 1.066 |
| quick lane | 1.622 |
| plan + plan_check | 921 |
| orca_client + quota + startup_dialog | 726 |

Entre el 17 y el 25 de octubre hubo unos 190 commits, la mayoría «review round N».
**[I]** El coste de mantenimiento ya es alto. Cualquier propuesta debería **quitar** código o, como
mínimo, no añadir otro subsistema.

### 1.2 Cómo se construye y transfiere el contexto [H]

El brief de una tarea (`build_task_spec`, `ship.py:153-191`) contiene:

- el preámbulo fijo del worker;
- el título, los criterios de aceptación y el comando de verificación;
- el *why* del plan;
- las decisiones del plan: las citadas siempre, el resto hasta 1.500 caracteres;
- lo que queda fuera de alcance;
- en un reintento, el fallo del gate (hasta 6.000 caracteres, `ship_decide.GATE_FAILURE_CAP`);
- en un fix de auditoría, los hallazgos;
- las guardas.

Tamaño medido del brief: mediana de 9.400 caracteres (unos 2,4k tokens).

**Lo que no se transfiere [H]:**

1. **El resumen del worker anterior.** Orca obliga al worker a terminar con un `worker_done` cuyo
   cuerpo es un «three-sentence executive summary: work, findings, remaining» (skill `orchestration`,
   línea 843). REIN solo lee `payload.outcome` y `dispatchId` (`ship.py:1895-1898`) y descarta el
   cuerpo. Las tareas dependientes heredan el código por git, porque comparten el worktree del repo,
   pero no los hallazgos.
2. **Lo que intentó y descubrió el worker fallido.** Un reintento recibe el log del gate, pero no
   qué probó el worker ni por qué.
3. **Ningún índice.** `retrieval.codegraph: "auto"` solo lo lee `rein-discover`, que es la sesión de
   planificación. Los briefs de los workers no mencionan CodeGraph ni Graphify, y en el corpus real
   casi no se usan (§4.1).

**El contexto que domina el coste no es el brief, sino el arranque [H, E1].**
En los 459 transcripts reales de workers Claude:

- El primer turno arranca con una mediana de **68.670 tokens**, y el brief es solo unos 2,4k de ellos.
- Codex arranca con **23.108**.
- La composición se ve en el `api-request-shape` del propio transcript:
  - unos 20k tokens de definiciones de herramientas built-in (Agent, Artifact, Workflow…);
  - unos 3k del system prompt;
  - unos 9k de skill listing;
  - unos 9k de deferred tools y MCP;
  - unos 3k de hooks `SessionStart` de plugins de usuario, como AWS Startup Advisor y superpowers,
    que no aportan nada a un worker;
  - CLAUDE.md y AGENTS.md;
  - el brief.
- Con 13 turnos de mediana, el **77%** (mediana; p25 63%, p75 87%) de los tokens de un worker Claude
  es releer ese arranque.

### 1.3 Comunicación, handoffs, estado y recuperación [H]

- **Comunicación**:
  - worker → driver por el mailbox de Orca (`check --wait --types worker_done,escalation,question`);
  - driver → humano por decision gates;
  - humano → worker por `reply`.

  Los cuerpos de los mensajes se tratan como datos (`ship.py:9`). Es correcto, pero no se cercan
  con un nonce cuando se reinyectan en el brief de otro agente.
- **Estado**: el JSON por change, que se guarda tras cada llamada mutante y se reanuda con `run-use`.
  Hay reanudación automática tras una caída de Orca (`RESUME_MAX = 3` por incidente, `ship.py:2280`).
- **Supervisión**:
  - `stallMinutes` (10) abre un gate `stalled` (wait | resend | retry);
  - `taskTimeoutMinutes` (45) abre otro gate;
  - `trustProbeSeconds` vigila los diálogos de arranque.

### 1.4 Memoria, índices y caché por proveedor [H]

| | Claude Code (Sonnet/Opus 5.5) | Codex (gpt-6.1-sol) | Antigravity |
|---|---|---|---|
| Caché de prompt | Sí. `cache_read` es el 93% (mediana) de los tokens de un worker | Sí. `cached_input_tokens` es el 94% | No observable |
| Contexto de arranque | 68,7k (mediana real) | 23,1k | No medido |
| Memoria del proyecto | CLAUDE.md, auto-memory | AGENTS.md | AGENTS.md (probado el 2026-10-19) |
| Reanudación | `--resume`, `--fork-session` | `codex resume` | `--conversation` |
| Búsqueda entre sesiones | `orca search` (los tres, indexados por Orca) | ídem | ídem |
| Modelos descubribles | No (lista fija) | `app-server model/list` (según Agetor) | `agy models`: Gemini 4 Argon, Gemini 3.8 Flash, **Claude Opus/Sonnet 5.5**, GPT-OSS |
| Cuota visible en Orca | session/weekly/fableWeekly | session/weekly + créditos de reset | **buckets por familia de modelo** |

### 1.5 Limitaciones observadas en ejecuciones reales [H]

Datos de las 53 ejecuciones únicas de `rein ship` (`bench/analyze_states.py`):

**Primer intento y tendencia.**

- El 66% de las tareas de plan pasan al primer dispatch (418 dispatches para 235 tareas, contando
  los fix).
- La tasa de primer intento **empeora con el tiempo**:

  | Ejecuciones | Primer intento | Dispatches por tarea |
  |---|---:|---:|
  | Hasta el 20-oct | 77% | 1,58 |
  | 21–22-oct | 66% | 2,06 |
  | Desde el 23-oct | 55% | 2,18 |

  **[I]** Influye que las tareas recientes (la serie *availability*) son más grandes, así que esta
  tendencia no demuestra una regresión de REIN.

**Por qué se re-despachan las tareas** (ejecuciones desde el 23-oct, 30 re-despachos):

| Causa | Re-despachos |
|---|---:|
| Sin `last_failure`: stall, arranque abortado o retry del dueño | 11 |
| El hook pre-commit de beads reescribe `.beads/issues.jsonl` y el guard rechaza el commit | 7 |
| Escritura fuera del worktree | 2 |
| Tests rojos reales | 10 |

**Unos dos tercios de los reintentos recientes no tienen que ver con la calidad del código** del
worker, sino con el entorno, los hooks del repo o la supervisión.

**Calidad del feedback de un reintento.**

- En 19 fallos de test, el texto que recibe el worker siguiente tiene una mediana de 5.044 caracteres.
- El 74% conserva secuencias ANSI.
- En el 53%, las líneas de ruido (warnings de Angular, tablas de chunks) superan a las de señal.
- Los workers Claude de reintento no editan nada en el 78% de los casos (heurística, ver §4.1).

**[I]** Muchos reintentos solo «re-verifican» un fallo que no era suyo.

**Problemas de gate ya corregidos.** El slot `testOne` pasaba `{target}` literal y había
`--include={target}` desconocido para `ng test`. Fueron causas de reintento del 20 al 22-oct y se
corrigieron en `0e9aa35`, `904e412` y `45dd108` (22-oct).

**Auditoría.**

- 35 de 53 ejecuciones tienen tareas de fix de auditoría (63 fix frente a 172 tareas de plan).
- Rondas de auditoría: 1 en 23 ejecuciones, 2 en 10 y 3 en 2. El resultado final fue *approved*
  en 49 de 53.
- La auditoría y sus fixes son el 36% de los tokens de Claude en workers: audit 20% y audit_fix 16%.

**Repos grandes y multi-repo.**

- 14 de las 53 ejecuciones fueron multi-repo y funcionan, una por worktree y repo.
- En proxima-api, el gate llega a ejecutar la suite completa (11.400 tests, 6–10 min) dentro del
  ciclo de tarea, cuando se supera `affectedMax`.

### 1.6 Duplicaciones, sobrecargas e infrautilización [H]

| Item | Estado |
|---|---|
| Resumen `worker_done` | Orca lo exige y REIN lo descarta |
| `orca search` (Seance multi-proveedor ya construido) | No se usa |
| `orca orchestration worker-read` (salida archivada del worker) | No se usa |
| `task-update --result` | No se usa |
| Buckets de cuota de Antigravity y `resetsAt` | Se ignoran: el freno usa el máximo y no la ventana de reset |
| Modelos hardcodeados (`quota.py:10-13`: `gpt-6.1-sol`, `gemini-4-argon-medium`) | Sin descubrimiento |
| Serena | Usada 0 veces en 657 workers. D2 ya la marcó como «UNMEASURED» |
| CodeGraph | 11 de 657 workers. El índice de proxima-api pesa 243 MB |
| Graphify | 5 de 657 workers |
| Plugins de usuario (AWS Startup Advisor, superpowers, playwright) cargados en cada worker | Unos 3,6k tokens por turno sin función (E1) |

---

## 2. Investigación competitiva

Los informes completos, con `archivo:línea` a un SHA fijo, están en
[`research/gastown.md`](research/gastown.md) y [`research/agetor.md`](research/agetor.md). Ahí
cada afirmación está marcada como implementada, solo documentada o inferencia.

### 2.1 Gas Town (`gastownhall/gastown` @ `de2ce8e`)

**Qué es [H].**

- Un binario Go más **sesiones LLM de larga vida en tmux** para casi todos los roles: Mayor, Deacon,
  Boot, Dogs, Witness, Refinery, Polecats y Crew.
- Beads guarda el estado sobre un **servidor Dolt SQL**, que funciona a la vez como cola, mailbox,
  registro y hook.
- Lo único determinista y siempre encendido es el daemon, con un latido cada 3 min
  (`daemon.go:853`).

**Lo que el README anuncia y el código no hace [H].**

- La cola Bors *batch-then-bisect* (`refinery/batch.go:211,487`) solo la llaman los tests. El
  Refinery real es un LLM que ensaya el merge rama por rama.
- Su revisión de calidad «does NOT gate merges».
- **Seance** es `claude --fork-session --resume <id> -p <pregunta>`. Solo funciona con Claude y
  ningún flujo la invoca de forma automática.

**Coste operativo [H].**

- Con R rigs hay 2+2R agentes LLM siempre encendidos.
- Requiere tmux, Dolt ≥ 2.6 y `bd`.
- 249k LOC de Go más 226k de tests; solo `doctor` ocupa 20k.
- 512 issues abiertos, con falsos positivos de liveness que matan agentes sanos (#4787, #4890, #4887).
- Estuvo parado de julio a octubre, y el issue #4930 lo da por deprecado.

### 2.2 Agetor (`alamops/agetor` @ `c5cf1a2`)

**Qué es [H].**

- Un kanban local para macOS (Electrobun/Bun + React, con CLI/TUI Ink) que lanza los CLIs reales,
  una tarea por worktree.
- Pipelines en grafo con un **handoff JSON**
  `<handoff>{purpose, summary, reason, next, artifacts, openQuestions, status}`:
  - lo lee un parser tolerante con topes;
  - si falta, manda un único recordatorio a la misma sesión antes de marcar Blocked;
  - el texto de otros agentes va **cercado con un nonce**.
- SQLite con 74 migraciones.
- Descubre modelos de verdad para Codex (`app-server model/list`), Cursor, agy y fx. Claude y Gemini
  usan una lista fija.
- Watchdog de silencio del transcript (10 min) que no mata nada.

**Debilidades [H].**

- **Solo valida el formato del handoff.** No hay gates deterministas ni tests en el runner.
- Unas 312k LOC de TypeScript, sin CI.
- 308 de 324 commits son de un mismo autor.
- Lee la cuota de endpoints no documentados.

### 2.3 Otros (resumen de un subagente, verificado a nivel de mecanismo)

- **agent-orchestrator (ComposioHQ)**: deduplica reintentos por **firma de fallo**
  `(gate, commit, log_tail)` (`reactions.go:929-997`).
- **claude-squad**: detecta inactividad por el hash de `capture-pane`.
- **vibe-kanban**: lee la cuota de Codex con `account/rateLimits/read`.

### 2.4 Comparación por capacidad

**Leyenda:**

- **KEEP**: REIN u Orca ya lo resuelven bien.
- **ADOPT**: adoptarlo, con evidencia suficiente.
- **EXPERIMENT**: puede valer, pero falta medirlo.
- **REJECT**: duplica, complica o aporta poco.

| Capacidad | Externo (cómo) | ¿En REIN u Orca? | Ventaja comprobable | Coste | Veredicto |
|---|---|---|---|---|---|
| Orquestación determinista sin LLM | Gas Town: patrullas LLM. Agetor: runner TS | REIN: `ship_decide` puro | REIN gana. Gas Town lleva años migrando lógica de prompts a Go | — | **KEEP** |
| Gates deterministas por tarea más auditoría independiente | Gas Town: gates ejecutados por un LLM. Agetor: ninguno | REIN | REIN gana | — | **KEEP** |
| Worktrees, terminales, lanzamiento multi-CLI | tmux propio en ambos | Orca | Orca gana: no hay que mantener tmux | — | **KEEP** |
| Persistencia | Dolt (Gas Town), SQLite (Agetor) | JSON por change más Run durable en Orca | No hay evidencia de que haga falta una BD. 53 ejecuciones reanudadas con JSON | — | **KEEP** |
| Contrato de resultado del worker más un recordatorio antes de rojo | Agetor `pipeline.ts:781-871` | Orca ya exige el resumen en `worker_done`; REIN lo tira | Evita gastar un worker fresco por un formato perdido. Da triaje y handoff | ~200 LOC | **ADOPT** |
| Cercado con nonce del texto entre agentes | Agetor `pipeline.ts:86-145` | No | Defensa barata frente a la inyección a través de logs de gates y hallazgos | ~40 LOC | **ADOPT** |
| Firma de fallo para no repetir el mismo reintento | agent-orchestrator `reactions.go:929` | No (`maxRetries` ciego) | En §1.5, el mismo fallo de entorno (hook de beads) se repite en 3 ejecuciones | ~60 LOC | **ADOPT** |
| Distinguir cuota o entorno de rojo antes de gastar un reintento | Gas Town `quota/scan.go` | Parcial: el gate 126 es «setup» | 2/3 de los reintentos recientes no eran del código | ~100 LOC más una tabla | **ADOPT** |
| Circuit breaker por tarea con escalado | Gas Town `deacon/redispatch.go:218` | `maxRetries` más gates. Orca corta en 3 `worker-start` | REIN ya escala a gate. Falta pausar todo ante una «muerte masiva» (proveedor caído) | ~40 LOC | **ADOPT** (solo lo de muerte masiva) |
| Seance (fork-resume para preguntar al worker anterior) | Gas Town `seance.go:208-291` | `orca search` indexa las sesiones, y Claude tiene `--fork-session` | No demostrada en Gas Town (uso manual). Solo Claude | Bajo | **EXPERIMENT** |
| Watchdog de silencio del transcript | Agetor `claude-tmux.ts:1954` | `stallMinutes` y la evidencia «parked on a prompt» de Orca | Equivalente. REIN ya lo tiene a su manera | — | **KEEP** |
| Descubrimiento de modelos | Agetor (Codex, agy) | Hardcodeado en `quota.py` | `agy models` lista Claude 5.5 dentro de Antigravity (§1.4) | ~80 LOC | **ADOPT** (versión mínima) |
| Lectura de cuota desde endpoints no documentados | Agetor | Orca la da de forma estable | Orca gana | — | **REJECT** |
| Snapshot congelado del plan al lanzar | Agetor `types.ts:628` | No | Evita cambios de semántica a mitad de ejecución. Sin incidentes observados | ~40 LOC | **EXPERIMENT** (baja prioridad) |
| Merge rehearsal y post-merge proof | Gas Town (formula y `engineer.go:1538`) | REIN no integra (`in-review`); el dueño abre el PR | Solo útil si REIN llegara a integrar | Medio | **REJECT** por ahora |
| Agentes supervisores LLM siempre encendidos (Mayor, Witness, Deacon) | Gas Town | No | Coste continuo y falsos positivos documentados | Alto | **REJECT** |
| Base de datos de trabajo (Beads/Dolt) | Gas Town | No | Duplica Orca y el plan | Alto | **REJECT** |
| Kanban/UI propia | Agetor | Orca tiene UI | Duplica | Alto | **REJECT** |

---

## 3. Context Engineering

Se evaluaron estas estrategias:

| | Estrategia |
|---|---|
| A | Nativa |
| B | Nativa + CodeGraph |
| C | Nativa + Graphify |
| D | Context Pack determinista |
| E | Context Pack + CodeGraph |
| F | Memoria compartida (handoff verificado) |

Los resultados de E2 y E3 están en §4. El veredicto por tipo de tarea está en §5.

**Coste de indexación medido [H]** (clones `--depth 1`, máquina local):

| Repo | LOC Python | CodeGraph `init` | RSS | DB | Graphify `update` (AST, sin LLM) | RSS | Salida |
|---|---:|---:|---:|---:|---:|---:|---:|
| rein | 18k (lib) + 25k (tests) | 1,1 s | 352 MB | 17 MB | 3,4 s | 210 MB | 20 MB |
| proxima-api | 632k | 6,9 s | 2,2 GB | 201 MB | 74,8 s | 1,7 GB | 234 MB |

**Tipos de memoria:**

| Tipo | Qué es | Dónde vive hoy |
|---|---|---|
| Ejecución | Qué tarea, qué dispatch, qué intento | JSON de REIN más Run de Orca. **Resuelto** |
| Episódica | Qué hizo el worker N | Transcripts, indexados por `orca search`. **Existe, pero no se usa** |
| Semántica | Hechos validados: «X se llama desde estos 10 archivos», «este hook reescribe .beads» | **No existe.** Es lo que transmitiría un handoff |
| Estructural | El grafo de símbolos | CodeGraph y Graphify. **Existe, pero apenas se usa** |

Solapamiento real entre workers sucesivos del mismo change [H] (archivos leídos por el worker k que
ya había leído alguno anterior, sin contar archivos meta):

| Tipo de worker | Mediana | p75 | p90 |
|---|---:|---:|---:|
| Tarea de implementación | 8% | 20% | 37% |
| Reintento | 13% | 29% | 58% |
| Fix de auditoría | 23% | 43% | 73% |
| Re-auditoría | 50% | 68% | 100% |

En términos absolutos: una mediana de 1 archivo y una media de 2,6 archivos ya leídos por worker.
El tope de ahorro que podría dar una memoria compartida en tareas de implementación es pequeño. Donde
se repite la exploración es en el ciclo **fallo → reintento → auditoría → fix**, que es precisamente
donde un handoff del worker anterior aporta más.

---

## 4. Informe de benchmarks

### 4.1 Observacional: el corpus real (sin coste de cuota) [H]

**Fuente.** `bench/analyze_workers.py` sobre 657 transcripts de workers reales de `rein ship`:

| Proveedor | Workers | Modelo |
|---|---:|---|
| Claude | 419 | Sonnet 5.5 |
| Claude | 40 | Opus 5.5 |
| Codex | 198 | gpt-6.1-sol |

Por tipo de worker:

| | Workers |
|---|---:|
| Tarea | 258 |
| Reintento | 139 |
| Fix de auditoría | 109 |
| Fix de gate | 17 |
| Auditoría | 73 |
| Re-auditoría | 61 |

Medianas:

| Métrica (mediana) | Claude tarea | Claude reintento | Claude fix auditoría | Codex tarea | Codex reintento |
|---|---:|---:|---:|---:|---:|
| Duración (min) | 3,8 | 0,95 | 2,3 | 6,6 | 2,4 |
| Turnos | 20 | 6 | 11,5 | 7 | 5 |
| Llamadas a herramientas | 19,5 | 5 | 11 | 35 | 17,5 |
| Archivos únicos leídos | 9 | 0 | 3 | 27,5 | 7 |
| Relecturas del mismo archivo (fracción) | 0,09 | 0 | 0 | 0,11 | 0 |
| Tokens totales (M) | 2,01 | 0,47 | 1,06 | 2,90 | 1,03 |
| `cache_read` (fracción) | 0,95 | 0,88 | 0,93 | 0,96 | 0,93 |
| Contexto máximo (k) | 115 | 82 | 99 | 111 | 72 |
| Contexto del primer turno (k) | 68,7 (todos los workers Claude) | | | 23,1 (todos los Codex) | |

Uso de herramientas de recuperación en los 657 workers:

| Herramienta | Workers que la usaron |
|---|---:|
| CodeGraph | 11 |
| Graphify | 5 |
| Serena | 0 |
| `orca search` | 0 |

Reparto de tokens de Claude en workers (1.022 M tokens):

| Tipo | Share |
|---|---:|
| Tarea | 54% |
| Auditoría | 20% |
| Fix de auditoría | 16% |
| Reintento | 7% |

Codex suma 402 M tokens, el 28% del total de workers.

**Advertencias.**

- «Tiempo hasta la primera edición» y «sin edición» son heurísticas. Los workers Claude en modo auto
  editan casi siempre con Bash (heredoc, sed), no con la herramienta Edit, y la regex de escritura
  tiene falsos positivos.
- Antigravity no deja transcripts parseables en este corpus: solo hubo 2 tareas.

### 4.2 E1: contexto de arranque de un worker Claude [H]

**Montaje.** `bench/e1_startup_context.sh`: una llamada `claude -p "Reply with exactly: OK"` con Haiku
por configuración, en un clon de proxima-pos (con su CLAUDE.md y AGENTS.md). 2 repeticiones por
configuración; las diferencias entre repeticiones fueron de ≤5 tokens.

| Config | Qué cambia | Contexto del 1.er turno | Δ frente a C0 |
|---|---|---:|---:|
| C0 | Por defecto (como hoy, en modo `-p`) | 59.019–59.024 | — |
| C1 | `--strict-mcp-config` | 47.641 | −19% |
| C2 | + `--disable-slash-commands` | 46.166 | −22% |
| C3 | + `--tools Bash,Read,Edit,Write,Grep,Glob` | 16.116–16.119 | −73% |
| C4 | + plugins de usuario desactivados (`--settings`) | 12.534–12.535 | **−79%** |
| C5 | Solo `.claude/settings.local.json`: plugins off + deny de 7 herramientas | 29.925–29.927 | −49% |
| **C8** | Solo `.claude/settings.local.json`: plugins off + deny de 10 herramientas, **ToolSearch se mantiene** | **21.224** | **−64%** |
| C6/C7 | Como C8, pero **denegando también ToolSearch** | **533.541** | **+804%** ⚠️ |

**Lectura [H].**

- La mayor parte del arranque son definiciones de herramientas built-in que un worker no usa:
  Agent, Artifact, Workflow, ScheduleWakeup, ReportFindings…
- Denegar ToolSearch sin quitar los servidores MCP hace que **todas** las herramientas diferidas
  (rea, signoz, context7…) se carguen en el primer turno, y el arranque se multiplica por 9.
- **Un perfil lean sin una medición que lo vigile es peligroso.**

**Contrafactual [I].**

- Hipótesis: los mismos turnos y un arranque menor en la misma cantidad absoluta.
- Con C8 aplicado solo a implementadores (los auditores conservan el perfil completo): **−27%** de
  todos los tokens de workers Claude del corpus.
- Con C4: −40%.
- No se midió en modo interactivo dentro de Orca, ni el efecto sobre el porcentaje de cuota. Anthropic
  no publica cómo pesan los `cache_read` en la cuota de suscripción.

### 4.3 E2: estrategias de contexto, con el mismo modelo, configuración y commit [H]

**Montaje.**

- `bench/e2_context.py`, Haiku, perfil lean C4 en todas las estrategias, 2 repeticiones por celda,
  orden aleatorio con semilla y un calentamiento previo.
- Tareas:
  - Q1: callers en rein (18k LOC).
  - Q2: callers en proxima-api (632k LOC, 3.381 archivos).
  - Q3: análisis de impacto en proxima-api.
  - Q4: bug sembrado en rein (`>=` cambiado a `>` en el freno de cuota), descrito solo por su
    síntoma.
  - Q6: impacto a dos niveles en proxima-api (25 funciones de nivel 2 en 15 archivos).
- Verdad de referencia por AST.

Medianas [mín–máx]:

| Tarea | Estrategia | Calidad | Turnos | Llamadas | Usos del grafo | Tokens (k) |
|---|---|---|---|---|---:|---|
| Q2 callers, repo grande | A nativa | F1 1,00 | 4 [3–4] | 2 [2–3] | 0 | 48 [47–48] |
| | B + CodeGraph | F1 1,00 | 4 [2–5] | 2 [1–4] | 0 | 50 [35–65] |
| | C + Graphify | F1 1,00 | 2 [2–3] | 2 [1–2] | 0 | 43 [35–50] |
| | D pack | F1 1,00 | 3 [3–3] | 2 [2–2] | 0 | 38 [36–40] |
| | E pack + CodeGraph | F1 1,00 | 4 [4–5] | 4 [3–4] | 0 | 58 [57–59] |
| Q3 impacto, repo grande | A | F1 1,00 | 2 | 1 | 0 | 30 [30–30] |
| | B | F1 1,00 | 3 | 2 | 1 | 39 [31–47] |
| | D | F1 1,00 | 3 | 2 | 0 | 48 [47–48] |
| | BF (CodeGraph forzado) | F1 1,00 | 4 [3–4] | 2 [2–3] | 2 | 55 [47–63] |
| | CF (Graphify forzado) | F1 1,00 | 4 [3–4] | 2 [2–3] | 2 | 40 [31–48] |
| Q4 bug sembrado | A | 2/2 tests ok, diff mínimo | 6 [6–7] | 6 [5–6] | 0 | 78 [69–87] |
| | B | 2/2 | 10 [8–11] | 8 [7–10] | 0 | 138 [111–166] |
| | C | 2/2 | 6 [6–7] | 6 [5–6] | 1 | 74 [55–93] |
| | D | 2/2 | 6 [4–8] | 5 [3–7] | 0 | 97 [60–134] |
| | E | 2/2 | 6 [5–6] | 4 [4–5] | 0 | 70 [64–77] |
| Q6 impacto a 2 niveles | A | F1 1,00 | 22 [21–22] | 20 [20–21] | 0 | 262 [252–272] |
| | B | F1 1,00 | 22 [21–23] | 21 [20–22] | 4 | 253 [250–255] |
| | C | F1 1,00 | 20 [19–20] | 18 [18–19] | 0 | 223 [211–234] |
| | D | F1 1,00 | 22 [21–23] | 21 [20–22] | 0 | 286 [225–346] |
| | E | F1 1,00 | 21 [19–23] | 20 [18–22] | 4 | 248 [206–290] |
| | BF | F1 1,00 | 22 [21–23] | 21 [20–22] | 4 | 259 [250–268] |
| | CF | F1 1,00 | 22 [22–22] | 21 [21–21] | 2 | 259 [231–287] |
| **Q6, confirmación con Sonnet** | A | F1 1,00 | 18 [17–18] | 16 [16–17] | 0 | 218 [192–244] |
| | BF | F1 1,00 | 18 [17–18] | 16 [16–17] | 3 | 171 [145–197] |

Q1 (repo pequeño) da F1 1,00 con 2 turnos y unos 21k tokens en todas las estrategias. La tabla
completa está en `data/e2_summary.md`.

**Conclusiones [H, con n pequeño].**

1. **Calidad.** Todas las estrategias llegan a F1 1,00 o al fix correcto y mínimo en todas las
   celdas. Con tareas de este tipo hay **efecto techo**: grep más la lectura de archivos ya resuelven
   las búsquedas por nombre y el impacto a dos niveles en un repo de 632k LOC.
2. **Adopción.** Ofrecer CodeGraph o Graphify con una pista en el system prompt **no** hace que el
   agente los use (0 usos en B/C para Q1, Q2 y Q4). Esto coincide con los 11 y 5 usos de 657 en
   producción.
3. **Uso forzado.** Cuando se obliga al agente a usarlos (BF/CF), los usa 1–2 veces, **pero sigue
   verificando con grep y lecturas**, así que los turnos no bajan. Con Haiku los tokens no se mueven.
   Con Sonnet, BF gastó un 22% menos de tokens en Q6 (171k frente a 218k), con rangos que casi se
   tocan y solo n=2: **señal débil, no concluyente**.
4. **El Context Pack determinista (D)** no empeora la calidad y en Q2 recorta un poco los tokens
   (38k frente a 48k). En Q3, Q4 y Q6 añade su propio coste. **[I]** Su valor es mayor cuando
   sustituye exploración, no cuando la precede.

### 4.4 E3: handoff verificado frente a exploración nueva [H]

**Montaje.** Q5 es una pregunta de seguimiento: «¿qué callers de `resolve_effective_sale_price`
pasan `ctx`?». H recibe en el prompt el resultado validado de la tarea anterior (la lista de 10
callers y la firma). A empieza en blanco. Haiku, 3 repeticiones.

| | Calidad | Turnos | Tokens (k) |
|---|---|---|---|
| A sin handoff | F1 1,00 (3/3) | 5 [5–6] | 83 [83–87] |
| H con handoff | F1 1,00 (3/3) | 9 [5–9] | 96 [90–203] |

**Lectura [H].** El handoff **no** ahorró trabajo. En 2 de 3 repeticiones el agente volvió a
verificar la lista recibida.

**[I]** Transmitir hechos que se pueden volver a obtener con un grep no rinde. Un handoff vale cuando
lleva información que **no se puede derivar del código**:

- qué se intentó y por qué falló;
- qué decidió el dueño;
- qué trampa del entorno hay (el hook de beads);
- qué preguntas quedaron abiertas.

### 4.5 Coste de los experimentos y limitaciones

**Coste.**

- 69 sesiones con modelo en E2/E3: 7,2 M tokens, la mayoría `cache_read` de Haiku. Equivale a
  1,32 USD de API.
- E1: unos 2,6 M tokens, de los que 2,1 M fueron las 4 llamadas de la trampa de ToolSearch.
- Cuota de Claude durante todo el trabajo: sesión del 5% al 8%, semanal del 3% al 4%. Incluye esta
  propia sesión de investigación con Opus, así que **no se puede atribuir** solo a los experimentos.
- Codex y Antigravity: 0 consumo.

**Limitaciones.**

- No se ejecutó ningún benchmark de **implementación mediana, refactor complejo, cambio multi-repo ni
  recuperación tras interrupción con agentes reales**, porque cada uno exige `rein ship` sobre
  repositorios de trabajo, con worktrees y workers de Orca: escritura y riesgo para el producto. Su
  evidencia sale del corpus observacional (§4.1, §1.5).
- Para decidir con datos sobre esos casos falta:
  1. un change piloto real con el perfil P0-1 frente al ledger actual;
  2. 3–5 tareas sombra con Antigravity-Sonnet;
  3. una tarea sembrada multi-repo (contrato API → admin) para medir el gate de contrato de P2-4.
- La comparación entre proveedores se dejó fuera de forma deliberada, solo con datos observacionales:
  Codex arranca con 3 veces menos contexto y hace menos turnos (7 frente a 20), pero más llamadas por
  turno. Su coste por tarea en tokens es parecido (2,9 M frente a 2,0 M).

---

## 5. Recomendación de Context Engineering por tipo de tarea [R]

| Tipo de tarea | Estrategia recomendada | Por qué |
|---|---|---|
| Bug pequeño o cambio localizado | **A nativa + perfil lean (P0-1)** | E2-Q4: todas las estrategias resuelven; B incluso empeora (10 turnos frente a 6). El coste lo domina el arranque, no la exploración |
| Implementación mediana | **A + brief con los archivos que el plan ya conoce** (un pack mínimo, D, solo con lo que `rein-plan` descubrió) | El planner ya exploró. Repetirlo es el único solapamiento real (§3). En E2, D nunca empeora la calidad |
| Análisis de dependencias o impacto | **A** (grep). CodeGraph solo como verificación opcional en la sesión de **planificación** | E2-Q3/Q6: sin diferencia de calidad ni de turnos. Con Sonnet, señal débil de −22% de tokens |
| Refactor amplio (muchos callers, varios niveles) | **EXPERIMENT**: B forzado con Sonnet y n ≥ 5 | Es el único sitio con una señal. Falta evidencia |
| Multi-repo | **A + contrato explícito en el plan** (endpoint, schema) **+ gate de contrato** (P2-4) | Los BLOCKING reales de auditoría son mayoritariamente contratos entre repos, no falta de contexto |
| Reintento | **Fallo limpio + firma (P0-2) + resumen del worker anterior como «no verificado»** | 2/3 de los reintentos no son del código. E3: los hechos derivables no ahorran nada; lo que se intentó, sí puede |
| Fix de auditoría / re-auditoría | **Hallazgos + `worker_done` del implementador** | Solapamiento del 23–50%; los hallazgos ya son el contexto |

**Veredicto sobre las herramientas.**

- **CodeGraph:** KEEP solo como herramienta del planner, sin promoverlo a los workers. Tiene un coste
  de índice de 7 s y 201 MB en proxima-api, y el agente no lo adopta.
- **Graphify:** REJECT para el flujo. Construir el grafo tarda 75 s y ocupa 234 MB; no se adopta y no
  mejora nada.
- **Context Packs:** ADOPT en versión mínima y determinista, generados por `rein-plan`.
- **Memoria compartida o grafo de conocimiento:** REJECT. Véase la arquitectura de memoria en
  [TO-BE.md §6](TO-BE.md).

Continúa en [TO-BE.md](TO-BE.md): §6 Memoria, §7 TO-BE y §8 Roadmap. La reproducibilidad está en
[REPRODUCIBILIDAD.md](REPRODUCIBILIDAD.md).
