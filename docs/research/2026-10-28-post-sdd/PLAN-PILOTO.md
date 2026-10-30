# Plan del piloto: ¿qué de REIN produce software correcto más rápido?

> **Estado (2026-10-29):** fases 0 y 1 (smoke) ejecutadas; resultados en
> [RESULTADOS-SMOKE.md](RESULTADOS-SMOKE.md).
>
> Antes de la fase 2 hay que aplicar los seis cambios de su §6, sobre todo la **criba de dificultad
> con N0** y un **entorno con la suite base en verde**.
>
> El guard se trata aparte, en [GUARD-AISLAMIENTO.md](GUARD-AISLAMIENTO.md).
>
> **2026-10-29:** la criba de dificultad está diseñada y pre-registrada en [CRIBA.md](CRIBA.md).
> Sustituye la selección de tareas de §2.1 y fija los criterios de éxito, coste y decisión de la
> fase 2.

Fecha: 2026-10-28 · REIN fijado en `80b1732` · Base del diagnóstico: [INFORME.md](INFORME.md), §9.

**Pregunta única:** con el mismo modelo implementador, ¿qué piezas de REIN (plan, gates,
auditoría independiente y orquestación multi-worker) reducen el tiempo hasta una solución correcta,
la tasa de defectos y el esfuerzo humano frente a Claude Code o Codex nativos?

**Fuera de alcance:** no se construye el TO-BE ni se cambia REIN. Lo nuevo vive en `pilot/`, fuera
del plugin, y es desechable.

---

## 1. Diseño: una escalera de brazos con el implementador fijo

Todos los brazos implementan con **Claude Sonnet 5.5 en Claude Code**, con el mismo perfil lean de
arranque. Todos los que auditan lo hacen con **Codex gpt-6.1-sol**. Así, cada diferencia entre dos
peldaños consecutivos se atribuye a **una sola** pieza.

| Brazo | Qué recibe | Qué se ejecuta | Mide (frente al peldaño anterior) |
|---|---|---|---|
| **N0**: nativo | La intención (§2.2) | Una sesión `claude -p`, sin REIN. Se le pide correr los tests del repo | Línea base nativa |
| **N1**: + gates | Lo mismo | N0 más un hook `Stop` que corre `rein gate` y bloquea mientras esté en rojo | **Gates deterministas** |
| **N2**: + auditoría (REIN simplificado) | Lo mismo | N1, luego una auditoría de Codex con las 4 clases de BLOCKING de `rein-audit`, como máximo una ronda de fix en la misma sesión (`--resume`) y otra vez el gate | **Auditoría independiente** |
| **P2**: + plan | La intención **más el `tasks.md` congelado** | N2, pero una sola sesión ejecuta todas las tareas del plan | **Workflow de planificación** |
| **A**: REIN actual | El `tasks.md` congelado | `rein ship --agent claude --model sonnet` (un worker por tarea, gates, auditoría Codex, hasta 2 rondas) | **Orquestación multi-worker** |
| *M*: modelo (opcional, fase 3) | Igual que N2 | N2 con Codex como implementador y Claude Sonnet 5.5 como auditor | **El modelo** |

**Las tres comparaciones que pediste** salen de la escalera: **A** es REIN actual, **N0** la
alternativa nativa con el mismo modelo, y **N2** el REIN simplificado (gate más auditoría entre
proveedores sobre una sesión nativa).

**Confusiones que quedan y se declaran:**

- `claude -p` (N) frente a un worker interactivo de Orca (A).
- Una ronda de fix en N2 frente a dos en A, porque A es la configuración actual.
- El plan histórico se escribió con conocimiento del repo de entonces. Es justo: en REIN real, el
  plan existe antes del `ship`.

## 2. Tareas y lo que se congela antes de ejecutar

### 2.1 Selección: 10 changes históricos aprobados, 1 o 2 repos

Elegidos con `bench/candidates.py` entre las 77 ejecuciones aprobadas. Todos tienen el commit base
recuperable y el plan archivado. **Cinco tienen BLOCKING histórico y cinco no**, para que la
auditoría no juegue en casa.

| Fase | Change | Estrato | Repos | Tareas | Archivos | BLOCKING hist. | Coste hist. de A (Claude, eq. API) | Wall hist. |
|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | support-v3-widget | S | proxima-auth | 1 | 7 | 1 | $2,0 | 11 min |
| 1 | ai-support-p0-obs-cors | S | proxima-api | 1 | 2 | 0 | $0,6 | 7 min |
| 2 | availability-p1-storefront-tolerance | M | storefront-v2 | 1 | 21 | 3 | $2,4 | 17 min |
| 2 | support-v3-agent | M | rag-api | 2 | 19 | 3 | $5,8 | 50 min |
| 2 | sale-note-pos | M | proxima-pos | 2 | 19 | 0 | $2,6 | 45 min |
| 2 | availability-p0-api | M | proxima-api | 3 | 18 | 0 | (Codex) | 14 min |
| 2 | support-v3-new-errors | L | api + rag-api | 2 | 18 | 4 | $4,7 | 27 min |
| 2 | feature-tree-variant-landing | L | proxima-admin | 4 | 12 | 0 | $2,2 | 19 min |
| 2 | mcp-oauth-public-clients | C (OAuth) | proxima-api | 2 | 10 | 1 | $1,6 | 21 min |
| 2 | sale-note-api | C (fiscal) | proxima-api | 3 | 24 | 0 | $7,2 | 92 min |

Si un candidato no supera la validación de la fase 0, se sustituye por otro del mismo estrato y con
la misma condición de BLOCKING (lista completa en `bench/candidates.py`).

### 2.2 Qué recibe cada brazo

- **Intención:** el *Why*, el *Scope* y **todos** los criterios de aceptación del `tasks.md`,
  aplanados en un texto. Sin descomposición en tareas, sin decisiones, sin `Verification` y sin
  `Repo:`. Es lo que el dueño diría al pedir el cambio.
- **Plan** (solo P2 y A): el `tasks.md` tal como estaba en el commit base del plan root.

### 2.3 Congelación (manifest con hashes, antes de la primera ejecución)

`pilot/manifest.json` registra por tarea:

- el commit base de cada repo;
- el SHA-256 de `intent.md` y de `tasks.md`;
- la ruta y el hash de cada test oculto;
- el comando del gate y el de la suite completa.

Una ejecución cuyo manifest no coincida se invalida.

### 2.4 Tests ocultos: el criterio de «correcto»

1. **Tests de testigo de los BLOCKING históricos.** En 20 de los 21 fixes de auditoría con un diff
   limpio, el fix trae cambios en tests (`bench/fix_tests.py`). Un test de testigo se acepta solo si
   **falla en el commit anterior al fix y pasa después**. Esto convierte por primera vez un hallazgo
   del LLM en un defecto demostrado (§9.2 del informe).
2. **Tests de aceptación del change,** los que añadió el change histórico. Deben fallar o no
   existir en el commit base y pasar en el final.
3. **Filtro de acoplamiento.** Se descarta cualquier test oculto que llame a símbolos internos que
   los criterios no nombran. Solo valen fronteras públicas: endpoints HTTP, componentes exportados y
   funciones nombradas en los criterios.
4. **Suite completa del repo,** para detectar regresiones respecto al commit base (los fallos que ya
   existían en la base no cuentan).

**Sesgos que quedan y se declaran:**

- Los testigos salen de auditorías de Codex, lo que favorece a N2, P2 y A. Por eso se informa
  aparte del subconjunto sin BLOCKING.
- Los tests de aceptación los escribieron workers de REIN, lo que favorece a quien converge al
  mismo diseño. Por eso se aplica el filtro 3.
- Cada fallo de un test oculto lo **adjudica a ciegas** el dueño (sin saber de qué brazo es): ¿es un
  defecto o una diferencia legítima de API? Se publican los dos números, el bruto y el adjudicado.

### 2.5 Aislamiento y fugas

- **Clones saneados** en `~/rein-pilot/<task>/<arm>-<rep>/`:
  - `git clone --no-hardlinks` del repo local, con checkout del commit base;
  - se borran todas las refs posteriores y el `openspec/changes/**` del propio change;
  - sin remoto.

  Los repos de producto no se tocan.
- **Las sesiones corren en rutas nuevas,** así que no hay auto-memory del proyecto.
- **Brazos N:** `--settings pilot/settings/<arm>.json` con `deny` de `orca` y de `git log --all`, y
  sin acceso de escritura a `~/.claude/projects`.
- **Brazo A:** los clones se registran en Orca con nombres `pilot-*`.
- **Control de fugas posterior,** con `pilot/leak_scan.py` sobre cada transcripción. Busca:
  - el SHA del commit final histórico;
  - rutas de `~/Developer/products`;
  - `orca search`;
  - lecturas de `openspec/changes/archive`.

  Una ejecución con una fuga se excluye. Si hay más de una por brazo, se para el piloto.
- **El dueño responde con un guion congelado** a cualquier pregunta: la decisión histórica de
  `owner_decisions` si existe, si no «decide dentro de los criterios». Las respuestas se registran
  con marca de tiempo.

## 3. Métricas: primero corrección, tiempo y humano; los tokens después

| # | Métrica | Definición | Fuente |
|---|---|---|---|
| 1 | **Correcta** | Pasan todos los tests ocultos, una vez adjudicados, y no hay regresiones en la suite | `pilot/score.py` |
| 2 | **Defectos escapados** | Tests ocultos que fallan por defecto, separados en testigo histórico o aceptación | `score.py` más la adjudicación |
| 3 | **Tiempo hasta solución correcta** | Desde el arranque hasta el «done» del brazo, si es correcta. Si no lo es, se cuenta como fallo (censurado) | Marcas del harness |
| 4 | **Esfuerzo humano** | Número de intervenciones: preguntas, gates y desbloqueos manuales. Más un coste modelado de 3 min por intervención (supuesto declarado) | Log del guion |
| 5 | Latencia por etapa | Implementación, gate, auditoría, fix y tiempo ocioso | `bench/latency.py` |
| 6 | Tokens y coste equivalente API, por proveedor | Claude: lectura y escritura de caché, entrada y salida. Codex: tokens | `bench/cost.py`, `analyze_workers.py` |
| 7 | Llamadas, sesiones y agentes; iteraciones (dispatches, rondas) | Transcripciones y estado | Ídem |
| 8 | Tamaño y complejidad del diff | LOC y archivos, más la complejidad ciclomática añadida (`lizard`) | `score.py` |

**No medible y declarado así:**

- la mantenibilidad a largo plazo;
- el % real de cuota de suscripción (Orca da el antes y el después, pero no aísla de otras sesiones);
- la atención humana más allá del número de intervenciones.

## 4. Criterios de decisión, fijados antes de ejecutar

**Análisis:** pareado por tarea (8 tareas × 2 repeticiones en la fase 2). Se informa la tabla por
tarea y la mediana por brazo. Con n=8, **una diferencia de ≤1 tarea correcta o ≤1 defecto entre dos
brazos es «no distinguible»**: no se decide nada con ella.

| Hipótesis | Comparación | Se acepta si | Consecuencia |
|---|---|---|---|
| H-gate | N1 frente a N0 | N1 corrige ≥2 tareas más **o** escapa ≥2 defectos menos | Los gates se quedan como núcleo (esperado) |
| H-audit | N2 frente a N1 | N2 escapa ≥2 defectos menos, contando **también** el subconjunto sin BLOCKING | La auditoría entre proveedores se queda. Si es indistinguible: se queda solo para C y L, y se vuelve a medir |
| H-plan | P2 frente a N2 | P2 corrige ≥2 tareas más **o** baja el tiempo mediano ≥30% | `rein-plan` se queda para M, L y C. Si no: el plan pasa a ser opcional |
| H-orq | A frente a P2 | A gana en corrección o tiempo con ≥2 de margen | `ship` multi-worker justificado. Si no: `ship` solo para multi-repo o recuperación |
| H-capa | N2 frente a A | N2 es correcta en ≥ (A − 1) tareas, escapa ≤ (A + 1) defectos, tarda ≤0,7× A en mediana y requiere ≤ las intervenciones de A | **REIN pasa a capa ligera** (gate más auditoría sobre sesión nativa) para un solo repo |
| H-nativo | N0 frente a A | A no supera a N0 por ≥2 en corrección ni en defectos | REIN no aporta en este rango de tareas. Se replantea entero |

**Parada anticipada:**

- más de una fuga por brazo;
- el coste de la fase 1 supera 2× la estimación;
- la cuota semanal de Claude supera el 70%;
- un brazo deja el repo base modificado (un fallo de aislamiento).

## 5. Fases, coste y esfuerzo

Costes en equivalente API con los precios de la API (Sonnet 5.5: $2 de entrada, $10 de salida y
$0,20 de lectura de caché por MTok). En la suscripción se mide el % de cuota antes y después.

| Fase | Contenido | Ejecuciones con modelo | Coste estimado (Claude) | Codex | Tiempo humano |
|---|---|---:|---:|---:|---|
| **0: validar** | Clones saneados; extraer y **validar** los tests de testigo (fallo a pase) y los de aceptación; `manifest.json`; un *dry run* de `score.py` contra el commit final histórico, que debe dar «correcta», y contra la base, que debe dar «incorrecta»; `leak_scan.py` sobre transcripciones antiguas | 0 | $0 | 0 | **6–10 h**: es el coste real del piloto |
| **1: humo** | 2 tareas S × 5 brazos (N0, N1, N2, P2, A) × 1 repetición. Valida el harness, las métricas y el coste por ejecución | 10 | ~$10–20 | ~5 M tok | 1–2 h |
| **2: comparación** | 8 tareas × (A, N0, N2 × 2 repeticiones + N1, P2 × 1) | 64 | ~$150–400 (~0,4–0,9 B tok) | ~60–120 M tok | 3–5 h (guion y adjudicación) |
| 3 (opcional) | Brazo M (modelo) sobre las 8 tareas × 1 | 8 | ~$30–60 | ~40 M tok | 1 h |

**Base de la estimación:** las 10 tareas costaron en su día $29 de Claude más 20 M de tokens de
Codex en una pasada de REIN, y $21 solo la implementación (`bench/candidates.py`). La fase 2 son
unas 8 pasadas, con un margen de ×2 por la variabilidad.

**Comparación:** el gasto total de Claude de la cuenta es de unos $30.9k al mes en equivalente API.
El piloto completo es menos del 1,5% de un mes.

**Wall-clock:** unas 64 ejecuciones de ~15–90 min, con 2 o 3 en paralelo, son 1–2 días de máquina.

## 6. Lo mínimo que hay que construir (aislado de REIN)

Todo bajo `docs/research/2026-10-28-post-sdd/pilot/`, en stdlib de Python más bash, unas 600 LOC.

| Archivo | Qué hace | LOC aprox. |
|---|---|---:|
| `prepare.py` | Clona y sanea, escribe `intent.md` y el plan congelado, extrae los tests ocultos y crea `manifest.json` | 150 |
| `run_native.sh` | Brazos N0, N1, N2 y P2: `claude -p --model sonnet --settings …`; luego `codex exec` como auditor (N2 y P2) y `claude -p --resume` para la ronda de fix | 120 |
| `settings/{n0,n1}.json` | El perfil lean (copia del de `launch_profile`), más el hook `Stop` con `rein gate` en N1, N2 y P2 | 40 |
| `audit_prompt.md` | Las 4 clases de BLOCKING y la severidad de `rein-audit`, copiadas sin cambios | — |
| `run_rein.sh` | Brazo A: `rein ship <change> --root <clone-del-plan-root> --agent claude --model sonnet --detach` | 30 |
| `score.py` | Tests ocultos, suite, `lizard` y `results.jsonl` | 150 |
| `collect.py` / `leak_scan.py` | Reutilizan `bench/cost.py`, `latency.py` y `analyze_workers.py` por id de sesión | 120 |

**Ningún archivo de `plugins/rein/` cambia.** El brazo A usa el `rein` instalado; su versión
(`rein --version`) y el commit `80b1732` se registran en el manifest.

## 7. Reversibilidad

| Efecto | Cómo se revierte |
|---|---|
| Clones en `~/rein-pilot/` | `rm -rf ~/rein-pilot` |
| Repos `pilot-*` registrados en Orca (solo el brazo A) | Quitarlos de Orca (`orca repo …`; se anota el comando exacto en la fase 0) |
| BDs de test `pilot_<task>_<arm>_<rep>` (vía `gate.env` en el clon) | `dropdb` de cada una; el script lo hace al final y lista las que queden |
| Sesiones en `~/.claude/projects/-Users-…-rein-pilot-*` y `~/.codex/sessions` | Se quedan como datos; se pueden borrar por ruta |
| Directorio `pilot/` en este repo | `git rm -r` o no commitearlo |
| Configuración global de Claude, Codex u Orca, y el plugin REIN | **No se tocan** |

## 8. Quick wins que se pueden medir sin cambiar producción

Ninguno altera el comportamiento de `rein ship`. Primero se miden en modo sombra o se aplican como
hábito.

| # | Quick win | Cambio mínimo | Evidencia | Cómo se valida |
|---|---|---|---|---|
| QW1 | No supervisar desde la sesión Opus de planificación | Hábito: tras `/ship`, `/clear` o abrir otra sesión. Sin código | Episodios disparados por notificaciones tras `/ship`: ~$1,2k/mes en equivalente API, unas 0,9× todos los workers (§9.1) | `bench/watch_cost.py` antes y después |
| QW2 | El guard deja de culpar al worker de lo que hizo otro | **Sombra:** cuando salta el check de fuera del worktree, buscar en la transcripción **del propio worker** una escritura o un `git` en esa ruta, y registrar lo que se habría decidido | 26 de 26 rechazos sin escritura del worker (§9.4) | `bench/guard_attribution.py` sobre los casos nuevos: ¿aparece algún positivo verdadero? |
| QW3 | No re-auditar el mismo hallazgo | **Sombra:** firma `archivo:símbolo` más severidad; registrar «habría escalado» | ~13 de 84 BLOCKING repetidos | Contar en las ejecuciones siguientes |
| QW4 | Menos reintentos que acaban en el dueño | Ya existe (`80b1732`): medir su efecto | 40 de 132 decisiones del dueño son `retry_exhausted` | `analyze_states.py` en dos semanas |
| QW5 | Quitar lo redundante con lo nativo | **Lo decide el piloto** (H-capa): N1 ya es «sesión nativa más `rein gate` como hook `Stop`» | — | Fase 2 |

## 9. Qué se concluye y qué no

- **Con 8 tareas** se puede detectar una diferencia grande, como que la auditoría evite ≥2 defectos
  o que REIN tarde el doble. No se pueden detectar diferencias finas. «No distinguible» es un
  resultado válido y no justifica borrar nada.
- **Solo proxima.** Generalizar a otros repos o stacks queda fuera.
- **Después de la fase 2,** las decisiones de §4 se trasladan a `INFORME.md` §5, con los datos, y
  solo entonces se toca código de producción.
