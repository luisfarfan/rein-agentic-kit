# ¿Qué viene después del SDD? Auditoría de REIN y evolución adaptativa

Fecha: 2026-10-28 · REIN `80b1732` · continúa [2026-10-26-arquitectura](../2026-10-26-arquitectura/INFORME.md),
que ya midió el contexto de arranque, el handoff y CodeGraph/Graphify. Aquí no se repite: se mide
**el workflow** (discover → plan → ship → audit) y su coste real.

Convención: **[H]** hecho medido o leído en código · **[I]** inferencia · **[R]** recomendación ·
**[O]** opinión de terceros (no evidencia).

---

> **Revisión del 2026-10-28 (§9).** Se intentó refutar este informe y varias afirmaciones no
> resistieron. Cada una está corregida donde aparece, marcada con **[corregido §9.x]**:
>
> - El coste tras `/ship` es 1,4× el de los workers en coste equivalente API, no 2,2×.
> - La auditoría **no** es una fuente importante de latencia.
> - Los 84 BLOCKING aún no están demostrados con tests.
> - `proposal.md` y `specs/` sí se consumen, pero por el planner y el dueño.
>
> El A/B de §6 queda sustituido por [PLAN-PILOTO.md](PLAN-PILOTO.md).
>
> **Actualización 2026-10-29.** Smoke ejecutado ([RESULTADOS-SMOKE.md](RESULTADOS-SMOKE.md)):
>
> - el primer BLOCKING histórico queda **demostrado** como defecto real (§9.2);
> - en 2 tareas, N0 (Claude nativo) fue correcto en ambas;
> - `ship` reprodujo el defecto histórico en el widget.
>
> El guard tiene una propuesta probada en [GUARD-AISLAMIENTO.md](GUARD-AISLAMIENTO.md).

## 0. Resumen en diez líneas

1. **El SDD de REIN no es lo que más cuesta.** En 30 días, los workers de `rein ship` gastaron el
   **4,3%** del coste equivalente API de Claude (4,1% del volumen de tokens). Las sesiones
   interactivas sin REIN, el **69%**. Las sesiones donde se hizo discover/plan, el **26%**, pero la
   mayor parte no es planificar: es la misma sesión Opus que sigue viva, haciendo otras cosas
   **[corregido §9.1]**.
2. **Planificar en sí es moderado:** mediana de 38 turnos, 8,7 M tokens y 10 min desde `/rein-plan`
   hasta el plan escrito, con 2 mensajes del dueño.
3. **La auditoría independiente es lo que más valor ha demostrado:** 84 hallazgos BLOCKING en 83
   ejecuciones, y al leerlos uno por uno son defectos reales (contratos rotos entre repos, carreras,
   lógica fiscal y de pagos). El 61% de las ejecuciones necesitó al menos un fix, incluso las de una
   sola tarea (64%). Cuesta ~1,5% de los tokens totales y **solo ~5% de la latencia** de una
   ejecución. **Que sean defectos reales aún no está demostrado con tests** [corregido §9.2, §9.3].
4. **El bucle de auditoría existe pero está acotado:** el 19% de las ejecuciones hizo ≥2 rondas y el
   máximo fue 3. Unos 13 de los 84 BLOCKING son repeticiones de un hallazgo ya hecho.
5. **El guard genera reintentos falsos:** 26 rechazos por «escritura fuera del worktree» en todo el
   corpus, y **en ninguno de los 26** hay una escritura del worker en su transcripción. Las causas
   son la actividad concurrente del dueño o de otras sesiones: planes de *otros* changes, commits en
   el checkout principal, `flow.config.json` y hooks de beads [corregido §9.4].
6. **Los artefactos no son voluminosos (mediana 6,2 KB por change).** Los workers casi no los leen
   (`proposal.md` en el 2% de sus sesiones), pero sí las sesiones de planificación (31 de 47) y las
   del dueño. Que los workers no los lean no prueba que no valgan [corregido §9.6].
7. **El mayor coste no medible en tokens es el mantenimiento:** 196 commits en octubre, unos 3 `fix`
   por cada `feat`, 32k líneas de tests modificadas y 22 commits de «review round» sobre el propio
   REIN.
8. **Lo que sobra con evidencia directa:** `rein-role` (0 usos) y el cableado de Serena (0 de 657
   workers). El resto (`proposal.md`/`specs/`, `ship` para planes de una tarea, `quick`) depende del
   piloto [corregido §9.6].
9. **El siguiente paso no es otra metodología:** es planificar en proporción a la incertidumbre y
   verificar en proporción al riesgo del diff real, y convertir los hallazgos recurrentes de la
   auditoría en gates deterministas.
10. **Nadie ha medido todavía si REIN supera a Claude Code o Codex usados directamente.**
    [PLAN-PILOTO.md](PLAN-PILOTO.md) define el experimento que lo decide, con el modelo implementador
    fijo.

---

## 1. Diagnóstico

### 1.1 Dónde se van los tokens: 30 días, 1.139 sesiones de Claude Code [H]

Fuente: `bench/plan_sessions.py`. Tokens = contexto releído + salida por turno, la misma definición
que el ledger de REIN. **No incluye** Codex (~400 M en workers según el estudio del 26-oct) ni las
transcripciones de subagentes.

| Tipo de sesión | Sesiones | Tokens | Share | Mediana por sesión | Contexto máx. (mediana) |
|---|---:|---:|---:|---:|---:|
| Interactiva sin REIN | 336 | 49,9 B | **67%** | 8,6 M (p75 96 M) | 144k |
| Con `/rein-discover` o `/rein-plan` | 47 | 19,8 B | **27%** | 305 M | 600k |
| Worker de `rein ship` | 752 | 2,95 B | **4,1%** | 1,9 M | 96k |
| Lanzador de `/quick` | 2 | 0,06 B | 0,1% | 31 M | 176k |

Dentro de las 47 sesiones de planificación (`bench/plan_window.py`, `bench/post_ship.py`):

| Tramo | Medida |
|---|---|
| Contexto ya acumulado al invocar `/rein-plan` | mediana 186k (p75 299k) |
| `/rein-plan` → plan escrito (`rein tasks`) | 38 turnos · **8,7 M tokens** · 10 min · 2 mensajes del dueño (n=45) |
| Tras `/ship` en la misma sesión (21 de 47) | mediana **716 turnos** a 252k de contexto por turno; **6,5 B tokens** en total, 2,2× el volumen de los workers. **En coste equivalente API son $1,85k: 1,4× los workers** [corregido §9.1] |
| De esos turnos, los que dispara una notificación en segundo plano | ~$1,2k/mes en equivalente API. Al menos la mitad de las notificaciones con resumen son de `rein watch`; el resto, esperas de PR o de otros procesos [corregido §9.1] |

**Lectura.** El coste dominante no es el SDD sino **la higiene de contexto**. Una sesión Opus que
descubre, planifica, lanza, vigila y sigue con otras cosas paga 250–600k de relectura en cada turno.
El propio `rein-plan` dice en su paso 9 «End the session», y en 21 de 47 sesiones no se hizo. El
mismo patrón domina las sesiones nativas (p75 de 96 M por sesión): no es culpa de REIN, pero es la
mayor palanca de todas.

### 1.2 Qué hace realmente `rein ship`: 83 ejecuciones [H]

Fuente: `analyze_states.py` del estudio anterior, re-ejecutado, más `bench/runs2.py`.

| Métrica | Mediana | p75 | Máx. |
|---|---:|---:|---:|
| Tareas del plan | 3 | 4 | 10 |
| Archivos cambiados | 21 | 31 | 73 |
| Dispatches por ejecución | 6 | 9 | 19 |
| Dispatches por tarea del plan | 2,0 | 3,0 | 11 |
| Tokens de Claude por ejecución (ledger) | 23,2 M | 44,6 M | 124 M |
| Wall-clock por ejecución (ledger) | 45 min | 102 min | 794 min |
| Decisiones del dueño (gates) | 0 | 2 | 11 |
| Tamaño del directorio del plan | 6,2 KB, 2 archivos | 8,6 KB | 28,6 KB |

- **`ship` no se usa para tareas triviales:** solo 5 de 83 ejecuciones tocaron ≤5 archivos. Las
  tareas pequeñas van a sesiones nativas o, rara vez, a `/quick` (13 sesiones en 30 días, frente a
  43 de `/rein-plan`).
- **El 47% de las ejecuciones tenía 1 o 2 tareas.** En un plan de una tarea, la descomposición no
  aporta nada: el plan es un brief con otro nombre.
- **Primer intento:** el 66% de las tareas del plan pasa al primer dispatch, y 20 tareas llegaron a 5.
- **Causas de reintento** en tareas del plan: tests rojos 48, **guard «fuera del worktree» 23**,
  setup/entorno 17, hooks 5, otras 8. Los rechazos del guard por fecha de inicio: 2 el 21-oct, 10 el
  27-oct y 14 el 28-oct. Las muestras nombran `openspec/changes/<c>/tasks.md`, `proposal.md` y
  commits nuevos en el checkout principal del repo; el propio mensaje dice «if you changed it
  yourself meanwhile, retry». **[I]** Es actividad del dueño o de otra sesión sobre los artefactos del
  plan, no del worker. Los commits `47bbbc1`…`229cf04` («review 6–6d») tocaron justo esa parte del
  guard.

### 1.3 La auditoría: el componente con más valor demostrado [H]

- **Volumen:** en las tareas de fix hay 84 BLOCKING, 71 IMPORTANT y 56 SUGGESTION. 51 de 83
  ejecuciones (61%) tuvieron al menos un fix de auditoría, 16 (19%) hicieron ≥2 rondas y el máximo
  fue 3.
- **No depende del tamaño:** los planes de una tarea (mediana de 7 archivos) necesitaron fix en el
  64% de los casos, y los de 4+ tareas en el 77%.
- **Leídos los 84 BLOCKING uno a uno,** todos son defectos con `archivo:línea` y un escenario que
  falla. Ninguno es de estilo. Por tipos:

  | Tipo | Proporción |
  |---|---|
  | Contrato roto entre repos (el cliente envía `{message}` y la API exige otro esquema; un campo eliminado que otro repo consume; cliente OpenAPI sin regenerar) | ~1 de cada 8 |
  | Concurrencia o estado obsoleto (dos POST simultáneos, paginación que sobrevive al cambio de tenant, una respuesta de polling antigua que pisa a la nueva) | ~1 de cada 10 |
  | Lógica de dominio: fiscal/SUNAT, notas de crédito, estados de pedidos, precios que se saltan promociones, paginación que solo lee la primera página | La mayoría |
  | Repetición de un hallazgo ya hecho («still partially unfixed») o duplicado exacto | ~13 de 84 |

- **Coste:** auditoría más fixes son el 36% de los tokens de Claude en workers (estudio del 26-oct),
  es decir, ~1,5% del total. **En tokens es un seguro barato, y tampoco cuesta mucha latencia:**
  la auditoría y los fixes son el ~5% del wall-clock de una ejecución (§9.3). La afirmación
  anterior, «lo que cuesta es la latencia», era falsa [corregido §9.3].

**[I]** Los implementadores son sesiones Sonnet y Codex equivalentes a usarlas «en nativo». Sin la
auditoría independiente, esos 84 defectos habrían llegado al PR, salvo que los cazara una revisión
humana. Esto es lo que más pesa a favor de REIN y lo que el experimento B de §6 debe confirmar o
refutar.

### 1.4 Artefactos: poco volumen, poca lectura [H]

- **Composición del directorio de un change** (82 encontrados): `tasks.md` en 82, `proposal.md` en
  68, `specs/` en 30, `notes-overnight.md` en 12 y `design.md` en 6.
- **Lectura por los workers** (752 sesiones): `tasks.md` en el 13%, `design.md` en el 2,8%,
  `proposal.md` en el 2%, `specs/` del change en el 1,5% y `openspec/specs` durable en el 1,2%. El
  brief de `ship` ya lleva el *Why*, las decisiones y los criterios, así que los workers no
  necesitan estos documentos.
- **En las sesiones de planificación,** `tasks.md` se toca 1.163 veces en 43 sesiones (unas 27 por
  sesión): el plan se reescribe mucho antes de confirmarse.
- **[I]** El problema no es la cantidad de documentos, sino que viven en el checkout principal y se
  editan mientras corren workers (§1.2). **[corregido §9.6]** Sí tienen lector: las sesiones de
  planificación (31 de 47 leen `proposal.md`) y las del dueño (29 leen `openspec/specs`).

### 1.5 Coste de mantenimiento [H]

- **Historial:** 448 commits desde el 27-jul y 196 en octubre.
- **Tipos en octubre:**
  - unos 116 `fix` frente a 37 `feat`;
  - 22 commits de «review round»;
  - 32.190 líneas de tests añadidas o borradas.
- **Archivos con más churn:**

  | Archivo | Líneas cambiadas | Commits `fix` |
  |---|---:|---:|
  | `ship.py` | 3.378 | — |
  | `guard.py` | 1.670 | 16 |
  | `ship_decide.py` | 1.637 | — |
  | `quick_driver.py` | 1.295 | — |
  | `ship_done.py` | 1.154 | — |

- **Tamaño actual:** 14.643 LOC en `lib/`.
- **Superficie de configuración:** 15 claves en `ship` y unas 10 en `gate`.
- **Lo ya eliminado (bien hecho):** Linear, dashboard, `setup`, `rein-step(s)`, `rein-apply` y
  `loop.js`.

**[I]** El coste más grande de REIN no está en ningún ledger: es el tiempo del dueño manteniéndolo.
Cada nuevo caso del guard o del driver trae otra «review round». Es el mismo bucle
auditoría → fix → re-auditoría del que se queja el prompt, aplicado al propio REIN.

### 1.6 Uso real de cada pieza (30 días) [H]

| Pieza | Invocaciones / sesiones | Veredicto de uso |
|---|---|---|
| `/rein-plan` | 76 / 43 | Núcleo |
| `/rein-discover` | 38 / 24 | Usado; es solo un prompt |
| `/ship` | 29 / 21 | Núcleo |
| `rein-audit` | 20 / 20 (casi siempre dentro de `ship`) | Núcleo |
| `/quick` | 17 / 13 | Poco uso para 1,6k LOC (`quick*.py`) |
| `/answer` | 3 / 3 | Marginal |
| `rein-role` | **0** | Muerto |
| Serena | 0 de 657 workers (estudio anterior); sigue con 19 referencias en `detect.py` | Muerto |
| CodeGraph / Graphify | 11 y 5 de 657 workers | Solo planner |

---

## 2. Investigación: qué está superando al SDD

Revisión de fuentes hecha el 2026-10-28 por un subagente que abrió cada URL; lo que no pudo
verificar está al final. Tipos de evidencia: **CS** estudio controlado · **BM** benchmark · **OD**
datos o código reproducibles · **VC** afirmación de un vendor · **OP** opinión o experiencia.

### 2.1 SDD: lo que dicen quienes lo probaron

- **Böckeler, Thoughtworks** (2025-10-15) [OP, prueba práctica estructurada]. Comparó Kiro, Spec Kit
  y Tessl.
  - En Kiro, un bug pequeño acabó en 4 historias y 16 criterios.
  - Spec Kit le resultó «overkill» para una feature de 3–5 puntos.
  - Prefiere revisar código antes que «all these markdown files».
  - https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- **Zaninotto, Marmelab, «The Waterfall Strikes Back»** (2025-11-12) [OP].
  - Spec Kit generó 8 archivos y 1.300 líneas para mostrar una fecha.
  - El agente marcó «verify implementation» sin escribir un solo test.
  - https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html
- **Thoughtworks Radar:**
  - SDD en *Assess* (nov-2025): «elaborate and opinionated», «we may be relearning a bitter
    lesson». En la edición actual ya no aparece.
  - Spec Kit en *Assess* (abr-2026): markdown «overly verbose».
  - https://www.thoughtworks.com/radar/techniques/spec-driven-development
- **Los propios vendors están quitando ceremonia** [VC]:
  - Kiro lanzó «Quick Plan», sin gates de aprobación (2026-05-12), y admite que el flujo paso a paso
    «can feel like more process than the problem requires».
  - En octubre de 2026, Kiro hizo que los workflows solo arranquen cuando se piden.
  - https://kiro.dev/blog/faster-smarter-specs/ · https://kiro.dev/changelog/
- **Estado de las herramientas:** Spec Kit sigue muy activo. OpenSpec se vende como la versión ligera
  de Spec Kit, pero sin datos.
- **No existe ningún estudio controlado** que muestre que el SDD mejora resultados.

### 2.2 Harness engineering: la tendencia con más respaldo

- **Anthropic, «Harness design for long-running apps»** (2026-03-24) [OP con costes].
  - El mismo encargo costó **20 min y $9 con un agente solo, frente a 6 h y $200 con el arnés
    completo** de planner, generador y evaluador.
  - Al mejorar los modelos, **quitaron** la descomposición en sprints, los resets de contexto y la
    evaluación por sprint: «each component encodes an assumption» que caduca.
  - Su evaluador encontraba problemas reales y luego **se convencía a sí mismo de aprobar**.
  - https://www.anthropic.com/engineering/harness-design-long-running-apps
- **Anthropic, «Effective harnesses for long-running agents»** (2025-11-26) [OP].
  - Una feature por sesión.
  - Lista de features en JSON en la que solo se toca `passes`.
  - Log de progreso, commits y tests E2E.
  - https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- **Carlini, Anthropic** (2026-02-05) [OP con datos]. Un compilador de C con 16 agentes.
  - «The task verifier is nearly perfect, otherwise Claude will solve the wrong problem».
  - https://www.anthropic.com/engineering/building-c-compiler
- **Böckeler, «Harness engineering»** (2026-04-02) [OP]. Resume el post de OpenAI, que dio 403: linters
  y tests estructurales propios, y «garbage collection» periódica de la deriva. Avisa de que verificar
  el comportamiento funcional sigue siendo «the elephant in the room».
  https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html
- **Thoughtworks Radar, Vol. 34** (abr-2026):

  | Blip | Posición |
  |---|---|
  | Feedback sensors for coding agents (gates deterministas *durante* la sesión) | **Trial** |
  | Agent instruction bloat | Caution |
  | MCP by default | Caution |
  | Coding throughput como métrica | Caution |

  https://www.thoughtworks.com/en-us/radar/techniques
- **ETH, «Evaluating AGENTS.md»** (2026-02, v3 2026-09) [BM].
  - Los archivos de contexto **no mejoran la tasa de éxito** y suben el coste **más de un 20%**.
  - Solo sirven para las prácticas no estándar del proyecto.
  - https://arxiv.org/abs/2602.11988

### 2.3 Multi-agente frente a un solo agente

- **Kim et al., Google** (2025-12 / 2026-01) [CS/BM, 180 configuraciones].
  - En tareas **secuenciales, todas las variantes multi-agente rinden un 39–70% peor**.
  - En tareas paralelizables, el multi-agente centralizado gana un +80,9%.
  - Los errores se amplifican 17,2× con agentes independientes y 4,4× con un coordinador.
  - El overhead crece con el número de herramientas.
  - https://arxiv.org/abs/2512.08296
- **Anthropic, sistema de investigación multi-agente** (2025-06) [VC con evaluación interna]: unos
  15× tokens frente a un chat, y «most coding tasks involve fewer truly parallelizable tasks than
  research». https://www.anthropic.com/engineering/multi-agent-research-system
- **MAST** (2025) [OD, 1.600+ trazas]: 14 modos de fallo. Arreglar la verificación de alto nivel dio
  un +15,6%. https://arxiv.org/abs/2503.13657
- **mini-swe-agent** [BM/OD]: unas 100 líneas, solo bash, y >74% en SWE-bench Verified. Un arnés
  simple llega a la frontera. https://github.com/SWE-agent/mini-swe-agent
- **Cognition, «Don't build multi-agents»** [OP]: por defecto, un solo agente lineal.
- **Claude Code, agent teams** [docs]: para tareas secuenciales es mejor una sola sesión o
  subagentes.

### 2.4 Bucles de revisión con LLM

- **Sin señal externa, autocorregirse no funciona:**
  - Huang et al. (ICLR 2024) [CS]: sin feedback externo, la autocorrección **empeora** el
    razonamiento.
  - Olausson et al. (ICLR 2024) [CS]: contando el coste, el self-repair rinde poco; lo que decide es
    la calidad del feedback.
- **Jin & Chen** (2025–2026) [BM]: los LLM marcan como incorrecto código que es correcto, y **pedir
  explicación y arreglo aumenta los falsos positivos**. Es justo el mecanismo del churn
  auditoría → fix → re-auditoría. https://arxiv.org/abs/2508.12358
- **Shukla et al.** (2025) [CS, pequeño]: 5 iteraciones de «mejora» por IA dieron un +37,6% de
  vulnerabilidades críticas. https://arxiv.org/abs/2506.11022
- **Claude Code best practices** [docs]:
  - «A reviewer that's asked to find problems will always find *something*».
  - Pedir que se ignore el estilo y se revise contra la spec.
  - Tras dos correcciones fallidas, `/clear`, no una tercera.
  - https://code.claude.com/docs/en/best-practices
- **Cherny** [OP, vía InfoQ]: un bucle de verificación mejora la calidad «2–3x». Es su estimación, no
  una medida.

**Contraste con REIN [H].** Los 84 BLOCKING de §1.3 **no** son el «always find something»: son
defectos concretos con un escenario que falla. REIN ya aplica lo que recomienda la literatura (solo 4
clases de BLOCKING; IMPORTANT y SUGGESTION no cuestan ronda; un máximo de 2 rondas). Lo que falta es
cortar las repeticiones (P0-5).

### 2.5 Contexto y caché

- **Anthropic, «Effective context engineering»** (2025-09) [VC/OP]: el mínimo de tokens de alta señal,
  la «context rot», y subagentes que devuelvan resúmenes de 1–2k tokens.
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- **Claude Code** [docs]:
  - los subagentes usan una caché de 5 min y la conversación principal una de 1 h;
  - un subagente nuevo no lee la caché del padre y un fork sí;
  - cambiar de modelo a mitad de sesión invalida la caché.

  https://code.claude.com/docs/en/prompt-caching.md

### 2.6 Productividad

- **METR, RCT** (2025-07) [CS]: los desarrolladores expertos fueron **un 19% más lentos** con IA, y
  creían haber sido un 20% más rápidos.
- **Seguimiento de METR** (2026-02): sus propios autores lo califican de «unreliable signal».
  https://metr.org/blog/2026-02-24-uplift-update/
- **DORA 2025** [encuesta]: la IA es «mirror and multiplier» de lo que el equipo ya hace bien o mal.
- **Faros** [VC]: unas 2× más cambios, con las métricas de empresa planas.
- **[I] Para REIN:** la productividad que se percibe no es una medida. Por eso el §6 mide el
  tiempo hasta una solución *aceptada* y los defectos que escapan.

### 2.7 Lo que ya traen Claude Code y Codex [docs]

| Etapa de REIN | Equivalente nativo (oct-2026) |
|---|---|
| Discover y plan | Plan mode, el patrón de entrevista «interview me… then write SPEC.md», `/goal` |
| Un worker nuevo por tarea | Subagentes, Workflows (`agent()`/`pipeline()`, reanudables), `/batch` (de 5 a 30 subagentes en worktrees), `claude -p --bare --max-turns --max-budget-usd` |
| Gates deterministas | Hooks `Stop` y `PostToolUse` (el `Stop` deja de bloquear tras 8 veces seguidas), hooks de Codex |
| Auditoría independiente | `/code-review` en contexto nuevo con `--fix`; `/review` de Codex |
| Rondas de fix | `--fix` y la regla de Workflows de parar si dos rondas seguidas no avanzan |
| Worktrees | Nativos en Claude Code y en Orca |

**Lo que no traen:**

- routing entre suscripciones por cuota;
- un auditor de **otro proveedor**;
- un veredicto ligado al hash del estado;
- un driver determinista, reanudable y multi-repo.

### 2.8 Veredicto de evidencia

| Enfoque | Evidencia | Qué muestra | ¿Solo etiqueta? |
|---|---|---|---|
| SDD con ceremonia completa | Débil (solo OP) | Pesado para tareas pequeñas; los vendors añaden modos ligeros | No, pero anecdótico |
| Spec breve o criterios de aceptación por tarea | Media | Una definición de «hecho» verificable ayuda; lo que importa es el tamaño | No |
| Harness engineering / sensores deterministas | **Media-fuerte** | El feedback externo gana a la autocrítica (ICLR); todos los practicantes convergen | Es en parte otra etiqueta para CI más linters dentro del bucle |
| AGENTS.md como enciclopedia | Media, negativa | +20% de coste sin mejora | — |
| Multi-agente en coding secuencial | **Fuerte, negativa** | −39 a −70% | — |
| Arnés simple (bash, lineal) | Fuerte | Puntuación de frontera en SWE-bench | No |
| Bucles LLM de review → fix | Media, negativa | Sesgo de sobrecorrección; los evaluadores acaban aprobando | — |
| Ralph loop | Débil | Una anécdota, solo greenfield, caro | Casi |
| «Loop engineering» | Ninguna | Un ensayo sin datos | **Sí** |
| «Intent-driven» / «adaptive agentic workflows» | Ninguna | Uso en blogs y vendors | **Sí** |
| Gas Town / swarms | Ninguna en resultados | Thoughtworks lo pone en Caution | Casi |

**No se pudo verificar:**

- el post de OpenAI «Harness engineering», que dio 403;
- el tuit original de Cherny;
- las cifras primarias de Faros y GitClear;
- el estado actual de Gas Town;
- la atribución de «intent-driven» a Anthropic.

---

## 3. Comparativa

| Enfoque | Ventaja | Riesgo | Evidencia externa | Evidencia en REIN |
|---|---|---|---|---|
| **SDD completo** (discover → plan escrito → tareas → ship → audit) para todo | Intención explícita, tareas verificables, trazabilidad | Ceremonia en lo pequeño; artefactos que no se leen; latencia | Débil (OP) | Planes de una tarea con toda la maquinaria; `proposal.md`/`specs/` leídos por el 1,5–2% de los workers |
| **Nativo, un agente** (plan mode, implementar, sus tests, `/review`) | Mínimo overhead, caché caliente, sin traspasos | Se revisa a sí mismo (mismo modelo y contexto); depende de la disciplina de contexto | Fuerte a favor de un solo agente en tareas secuenciales; negativa sobre la autocorrección | 67% de los tokens ya va aquí, sin datos de calidad |
| **Harness engineering** (gates deterministas en el bucle, convertir los fallos en checks) | Señal externa barata y repetible, sin bucles LLM | Solo verifica lo que alguien ha codificado; los tests generados por IA son débiles | Media-fuerte | REIN ya lo tiene (gate por tarea más suite por repo); falta el gate de contrato |
| **Revisión independiente con otro modelo** | Caza lo que tests y tipos no ven (contratos, carreras, dominio) | Bucles, sobrecorrección, latencia | Mixta: útil con feedback externo, mala como autocrítica | **Fuerte a favor:** 84 BLOCKING reales en 83 ejecuciones |
| **Multi-agente o swarm** | Paralelo real en tareas independientes | Amplifica errores; unas 15× tokens; overhead de coordinación | Fuerte en contra para coding secuencial | REIN usa workers secuenciales por dependencia, no un swarm; está bien |
| **Adaptativo por riesgo** (carriles §4) | Ceremonia proporcional | Clasificar mal; más ramas en el código | Indirecta (Kiro Quick Plan, Anthropic quitando piezas del arnés) | `quick` ya audita por `risky` y `ship` ya enruta a Opus por riesgo: es generalizar lo que existe |

---

## 4. Arquitectura TO-BE: REIN como arnés de verificación, no como framework de planificación

**Principio.** Planificar en proporción a la **incertidumbre**, verificar en proporción al
**riesgo del diff real** y escalar solo con evidencia. Un LLM escribe, otro distinto revisa y las
máquinas verifican.

### 4.1 Cuatro carriles sobre las piezas que ya existen

| Carril | Cuándo | Qué se ejecuta | Artefactos | Auditoría | Rondas máx. |
|---|---|---|---|---|---|
| **L0 Directo** | Un repo, intención de una frase, sin rutas de riesgo | Sesión nativa (Claude o Codex) en un worktree, más `rein gate` | Ninguno | Solo si el diff toca `riskyPaths` | 0–1 |
| **L1 Estándar** (por defecto) | Un repo; lo que hoy sería un plan de 1–2 tareas (47% de las ejecuciones) | Plan nativo efímero (plan mode, no se escribe en el repo) → un implementador → `rein gate` → auditoría independiente | La descripción del PR | Siempre, con otro proveedor | 1 |
| **L2 Complejo** | Multi-repo (18 de 83), ≥3 tareas independientes, o ~25+ archivos | `rein-plan` → `rein ship`, como hoy | Solo `tasks.md`, archivado al cerrar | Siempre, más el gate de contrato entre repos | 2 |
| **L3 Crítico** | `riskyPaths`: auth, pagos, facturación/SUNAT, migraciones, borrado de datos | L1 o L2, más invariantes escritas **como tests antes** de implementar | Tests de invariantes, que son la spec ejecutable | Opus, más revisión humana obligatoria | 2, luego el humano |

**Selección automática, determinista y en dos momentos:**

1. **Antes de empezar,** con señales baratas que el dueño o el planner ya conocen: número de repos
   implicados, si la petición nombra un dominio de riesgo y si cabe en una frase verificable. Ningún
   LLM clasifica.
2. **Después del diff,** el carril de **verificación** se recalcula con el diff real: archivos,
   `riskyPaths`, archivos de contrato (OpenAPI, esquemas, migraciones). **Escalar es automático y
   bajar de carril no lo es.** Hoy `quick` ya audita solo si `risky`, y `ship` ya enruta a Opus por
   `riskyPaths` y `largeChangeFiles`: es generalizar lo que existe.

**Bajar de carril:** un plan de una sola tarea en un solo repo no pasa por `ship`, sino por L1.
**Subir:** un L0/L1 cuyo diff toca rutas de riesgo o contratos pasa a auditoría L3.

### 4.2 Control de bucles, coste e iteraciones

| Mecanismo | Regla | Evidencia |
|---|---|---|
| Firma de hallazgo | Un BLOCKING que reaparece sobre el mismo `archivo:símbolo` tras un fix va **al humano**, no a otra ronda. Es lo mismo que `80b1732` ya hace con los fallos de gate | Unos 13 de 84 BLOCKING son repeticiones |
| Rondas por carril | L1: 1 · L2/L3: 2 y luego el humano. IMPORTANT y SUGGESTION nunca abren ronda (ya es así) | 19% de las ejecuciones con ≥2 rondas |
| Hallazgo recurrente → gate determinista | Una clase de BLOCKING que se repite en ≥3 ejecuciones se convierte en un check mecánico. El primero es el **gate de contrato entre repos**: regenerar el cliente OpenAPI y hacer el typecheck del consumidor | ~1 de cada 8 BLOCKING es un contrato roto |
| Presupuesto por ejecución | `ship` abre un gate al dueño al superar N tokens o N minutos, por ejemplo 2× la mediana de su carril | p75 de 44,6 M y máximo de 124 M sin tope |
| Supervisar fuera de la sesión cara | `/ship` cierra la sesión de planificación. `rein watch` corre en una sesión nueva barata (Haiku o Sonnet), o el dueño usa la UI de Orca | 6,5 B tokens tras `/ship` en sesiones de planificación |
| Artefactos fuera del checkout vivo | El plan se escribe en el worktree del change o en `.rein/`, nunca en el checkout que el guard vigila | 24 rechazos del guard el 27–28-oct |

### 4.3 Contexto y documentación

- **Documentación durable:** solo `AGENTS.md`/`CLAUDE.md` (convenciones y trampas del entorno,
  revisadas por el dueño), los tests y la descripción del PR. `proposal.md` y `specs/` por change
  dejan de generarse por defecto: los lee el 1,5–2% de los workers.
- **El plan es efímero en L0 y L1.** En L2 solo existe `tasks.md`, porque `ship` lo necesita como
  contrato de ejecución.
- **Se mantiene del estudio anterior:** el perfil lean (ya implementado), el handoff `worker_done`
  con lo que se intentó y las trampas del entorno, y nada de grafo ni memoria compartida.

---

## 5. KEEP / SIMPLIFY / REMOVE / ADD

| Componente | Decisión | Por qué (evidencia) |
|---|---|---|
| Driver determinista (`ship_decide`, `ship_state`, Orca como runtime) | **KEEP** | Sin LLM en medio; 83 ejecuciones reanudables. Ventaja real frente a Gas Town y Agetor (estudio anterior) |
| Gates por tarea (`gate`, `affected`, `gate_cache`, `verify`) | **KEEP** | La verificación determinista es la palanca con más consenso (§2) |
| Auditoría independiente entre proveedores, ligada al hash del estado | **KEEP** | 84 BLOCKING reales y ~1,5% de los tokens |
| Routing por cuota y perfil lean | **KEEP** | Medido en el estudio anterior: el arranque baja de 53,6k a 19,0k |
| Triage de fallos y «mismo fallo → dueño» (`80b1732`) | **KEEP** | Ataca reintentos ciegos |
| `guard.py` | **SIMPLIFY** | 25 `fix` en octubre (la mayor densidad por kLOC) y 26 de 26 rechazos sin escritura del worker (§9.4). Atribuir con evidencia en modo sombra (QW2) y probar el sandbox nativo (P1-3) |
| `rein-plan` | **SIMPLIFY** | Solo para L2. Escribe solo `tasks.md`. Sin `openspec validate` cuando no hay `specs/` |
| `rein-discover` | **SIMPLIFY → fusionar en `rein-plan`** | Es un prompt; la mitad de su contenido ya está en el paso 2 de `rein-plan`. Una entrada menos |
| `quick` (1,6k LOC) | **SIMPLIFY → pasa a ser L1** | 13 sesiones al mes. L1 es `quick` con auditoría por riesgo y una ronda: unifica dos caminos en uno |
| `/answer` | **SIMPLIFY → dentro del skill `ship`** | 3 usos |
| Configuración (15 claves de `ship` y unas 10 de `gate`) | **SIMPLIFY** | Valores por defecto por carril. Documentar 5 claves |
| `detect.py` (1.089 LOC de autodetección) | **SIMPLIFY (congelar)** | Sin evidencia de fallo reciente. No crecer: un comando faltante se declara en `flow.config` |
| `rein-role` | **REMOVE** | 0 usos en 30 días |
| Cableado de Serena | **REMOVE** | 0 de 657 workers; D2 ya lo dejó «UNMEASURED» |
| `proposal.md` y `specs/` por change | **SIN DECISIÓN** [corregido §9.6] | Los lee el planner y no los workers; cuestan unos 2,3 KB. No se prueba en el piloto |
| `ship` para planes de una tarea | **Depende del piloto (H-orq)** | 11 ejecuciones de una tarea con toda la maquinaria de `ship` |
| Supervisar desde la sesión de planificación | **REMOVE (hábito más guardarraíl)** | 6,5 B tokens en 30 días |
| Selector de carril (texto del skill, más unas ~100 LOC para el recálculo tras el diff) | **ADD** | Generaliza `risky` y `largeChangeFiles`, que ya existen |
| Firma de hallazgo de auditoría | **ADD (~60 LOC)** | ~13 de 84 BLOCKING repetidos |
| Gate de contrato entre repos | **ADD (medir antes)** | ~1 de cada 8 BLOCKING |
| Presupuesto por ejecución | **ADD (~40 LOC)** | Colas sin tope |
| `rein bench ab` (reutiliza el ledger y el estado) | **ADD** | Sin él, nada de esto se puede decidir con datos |

**No se añade:** ni otro agente, ni un orquestador LLM, ni memoria, ni grafo, ni MCP, ni otro
formato de spec.

---

## 6. Plan de experimentación A/B

> **[corregido §9.7] Este diseño queda sustituido por [PLAN-PILOTO.md](PLAN-PILOTO.md).** Tenía tres
> fallos:
>
> - comparaba REIN (Sonnet/Codex) con Claude nativo (Opus): el modelo y el workflow quedaban
>   confundidos;
> - no separaba qué aporta el plan, los gates, la auditoría y la orquestación;
> - elegía tareas con BLOCKING, lo que favorece a los brazos con auditoría.
>
> El texto original se conserva abajo como registro.

### 6.1 Idea central: repetir el historial contra una verdad que ya existe

REIN ya tiene lo más caro de un benchmark: **84 defectos reales**, cada uno con su `archivo:línea`
y un escenario que falla, encontrados en 83 changes cuyo commit base se conoce. Basta con
repetirlos.

**Montaje:**

1. **Elegir tareas:** 12 changes cerrados, estratificados.

   | Estrato | Criterio | Tareas |
   |---|---|---:|
   | S | 1 tarea, ≤10 archivos | 3 |
   | M | 2–3 tareas, un repo | 3 |
   | L | Multi-repo o 4+ tareas | 3 |
   | C | Rutas de riesgo: facturación, pagos o auth | 3 |

   Se prioriza que cada una tenga al menos un BLOCKING conocido.
2. **Construir una suite oculta por tarea,** congelada antes de correr nada:
   - la `Verification` original de cada tarea;
   - **un test de regresión por cada BLOCKING conocido** de ese change, escrito y comprobado contra
     el commit final aprobado (debe pasar) y contra el commit previo al fix (debe fallar);
   - la suite completa del repo para detectar regresiones.
3. **Dar a cada brazo la misma entrada:** el commit base y la **intención** del change (*Why* y
   criterios de aceptación). Al brazo B no se le da la descomposición en tareas.

### 6.2 Brazos

| Brazo | Qué corre | Modelos |
|---|---|---|
| **A**: REIN actual | `rein-discover` → `rein-plan` → `rein ship` → audit, con la configuración de hoy | Plan con Opus 5.5; implementación con Sonnet 5.5 o Codex según la cuota; auditor según la regla actual |
| **B1**: Claude Code nativo | Una sesión en un worktree: plan mode → implementar → el agente corre los tests del repo → `/review` nativo. Sin REIN | Opus 5.5 para todo |
| **B2**: Codex nativo | Igual que B1 con `codex`, incluido su propio review | gpt-6.1-sol |
| **C**: REIN adaptativo | Selector de carril (§4.1): L0/L1 sin plan escrito, L2 con `ship`, más la firma de hallazgo, el presupuesto y la supervisión fuera de la sesión | Los mismos roles que A |

**Controles:**

- el mismo commit base y la misma intención;
- sesiones nuevas;
- perfil lean en todos los implementadores Claude;
- **el mismo modelo implementador en A y C** (fijar `--agent claude --model sonnet` para aislar el
  workflow de la cuota);
- orden de ejecución aleatorio con semilla y 2 repeticiones por celda;
- el dueño responde a los gates con un guion escrito antes (mismas respuestas en todos los brazos);
- nadie ve la suite oculta.

### 6.3 Métricas y cómo se obtienen

| Métrica | Fuente | ¿Medible hoy? |
|---|---|---|
| Tests ocultos superados y **defectos conocidos que escapan** | Ejecutar la suite oculta sobre el resultado final de cada brazo | Sí |
| Regresiones | Suite completa del repo | Sí |
| Tiempo hasta una solución aceptada (wall-clock) | Marcas de tiempo de las transcripciones y del estado | Sí |
| Tokens por modelo (Claude) | `token_report` / ledger | Sí |
| Tokens de Codex | Transcripciones de `~/.codex/sessions` (el ledger no las lee; P1-3 del estudio anterior) | Con un script |
| Llamadas a modelos, número de agentes y sesiones | Transcripciones | Sí |
| Iteraciones: dispatches, rondas de auditoría y reintentos | `.rein/ship/*.json` y transcripciones en B | Sí |
| Intervenciones humanas | Gates respondidos más mensajes del dueño | Sí (el número; no la atención) |
| Complejidad del diff | LOC y archivos cambiados, más la complejidad ciclomática del código añadido (`lizard`, que soporta Python y TS) | Sí |
| Mantenibilidad a largo plazo | — | **No.** Se declara como no medida |
| Coste en % de cuota de suscripción | Orca da el % antes y después, pero no se puede atribuir con otras sesiones abiertas | **Parcial** |

### 6.4 Criterios definidos antes de correr

- **C sustituye a A** si se cumplen las tres condiciones:
  - escapes de defectos conocidos de C ≤ los de A, por estrato;
  - tests ocultos de C ≥ A − 5 pp;
  - y además tokens ≤ 0,6× A **o** wall-clock ≤ 0,6× A, en la mediana de las 12 tareas.
- **REIN se reduce a un plugin de gate más auditoría sobre Claude Code nativo** si B1 o B2 iguala a
  A en escapes en los estratos M, L y C con menos coste.
- **La auditoría se queda aunque cueste latencia** si B (con el `/review` nativo) deja escapar más
  defectos conocidos que A o C en M, L o C.
- **Resultado inconcluso:** con 12 tareas y 2 repeticiones, una diferencia de ≤1 defecto por estrato
  es ruido. Se declara así, sin forzar conclusión.

### 6.5 Coste y fases

- **Piloto:** 6 tareas (S, M y C × 2) × 4 brazos × 1 repetición = 24 ejecuciones. Con la mediana
  actual de 23 M tokens por ejecución de `ship`, es del orden de 0,5–0,8 B tokens [I], menos que una
  semana de sesiones de planificación (§1.1).
- **Completo:** solo si el piloto separa los brazos.
- **Riesgo:** escribir los ~20 tests de regresión de los BLOCKING es trabajo humano real, de unas
  horas. Sin ellos, el experimento solo mide coste y no calidad.

### 6.6 Experimento barato que se puede correr ya, sin modelo

Repetir `bench/post_ship.py` y `bench/runs2.py` dos semanas después de aplicar P0-1 y P0-2, para
comparar los tokens tras `/ship`, los rechazos del guard y los dispatches por tarea. Es
observacional, no causal.

---

## 7. Roadmap

> **[corregido §9.8]** Antes de tocar producción va el piloto
> ([PLAN-PILOTO.md](PLAN-PILOTO.md)), más los quick wins en modo sombra (§8 de ese plan). La tabla de
> abajo es la hipótesis que el piloto debe confirmar.
>
> - **P0-2** (sacar los planes del checkout) pasa a ser **QW2**: atribuir con evidencia, en modo
>   sombra.
> - **P0-3** queda reducido a `rein-role` y Serena.
> - **P0-4** depende de H-orq.

Impacto, riesgo y esfuerzo estimados. El esfuerzo va en LOC con sus tests. Todo lo de P0 **quita**
complejidad o es de muy bajo riesgo.

### P0: evidencia fuerte, esfuerzo bajo

| # | Cambio | Evidencia | Esfuerzo | Cómo se mide |
|---|---|---|---|---|
| P0-1 | **No supervisar desde la sesión de planificación.** `/ship` dice «cierra esta sesión» y lanza `rein watch` en una sesión nueva de Haiku o Sonnet, o deja la supervisión en la UI de Orca | 6,5 B tokens tras `/ship` en 30 días, 2,2× todos los workers | Texto del skill más ~20 LOC | `bench/post_ship.py` |
| P0-2 | **Sacar los artefactos del plan del checkout vigilado por el guard**, o excluir del guard la ruta del plan de ese change | 24 rechazos del guard el 27–28-oct sobre `tasks.md`/`proposal.md` y commits del checkout | ~30 LOC | Causas de reintento en `analyze_states.py` |
| P0-3 | **Borrar** `rein-role`, el cableado de Serena y la generación por defecto de `proposal.md` y `specs/` | 0 usos; lecturas del 1,5–2% | Negativo (resta LOC) | — |
| P0-4 | **Planes de una tarea → L1** (implementador y auditoría, sin la maquinaria de `ship`) | 11 de 83 ejecuciones. La descomposición no aporta en una tarea | Mayormente texto del skill `rein-plan` | Wall-clock y dispatches de esas ejecuciones |
| P0-5 | **Firma de hallazgo de auditoría:** un BLOCKING repetido va al humano | ~13 de 84 BLOCKING repetidos; mismo patrón que `80b1732` | ~60 LOC | Rondas ≥2 (hoy 19%) |
| P0-6 | **Piloto A/B** (§6.5) | Sin él, ninguna de las decisiones grandes está justificada | Tests de regresión más un script | §6.4 |

### P1: evidencia media, o depende del piloto

| # | Cambio | Paso previo |
|---|---|---|
| P1-1 | Selector de carril completo (L0–L3) con recálculo tras el diff | El piloto muestra que C ≥ A en calidad |
| P1-2 | Gate de contrato entre repos: regenerar OpenAPI más el typecheck del consumidor | Contar cuántos de los ~10 BLOCKING de contrato habría cazado |
| P1-3 | **Probar el sandbox nativo** (Codex `workspace-write`, sandbox de Bash de Claude Code) como sustituto de la parte «fuera del worktree» de `guard.py` | Sesión de prueba: ¿bloquea las mismas escrituras sin falsos positivos? |
| P1-4 | Fusionar `rein-discover` en `rein-plan`, `/answer` en `ship` y `quick` en L1 | P1-1 |
| P1-5 | Presupuesto de tokens y tiempo por ejecución, que abre un gate | Ninguno |
| P1-6 | Handoff `worker_done` (P0-3 del estudio anterior), solo para el triage y la auditoría | Ninguno |

### P2: simplificación de fondo y experimentos

| # | Cambio | Nota |
|---|---|---|
| P2-1 | Reducir la configuración a valores por defecto por carril y documentar 5 claves | Tras P1-1 |
| P2-2 | Congelar `detect.py`: los comandos que falten se declaran en lugar de autodetectarse | Sin evidencia de que hoy falle |
| P2-3 | Re-auditoría acotada: solo el diff del fix más los hallazgos originales | Experimento; hoy la re-auditoría relee un 50% de archivos (estudio anterior) |
| P2-4 | Higiene de contexto en las sesiones nativas, que son el 67% de los tokens: sesiones por tarea, `/clear` y Sonnet por defecto | No es REIN, pero es la palanca más grande de toda la cuenta |
| P2-5 | Presupuesto de complejidad para REIN: ningún `feat` que no retire LOC equivalentes o resuelva un problema medido | Contra el ratio de 3 `fix` por cada `feat` |

---

## 8. Respuestas

**¿Es REIN realmente mejor que usar Claude Code o Codex directamente?**

- **Calidad:** no se sabe, porque nadie lo ha medido de forma controlada.
- **Coste de ejecución:** es mejor. Los workers son el 4% de los tokens, con el perfil lean y Sonnet
  o Codex en lugar de Opus.
- **Cambios medianos y grandes, sobre todo multi-repo:** probablemente sí [I]. La auditoría
  independiente encontró unos 84 defectos reales que los implementadores, que son sesiones
  «nativas», y los gates habían dejado pasar.
- **Cambios pequeños:** casi seguro que no. Ahí `ship` es pura ceremonia.

El experimento B de §6 decide la parte de calidad.

**¿Qué partes de REIN son una ventaja competitiva real?**

1. La auditoría con **otro proveedor**, ligada al hash del estado y con solo 4 clases de BLOCKING.
2. El driver determinista sin LLM en medio, reanudable y multi-repo.
3. Los gates por tarea con los tests afectados.
4. El routing entre suscripciones por cuota.
5. El perfil lean de arranque.

Nada de esto lo ofrecen Claude Code ni Codex de forma nativa. Los skills de discover y plan, en
cambio, sí tienen equivalente nativo (plan mode y `/goal`).

**¿Qué eliminarías inmediatamente?**

- `rein-role`.
- El cableado de Serena.
- `proposal.md` y `specs/` por change.
- `ship` para planes de una tarea.
- Supervisar desde la sesión Opus de planificación.
- Los artefactos del plan dentro del checkout que vigila el guard.

Después, si el piloto lo respalda, fusionar `discover` en `plan` y `quick` en L1.

**¿Cuál es el siguiente paso evolutivo después del SDD para este proyecto?**

No es otra metodología con nombre: «loop engineering» e «intent-driven» no tienen evidencia. Lo que
sí la tiene es pasar de **especificar más** a **verificar mejor**:

- La spec, en carriles proporcionales al riesgo.
- La intención, en una frase más criterios verificables.
- Los invariantes críticos, como tests escritos antes de implementar.
- Cada clase de defecto que la auditoría encuentre dos o tres veces, convertida en un gate
  determinista. El primero, el de contrato entre repos.

Y quitar piezas del arnés a medida que los modelos dejen de necesitarlas, como hizo Anthropic.

**¿Cómo conseguimos máxima productividad sin sacrificar calidad?**

- **Roles:** un agente escribe, otro de otro proveedor revisa y las máquinas verifican.
- **Proporción:** se planifica según la incertidumbre y se audita según el diff real.
- **Bucles:** como máximo una ronda de fix en L1; un hallazgo repetido va al humano.
- **Contexto:** sesiones cortas, una por propósito, y nunca supervisar desde una de 600k.
- **Mantenimiento:** ningún `feat` en REIN sin un problema medido detrás, ni sin retirar código.
- **Medición:** decidir con el piloto A/B, no con la sensación de velocidad, que METR mostró que
  engaña.

---

## 9. Revisión crítica (2026-10-28): intentando refutar este informe

Scripts nuevos en `bench/`: `cost.py`, `watch_cost.py`, `latency.py`, `guard_check.py` +
`guard_attribution.py`, `fix_tests.py` y `candidates.py`. Todos son de solo lectura.

### 9.1 Volumen procesado frente a coste efectivo [H]

Precios oficiales de la API (skill `claude-api`, caché del 2026-10-06), en $/MTok:

| Modelo | Entrada | Salida | Lectura de caché |
|---|---:|---:|---:|
| Opus 5.5 | 4 | 20 | 0,20 |
| Sonnet 5.5 | 2 | 10 | 0,20 |

La escritura en caché cuesta 1,25× la entrada (TTL de 5 min) o 2× (TTL de 1 h). **Lo que pagas es la
suscripción.** El equivalente API es una medida comparable del peso relativo, no tu factura.

| Tipo de sesión (30 días) | Volumen | Lectura de caché (% del volumen) | Coste eq. API | Share del coste | % del coste en lectura / escritura / salida |
|---|---:|---:|---:|---:|---|
| Interactiva sin REIN | 49,9 B | 98,5% | $21.370 | 69,3% | 64 / 25 / 11 |
| Con discover/plan | 19,9 B | 98,7% | $8.135 | 26,4% | 67 / 23 / 10 |
| — de ella, tras `/ship` | 6,5 B | 99,1% | $1.853 | 6,0% | 70 / 19 / 11 |
| Workers de `ship` | 2,95 B | 95,2% | $1.327 | 4,3% | **42 / 42** / 16 |

**Lectura:**

- Los workers pagan proporcionalmente más por token, porque cada sesión nueva escribe su caché: un
  42% de su coste es escritura de caché.
- El tramo posterior a `/ship` pesa **1,4× los workers** en coste, no 2,2× como decía en volumen.
- **Atribución de ese tramo** (`watch_cost.py`): los episodios que dispara una notificación en
  segundo plano cuestan ~$1,2k y los que dispara un mensaje del dueño, ~$1,6k. Este reparto usa una
  detección de `/ship` algo más amplia que la de la tabla, así que los totales no cuadran exactamente.
  De las notificaciones con resumen, 1.484 son de `rein watch`, 929 de otras esperas en bash y 424
  de subagentes.
- **Conclusión:** supervisar desde la sesión cara cuesta del orden de $0,6–1,2k al mes en
  equivalente API, un 2–4% del total. **Es real, pero no es el problema principal.** El problema
  principal sigue siendo el 69% de las sesiones nativas largas.

### 9.2 ¿Son reales los 84 BLOCKING? Aún no está demostrado [H/I]

- **A favor** [H]:
  - los 84 citan `archivo:línea` y un escenario concreto;
  - **20 de los 21 diffs de fix limpios** (≤40 archivos, `fix_tests.py`) incluyen cambios en tests,
    es decir, el fixer escribió un test para el defecto.
- **En contra o sin probar:**
  - que el fix traiga un test **no** prueba que el defecto existiera: el test podría pasar también
    antes del fix;
  - la literatura documenta que los revisores LLM marcan como incorrecto código que es correcto
    (§2.4);
  - unos 13 son repeticiones.
- **Cómo demostrarlo:** ejecutar cada test de testigo en el commit anterior al fix (debe fallar) y
  en el posterior (debe pasar). Es la fase 0 del piloto. Hasta entonces, «84 defectos reales» se
  rebaja a **«84 hallazgos concretos, 20 de 21 acompañados de un test de regresión, sin validar»**.

### 9.3 Latencia por etapa: dónde se va el tiempo [H]

`latency.py` cruza los intervalos de 998 transcripciones de workers con los `.gates.jsonl`.
Sobre 41 changes con log de gates (wall-clock mediano de 49 min, p75 de 102), este es el share del
wall-clock total:

| Etapa | Share | Mediana por change | Naturaleza |
|---|---:|---:|---|
| Implementación (workers de tarea) | **59%** | 17 min | Trabajo útil |
| **Ocioso** (ningún worker ni gate activo: dispatch, arranque, diálogos, espera de gates del dueño) | **23%** | 8 min (p75 31) | **Overhead de coordinación** |
| Gates de tarea más gate de repo | 8% | 2 + 2 min | Verificación útil |
| Reintentos | 6% | 2 min | Mayormente overhead (§1.2) |
| Auditoría | 2% | 3,5 min | Verificación |
| Fix de auditoría | 2,5% | 1,6 min | Trabajo útil si el defecto es real |

**Fuera de `ship`:**

- `/rein-plan` hasta el plan escrito: mediana de 10 min (n=45).
- `/rein-discover` hasta `/rein-plan` en la misma sesión: mediana de **91 min** de wall-clock, con
  solo 3 mensajes del dueño (n=6, muestra débil). Es sobre todo tiempo del dueño fuera de la sesión.

**Esfuerzo humano:** 132 decisiones del dueño en 83 ejecuciones:

| Tipo de decisión | Número |
|---|---:|
| Preguntas | 48 |
| `retry_exhausted` | 40 |
| `audit_exhausted` | 11 |
| `timeout` | 8 |
| `setup` | 8 |
| `stalled` | 7 |
| Otras | 10 |

Las decisiones no llevan marca de tiempo, así que la espera humana no se puede separar dentro del
23% ocioso.

**Lo que esto refuta:**

- «La auditoría cuesta latencia»: falso, es ~5%.
- «El SDD alarga la ejecución»: tampoco. La ejecución es en un 59% implementación. La latencia
  evitable está en el ocio (23%) y los reintentos (6%).

### 9.4 El guard: 26 rechazos, 0 escrituras del worker [H]

**Método** (`guard_attribution.py`):

1. Para cada tarea cuyo último fallo es «wrote outside its worktree», se buscan las transcripciones
   de worker (marcador exacto «You are a Rein ship worker») del mismo change y tarea.
2. En ellas, se busca un Edit/Write sobre la ruta exterior o un `git commit/merge/checkout/…` dentro
   de ella.

**Resultado:**

- **0 de 26.** El único positivo de la heurística era un `git log` (lectura).
- **Lo que el guard vio:**
  - `tasks.md`/`proposal.md` de **otros** changes;
  - commits nuevos en el checkout principal;
  - `flow.config.json`;
  - `.beads/interactions.jsonl`.

  Todo coincide con las sesiones del dueño trabajando en paralelo, que corrían
  `rein accept-outside` y `rein answer` desde el checkout del plan root.
- **No se observó ningún positivo verdadero en el corpus.**
- **Límite:** un worker podría escribir fuera mediante una ruta relativa o un script sin la ruta
  literal. Por eso QW2 propone atribuir con evidencia **en modo sombra**, no apagar el check.

### 9.5 Mantenimiento por componente (octubre) [H]

| Componente | LOC hoy | Commits | `fix` | `feat` | «review» | `fix` por kLOC | Valor demostrado |
|---|---:|---:|---:|---:|---:|---:|---|
| Driver de `ship` (`ship*`, `orca_client`, `quota`, `startup_dialog`) | 5.155 | 105 | 69 | 31 | 13 | 13 | Orquestación, recuperación, multi-repo (18/83) y routing por cuota. Valor del multi-worker: **por medir (H-orq)** |
| `quick` | 1.622 | 49 | 32 | 14 | 10 | **20** | 13 sesiones/mes. Peor relación entre mantenimiento y uso |
| `guard` | 1.066 | 26 | 25 | 1 | 8 | **23** | 0 positivos verdaderos observados y 26 falsos |
| `done`/cierre | 736 | 20 | 13 | 6 | 1 | 18 | Cierre de worktrees; no medido |
| `gate` (`gate*`, `verify`, `affected`, `detect`, `serve`) | 3.978 | 31 | 18 | 7 | 3 | **4,5** | Detecta tests rojos (48 reintentos por test fallido). **Alto valor y bajo mantenimiento** |
| `plan` + `plan_check` | 921 | 5 | 4 | 1 | 0 | 4,3 | Estable |
| CLI (`bin/rein`) | 1.510 | 57 | 27 | 22 | 4 | 18 | Superficie |
| Skills | 434 | 30 | 15 | 12 | 3 | — | — |

**Lectura:**

- **Justifican su complejidad:** `gate`, por valor y estabilidad, y `plan_check`.
- **Concentran el mantenimiento:** el driver, `quick` y `guard`.
- `quick` y `guard` son los candidatos más claros a simplificar, **si** el piloto muestra que
  `quick` ≈ N1 y si el sandbox nativo cubre lo que hace el guard.

### 9.6 ¿`proposal.md` y `specs/` no aportan, o se consumen de forma indirecta? [H]

- **El brief de `ship` no los incluye.** `build_task_spec` usa el *Why*, el *Scope* y las
  *Decisions* de `tasks.md`.
- **Solapamiento textual:** de media, solo el 2% de los 5-gramas de `proposal.md` aparece en
  `tasks.md` (830 changes de openspec). La propuesta no se copia al plan, aunque puede estar
  parafraseada.
- **Lectores reales:**
  - 31 de 47 sesiones de planificación leen `proposal.md`;
  - 29 sesiones interactivas leen `openspec/specs` durable;
  - los workers, solo el 1,5–2%.
- **Conclusión:** sirven al **planner y al dueño** (continuidad entre changes), no a los workers.
  Que los workers no los lean no demuestra que no valgan. Medir su efecto exigiría un brazo
  de ablación que no compensa por 2,3 KB por change. **Se retira la recomendación de eliminarlos.**

### 9.7 Errores del diseño A/B anterior

1. **Modelo y workflow confundidos:** A con Sonnet/Codex frente a B con Opus. Ahora el implementador
   es siempre Sonnet 5.5 y el auditor siempre Codex.
2. **Brazos agregados:** no separaban qué aporta cada pieza. Ahora es una escalera
   N0 → N1 → N2 → P2 → A, más M para el modelo.
3. **Sesgo de selección:** solo tareas con BLOCKING. Ahora son 5 con y 5 sin, y se informa aparte.
4. **Sin tests ocultos validados:** ahora cada test oculto debe fallar en la base y pasar en el
   final, con un filtro de acoplamiento y adjudicación a ciegas.
5. **Los tokens eran la métrica principal:** ahora pasan a ser secundarios. Lo principal es la
   corrección, el tiempo hasta una solución correcta y las intervenciones humanas.

### 9.8 ¿REIN como capa ligera sobre lo nativo? [I, a probar]

| Garantía que quieres conservar | Mecanismo mínimo | ¿Lo da lo nativo? | Pieza de REIN que hace falta |
|---|---|---|---|
| Auditoría independiente entre proveedores | Diff y criterios hacia el otro CLI (`codex exec` / `claude -p`), más un veredicto ligado al hash | No: `/code-review` y `/review` usan el mismo proveedor | `rein-audit` más `review record/check` (pequeño) |
| Verificación determinista | `rein gate` como hook `Stop` (Claude) o `Stop` (Codex) | Los hooks sí; qué correr (affected, caché, timeouts), no | `gate` (el núcleo con mejor valor por LOC) |
| Orquestación y recuperación | Estado JSON reanudable más Orca | Los Workflows de Claude son reanudables, pero solo Claude | El driver de `ship`, **solo** para multi-tarea o multi-repo |
| Multi-repo | Una tarea por repo, gate de repo y auditoría conjunta | No | El driver de `ship` |
| Routing por cuota | `quota.py` más Orca | No | `quota` (112 LOC) |
| Worktrees | Orca o nativo | Sí | Nada propio |

**La hipótesis** es que REIN como «`rein gate` + auditoría entre proveedores sobre una sesión
nativa» (el brazo N2) iguala a `ship` en las tareas de un solo repo, y que `ship` solo se justifica
en multi-repo o multi-tarea. El piloto la contrasta con H-capa y H-orq. **No se quita nada antes.**

### 9.9 Qué queda en pie tras la revisión

- **Sigue en pie:**
  - el coste lo dominan las sesiones largas, no los workers;
  - la auditoría es barata en tokens **y** en latencia;
  - el guard genera falsos positivos (ahora con evidencia más fuerte);
  - `rein-role` y Serena están muertos;
  - `gate` es el mejor componente;
  - el mantenimiento se concentra en el driver, `quick` y `guard`.
- **Se rebaja:**
  - el beneficio de no supervisar desde la sesión Opus (2–4% del total, no 6%);
  - «84 defectos reales» pasa a «84 hallazgos sin validar».
- **Se retira:**
  - eliminar `proposal.md`/`specs/`;
  - «la auditoría cuesta latencia»;
  - el diseño A/B anterior.

---

## Anexo: método y limitaciones

- **Scripts** en `bench/`. Son de solo lectura sobre `~/.claude/projects`, `~/.claude/rein` y los
  `.rein/ship/*.json`, y no consumen cuota. `runs2.py` usa el `states.json` que genera
  `../2026-10-26-arquitectura/bench/analyze_states.py`.
- **Qué no se midió:**
  - las transcripciones de subagentes;
  - los tokens de Codex en las sesiones interactivas;
  - el peso real de `cache_read` en la cuota de la suscripción, que Anthropic no publica;
  - las horas del dueño.
- **La clasificación de sesiones es heurística:** una sesión «con plan» incluye todo lo demás que se
  hizo en ella. Por eso §1.1 separa la ventana pura de planificación.
- **La lectura de los 84 BLOCKING es manual** y su tipología es aproximada.
- **No se ejecutó ningún experimento con modelo:** el A/B de §6 está diseñado, no corrido.
