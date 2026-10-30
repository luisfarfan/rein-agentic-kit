# Agetor: investigación de arquitectura a nivel de código fuente

- **Repo:** https://github.com/alamops/agetor (MIT). El clon `--depth 50` está en `scratchpad/ext/agetor`.
- **Commit analizado:** `c5cf1a2d431f2891506a26010695289a1b6c46ae` (2026-10-25 21:09 -0400). Todas las citas `archivo:línea` se refieren a este SHA.
- **Método:** solo leí código, docs, tests e issues/PRs. No instalé, no compilé y no ejecuté nada del proyecto. La única excepción: apliqué los 74 `.sql` de `src/bun/migrations/` en el orden de `index.ts` a una base SQLite **en memoria** con Python (`sqlite3`), para reconstruir el esquema final. Antes verifiqué que no hay `ATTACH` ni `load_extension`. El resultado está en `scratchpad/research/agetor-schema.sql`.
- **Leyenda:** **[IMPL]** = está implementado en el código (con cita). **[DOC]** = solo lo afirma el README, `CLAUDE.md` o `docs/plans`. **[INF]** = inferencia mía.

---

## 0. Qué es Agetor, en una frase

Es una app de escritorio macOS (Electrobun: proceso principal en Bun y UI React en una WebView), más un CLI/TUI (Ink) y un daemon headless. Funciona como un **kanban que lanza los CLIs reales** (claude, codex, cursor-agent, gemini, fx, agy). Cada tarea corre en su propio git worktree. Encima tiene un motor de **pipelines** (un grafo de "Agents" con handoffs JSON). Todo el estado vive en SQLite. **[IMPL]** `package.json`, `src/bun/index.ts`, `src/bun/headless.ts`.

Comparado con REIN, la filosofía es otra. Agetor es un **plano de control interactivo centrado en el humano**: el humano mira, responde preguntas y aprueba. No es un ejecutor batch de planes con gates. Los pipelines existen, pero la validación entre etapas es de **formato**, no de **corrección**.

---

## 1. Pipelines de agentes

### 1.1 Definición de etapas
- **[IMPL]** El modelo es `PipelineGraph = { steps, edges, startStepId }` (`src/shared/types.ts:495`). Cada `PipelineStep` (`types.ts:456-480`) tiene:
  - `agentProfileId`: un perfil guardado con harness, modelo, effort, modo, instrucciones y skills.
  - `instructions`: texto libre, hasta 20 000 caracteres.
  - `transition: "choose" | "all"`: elegir una arista o hacer fan-out.
  - `join: "any" | "all"`: cada llegada relanza el paso, o el paso espera a todas sus fuentes (fan-in).
  - `subagents: { profileIds, cap }`.
- Las aristas llevan `label` y admiten ciclos (`types.ts:485`). `maxSteps` va de 1 a 200, con default 25 (`types.ts:749-750`).
- **[IMPL]** El grafo se valida con `validatePipelineGraph` (`src/shared/pipeline.ts:338`). Los límites están en `PIPELINE_LIMITS` (`types.ts:742-790`).
- **[IMPL]** **Snapshot congelado al lanzar.** Al primer Run se copian el grafo y todos los perfiles referenciados a `PipelineRunState.snapshot` (`types.ts:628-640`), y se persisten en `tasks.pipeline_run`. Editar el pipeline después no afecta a la ejecución en curso.
- **[IMPL]** **Cada ejecución de un paso es una tarea oculta nueva** (`tasks.insert`, `src/bun/pipeline-runner.ts:795`), con `pipeline_parent_id`/`pipeline_step_id`, que **comparte el worktree del padre**. Un loop Review→Implement crea otra tarea y por lo tanto otra sesión de agente nueva. **[DOC]** README:162 advierte que los pasos paralelos comparten worktree y que nada impide que editen los mismos archivos. **[IMPL]** El prompt solo pide no tocar fuera de su alcance ni hacer `checkout/reset/stash/rebase` (`pipeline.ts:1113-1121`). No hay ningún lock.

### 1.2 Cómo pasa el contexto entre etapas
- **[IMPL]** **JSON estructurado dentro de un tag**, al final del último mensaje del asistente. `HANDOFF_TAG = "handoff"` (`pipeline.ts:28`). El esquema (`types.ts:542-570`) es:
  `{schemaVersion:1, purpose, summary, reason, next, artifacts[], openQuestions[], status?: "done"|"blocked"}`.
- **[IMPL]** El prompt de cada paso se arma de forma **determinista** en `composeStepPrompt` (`pipeline.ts:1042-1164`), con estas secciones: cabecera "step N of at most CAP", "Overall goal" (el prompt de la tarea padre), "Context from previous step(s)", "Your step", "Running in parallel", "Delegation" y "Handoff (required)". El contrato se repite literalmente con `renderHandoffContract` (`pipeline.ts:998-1009`).
- **[IMPL]** **Doble canal: inline y archivo.**
  - Cada handoff previo se escribe a `~/.agetor/pipeline-runs/<parent>/handoff-<gen>-<seq>-from-<seq>.json` (`pipeline-runner.ts:709-729`). El contenido viene envuelto por `renderHandoffFile`, con un campo `_untrusted` (`pipeline.ts:873`).
  - Si el total cabe en 16 KB (`handoffInlineMaxBytes`, `types.ts:751`), el handoff también va inline. Si no, solo va un puntero al archivo (`pipeline.ts:1077-1095`).
  - Los archivos se añaden como `references` a la tarea del paso (`pipeline-runner.ts:782-784`).
- **[IMPL]** **El contenido de otro agente se trata como no confiable.** Va cercado por marcadores `BEGIN/END untrusted handoff <nonce>`, con un nonce aleatorio de 8 hex por prompt (`pipeline.ts:86-145`). Además se neutralizan las frases-marcador dentro del JSON (`escapeHandoffMarkerPhrases`, `pipeline.ts:141`).
- **[IMPL]** **En los joins** (`join:"all"`), las llegadas se acumulan en `run.joins[stepId].arrivals` y el paso se lanza con todos los handoffs a la vez (`pipeline-runner.ts:2306-2330`).

### 1.3 Cómo se valida la salida de una etapa
- **[IMPL]** **Parser tolerante y acotado** (`parseHandoff`, `pipeline.ts:781-829`):
  1. Busca el **último** `<handoff>` en los últimos 256 K caracteres (`handoffScanTailBytes`, `types.ts:767`).
  2. Quita un fence ```json si lo hay y hace `JSON.parse`.
  3. Si falla, intenta con el primer objeto `{…}` balanceado (`pipeline.ts:609-639`).
  4. Normaliza con `normalizeHandoff` (`pipeline.ts:748`): topes por campo (8000), por array (50) y total (64 KB), recortando los arrays primero (`fitHandoffToBudget`, `pipeline.ts:683`). Ignora `__proto__`.
- **[IMPL]** **Clasificación única** con `classifyStepResponse` (`pipeline.ts:831-871`). Los resultados posibles son `handoff | handoff-blocked | handoff-missing | handoff-invalid | user-ask | error | cancelled`. Un handoff válido tiene prioridad sobre una pregunta pendiente que pueda estar obsoleta.
- **[IMPL]** **Resolución de `next`** con `resolveNextSteps` (`pipeline.ts:900-948`):
  - Con una sola arista, `next` se ignora.
  - Con varias, se busca por nombre (normalizado NFC, sin distinguir mayúsculas), luego por id y luego por label de la arista.
  - El resultado puede ser `ambiguous` o `unknown`, en cuyo caso se devuelven los candidatos.
- **[IMPL]** **Un único recordatorio automático.** Si falta el handoff, es inválido o `next` es desconocido, el runner envía **una sola** vez un turno de seguimiento a la **misma** sesión. Ese turno empieza con `[handoff reminder]`, dice "Do not redo the work. Reply with ONLY the handoff block" y repite el contrato (`composeHandoffReminder`, `pipeline.ts:1206-1243`). El recordatorio se registra en `history[].reminder`. Si la segunda respuesta también es mala, el paso pasa a **Blocked** (`attemptHandoffReminderOrBlock`, `pipeline-runner.ts:1710-1730`).
- **[IMPL]** **Topes de pasos.** `effectiveStepCap = maxSteps * (1 + capExtensions)`, y cada Retry tras un bloqueo `step-cap` suma una asignación completa (`pipeline.ts:977-996`). Los tipos de bloqueo son `step-failed | step-blocked | handoff-missing | handoff-invalid | step-cap | profile-missing | join-incomplete` (`types.ts:574`).
- **[IMPL]** **No hay validación semántica ni gate determinista.** En `pipeline-runner.ts` no aparece ningún comando de verificación, test ni exit-code de un gate (grep de `verif|gate|bun test`: solo comentarios). "Éxito" significa que el turno terminó (`succeeded`) **y** que hay un JSON válido. Que el código compile o que pase sus tests lo decide, como mucho, otro paso LLM ("Review"), si el usuario lo diseña así.
- **[DOC/issue]** El issue #220 (cerrado) propone un pipeline SDD de 9 etapas con `PIPELINE_VERDICT: approve|revise`, verificación determinista de cobertura de ACs, `git merge` real y un "tree-fingerprint" para cortar loops sin progreso. **Vive en un fork y no está en `main`.** Lo menciono solo como señal de demanda.
- **[IMPL]** **Delegación:** el `cap` de subagentes solo se pasa al prompt (`pipeline-runner.ts:773`, `pipeline.ts:1123-1145`) y no se hace cumplir. El README:194 lo reconoce.

> **REIN, análogo o gap:**
> - REIN valida corrección con gates deterministas, que es más fuerte que Agetor.
> - Le **falta** un *contrato de salida estructurado* del worker con un parser tolerante, y el paso intermedio de "recordatorio barato a la misma sesión" antes de declarar rojo y relanzar un worker nuevo.
> - El grafo con ciclos y joins no tiene análogo en REIN (REIN usa tareas independientes con dependencias), y probablemente no hace falta.

---

## 2. Orquestación de CLIs y multi-cuenta

### 2.1 Cómo lanza cada CLI (`buildCommand`, `src/bun/agents.ts:479-890`)

| Harness | Modo | Evidencia |
|---|---|---|
| Claude Code | **REPL interactivo** (sin `-p`) en una sesión tmux por tarea. Prompt inicial como `-- <prompt>` en argv, o pegado (load-buffer/paste-buffer) si supera el límite de argv de tmux. `--session-id <uuid>` pregenerado, o `--resume <id>`. `--permission-mode` o `--dangerously-skip-permissions`. Effort vía env `CLAUDE_CODE_EFFORT_LEVEL`. | `agents.ts:487-604`, `claude-tmux.ts:33-48` |
| Codex | **Headless, un proceso por turno**: `codex exec --json --color never --skip-git-repo-check --sandbox … [resume <thread_id>] -`, con el prompt por stdin. Corre dentro de tmux como `sh -c 'exec <argv> < prompt.txt > <run>.jsonl 2>&1'`. | `agents.ts:869-890`, `codex-tmux.ts:40-56, 470-492` |
| Cursor | `cursor-agent -p --output-format stream-json [--resume id] --mode plan/ask …`, en tmux | `agents.ts:599-655` |
| Gemini | `gemini -m … --output-format stream-json --session-id/--resume -p` | `agents.ts:655-721` |
| Antigravity | `agy --output-format stream-json --model … [--conversation id] -p` | `agents.ts:726-760` |
| fx | **ACP (JSON-RPC sobre stdio)** como proceso hijo directo. No sobrevive a un reinicio. | `fx-acp.ts` (3502 LOC), README:34 |

- **[IMPL]** **Parseo de la salida.**
  - Claude: Agetor **no** lee la terminal para el contenido. Sigue el JSONL que Claude escribe en `~/.claude/projects/<cwd codificado>/<sessionId>.jsonl` (`claude-tmux.ts:33-48`) y deduplica por `line_uuid` (índice único en `run_events`). El fin de turno es `stop_reason: "end_turn"` (`claude-tmux.ts:78`), con un `END_TURN_IDLE_FIRE_MS = 800` de gracia (`:3460`).
  - Codex: mapea los eventos NDJSON `thread.started` / `turn.completed` (0) / `turn.failed` (1) (`codex-tmux.ts:130-146`).
- **[IMPL]** **La terminal se lee solo para lo que no está en el JSONL:** modales de AskUserQuestion, ExitPlanMode, prompts de permisos y diálogos de arranque. Lo hace un scraper con `capture-pane` cada 1 s (`SCRAPE_INTERVAL_MS`, `claude-tmux.ts:4973`), que pasa a 2 s y luego a 10 s cuando la sesión está inactiva (`:6213-6232`). Los parsers son `detectAskModal`/`parseModalPane` (`claude-questions.ts:197, 410`), con fixtures de panes reales en `src/bun/fixtures/askuserquestion/*.txt`. Las respuestas se devuelven como **pulsaciones** (`send-keys` tecla a tecla, `claude-tmux.ts:2112-2274`).
- **[IMPL]** **Liveness barato:** `kill(pid,0)` sobre el pid del pane, revalidado contra `tmux has-session` cada 10 s (`session-liveness.ts`). La muerte se declara solo tras `DEATH_MISS_THRESHOLD = 4` sondeos consecutivos `gone` (`claude-tmux.ts:7477`).
- **[IMPL]** Hay drivers *fake* por harness (`AGETOR_CLAUDE_DRIVER=fake`, `agents.ts:3126`) para tests sin CLI real.

### 2.2 Multi-cuenta
- **[IMPL]** Una "harness" es una fila en `harnesses` con su `home`, `bin` y `env_json` propios. `harnessEnv` (`agents.ts:340-395`) traduce `home` a la variable correcta de cada CLI:
  - Claude: `CLAUDE_CONFIG_DIR`. **No** cambia `HOME`, porque así se rompería el keychain de macOS.
  - Codex: `HOME` + `CODEX_HOME`.
  - Cursor, fx y agy: `HOME`.
  - Gemini: `GEMINI_CLI_HOME`.
- **[DOC]** Los homes viven en `~/.agetor/harnesses/<id>/` (README:292).
- **[IMPL]** **No hay selección automática de cuenta ni rotación por cuota.** "Quota" no aparece ni en `orchestrator.ts` ni en `pipeline-runner.ts` (grep: 0). La cuenta la fija el perfil o la tarea.

> **REIN, análogo o gap:** Orca resuelve el lanzamiento y las terminales. REIN **ya hace** algo que Agetor no hace: enrutar por cuota. El mapeo `home → CLAUDE_CONFIG_DIR/CODEX_HOME/GEMINI_CLI_HOME` (y "no cambiar HOME en Claude por el keychain") es conocimiento directo que REIN necesitaría si algún día maneja cuentas fuera de Orca.

---

## 3. Descubrimiento automático de modelos y capacidades

**[IMPL]** Está en `src/bun/agent-discovery.ts`, como módulo hoja con la regla "best-effort, nunca lanza". El scheduler está en `model-discovery.ts`.

| CLI | Sondeo exacto | Cita |
|---|---|---|
| Codex | Lanza `codex app-server` y habla JSON-RPC 2.0 por stdio: `initialize` → `initialized` → `model/list`, paginado hasta 5 páginas con `nextCursor`. Lee `supportedReasoningEfforts`. Timeout de 5 s; si vence, se descartan incluso los datos parciales. Por harness, con su propio env y bin (catálogo por cuenta). | `agent-discovery.ts:182-450` |
| Cursor | `cursor-agent --list-models` (timeout 3 s) | `:160-170` |
| Antigravity | `agy models` | `:492-500` |
| fx | `fx models --json` (efforts, fast/ultrafast) | `:740-790` |
| Claude | **Nada.** Devuelve `[]` porque no existe un comando programático; la lista viene curada en `AGENT_OPTIONS` | `:449-457`, `types.ts:3597+` |
| Gemini | **Nada**, por la misma razón | `:459-467` |

- **[IMPL]** **Cuándo se sondea:** en el arranque, en un barrido periódico cada **15 min** (`model-discovery.ts:42`), y con un debounce de 500 ms cuando cambia el `HarnessStatus` (instalación o login). Solo se emite `agent_models_changed` si cambia el hash de id, label, efforts y flags (`model-discovery.ts:79-118`).
- **[IMPL]** **Instalación y login** (`agent-status.ts`):
  - `<bin> --version` y `--help` con timeout (`:89-150`).
  - Para fx, `fx status --json`, con una política estrictamente **fail-open**: solo `auth:"missing"`, o una sesión expirada y no refrescable, cuenta como "logged out" (`:200-371`).
  - Para Claude y Codex no hay sondeo de login explícito en este módulo **[INF: se deduce de los lectores de cuota del §8]**.
- **[IMPL]** **Versión mínima de CLI por modelo.** `MODEL_MIN_CLI_VERSION` (`types.ts:2638-2646`, hoy solo para modelos de Codex) se combina con `cliVersionSatisfies`, que es **fail-open**: una versión que no se puede parsear no bloquea (`shared/cli-version.ts:13-17, 87`). La comprobación se hace antes de lanzar y se puede saltar con `AGETOR_SKIP_CLI_VERSION_FLOOR`.
- **[IMPL]** **Skills, slash-commands y MCP** se descubren leyendo los directorios del CLI (`src/bun/commands.ts`, PR #263) para el autocompletado del composer.
- **Robustez, en mi opinión [INF]:**
  - Es buena como red de seguridad: todo es fail-open, con timeouts cortos, y la lista curada sigue siendo la fuente de verdad.
  - Es frágil en el fondo. Hay **muchísima** lógica atada a versiones concretas: comentarios del tipo "verified against codex-cli 0.147.0 and 0.153.0", "claude 2.1.296", "fx 0.0.4–0.0.14". `docs/plans/` contiene unos 15 planes "add-<modelo>.md" y 10 migraciones SQL `normalize_*`/`retire_*` que solo renombran ids de modelos. El costo de mantenimiento por cada versión de CLI es alto.

> **REIN, análogo o gap:** REIN delega en Orca qué modelos lanza. Le serviría un **preflight de versión mínima fail-open** antes de despachar, para no gastar un worker en un CLI viejo. Para Codex, `app-server model/list` es el único descubrimiento real que existe.

---

## 4. Sesiones persistentes y resume

- **[IMPL]** **Usa los IDs de sesión nativos de cada CLI.** No tiene un replay propio hacia el modelo.
  - Claude: `--session-id <uuid>` al crear y `--resume <id>` en el follow-up si la sesión tmux murió (`agents.ts:513-517`, `orchestrator.ts:6213`).
  - Codex: `codex exec … resume <thread_id>` (`agents.ts:885-887`).
  - Cursor y Gemini: `--resume <id>`.
  - Antigravity: `--conversation <id>`.
  - fx: ACP `session/resume`, con `session/load` como fallback.
  - Los IDs se guardan por run en `runs.{claude,codex,cursor,gemini,fx,antigravity}_session_id`.
- **[IMPL]** **Para la UI sí hay replay propio.** En el arranque, `reconcileOrphans` (`orchestrator.ts:789-860`) recorre los runs `running`. Si la sesión tmux sigue viva, **re-adjunta** y vuelve a leer el JSONL desde el offset 0 sin duplicar nada (índice único `run_events(run_id, subagent_id, line_uuid)`). Si murió, el run queda `orphaned` y la tarea vuelve a Ready. Si quedaron dos runs `running` para la misma tarea, solo se re-adjunta el más nuevo.
- **[IMPL]** **Claude vive en un REPL tmux persistente entre turnos.** Los follow-ups se pegan en el composer y se encolan si hay un turno en curso. Codex, Cursor, Gemini y agy son un proceso por turno: entre turnos no hay nada vivo, solo el id para el resume (`codex-tmux.ts:40-50`).
- **[IMPL]** **Importación de sesiones de Claude** iniciadas fuera de Agetor: `claude-session-scan.ts`, `claude-session-import.ts` y las tablas `claude_session_seen` y `tasks.imported_claude_session_id`.
- **[IMPL]** **Pipelines:** `reconcilePipelineRuns` (`pipeline-runner.ts:2619`) es el self-heal de arranque para settles perdidos. Las operaciones por pipeline se serializan con un lock por padre (`withPipelineLock`, `:136-160`).

> **REIN, análogo o gap:** REIN reanuda desde un JSON de estado y **siempre** usa un worker nuevo, lo cual es deliberado y correcto para él. La idea útil es la **deduplicación idempotente por id de línea** al reingerir transcripts (si REIN lee transcripts para auditar o para la cuota), más la regla "si hay dos runs vivos para una tarea, gana el más nuevo y el resto queda huérfano".

---

## 5. Persistencia SQLite

- **[IMPL]** `bun:sqlite` con `PRAGMA journal_mode=WAL; synchronous=NORMAL; foreign_keys=ON` (`src/bun/db.ts:64-75`).
- **[IMPL]** **Migraciones:**
  - Son 74 archivos `NNN_name.sql`, con un orden explícito en `migrations/index.ts`.
  - La tabla de control es `_migrations(id, applied_at)` (`migrate.ts:90`).
  - Cada migración corre en **una transacción** (`migrate.ts:112`). Las sentencias se ejecutan una a una mediante un splitter propio que respeta strings y comentarios, porque `exec` multi-sentencia en bun:sqlite no aborta al primer error (`migrate.ts:12-30`).
  - Hay **alias** de IDs para las migraciones renumeradas al mergear ramas (`index.ts:136+`).
  - La regla es "nunca editar una migración aplicada" (README:386).
- **[IMPL]** **Patrón dominante:** se guarda mucho JSON en columnas TEXT, como `tasks.pipeline_run`, `plans`, `backlog`, `refs`, `attachments`, `sent_files`, `fx_recovery`, `agent_profile` (snapshot), `pipelines.graph`, `agent_profiles.skills_json` y `harness_usage.snapshot_json`. **Las preguntas pendientes NO se persisten**: viven en `Map` en memoria y se re-derivan del pane vivo tras un reinicio (`interactions.ts:9-35, 299-301`).

**Esquema final reconstruido** (las 74 migraciones aplicadas, sin errores):

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, column TEXT NOT NULL,
  agent TEXT NOT NULL, workdir TEXT NOT NULL, run_id TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  isolation TEXT NOT NULL DEFAULT 'worktree', branch TEXT, worktree_path TEXT, base_ref TEXT,
  mode TEXT, model TEXT, attachments TEXT NOT NULL DEFAULT '[]', effort TEXT,
  refs TEXT NOT NULL DEFAULT '[]', archived_at INTEGER, task_type TEXT NOT NULL DEFAULT 'task',
  backlog TEXT NOT NULL DEFAULT '[]', branch_source TEXT NOT NULL DEFAULT 'created', draft TEXT,
  pr_url TEXT, max_mode INTEGER NOT NULL DEFAULT 0, plans TEXT NOT NULL DEFAULT '[]',
  todo_progress TEXT, last_assistant_event_id INTEGER, last_seen_event_id INTEGER, issue_url TEXT,
  sent_files TEXT, fx_recovery TEXT, agent_profile_id TEXT, agent_profile TEXT,
  pipeline_id TEXT, pipeline_run TEXT, pipeline_parent_id TEXT, pipeline_step_id TEXT,
  checkout_branch TEXT, imported_claude TEXT, imported_claude_session_id TEXT,
  speed TEXT NOT NULL DEFAULT 'off' CHECK (speed IN ('off','fast','ultrafast')));

CREATE TABLE runs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  agent TEXT NOT NULL, status TEXT NOT NULL, started_at INTEGER NOT NULL, ended_at INTEGER,
  exit_code INTEGER, tmux_session TEXT, claude_session_id TEXT, codex_session_id TEXT, origin TEXT,
  cursor_session_id TEXT, gemini_session_id TEXT, fx_session_id TEXT, antigravity_session_id TEXT);

CREATE TABLE run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  stream TEXT NOT NULL, data TEXT NOT NULL, ts INTEGER NOT NULL, line_uuid TEXT, subagent_id TEXT);
CREATE UNIQUE INDEX run_events_run_sub_line_uuid
  ON run_events(run_id, IFNULL(subagent_id,''), line_uuid) WHERE line_uuid IS NOT NULL;

CREATE TABLE subagents (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE CASCADE, parent_kind TEXT NOT NULL DEFAULT 'subagent',
  agent_type TEXT, description TEXT, spawn_depth INTEGER NOT NULL DEFAULT 1,
  source_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
  started_at INTEGER NOT NULL, ended_at INTEGER, tool_use_id TEXT);

CREATE TABLE harnesses (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('claude-code','codex','cursor','grok','gemini','kimi','fx','antigravity')),
  label TEXT NOT NULL, is_builtin INTEGER NOT NULL DEFAULT 0, home TEXT, bin TEXT,
  env_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, profile_mcp_servers INTEGER NOT NULL DEFAULT 1);

CREATE TABLE agent_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, harness_id TEXT NOT NULL,
  model TEXT NOT NULL, effort TEXT, mode TEXT, max_mode INTEGER NOT NULL DEFAULT 0,
  instructions TEXT NOT NULL DEFAULT '', skills_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  speed TEXT NOT NULL DEFAULT 'off' CHECK (speed IN ('off','fast','ultrafast')));

CREATE TABLE pipelines (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '', graph TEXT NOT NULL, max_steps INTEGER NOT NULL DEFAULT 25,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);

CREATE TABLE usage_daily (config_dir TEXT NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (config_dir, day, model));
CREATE TABLE usage_files (path TEXT PRIMARY KEY, config_dir TEXT NOT NULL, mtime_ms REAL NOT NULL,
  size INTEGER NOT NULL, offset INTEGER NOT NULL);
CREATE TABLE usage_seen (config_dir TEXT NOT NULL, key TEXT NOT NULL,
  PRIMARY KEY (config_dir, key)) WITHOUT ROWID;
CREATE TABLE harness_usage (harness_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, updated_at INTEGER NOT NULL);

CREATE TABLE projects (path TEXT PRIMARY KEY, name TEXT NOT NULL, added_at INTEGER NOT NULL, branch_config TEXT);
CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE saved_prompts (id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE claude_session_seen (session_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL);
CREATE TABLE retention_swept_tasks (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  sweep_key TEXT NOT NULL);
-- + índices: idx_runs_task, runs_claude_session_id_idx, idx_subagents_task, idx_subagents_running,
--   idx_tasks_pipeline_parent, idx_tasks_pipeline_id, idx_run_events_run, idx_run_events_user_history,
--   tasks_imported_claude_session_uidx, idx_agent_profiles_harness_id, claude_session_seen_at_idx
-- + _migrations(id, applied_at) creada por migrate.ts:90
```

> **REIN, análogo o gap:** el JSON de estado de REIN equivale a `tasks.pipeline_run`, porque Agetor también guarda el estado del motor como **un blob JSON por padre**, escrito con un UPDATE dirigido (`types.ts:707-717`). SQLite le da a Agetor la indexación de eventos y la deduplicación, y REIN no lo necesita. Lo transferible es la *disciplina*: estado sanitizado al leer, topes de tamaño y "nunca editar una migración aplicada" (equivalente a versionar el esquema del JSON).

---

## 6. Comunicación entre etapas, bloqueos y estancamientos

- **[IMPL]** **La única comunicación entre etapas es el handoff** (§1.2). Las etapas no hablan en vivo entre sí. Lo único que comparten es el **worktree**.
- **[IMPL]** **Humano en el loop:**
  - AskUserQuestion, ExitPlanMode y los prompts de permisos aparecen como tarjetas en la UI y en el CLI (`agetor answer`). La respuesta vuelve como pulsaciones (`interactions.ts:37-70`).
  - En un pipeline, `user-ask` deja el run en `blocked` hasta que el humano responde (`pipeline-runner.ts:2129-2145`).
  - Las acciones manuales son **Retry / Advance (elegir next o terminar) / Stop / Restart** (`advancePipeline`, `retryPipelineStep`, `cancelPipelineRun` en `pipeline-runner.ts:1285-1540`, más el CLI `agetor pipeline retry|advance|restart`).
  - El prompt del paso dice "If you need the user's input, ask before writing the handoff" (`pipeline.ts:1155`).
- **[IMPL]** **Estancamientos, en tres capas:**
  1. **Prompt desconocido:** si el JSONL lleva 60 s en silencio (`STUCK_TURN_FALLBACK_MS`, `claude-tmux.ts:5465`), no hay spinner y en el pane aparece un footer tipo `esc to cancel|enter to confirm|enter to continue` (`MODAL_FOOTER_RE`, `:5458`), se genera una tarjeta genérica `tmux_prompt` con las opciones parseadas (`matchUnparsableModal`, `:5548`).
  2. **Watchdog suave de turno:** si no hay **actividad en el transcript** durante 10 min (`TURN_STALL_DEFAULT_MS = 600_000`, ajustable con `AGETOR_TURN_STALL_MS`), se marca "may be stuck" en ámbar y se incluyen las **últimas 12 líneas del pane** y el comando `tmux attach`. Deliberadamente **no** cuenta la actividad del pane, porque un TUI congelado sigue animando su spinner. Si hay subagentes con actividad reciente, no se dispara. Se limpia solo cuando vuelve la actividad (`stallTickOutcome`, `claude-tmux.ts:1954-2051`, `stall-registry.ts`). **Nunca liquida el run.** Origen documentado: un incidente real de 13 min detrás de un wizard `/auto-mode-setup` (`:1975-1978`).
  3. **Muerte de sesión:** sondeo pid/tmux (§2.1) → `SESSION_DIED_STATUS_PREFIX` → run fallido u orphaned.
- **[IMPL]** **"Held":** si el turno terminó pero quedan subagentes en background corriendo, la tarea se mantiene en Running y el pipeline **no avanza** hasta que terminan (`pipeline-runner.ts:2103-2106`). Los subagentes sin actividad pasan a stale a los 10 min (`claude-subagents.ts:484`).
- **[IMPL]** **No hay timeout duro por turno** que mate al agente. **[INF]** La filosofía es avisar sin matar, y el humano decide.

> **REIN, análogo o gap:**
> - Las decision gates de Orca equivalen a las tarjetas de Agetor.
> - Falta un **watchdog de silencio de transcript** (no de terminal) con exención por subagentes y "últimas N líneas" en el aviso. REIN podría alimentarlo con el mtime del JSONL del worker y el estado de Orca.
> - La regla "un handoff válido gana sobre una pregunta pendiente obsoleta" (`pipeline.ts:845-852`) sirve para evitar falsos bloqueos.

---

## 7. Integración con herramientas existentes sin modificarlas

- **[DOC]** README:30-34 dice: "installs no hooks and no MCP server, and it doesn't edit your CLAUDE.md. It drives the real CLI the way you would."
- **[IMPL] Con matices:**
  - `hook-installer.ts:8-33` **sí escribe** `<cwd>/.claude/settings.local.json` en cada spawn. Elimina hooks y servidores MCP `agetor` que dejaron builds anteriores y sanea entradas `permissions.allow` inválidas.
  - En un worktree propio de Agetor, ese archivo es suyo. Con `isolation=none` hace *merge* en el archivo del usuario (`ensureInstalledMerged`, `:117`).
  - Inyecta `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false` (`agents.ts:496`) para que el scraper no confunda placeholders.
  - **Historia:** `.claude/CLAUDE.md` del repo todavía documenta una herramienta MCP `ask_user` del servidor `agetor` que ya no existe. **[INF]** La arquitectura evolucionó de hooks + MCP a scraping puro.
- **[DOC]** **Efecto colateral real reconocido:** versiones anteriores de Agetor enviaban un cambio de modo a fx, y fx 0.0.14 lo guardaba en el perfil del usuario (`~/.fx/settings.json`), así que el fx del usuario arrancaba en Ask (README:100). Es una prueba de que "manejar el CLI real" puede mutar la configuración del usuario sin querer.
- **[IMPL]** **Git hosts:** GitHub, GitLab y Bitbucket mediante APIs REST con tokens propios (`github.ts` 4991 LOC, `gitlab.ts`, `bitbucket.ts`), para PRs, mergeability, issues→tarea (el texto del issue se marca como no confiable) y clone.
- **[IMPL]** **Worktrees:** `agetor/<id>-<slug>` bajo `~/.agetor/worktrees/`. El **base ref se fija como sha al crear la tarea** (`worktree.ts`, `CLAUDE.md` §Orchestration flow).

> **REIN, análogo o gap:** REIN también delega en Orca y no toca los CLIs. Lección para REIN: **auditar qué archivos de config del CLI escribe Orca o el propio CLI** (settings.local.json, perfiles), porque "no invasivo" se rompe por efectos secundarios de los flags.

---

## 8. Telemetría, tokens y costo, estado, DX

- **[IMPL]** **Sin telemetría.** Los únicos hosts de red en `src/bun` son los git hosts, GitHub Releases y los endpoints de cuota (grep de URLs; no hay SDKs de analítica). Coincide con README:32 y 349.
- **[IMPL]** **Cuota del plan** (barra de uso, solo informativa):
  - **Claude:** `GET https://api.anthropic.com/api/oauth/usage`, con el token OAuth leído del **keychain de macOS** (`security find-generic-password -s "Claude Code-credentials" -w`, con cooldown) (`usage/claude-usage.ts:13`, `usage/creds.ts:56-170`). Si falla, usa `cachedUsageUtilization` de `.claude.json`. Medidores: `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet` (`claude-usage.ts:20-26`).
  - **Codex:** `GET https://chatgpt.com/backend-api/wham/usage` con `auth.json` de `CODEX_HOME`. Si falla, usa el último evento `token_count.rate_limits` del `sessions/YYYY/MM/DD/rollout-*.jsonl` más reciente (`usage/codex-usage.ts:1-25, 302-352, 381`).
  - **Cursor:** `cursor.com/api/usage-summary`.
  - El código reconoce que los tres endpoints son **no documentados** (`codex-usage.ts:5-6`).
- **[IMPL]** **Tokens:** rollups locales y deterministas desde los JSONL de Claude (`message.usage`), con un cursor de bytes por archivo (`usage_files`), deduplicación por `message.id:requestId` (`usage_seen`) y un presupuesto de 16 MB por pasada (`account-usage.ts:1-40`). **El costo se omite a propósito**, porque para suscripciones la unidad honesta son los tokens (`account-usage.ts:25`). Solo cubre Claude.
- **[IMPL]** **Estado y API:** un core HTTP + SSE en `127.0.0.1` con un bearer token aleatorio por arranque, guardado en `agetor-core.json` con permisos 0600 (`server.ts:404-435`, SSE `:7394`). La UI y el CLI consumen la misma API. El daemon headless entrega el control a la app cuando esta se abre.
- **[IMPL]** **DX:**
  - UI de escritorio: kanban, canvas de pipelines (React Flow + dagre) con animación del run, diff review y terminales xterm.
  - TUI Ink (`src/cli/tui/Dashboard.tsx`).
  - CLI con `--json` en todos los comandos y prefijos de id de 8 caracteres.
  - Notificaciones nativas de macOS mediante un helper firmado.
  - Export/import de Agents y Pipelines en un bundle JSON.

> **REIN, análogo o gap:**
> - REIN enruta por cuota. Agetor solo la **muestra**, pero sus lectores son exactamente lo que REIN necesitaría para cuota real.
> - La **cadena de fallback local** (`.claude.json cachedUsageUtilization`; rollout `rate_limits` de Codex) no necesita red ni tokens: es la opción más segura y compatible con "cero dependencias".
> - Leer el keychain es delicado y en REIN debería ser opt-in.

---

## 9. Tamaño, complejidad y madurez

**LOC de TypeScript (wc -l, incluye comentarios):**

| Módulo | src | tests | archivos de test |
|---|---:|---:|---:|
| `src/bun` (core) | 78 234 | 106 704 | 206 |
| `src/mainview` (UI React) | 58 813 | 14 434 | 76 |
| `src/shared` | 16 219 | 14 918 | 30 |
| `src/cli` (CLI + TUI + daemon) | 9 489 | 13 651 | 38 |
| `e2e` (Playwright) | 27 559 | n/a | 60 specs |
| **Total `.ts/.tsx`** | **~312 000** | | |

- **Archivos más grandes:** `claude-tmux.ts` 11 537, `GitHubDialog.tsx` 9 879, `RunPanel.tsx` 8 321, `server.ts` 7 881, `orchestrator.ts` 7 741, `db.ts` 3 566, `fx-acp.ts` 3 502, `agents.ts` 3 315, `pipeline-runner.ts` 2 707 y `shared/pipeline.ts` 1 292.
- **El motor de pipelines** (runner + gramática + tipos) ronda las **4 000 LOC**, más unas 7 700 de tests.
- **Tests:** unos **8 100** casos `test()/it()` con `bun test`, más Playwright. **No hay CI**: `.github/` solo contiene `FUNDING.yml`, y no publican métricas de cobertura. **[INF]** La proporción tests:código es muy alta (~1,3:1 en el core), pero no hay garantía automática de que corran en cada PR.
- **Dependencias de runtime:** 27 paquetes npm (React, xterm, Ink, React Flow, dagre, motion, dnd-kit, pdfjs, react-markdown, etc.), más Electrobun, Bun ≥1.3.5, tmux (vendorizado y firmado), un notificador nativo y el helper `disclaim`. Solo macOS arm64.
- **Madurez:**
  - Creado el **2026-05-16**, con **324 commits** y el último push el 2026-10-26.
  - Release v1.0.2 (2026-10-15). 99 estrellas y 18 forks.
  - Unos 7 contribuidores, pero **alamops tiene 308 de 324 commits** (bus factor ≈ 1).
  - Solo 4 issues en total (1 abierta: soporte para OpenCode) y la mayoría de los PRs son del autor.
  - **[DOC]** README:357: "built almost entirely by Claude Code running inside Agetor". `CLAUDE.md` pesa 408 KB, y además trae instrucciones de identidad de agente de terceros ("JubarteAI"), que traté como datos.
  - **[INF]** Cinco meses de desarrollo muy rápido y asistido por IA, con mucho endurecimiento ante versiones concretas de CLIs y casos borde. Es robusto en los detalles y costoso de mantener.

---

## 10. Resumen por capacidad: análogo plausible en REIN o gap

| Capacidad de Agetor | Estado | REIN: análogo o gap |
|---|---|---|
| Grafo de pasos con ciclos, fan-out y join | IMPL | Gap no deseado. Las tareas con dependencias bastan |
| Handoff JSON en un tag, con parser tolerante | IMPL | **Gap útil**: contrato de resultado del worker |
| Un recordatorio único a la misma sesión antes de Blocked | IMPL | **Gap útil**: un paso barato antes de relanzar un worker nuevo |
| Cercado no confiable con nonce | IMPL | **Gap útil** para los prompts de auditor y de retry |
| Snapshot congelado del pipeline y los perfiles | IMPL | Parcial: REIN debería congelar el hash del plan en el estado |
| Tope de pasos con extensión explícita | IMPL | Análogo al presupuesto de reintentos de REIN |
| Validación semántica (tests, gates) | **No existe** | REIN es superior |
| Auditoría por un modelo distinto | Solo si el usuario diseña un paso Review | REIN es superior (independiente por diseño) |
| Lanzamiento tmux + JSONL / exec --json / ACP | IMPL | Lo cubre Orca |
| Multi-cuenta vía `home` → env por CLI | IMPL | Lo cubre Orca, o un gap si REIN maneja cuentas |
| Enrutamiento por cuota | **No existe** (solo se muestra) | REIN es superior. Puede reutilizar las fuentes de cuota de Agetor |
| Descubrimiento de modelos (codex app-server, `agy models`…) | IMPL, parcial | Gap menor |
| Versión mínima de CLI, fail-open | IMPL | **Gap útil**, barato |
| Resume con IDs nativos y replay deduplicado | IMPL | REIN usa un worker nuevo por diseño |
| Watchdog de silencio de transcript | IMPL | **Gap útil** |
| Preguntas humanas como tarjetas con respuesta por pulsaciones | IMPL | Lo cubren las decision gates de Orca |
| Persistencia SQLite + 74 migraciones | IMPL | El JSON basta. Conviene versionar el esquema |
| Rollup de tokens deduplicado y sin costo | IMPL | Opcional, para reportes |
| UI kanban, TUI, SSE | IMPL | Fuera del alcance de REIN |

---

## 11. Survey breve de otros orquestadores

Lo hizo un subagente con clones `--depth 1` y sin ejecutar nada. Las citas son a los SHAs indicados. Revisé el resumen, pero no volví a verificar cada línea.

- **ComposioHQ/agent-orchestrator** (SHA `c7a99e1ad8`, Apache-2.0, ~13.7k★). Daemon en Go + SQLite que reacciona a CI rota, reviews y conflictos de los PRs de sus workers.
  - Mecanismo **[VERIFICADO]**: `sendOnce` (`backend/internal/lifecycle/reactions.go:929-997`). Deduplica cada "nudge" por `key` y `sig`. La firma de un fallo de CI es check + commit + estado + URL + `LogTail` (`ciFailureSignature`, `:768`), con `maxAttempts` por tipo de nudge.
  - El worker se reporta solo: `ao report --checkpoint|--needs-input|--stuck|--done` (`backend/internal/cli/report.go:50-61`).
  - Aplicación en REIN: **alta**. Una firma de fallo `(gate, commit, log_tail)` evita relanzar un worker contra un fallo idéntico.
- **smtg-ai/claude-squad** (SHA `ce1ffb4392`, AGPL-3.0). TUI de agentes en tmux + worktree.
  - Mecanismo **[VERIFICADO]**: `HasUpdated` (`session/tmux/tmux.go:246-268`) calcula el sha256 de `capture-pane` y lo compara con el hash anterior. El prompt se detecta con strings fijos por agente.
  - Aplicación en REIN: **media**, como último recurso de detección de inactividad. Agetor rechaza explícitamente la actividad del pane como señal (§6), una buena contrapartida.
- **BloopAI/vibe-kanban** (SHA `a019cebf06`, Apache-2.0, ~28.5k★). Kanban en Rust con ejecutores headless.
  - Mecanismo **[VERIFICADO]**: Claude se lanza con `-p --output-format=stream-json --input-format=stream-json` (`crates/executors/src/executors/claude.rs:159,190-191`) y parsea `RateLimitEvent{rate_limit_info}` (`:2371`). Para Codex llama a `account/rateLimits/read` por JSON-RPC (`codex/client.rs:293-300`), que devuelve `used_percent` primario y secundario, `plan_type` y `credits`.
  - Aplicación en REIN: **muy alta** para el enrutamiento por cuota con datos reales.
- **devflowinc/uzi** (SHA `421685f65d`, inactivo desde 2025-06). Variante de claude-squad. La lógica de `noUpdateCount` quedó a medias (`cmd/watch/auto.go:70-140`). Aplicación: baja.
- **Conductor (Melty Labs):** es de código cerrado, así que no hay nada verificable. No cubrí `ccmanager` ni los docs de agent teams de Claude Code.

---

## 12. Las 5 ideas más transferibles a REIN

1. **Contrato de resultado del worker y "un recordatorio antes de rojo".**
   - Qué es: el worker termina con un `<result>{status, summary, artifacts, openQuestions}</result>`. REIN lo parsea con un parser tolerante: el último bloque, sin fence, con fallback a un objeto balanceado y con topes de tamaño. Si falta o es inválido, REIN envía **un solo** mensaje "no rehagas el trabajo, emite solo el bloque" a la **misma** terminal de Orca antes de marcar la tarea en rojo y gastar un worker nuevo. Los gates deterministas siguen siendo la autoridad. El bloque solo sirve para triaje, para `openQuestions` y para el auditor.
   - Evidencia: **fuerte** (`pipeline.ts:781-871, 1206-1243`; `pipeline-runner.ts:1710+`).
   - Costo: **bajo**, unas 150-250 LOC de Python stdlib más tests. Riesgo: un agente podría "complacer" el formato sin hacer el trabajo, pero los gates lo detectan.
2. **Cercado no confiable con nonce para todo el texto entre agentes.**
   - Qué es: la salida del worker, los logs de gates fallidos que se pasan al retry y lo que se le da al auditor van entre `BEGIN/END untrusted <nonce>`, con un nonce por prompt, y se neutralizan las frases-marcador dentro del contenido.
   - Evidencia: **fuerte** (`pipeline.ts:86-145, 1066-1098`).
   - Costo: **muy bajo**, unas 40 LOC.
3. **Watchdog de silencio de transcript, no de terminal.**
   - Qué es: una función pura `tick(turnInFlight, quietMs, threshold, fired, subagentsFresh) → none|fire|clear-live|clear-silent`. Se alimenta con el mtime del JSONL del worker (o el estado de Orca), con exención si hay subagentes con actividad. El aviso incluye las últimas 12 líneas de la terminal y se limpia solo. No mata al worker: abre un gate o lo marca. Se puede combinar con la heurística de 60 s + regex de footers modales para "diálogo desconocido".
   - Evidencia: **fuerte** (`claude-tmux.ts:1954-2051, 5458-5548`), y el umbral de 10 min está justificado por un incidente real.
   - Costo: **bajo**, unas 80-120 LOC.
4. **Cuota real leída de fuentes locales del proveedor, con la API como mejora opcional.**
   - Qué es:
     - Claude: `.claude.json` `cachedUsageUtilization` (five_hour, seven_day…), y opcionalmente `/api/oauth/usage`.
     - Codex: el último `token_count.rate_limits` del rollout `sessions/…/rollout-*.jsonl` más reciente, y opcionalmente `wham/usage` o `app-server account/rateLimits/read` (vibe-kanban).
     - Esto sustituye las estimaciones en el enrutamiento de REIN.
   - Evidencia: **media**. El código está verificado, pero los endpoints y archivos son **no documentados** y cambian con cada versión del CLI. El acceso al keychain debe ser opt-in.
   - Costo: **medio**, unas 200-300 LOC más el mantenimiento por versión.
5. **Preflight fail-open de versión mínima de CLI por modelo, más el snapshot congelado del plan.**
   - Qué es: antes de despachar, parsear `<cli> --version` y compararlo con una tabla `modelo → versión mínima`. Si la versión no se puede parsear, se deja pasar. Además, guardar en el estado de REIN el hash y una copia del plan y del enrutamiento al lanzar, para que una edición a mitad de la ejecución no cambie la semántica (equivalente a `PipelineRunSnapshot`).
   - Evidencia: **media** para la versión mínima (`shared/cli-version.ts:13-17, 87`; `types.ts:2638`), que está implementada pero hoy solo cubre Codex. **Fuerte** para el snapshot (`types.ts:628-640`).
   - Costo: **bajo**, unas 60-100 LOC.

**Menciones:**
- La **firma de fallo deduplicada** de agent-orchestrator (`reactions.go:929-997`): evidencia fuerte y costo bajo. Encaja directamente en los reintentos de REIN.
- El **tope de pasos con extensión explícita** de Agetor, `maxSteps*(1+ext)`, como modelo de "presupuesto de reintentos ampliable por el humano".
