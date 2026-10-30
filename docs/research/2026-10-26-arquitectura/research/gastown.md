# Gas Town: investigación de arquitectura a nivel de código fuente

- **Repo**: https://github.com/gastownhall/gastown (canónico; el módulo Go sigue llamándose `github.com/steveyegge/gastown`, `go.mod:1`). ~18.6k estrellas, creado 2025-12-16.
- **SHA analizado**: `de2ce8efa7aa3b1ca942b39319c5f4bd5aeac1b1` (2026-10-25, "feat(deps): require Dolt 2.6.0"). Clon `--depth 50`.
- **Método**: solo lectura de código, docs, issues y PRs. No se instaló, compiló ni ejecutó nada.
- **Convención**: **Implementado** = verificado en código (`archivo:línea` en este SHA). **Solo documentado** = afirmado en README/docs, sin verificar o contradicho por el código. **Inferencia** = conclusión mía.
- Todas las rutas son relativas a la raíz del repo.

---

## 0. Resumen

Gas Town es un binario Go (`gt`) que coordina **muchas sesiones de agentes LLM de larga vida dentro de tmux**. Usa un issue tracker propio (`bd`/Beads) sobre un **servidor Dolt SQL** como bus de estado y de mensajes. Casi todos los "roles" son **agentes LLM** (Mayor, Deacon, Witness, Refinery, Boot, Dogs, Polecats, Crew). Cada uno ejecuta un "formula" TOML: un prompt largo con pasos y comandos `gt`/`bd` incrustados.

La única pieza determinista de larga vida es el **daemon Go**. Su latido de recuperación corre cada 3 min. Arranca y reinicia sesiones, detecta crashes y "violaciones GUPP", alimenta convoyes y despacha la cola. La filosofía declarada es **ZFC ("Zero Framework Cognition")**: Go transporta y observa, los agentes deciden (`docs/agent-provider-integration.md:619-623`).

En la práctica el proyecto ha ido **moviendo lógica crítica de los prompts a Go**: `gt patrol scan`, `gt mq post-merge`, el planificador de capacidad. Los prompts ahora exigen "usa el comando Go, NO improvises".

Hallazgo importante: **la cola de merge "Bors batch-then-bisect" que anuncia el README NO está en el camino de producción**. `ProcessBatch`/`bisectBatch` existen en `internal/refinery/batch.go:211,487`, pero solo los llaman tests. El Refinery real es un LLM que sigue `mol-refinery-patrol.formula.toml`, que procesa **una rama a la vez** (detalle en §7).

Estado del proyecto: hay un hueco de commits entre 2026-07-23 y 2026-10-20. El issue #4930 (abierto, de un usuario) dice que el repo está de facto deprecado en favor de "Gas City". Hay 512 issues abiertos y muchos bugs de desfase de versión con Beads/Dolt (#4793, #4789, #4770, #4727).

---

## 1. Tabla de LOC (Go, sin `_test.go` salvo indicación)

Total Go de producción: **248,970 LOC**. Tests Go: **225,826 LOC**. Docs `.md`: 24,596 líneas. Formulas TOML embebidas: **23,763 líneas** (prompts operativos). Plantillas de rol: 3,010 líneas.

| Subsistema | LOC prod | LOC test | Notas |
|---|---:|---:|---|
| `internal/cmd` (CLI `gt`, 253 archivos) | 103,691 | 67,985 | aquí vive gran parte de la lógica |
| ↳ sling* + polecat_spawn + unsling | 6,965 | | despacho |
| ↳ convoy* | 6,320 | | |
| ↳ mail* | 4,582 | | |
| ↳ mq* (merge queue CLI) | 3,415 | | |
| ↳ prime* (inyección de contexto) | 3,291 | | |
| ↳ done* | 2,795 | | |
| ↳ handoff | 1,744 | | |
| ↳ patrol* | 1,607 | | |
| ↳ scheduler* | 1,145 | | |
| ↳ escalate* | 1,122 | | |
| ↳ nudge* | 1,107 | | |
| ↳ quota* | 981 | | |
| ↳ seance | 872 | | |
| `internal/doctor` | 20,484 | 20,288 | diagnósticos/auto-fix (síntoma de complejidad operativa) |
| `internal/daemon` | 13,456 | 13,100 | único proceso determinista de larga vida |
| `internal/beads` (wrapper de `bd`) | 12,246 | 14,430 | |
| `internal/config` | 8,577 | 13,275 | |
| `internal/doltserver` | 7,733 | 7,854 | gestión del servidor Dolt |
| `internal/web` + `tui` | 10,978 | 6,991 | dashboard |
| `internal/polecat` | 6,151 | 6,888 | |
| `internal/tmux` | 5,581 | 5,023 | |
| `internal/mail` | 5,351 | 5,385 | |
| `internal/witness` | 5,201 | 4,663 | detectores Go usados por el Witness LLM |
| `internal/refinery` | 5,091 | 5,567 | incluye batch/bisect no cableado |
| `internal/session` | 2,138 | 2,362 | |
| `internal/deacon` | 1,928 | 2,405 | |
| `internal/formula` | 1,771 | 3,571 | |
| `internal/hooks` | 1,645 | 2,932 | instalación de hooks de Claude/Codex/etc. |
| `internal/quota` | 1,412 | 3,056 | rotación de cuentas |
| `internal/dog` | 1,212 | 2,603 | |
| `internal/nudge` | 790 | 976 | |
| `internal/convoy` | 746 | 2,187 | |
| `internal/scheduler` | 650 | 993 | |
| `internal/checkpoint` | 350 | 627 | |

**Inferencia**: el núcleo de orquestación (daemon + sling + witness + refinery + scheduler + convoy) son unas 40k LOC. A eso se suman ~24k líneas de prompts TOML que también son "código" ejecutado por LLMs. `doctor` (20k) es casi tan grande como daemon + witness + refinery juntos, lo que sugiere un alto coste de mantener el sistema sano.

---

## 2. Roles

**Implementado**: todos los roles se lanzan como **sesiones tmux que ejecutan un CLI de agente LLM** (Claude Code por defecto). El camino unificado es `session.StartSession` (`internal/session/lifecycle.go:144`). Su comentario de tipo enumera "polecat, mayor, boot, deacon, witness, refinery, crew, and dog session managers" (`lifecycle.go:19-23`). La sesión se crea con `tmux new-session -d -s <name>` (`internal/tmux/tmux.go:285`).

- Witness: `t.NewSessionWithCommandAndEnv` en `internal/witness/manager.go:245` (dentro de `Start`, línea 116).
- Deacon: `internal/deacon/manager.go:164`.
- Mayor: `internal/mayor/manager.go:162`. Dogs: `internal/dog/session_manager.go:117`. Boot: `internal/boot/boot.go`.

| Rol | Tipo | Ámbito | Cómo se lanza | Contexto que recibe |
|---|---|---|---|---|
| **Daemon** | **Proceso Go determinista** | town | `gt daemon`, launchd/systemd (`internal/templates/launchd`, `systemd`) | config + Beads + tmux |
| **Mayor** | LLM, persistente, interactivo con el humano | town | `ensureMayorRunning` (`internal/daemon/daemon.go:1900`) | `mayor.md.tmpl` (357 líneas) vía `gt prime` |
| **Deacon** | LLM, patrulla en bucle (`mol-deacon-patrol`) | town | `ensureDeaconRunning` (`daemon.go:1463`) | `deacon.md.tmpl` (422) + formula |
| **Boot** | LLM efímero (haiku por defecto) que hace triage del Deacon | town | `ensureBootRunning` (`daemon.go:1320`), cooldown 2 min | `boot.md.tmpl` + `mol-boot-triage` |
| **Dogs** | LLM (haiku por defecto) para mantenimiento; pool máx. 4 (`config/operational.go:44`) | town | `handleDogs` desde el latido | `dog.md.tmpl` + `mol-dog-*` |
| **Witness** | LLM, patrulla por rig (`mol-witness-patrol`) | rig | `ensureWitnessesRunning` (`daemon.go:1723`) | `witness.md.tmpl` (340) |
| **Refinery** | LLM, patrulla por rig (`mol-refinery-patrol`, 1,285 líneas) | rig | `ensureRefineriesRunning` (`daemon.go:1800`) | `refinery.md.tmpl` (373) |
| **Polecats** | LLM trabajadores; identidad persistente, sesión efímera, worktree reutilizable | rig | `gt sling` → `SpawnPolecatForSling` (`internal/cmd/polecat_spawn.go:102`) | `polecat.md.tmpl` (597) + bead enganchado + `mol-polecat-work` |
| **Crew** | LLM en clon completo, ciclo de vida gestionado por el humano | rig | manual (`internal/crew`) | `crew.md.tmpl` (546) |

**Implementado: mapeo de modelo por rol.** `CostTierRoleAgents` (`internal/config/cost_tier.go`) define 5 niveles: standard, economy, budget, custom-groq-*. En `standard`, Boot y Dog usan `claude-haiku` y el resto el modelo por defecto. En `economy`, Deacon pasa a haiku y Witness/Mayor a sonnet.

**Implementado: inyección de contexto.** Los hooks de Claude Code instalados por Gas Town (`internal/hooks/templates/claude/settings-autonomous.json`) hacen esto:
- `SessionStart` y `PreCompact` → `gt prime --hook`.
- `UserPromptSubmit` → `gt mail check --inject` (excepto en witness/refinery/deacon/boot).
- `Stop` → `gt costs record`.
- `PreToolUse` → guardas contra `sudo`, `brew install`, `gh pr create`.
- Además fija `permissions.defaultMode: "bypassPermissions"`.

`gt prime` (`internal/cmd/prime.go:530-543`) emite en orden:
1. metadatos de sesión;
2. plantilla del rol;
3. directivas del operador;
4. archivo de contexto;
5. contenido de handoff;
6. estado de adjuntos;
7. molécula y checkpoint (`prime.go:232-233`);
8. si hay trabajo enganchado, una "directiva autónoma" con los detalles del bead (`checkSlungWork`, `prime.go:691-715`).

Para runtimes sin hooks (p. ej. Codex), se usa un comando de arranque de respaldo por tmux (README:474; ver `docs/agent-provider-integration.md`).

**Solo documentado**: "Scale comfortably to 20-30 agents" (README:15) y "At scale (20-50+ agents)" (README:585). No hay benchmark ni test de carga que lo respalde.

**Inferencia**: con R rigs hay **2 + 2R agentes LLM siempre encendidos** (Mayor, Deacon, Witness×R, Refinery×R). A eso se suman Boot y hasta 4 Dogs intermitentes, más N polecats. La propia formula del Witness intenta que un ciclo ocioso gaste "~10% of the tokens a full patrol uses" (`mol-witness-patrol.formula.toml:1`, sección "Abbreviated Patrol Mode"). Es decir, el coste en reposo **no es cero**.

> **REIN**: analogía parcial. REIN tiene un solo trabajador LLM fresco por tarea y **cero LLM en el bucle**. El daemon Go es lo más parecido a REIN. Witness, Refinery, Deacon y Mayor como LLM son justo lo que REIN evita.

---

## 3. Beads, Hooks, Convoys, Molecules/Formulas, Wisps

### Implementado
- **Almacenamiento: Beads sobre servidor Dolt SQL**, no JSONL en git como fuente de verdad.
  - Dependencias: `github.com/steveyegge/beads v1.0.5` y `go-sql-driver/mysql` (`go.mod`).
  - El wrapper invoca el binario `bd` con `exec.CommandContext(ctx, "bd", ...)` (`internal/beads/beads.go:794,866`).
  - El daemon arranca y vigila el servidor Dolt en cada latido (`ensureDoltServerRunning`, `daemon.go:1033`; health check cada 30 s, `operational.go:86`).
  - Hay una exportación de respaldo a JSONL en git (`internal/daemon/jsonl_git_backup.go`).
- **Dos niveles de beads**: town (`hq-*`) y rig (prefijo del proyecto). El enrutado por prefijo usa `routes.jsonl`, y los worktrees apuntan con `.beads/redirect`. Solo documentado en `docs/design/architecture.md`, pero coherente con `internal/beads`.
- **Hook = bead fijado por agente.**
  - Al trabajo enganchado se le pone el estado `hooked` (`internal/beads/handoff.go:21-22`).
  - El *agent bead* guarda un campo `hook_bead` (`internal/beads/beads.go:203`, `beads_agent.go:44`) y `agent_state` (spawning, working, done, nuked, …).
  - `gt sling` fija el `hook_bead` de forma atómica al crear el polecat (`polecat_spawn.go`, comentario "Build add options with hook_bead set atomically at spawn time").
- **GUPP** ("si hay trabajo en tu hook, ejecútalo") **es un prompt más un detector**:
  - El prompt lo imponen `gt prime` y la directiva autónoma.
  - El detector Go es `checkGUPPViolations` (`internal/daemon/lifecycle.go:1068`). Si un polecat tiene `hook_bead`, la sesión vive y el bead no se actualiza en más de 30 min (`DefaultGUPPViolationTimeout`, `operational.go:19`), el daemon **envía mail al Witness** (`lifecycle.go:1146-1180`). No reinicia directamente.
- **Formulas/Molecules**:
  - Las formulas son TOML con tipos `convoy|workflow|expansion|aspect` (`internal/formula/types.go:14-25`). Tienen `steps`, `vars`, `extends`, `compose`, `legs` y `synthesis`.
  - `Pour=true` materializa los pasos como sub-wisps con recuperación por checkpoint (`types.go:34`).
  - Flujo de `sling --formula`: "cook → wisp → attach to hook → nudge" (`internal/cmd/sling_formula.go:287`, `bd cook` en :463, `bd mol wisp` en :475).
  - Hay 46 formulas embebidas (`internal/formula/formulas/`): patrullas, trabajo de polecat, revisión, etc.
- **Wisps** = beads efímeros en tablas `wisps*`, marcadas `dolt_ignore` (sin historial). Ver `docs/design/dolt-storage.md:177`, `internal/wisp`. Riesgo: el issue #4605 reporta que `bd mol wisp gc --age` borra MRs vivos sin recuperación posible.
- **Convoys** = beads que rastrean un conjunto de issues.
  - Alimentación determinista: `feedNextReadyIssue` (`internal/convoy/operations.go:285`) ordena por prioridad e ID, salta los bloqueados o no "slingables" y despacha **un** issue por evento de cierre con `gt sling <id> <rig> --no-boot` (`operations.go:612-627`).
  - El daemon lo dispara por eventos (`internal/daemon/convoy_manager.go`).

### Solo documentado
- "Git-backed hooks" (README:7). En realidad el almacén primario es Dolt. Git solo aparece como respaldo JSONL y por el versionado interno de Dolt.
- El ciclo de vida de datos de seis etapas CREATE→…→FLATTEN (`architecture.md`): el propio documento dice que las etapas 4-6 están "being shipped".

### Inferencia
Beads ejerce a la vez de cola de trabajo, buzón de correo, registro de agentes y log de estado. Esto da una única fuente de verdad consultable, pero acopla todo a la disponibilidad de Dolt y a la compatibilidad de esquema con `bd`. Los issues lo confirman: #4793 "queries a beads column that no longer exists", #4789/#4770 desfase de esquema v53/v65, #4749 una migración que tarda más de 61 min.

> **REIN**: plan con tareas + JSON de estado ≈ Beads + hook, pero sin servidor. Brecha: REIN no tiene un "hook" por trabajador que sobreviva a un respawn dentro de la misma tarea; reintenta con un trabajador fresco, lo cual es más simple. Convoy ≈ el DAG del plan de REIN.

---

## 4. Persistencia entre sesiones y Seance

### Implementado
- **La persistencia es el bead, no la sesión.** Una sesión nueva ejecuta `gt prime` por el hook `SessionStart`, descubre su `hook_bead` (`findAgentWork`, `prime.go:743`) y recibe la directiva de continuar.
- **Handoff** (`internal/cmd/handoff.go`, 1,744 LOC): el flujo es "collect state → send handoff mail → respawn pane (fresh Claude instance)" (`handoff.go:125`).
  - El mail de handoff es un bead auto-enganchado al sucesor (`handoff.go:294-309`).
  - El reinicio usa `tmux respawn-pane -k` (`handoff.go:370-372`).
  - `--collect` añade hooked work, inbox, ready e in-progress.
  - `--cycle` lo usa `PreCompact` para refrescar el contexto.
  - En polecats, `gt handoff` se convierte en `gt done --status DEFERRED`.
- **Checkpoint**: `.polecat-checkpoint.json` con molecule_id, current_step, modified_files, last_commit, branch, hooked_bead, session_id y notes (`internal/checkpoint/checkpoint.go`). `prime` lo inyecta (`outputCheckpointContext`).
- **Seance** (`internal/cmd/seance.go`, 872 LOC):
  - **Descubrimiento**: lee eventos `session_start` de `~/gt/.events.jsonl` (`discoverSessions`, `seance.go:307-345`). Esos eventos los escribe `gt prime` (`prime_session.go:212-243`).
  - **"Hablar"**: `exec` de `claude --fork-session --resume <session-id>`, en modo interactivo o con `-p "<pregunta>"` de una sola vez (`seance.go:208-291`, args en :245).
  - **Lo que se transfiere**: nada se copia al agente actual. Se lanza un **proceso nuevo que hace fork** de la conversación previa completa (el transcript `.jsonl` de Claude Code), así que la sesión original no se modifica, y se le hace una pregunta. Solo la respuesta (stdout) vuelve a quien pregunta.
  - **Multi-cuenta**: localiza el `.jsonl` en el `projects/` de otra cuenta (`findSessionLocation`, :444) y crea un symlink temporal en la cuenta actual (`symlinkSessionToCurrentAccount`, :545). Limpia symlinks huérfanos (:758).
  - **Solo Claude**: requiere un preset con `SupportsForkSession`. Solo `claude` lo tiene (`internal/config/agents.go:243`); Gemini, Codex y demás tienen `false`.
  - **No se invoca automáticamente**: ninguna formula ni plantilla llama `gt seance`. Fuera de `seance.go` solo hay referencias en prime (emisión de eventos), doctor y events.

### Solo documentado
- "agents to recover context and decisions from earlier work without re-reading entire codebases" (README, sección Seance). Es una herramienta manual o a criterio del agente, sin integración en el flujo.

> **REIN**: análogo plausible y barato. Cuando una tarea queda roja, REIN podría hacer `claude --fork-session --resume <id del trabajador fallido> -p "¿por qué falló X?"` e inyectar la respuesta en el prompt del trabajador fresco. Brecha: solo funciona con Claude; Codex y Antigravity no tienen fork-resume equivalente en este código.

---

## 5. Comunicación entre agentes y escalado a humanos

### Implementado
- **Mail = beads.**
  - Enviar mail ejecuta `bd create --assignee=<recipient> -d <body> --labels=gt:message,...` (`internal/mail/router.go:1138-1142`).
  - Hay colas y listas, con `--assignee=queue:<name>` (`router.go:1282`).
  - Los tipos son task, escalation, scavenge, notification y reply (`internal/mail/types.go`).
  - La entrega pasiva ocurre por el hook `UserPromptSubmit` (`gt mail check --inject`).
- **Nudge** (`internal/cmd/nudge.go:49-68`). Tiene tres modos:
  - `immediate`: `tmux send-keys -l <texto>` y luego `Enter` por separado (`internal/tmux/tmux.go:1277-1288`).
  - `queue`: archivos JSON en `.runtime/nudge_queue/<session>/`, drenados por el hook en el siguiente turno (`internal/nudge/queue.go:1-9`). TTL de 30 min normal y 2 h urgente; profundidad máxima 50.
  - `wait-idle` (por defecto): espera a ver el prompt y, si no aparece, cae a `queue`.
  - El motivo documentado en el código es que send-keys "cancels in-flight tool calls".
- **Escalado** (`internal/cmd/escalate_impl.go`):
  - Severidades low/medium/high/critical.
  - Rutas por defecto (`internal/config/types.go:1751+`): low→bead; medium→bead+mail:mayor; high→+email:human; critical→+sms:human.
  - Las acciones externas son email SMTP, SMS por webhook (Twilio), Slack por webhook y log (`escalate_impl.go:686-860`).
  - **Re-escalado** de los no reconocidos: `StaleThreshold` por defecto 4 h y `MaxReescalations` 2 (`types.go` ~1680-1690). Comandos `gt escalate stale/ack/close`.
- **E-STOP**: si `estop.IsActive`, el daemon salta toda la gestión de agentes (`daemon.go`, inicio de `heartbeat`).

### Inferencia
Todo el protocolo entre agentes es texto libre interpretado por LLMs (asuntos como `GUPP_VIOLATION`, `MERGE_READY`, `POLECAT_DONE`). Es frágil. #4734: "gt done emits no POLECAT_DONE, so the witness never sends MERGE_READY". #4607: el mail crea un turno nuevo y cancela tool calls. #4666: nudge perdido silenciosamente en tmux 3.7b.

> **REIN**: Orca ofrece estado del trabajador y *decision gates* como canal estructurado, mejor que send-keys. Brecha transferible: severidad y re-escalado temporizado de las preguntas humanas sin responder.

---

## 6. Scheduler, concurrencia, cuota y coste, multi-proveedor

### Implementado
- **Scheduler de capacidad** (`internal/scheduler/capacity/config.go:12-41`):
  - `max_polecats` vale -1 por defecto, es decir despacho directo **sin límite**. Con N>0 el despacho se difiere y el daemon lo hace.
  - `batch_size` por defecto 1 bead por tick de 3 min; `spawn_delay` 0 s.
  - El daemon ejecuta `gt scheduler run` (`daemon.go:3083-3086`), que corresponde a `DispatchCycle.Plan/Run` (`capacity/dispatch.go:91-140`).
- **Presión del host** (`internal/daemon/pressure.go:43`): carga de CPU, memoria libre y número de sesiones activas. Los umbrales por defecto son 0, es decir **desactivados** (`operational.go:56-58`).
- **Admisión antes del spawn**: health check de Dolt y control de conexiones (`polecat_spawn.go` ~130-160). **Circuit breaker por bead** a las N re-ejecuciones (`polecat_spawn.go:161`; `DefaultWitnessMaxBeadRespawns = 3`, `operational.go:112`). Pool de nombres de 50 (`operational.go:81`).
- **Cuota = rotación de cuentas de Claude**, no enrutado entre proveedores:
  - `internal/quota/scan.go` captura el pane de tmux y aplica regex de límite duro y de cerca-del-límite (`internal/constants/constants.go:432-453`, p. ej. `You've hit your .*limit`, `\d{2,3}%\s*(of\s*)?(your\s*)?(daily\s*)?(usage|limit|quota)`).
  - `rotate.go` planifica y `executor.go` ejecuta el cambio de credencial en el **Keychain de macOS** (`keychain.go:32-34`, servicio "Claude Code-credentials-<sha>") y reinicia la sesión con `--resume` mediante symlink.
  - Un "quota dog" del daemon ejecuta `gt quota rotate` cada 5 min (`internal/daemon/quota_dog.go:25-43`).
- **Coste**: el hook `Stop` → `gt costs record` lee el transcript de Claude Code, aplica precios por modelo y añade a `~/.gt/costs.jsonl` (`internal/cmd/costs.go:51-80`). Solo Claude (#3835 pide soporte para opencode).
- **Multi-proveedor**:
  - Presets para claude, gemini, codex, kiro, cursor, auggie, amp, opencode, copilot, pi, omp y vibe (`internal/config/agents.go:27-50`, comandos en ~235-480).
  - Plantillas de hooks por runtime (`internal/hooks/templates/{claude,codex,copilot,cursor,gemini,omp,opencode,pi,vibe}`).
  - `role_agents` por town, rig y crew (`internal/config/types.go:64-69,692-701`).

### Solo documentado
- Que el scheduler "prevent API rate limit exhaustion" (README). Con el valor por defecto (-1) no limita nada.

> **REIN**: REIN enruta por cuota **entre proveedores**. Gas Town rota **cuentas dentro de Claude**. Idea transferible: detectar el límite duro y la proximidad al límite por regex sobre la salida del terminal, para distinguir "rate-limited" de "colgado" (#4887 muestra el coste de no hacerlo).

---

## 7. Recuperación ante agentes atascados, interrupciones y reinicios

### Implementado: daemon (determinista), latido de recuperación cada 3 min
`DefaultRecoveryHeartbeatInterval` está en `operational.go:48` y `heartbeat()` en `daemon.go:853-1017`. En cada tick hace, en orden:
1. matar sesiones fantasma;
2. asegurar Dolt;
3. asegurar Deacon y Boot y revisar el latido del Deacon;
4. asegurar Witnesses y Refineries;
5. asegurar el Mayor;
6. ejecutar los Dogs;
7. revisar violaciones GUPP;
8. revisar trabajo huérfano;
9. revisar la salud de las sesiones de polecat;
10. cosechar polecats ociosos;
11. limpiar procesos huérfanos;
12. podar ramas;
13. despachar la cola;
14. rotar logs.

Detectores y umbrales:

| Detector | Señal | Umbral por defecto | Acción | Ref |
|---|---|---|---|---|
| Crash de polecat | sesión tmux muerta + `hook_bead` abierto + estado ≠ done/nuked | guarda de spawning de 5 min; re-chequeo TOCTOU | evento + **mail al Witness** (no reinicia directo) | `daemon.go:2637-2746` |
| Muerte masiva | ≥3 muertes en 30 s | | evento `mass death` | `operational.go:38-39`, `daemon.go:2747-2799` |
| GUPP | `hook_bead` + sesión viva + bead sin actualizar | 30 min | mail al Witness | `lifecycle.go:1068-1180` |
| Latido del Deacon | archivo heartbeat | stale 5 min → nudge (solo si hay trabajo en vuelo); muy stale 20 min → kill+restart con backoff | | `operational.go:66-67`, `daemon.go:1525-1700` |
| Polecat ocioso | inactividad | 15 min | kill | `operational.go:41`, `daemon.go:2876-3010` |
| Latido de polecat | archivo tocado en **cada comando `gt`** del polecat/crew/dog | stale 3 min | usado por los detectores del Witness | `internal/polecat/heartbeat.go:11`, `internal/cmd/root.go:230-242` |
| Sesión colgada | sin actividad en tmux | 30 min | restart | `operational.go:20`, `internal/witness/handlers.go:33-36` |
| Arranque atascado | prompt de inicio | 90 s, gracia 60 s | auto-descartar | `operational.go:110-111` |
| Claim de MR | asignado sin avance | 30 min | re-claim | `internal/refinery/engineer.go:38-56` |
| Re-despacho | bead recuperado | máx. 3, cooldown 5 min → escalar al Mayor | | `internal/deacon/redispatch.go:218`, `operational.go:68-69` |

- **Witness LLM + detectores Go.** El paso `survey-workers` de `mol-witness-patrol` **obliga** a ejecutar `gt patrol scan --notify` ("Do NOT improvise…"). Ese comando llama `witness.DetectZombiePolecats`, `DetectStalledPolecats` y `DiscoverCompletions` (`internal/cmd/patrol_scan.go:162-168`; `handlers.go:1627,2251,2393`).
- **Política "restart-first"**: el Witness reinicia y nunca hace nuke automático (`handlers.go:1298-1320`; formula, sección "Restart-First Policy").
- **Heartbeat v2**: el agente auto-reporta working, idle, exiting o stuck (`heartbeat.go:13-35`). El Witness solo infiere "¿está fresco?".

### Inferencia y evidencia de issues
La detección por tiempo con múltiples fuentes de verdad (tmux, Beads, archivos de latido, nombres de sesión) produce **falsos positivos destructivos**:
- #4787 "healthy agents killed town-wide" por nombres de sesión que no coinciden.
- #4890: el idle reaper mata un polecat 1 s después del despacho porque lee el estado de la sesión anterior.
- #4887: un bloqueo por límite de uso se confunde con un cuelgue.
- #3638: el handler de Deacon atascado registra en el log pero no reinicia.
- #4386: el bucle await-event del Refinery muere en silencio.

Cada guarda que se ve en el código (spawning de 5 min, TOCTOU, estado terminal, hook cerrado) es el parche de un incidente pasado.

> **REIN**: análogo plausible. REIN se apoya en el estado de trabajador de Orca y en timeouts por tarea. Transferible: un circuit breaker por tarea (máx. N trabajadores frescos por tarea, con cooldown, y luego escalar), detección de "muerte masiva" para pausar el despacho, y re-verificar antes de actuar (TOCTOU). Brecha: REIN no necesita un watchdog del watchdog (Boot→Deacon) porque no tiene supervisores LLM.

---

## 8. Merge queue / Refinery

### Implementado (camino real de producción)
El Refinery es un **agente LLM** que ejecuta `mol-refinery-patrol.formula.toml` (1,285 líneas). Pasos: inbox-check → queue-scan (`gt mq list`) → merged-pr-sweep → **process-branch** → run-tests → quality-review → handle-failures → merge-push → loop-check.

- **Ensayo de merge, rama por rama** (`formula:326-421`). "Pick next branch from queue" → `git checkout -b temp origin/<branch>` y `git merge --no-ff --no-edit origin/<target>`.
  - Si hay conflicto: `git merge --abort` y `bd create --type=task --priority=1 --title="Resolve merge conflicts: …"` con los SHA. El MR queda abierto y la rama se conserva.
  - Existe la formula `mol-polecat-conflict-resolve` para despachar esa tarea a un polecat.
- **Gates** (`formula:423-466`): `setup_command`, `typecheck_command`, `lint_command`, `build_command` y `test_command` configurables por rig. `run_tests = "false"` salta todo el paso. **Las ejecuta el LLM** siguiendo el prompt.
- **Quality review** (`formula:468-540`): el mismo LLM revisa el diff y le da una puntuación de 0 a 1. Es **"measurement-only (Phase 1): reviews are recorded but do NOT gate merges"**.
- **Fallos**: nota en el bead con el número de intento; búsqueda de bugs preexistentes (`bd search … --label gt:bug`) antes de crear uno (`formula:541-608`).
- **Merge y limpieza deterministas en Go**: `gt mq post-merge <rig> <mr>` (`internal/cmd/mq.go:171-189`) verifica que el destino contiene el head enviado ("merge proof", `engineer.go:1538`) y luego cierra el MR y borra la rama. La formula obliga a usarlo.
- **Polecat antes del MR**: el paso `pre-verify` de `mol-polecat-work` hace rebase y gates (LLM). `gt done --pre-verified` lo registra (`internal/cmd/done.go:661`). `gt done` verifica en Go que el commit empujado es alcanzable (`done.go:1088,1234,1446`).
- **Tests de la rama principal**: el daemon ejecuta periódicamente los gates del rig sobre un worktree de main (`internal/daemon/main_branch_test_runner.go:139-290`). Esto sí es determinista.

### Implementado pero NO cableado
`internal/refinery/batch.go` contiene `AssembleBatch` (:58), `BuildRebaseStack` (:97), `ProcessBatch` (:211), `bisectBatch` (:487) y `bisectRight` (:553): un Bors con stack, rebase, gates sobre la punta y bisección. **`ProcessBatch` solo lo referencian `batch_test.go` y `prepush_recheck_test.go`.** `ProcessMRInfo` (`engineer.go:1321`) no tiene llamadores. Ninguna formula ni comando `gt mq` invoca batching.

### Solo documentado (y contradicho)
- README:96 y :651-661: "batches merge requests… merges to main using a Bors-style bisecting queue".
- El propio `docs/design/architecture.md` (tabla "Implementation Phases") marca *GatesParallel* como "In progress" y *Batch-then-bisect* como "Blocked by Phase 1".

> **REIN**: REIN corre gates deterministas por tarea en el resultado del trabajador, **sin LLM**, y además una auditoría con otro modelo. Eso es más fuerte que el Refinery LLM, cuyos gates "ejecuta el LLM" y cuya revisión no bloquea. Transferible: la **merge rehearsal + post-merge proof** (verificar que el destino contiene el SHA exacto) y, como referencia de diseño, el batch-bisect de `batch.go` cuando haya muchas tareas verdes que integrar.

---

## 9. Coste de orquestación y complejidad operativa

### Implementado
- **Dependencias en tiempo de ejecución**: tmux ≥3.0 (README:136); servidor Dolt (ahora ≥2.6.0, commit HEAD); binario `bd` de una versión compatible; CLI del agente (Claude Code por defecto); en macOS, Keychain para la rotación. Go 1.26 para compilar.
- **Procesos siempre encendidos**: daemon Go + servidor Dolt + (2 + 2R) sesiones LLM + polecats. Boot y Dogs son intermitentes.
- **Tests de integración** con testcontainers y Dolt (`go.mod`).
- **Modo de permisos**: `bypassPermissions` (plantilla de hooks), con guardas por patrón en `PreToolUse`.

### Puntos de dolor (issues, todos abiertos salvo que se indique)
- **Desfase de versiones Beads/Dolt**: #4793, #4789, #4770, #4727, #4816/#4817, #4749.
- **Liveness y falsos positivos**: #4787, #4890, #4887, #4669, #3877, #3638.
- **Durabilidad del hook y las moléculas**: #4870 ("Hook durability silently broken"), #4735, #4774, #4732 ("Formula instantiation creates a bare wisp"), #4720.
- **Merge queue**: #4386, #4514, #4195, #4627, #4605, #4748.
- **Protocolo por mail**: #4734, #4607, #4666.
- **Fragilidad de la UI de tmux**: #4818 (el diálogo de confianza de Claude Code ahora preselecciona "No, exit" y gt pulsa Enter), #4603.
- **Prompts peligrosos**: #4830 (las formulas embebidas incluyen `bd mol wisp gc --age 1h --force`, que borra wisps vivos), #4909.
- **Reaper**: #4911, #4885 (auto-cierra issues vivos).
- **Mantenimiento**: #4930 (deprecación de facto en favor de Gas City). Hueco de commits de unos 3 meses (2026-07-23 → 2026-10-20). En el log de commits aparecen autores con nombres de agente ("mayor", "coder", "deathclaw", …).

### Inferencia
El coste dominante no es el dinero de los polecats (que también tiene REIN) sino:
1. los tokens de las patrullas LLM permanentes;
2. la superficie de estado distribuido (tmux + Dolt + archivos `.runtime` + eventos JSONL) que hay que reconciliar;
3. la deriva de los prompts (24k líneas de TOML que el LLM puede seguir mal y que no se pueden testear unitariamente como el Go).

---

## 10. Las 5 ideas más transferibles a REIN

| # | Idea | Evidencia | Analogía o brecha en REIN | Costes y riesgos |
|---|---|---|---|---|
| 1 | **"Prime" determinista al arrancar una sesión**: la asignación vive fuera de la sesión (hook bead + checkpoint), y un hook `SessionStart`/`PreCompact` reinyecta rol, tarea, checkpoint y directiva de continuar. | **Fuerte**: `prime.go:530-543,691-715`; `hooks/templates/claude/settings-autonomous.json`; `checkpoint.go` | REIN ya hace "fresh worker per task" con el estado en JSON. Lo transferible es un payload de prime **reproducible**: tarea, criterios, salida del gate rojo anterior y diff previo, que se regenere también tras compactación. | Depende de que el runtime tenga hooks (Codex y otros necesitan fallback). Bajo coste si REIN lo genera como archivo/prompt. |
| 2 | **Circuit breakers y guardas de liveness**: máx. 3 re-ejecuciones por bead, cooldown de re-despacho de 5 min y luego escalar; detección de muerte masiva (≥3 en 30 s); guarda de spawning; re-chequeo TOCTOU antes de actuar; heartbeat implícito en cada comando CLI. | **Fuerte** en código (`polecat_spawn.go:161`, `deacon/redispatch.go:218`, `daemon.go:2637-2799`, `cmd/root.go:230-242`). **Media** en eficacia (muchos issues de falsos positivos). | REIN reintenta tareas rojas: añadir un tope por tarea con escalado a decision gate, y pausar todo el despacho ante muertes masivas (caída del proveedor). | Los umbrales por tiempo producen falsos positivos (#4787, #4890). Hay que usar la señal autoritativa de Orca y no múltiples fuentes. |
| 3 | **Distinguir "rate-limited" de "colgado" por regex sobre la salida del terminal**, con detección de cerca-del-límite para rotar antes del 429. | **Media**: `quota/scan.go`, `constants.go:432-453`, `daemon/usage_limit.go:25-31`. Patrones específicos de Claude; la rotación depende del Keychain de macOS. | REIN enruta por cuota entre proveedores. Aplicar el escaneo a la salida del trabajador para reclasificar un fallo como "cuota" (reencolar en otro proveedor) en vez de "rojo" (consumir reintento). | Los patrones se rompen cuando cambia la UI del CLI. Mantener una tabla por proveedor. |
| 4 | **Seance: consultar al trabajador anterior con fork-resume**, `claude --fork-session --resume <id> -p "<pregunta>"`, sin mutar su sesión. | **Fuerte** como implementación (`seance.go:208-291`). **Débil** en valor demostrado (no integrado en ningún flujo; uso manual). | Al reintentar una tarea roja, preguntar al trabajador fallido "qué intentaste y por qué falló" e inyectar la respuesta en el prompt del nuevo. Más barato que releer todo el transcript. | Solo Claude (`agents.go:243`); hay que localizar el `.jsonl` por cuenta/proyecto. Coste de tokens: re-cargar el contexto completo del predecesor (el caché ayuda). Riesgo de propagar el razonamiento erróneo. |
| 5 | **Merge rehearsal + post-merge proof + batch-then-bisect**: ensayar el merge contra el destino, crear tarea de conflicto conservando la rama, verificar que el destino contiene el SHA exacto, y en lote probar la punta del stack y bisecar los culpables. | **Fuerte** para rehearsal/proof (formula:326-421; `engineer.go:1538`; `mq.go:171`). **Débil** para batch-bisect (`batch.go:211,487`, solo tests, no en producción). | REIN valida cada tarea aislada. Brecha: la integración de varias tareas verdes puede romper main. Un paso determinista de integración (stack + gates en la punta + bisección) cerraría esa brecha sin LLM. | Coste de CPU de los gates sobre el stack. El bisect necesita gates deterministas y rápidos (REIN los tiene). El código de Gas Town no está probado en producción. |

Mención aparte, por si interesa:
- **Escalado con severidad y re-escalado temporizado** (`escalate_impl.go`, `types.go:1751`): evidencia fuerte en código. En REIN: preguntas de decision gate sin respuesta tras X horas → notificar por otro canal.
- **Nudge cooperativo en el límite del turno** en vez de send-keys (`nudge/queue.go`): poco relevante si Orca ya da un canal estructurado.

---

## 11. Notas de veracidad

- Lo afirmado en README que **no** se encontró en el camino de producción: la cola Bors con bisección; el escalado cómodo a 20-30 agentes (sin evidencia); "git-backed hooks" (el almacén es Dolt).
- Lo afirmado en README que **sí** se verificó: el multi-proveedor por presets y hooks; Seance mediante `--fork-session --resume`; la cadena de vigilancia daemon→Boot→Deacon→Witness; el scheduler de capacidad (desactivado por defecto); la rotación de cuentas por cuota; el escalado con email/SMS/Slack.
- No se ejecutó nada. Los umbrales citados son los valores por defecto compilados y pueden sobreescribirse en `settings/config.json` (`config.LoadOperationalConfig`, `operational.go:118-126`).
