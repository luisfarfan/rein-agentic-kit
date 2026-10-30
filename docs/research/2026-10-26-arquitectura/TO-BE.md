# TO-BE, memoria y roadmap

Complementa a [INFORME.md](INFORME.md); las referencias §N remiten a ese documento.

---

## 6. Arquitectura de memoria: ¿conviene una memoria compartida?

**Veredicto [R]: no conviene una memoria compartida como infraestructura nueva** (vector store,
grafo de conocimiento, Neo4j, Graphiti o RAG). Sí conviene una **memoria semántica mínima de tipo
handoff**, guardada en el JSON de estado que ya existe.

### Evidencia

| Pregunta | Evidencia | Fuerza |
|---|---|---|
| ¿Cuánto re-descubre un worker lo que otro ya leyó? | Implementación: mediana 8% y p90 37% de sus archivos. Fix de auditoría: mediana 23%. Re-auditoría: 50% (§3) | Fuerte (n=388 pares) |
| ¿Dónde se pierde información? | El resumen del `worker_done` (exigido por Orca) se descarta. El worker que falla no deja nada salvo el log del gate (§1.2) | Fuerte (código) |
| ¿Tiene valor el «Seance» multi-proveedor? | Orca ya lo indexa: 1.104 sesiones, `orca search` con `resumeCommand`. Gas Town lo tiene pero no lo usa en ningún flujo (§2.1) | Fuerte en existencia, nula en valor demostrado |
| ¿Ayuda un handoff verificado a la tarea siguiente? | E3 (§4.4): con hechos que se pueden volver a obtener con grep, **no**. Turnos: 9 [5–9] con handoff frente a 5 [5–6] sin él; el agente re-verifica | Media (n=3, una sola tarea) |

### Diseño mínimo

Son cuatro tipos de memoria, cada uno en el sitio que ya existe:

```
execution  →  .rein/ship/<change>.json  (sin cambios)
episodic   →  transcripts + `orca search` / `orca orchestration worker-read`  (solo lectura, bajo demanda)
semantic   →  TaskState.handoff = {summary, tried[], env_traps[], open_questions[]}   ← NUEVO, ~1-2 KB por tarea
              (lo NO derivable del código; E3 muestra que los hechos derivables no ahorran nada)
structural →  el código + git; CodeGraph/Graphify solo si E2 lo justifica (no lo justifica, §4.3)
```

### Flujo de un handoff

1. El worker termina con su `worker_done`, como ya exige Orca. El cuerpo lleva además un bloque
   `<result>{...}</result>` pequeño.
2. REIN lo parsea con tolerancia: toma el último bloque, con topes de tamaño.
3. Si el bloque falta, REIN envía **un único** recordatorio al mismo worker antes de cerrar el
   dispatch, siguiendo el patrón de Agetor.
4. El gate determinista sigue siendo la única autoridad. El handoff **no** marca una tarea como verde.
5. Lo que el código no dice (`tried`, `env_traps`, `open_questions`) se lleva a los briefs del
   reintento, del fix y de la auditoría de esa tarea. Las trampas del entorno van también a la tabla de
   triage. Cuando el gate quedó verde, se marcan como verificadas. Al inyectarse, van:
   - cercados con un nonce;
   - con un tope de 1.500 caracteres;
   - con la marca «verified-by-gate: yes/no».
6. Cuando el gate quedó rojo, el reintento recibe el handoff del worker fallido marcado como **«no
   verificado»**, junto con el fallo del gate ya limpio (ANSI y ruido fuera).

**Por qué no SQLite ni un grafo.** El volumen es de decenas de tareas por change, y el consumidor es
siempre la tarea siguiente del mismo change. Nada se consulta entre changes. Un JSON por change ya lo
cubre. **[I]** Si algún día hiciera falta memoria entre changes (convenciones del repo, trampas del
entorno como el hook de beads), su lugar natural es el `AGENTS.md`/`CLAUDE.md` del repo, que los tres
proveedores ya cargan. Ahí tendría revisión humana y no haría falta otra infraestructura.

---

## 7. TO-BE: la arquitectura propuesta

Se mantienen los principios: Python determinista, Orca como runtime, workers frescos con contexto
propio, routing por suscripción, gates y auditoría independientes, el humano en las decisiones y
ningún orquestador LLM.

```
 rein-discover → rein-plan            (sin cambios)
        │
        ▼
 rein ship <change>   (Python, sin LLM)
   ├─ preflight
   │    └─ + perfil de lanzamiento por rol  ............................ [P0-1]
   │         + `rein benchmark startup` (falla si arranque > umbral)
   ├─ routing
   │    └─ quota.choose_implementer(limits, buckets, discovered_models)  [P1-1]
   ├─ dispatch   (orca worker-start, sin cambios)
   │    └─ brief = task + decisions + [handoffs verificados de las deps]  [P0-3]
   ├─ supervisión (orca check --wait, sin cambios)
   │    └─ worker_done → parse <result> → 1 recordatorio si falta ........ [P0-3]
   ├─ gate (sin cambios en la autoridad)
   │    └─ RED → triage determinista ANTES de gastar un worker ........... [P0-2]
   │          ├─ entorno/hook (p. ej. reescritura de .beads) → arreglo mecánico o gate al dueño
   │          ├─ cuota/rate-limit → reencolar en otro proveedor, sin consumir reintento
   │          ├─ misma firma de fallo que el intento anterior → gate (no un 3.er worker)
   │          └─ código → reintento con fallo limpio + handoff no verificado
   ├─ auditoría (sin cambios; auditores con el perfil completo)
   └─ close + `rein benchmark observe` → ledger (Claude + Codex) ........ [P1-3]
```

### Contratos nuevos (solo los que resuelven un problema medido)

**1. Perfil de lanzamiento (`ship.launchProfile`)**

- Implementador: `.claude/settings.local.json` en el worktree, con los plugins de usuario
  desactivados y `deny` de Artifact, Workflow, ScheduleWakeup, ReportFindings, AskUserQuestion,
  ListAgents, SendFeedback, Agent y Skill. **ToolSearch se mantiene.** Medido en E1-C8:
  21,2k frente a 59,0k tokens de arranque. **Implementado** en `d1228ac` (`ship.launchProfile`). Piloto
  real en Orca: 53,6k → 19,0k en el primer turno y 244k → 89k tokens por worker. `advisor` se quitó de
  la lista: Claude no lo acepta como regla de `deny`, y en modo interactivo ese aviso bloqueó el
  arranque del worker.
- Las tareas `rendered` necesitan el MCP del navegador: se alcanza vía ToolSearch.
- Auditor: perfil completo, porque usa el agente code-reviewer.
- **Prohibido** denegar ToolSearch sin `--strict-mcp-config`: hace que se carguen en caliente todos
  los MCP y el arranque sube a 533k tokens (E1-C6). `rein benchmark startup` lo detecta.

**2. Bloque de resultado del worker**

```
<result>{"summary": "...", "tried": ["amend con el fix del hook: el guard sigue rechazando"], "env_traps": [".beads/issues.jsonl lo reescribe el pre-commit"], "open_questions": []}</result>
```

- Tope de 2 KB.
- Va dentro del cuerpo de `worker_done`, así que no cambia el contrato de Orca.
- Al pasarlo a otro agente se cerca con un nonce.

**3. Firma de fallo:** `sha1(slot, comando, primeras N líneas decisivas normalizadas)` por tarea. Una
repetición abre un gate en lugar de lanzar otro worker.

**4. Clasificador de fallos:** una tabla declarativa
`{patrón → clase (entorno | cuota | flaky | código), acción}` por proveedor y por repo, versionada en
REIN y con tests contra los fallos reales del corpus.

### Qué se simplifica o se quita [R]

- **El cableado de Serena** (`detect.py` serena-project, la regla de recuperación). En 657 workers se
  usó 0 veces. La decisión D2 ya lo marcó como «UNMEASURED».
- **`retrieval.codegraph: auto` como promesa a los workers.** Se queda solo como sugerencia en
  `rein-discover`, condicionada a los resultados de §4.3.
- **No añadir ningún subsistema nuevo:** ni base de datos, ni grafo, ni MCP, ni supervisor LLM. Lo
  propuesto cabe en `ship_decide.py`, `ship.py`, `quota.py` y una tabla de patrones, en unas 600–800
  LOC con sus tests. Es menos de lo que añadió cada «review round» de la última semana.

### Riesgos

| Riesgo | Mitigación |
|---|---|
| El perfil lean rompe algo en modo interactivo dentro de Orca (hooks de Orca, detección de tui-idle, preguntas con `orca orchestration ask`) | P0-1 empieza con un piloto de 1 change pequeño. `rein benchmark startup` en interactivo. Se puede volver atrás desactivando `ship.launchProfile` |
| `settings.local.json` aparece como cambio sin trackear y el guard lo trata como del worker | Escribirlo antes del lanzamiento y añadirlo a la lista de ignorados del guard. Comprobar `git check-ignore` |
| El handoff propaga un razonamiento erróneo | Solo los hechos de tareas verdes se marcan como verificados. Los de rojas van como «no verificado» |
| Claude dentro de Antigravity tiene otra calidad, otro comportamiento con `orca ask` u otro límite real | Antes de enrutar, EXPERIMENT P1-1: tareas sombra con verdad de referencia |
| Los patrones del clasificador se rompen al cambiar los CLIs | Tests sobre fallos reales del corpus. Un patrón desconocido se clasifica como «código», que es el comportamiento actual |

---

## 8. Roadmap

Priorizado por impacto demostrado, complejidad y esfuerzo. El esfuerzo está en LOC de Python con sus
tests.

### P0: evidencia fuerte, coste bajo

| # | Cambio | Evidencia | Impacto esperado | Esfuerzo | Cómo medirlo |
|---|---|---|---|---|---|
| P0-1 | **Perfil de lanzamiento lean para implementadores** (settings.local.json, E1-C8) más `rein benchmark startup` | E1: arranque −64% (59,0k → 21,2k). Corpus: el 77% de los tokens de un worker son relectura del arranque. Contrafactual: unos −27% de todos los tokens de workers Claude | Tokens por worker −25 a −35% **[I: sin validar en interactivo]** | ~150 LOC | Piloto con 1 change: contexto del primer turno y tokens por worker frente al ledger |
| P0-2 | **Triage determinista del fallo antes de reintentar**: limpiar ANSI y ruido, firma de fallo (no repetir), clase entorno/cuota/código, arreglo mecánico del caso del hook de beads | Unos 2/3 de los re-despachos recientes no son del código. Un mismo fallo de entorno se repitió en 3 ejecuciones. El 74% de los fallos llevan ANSI. Workers de reintento Claude sin edición: 78% | Menos dispatches por tarea: hoy 2,18, objetivo ≤1,5. Menos gates de stall | ~300 LOC | `analyze_states.py`: dispatches por tarea y causas, antes y después |
| P0-3 | **Capturar el `worker_done` y el bloque `<result>`** (qué se intentó, trampas del entorno, preguntas abiertas) y usarlo **para el triage de P0-2, para el auditor y para el reintento**, con un recordatorio único si falta. **No** usarlo para inyectar «hechos» con la idea de ahorrar exploración | Orca ya lo produce y REIN lo descarta. E3: los hechos derivables no ahorran turnos. Los 7 reintentos por el hook de beads habrían quedado explicados con una línea del worker anterior **[I]** | Mejor triage y menos reintentos ciegos. El ahorro de tokens por exploración se espera **pequeño** | ~150 LOC | Dispatches por tarea y causas (junto con P0-2) |

### P1: evidencia media, o alto valor con incógnitas

| # | Cambio | Evidencia | Paso previo |
|---|---|---|---|
| P1-1 | **Routing por bucket** (Antigravity: «Claude and GPT models» frente a «Gemini») usando `resetsAt`, y **descubrimiento de modelos** (`agy models`; Codex `model/list`) en lugar de la lista fija de `quota.py` | `agy models` lista Claude Sonnet/Opus 5.5. El bucket está al 2% mientras REIN frena Antigravity por el 53% del bucket Argon | EXPERIMENT: 3–5 tareas sombra con Antigravity-Sonnet. Comprobar `orca ask` y la calidad |
| P1-2 | Cercar con nonce todo el texto entre agentes: fallos, hallazgos, handoffs | Agetor; riesgo de inyección vía logs | Ninguno (~40 LOC) |
| P1-3 | `rein benchmark observe` en el cierre de cada `ship`: el ledger añade Codex, el contexto de arranque y las causas de reintento | Hoy `token_report` solo lee Claude, y Codex es el 28% de los tokens de workers | Ninguno |
| P1-4 | Pausar el despacho ante una «muerte masiva» (≥3 workers caídos en poco tiempo, por proveedor caído) | Gas Town; 11 re-despachos recientes sin `last_failure` | Revisar los 11 casos uno a uno |

### P2: experimentos y simplificación

| # | Cambio | Nota |
|---|---|---|
| P2-1 | Quitar el cableado de Serena y la promesa de CodeGraph a los workers | Uso 0 y 11 de 657; véase §4.3 |
| P2-2 | EXPERIMENT «Seance»: en el 2.º reintento, `worker-read` del worker fallido u `orca search` en lugar de releer | Solo si P0-2 y P0-3 no bajan los dispatches |
| P2-3 | Snapshot del plan al lanzar (hash más copia) | Sin incidentes observados; baja prioridad |
| P2-4 | Adelantar los defectos que hoy solo caza la auditoría (36% de los tokens de Claude) | Muestra de los 94 BLOCKING reales: son defectos de verdad, no estilo. Los más repetidos son contratos rotos entre repos (el admin llama a una ruta que la API ya quitó; un schema pierde un campo que el storefront consumía), carreras, migraciones de datos y criterios no cumplidos. **La auditoría es KEEP.** Lo barato sería un gate determinista de *contrato entre repos*, que regenere el cliente OpenAPI y haga el typecheck del consumidor en las ejecuciones multi-repo. Hay que medirlo antes |

### Lo que NO se recomienda (REJECT, con su razón)

- **Neo4j, Graphiti, RAG o embeddings:** sin evidencia de necesidad; el re-descubrimiento es bajo.
- **Supervisores LLM** al estilo de Gas Town: coste continuo y falsos positivos documentados.
- **Beads o Dolt como bus de estado:** duplica Orca.
- **Integrar CodeGraph o Graphify en el brief de los workers:** véase §4.3.
- **Leer la cuota de endpoints no documentados:** Orca ya la da.
