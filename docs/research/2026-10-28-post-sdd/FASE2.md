# Fase 2: qué pieza de REIN aporta frente a Claude Code nativo (pre-registro)

Fecha: 2026-10-30. Este documento se congela (`pilot/manifest-fase2.json`) **antes** de la primera
ejecución de un brazo nuevo. Cualquier cambio posterior va a §9 con fecha, motivo y qué afecta.

Pregunta: **¿cuál es la arquitectura mínima que da la mayor calidad con el menor coste total?** En
particular: ¿alcanza N2 (nativo, gates y auditoría independiente) una calidad comparable a A (REIN
completo) con bastante menos overhead?

## 1. Brazos (una pieza por peldaño)

Todos implementan con **Claude Sonnet 5.5** (`claude-sonnet-5-5`) y parten del mismo clon, con el
mismo entorno, el mismo prompt base y el mismo sandbox de la criba ([CRIBA.md](CRIBA.md) §15–§17).

| Brazo | Qué se ejecuta | Pieza que mide |
|---|---|---|
| **N0** | Las 2 ejecuciones de la criba (no se repiten) | Línea base |
| **N1** | N0 más un hook `Stop` que corre `rein gate --tier task` en cada repo y no deja terminar mientras esté rojo | Gates deterministas |
| **N2** | N1, después una auditoría de Codex con el brief de auditoría de REIN y, si hay BLOCKING, **una** ronda de fix en la misma sesión (`--resume`), con el hook otra vez | Auditoría independiente |
| **P2** | Un plan generado en la propia ejecución (§2), y después N2 con el plan añadido al prompt: una sola sesión ejecuta todo el plan | Planificación |
| **A** | `rein ship <change> --agent claude --model sonnet` sobre el **mismo plan** generado para P2 en esa repetición. Workers de Orca, gates, auditoría Codex, hasta las rondas que REIN haga por defecto. Un dueño simulado responde con el guion fijo del smoke | Orquestación completa |

- **Auditor:** Codex `gpt-6.1-sol`, el mismo que usa REIN en A. En N2 y P2 corre dentro del sandbox
  del sistema operativo. En A corre como lo lanza Orca hoy (sin confinar): **decisión del dueño del
  2026-10-30**, que acepta el riesgo a cambio de medir REIN tal como corre (§5).
- **REIN:** la versión instalada, `rein 2.1.0`, cuya `lib` es idéntica al repo en `80b1732`. Ni REIN
  ni Orca se modifican.
- **Topes por ejecución:**
  - cada sesión de Claude, $8 en equivalente API y 60 minutos;
  - la auditoría, 60 minutos;
  - A, 120 minutos de reloj en total;
  - el plan, 30 minutos.

  Llegar a un tope cuenta como fallo.

## 2. El plan de P2 y de A

- **Se genera, no se copia.** El plan histórico no se usa: sus *Decisions* se escribieron con
  conocimiento del repo de entonces y podrían llevar pistas de los defectos ocultos.
- **Quién lo genera:**
  - una sesión `claude -p` con **Opus 5.5**, el modelo con el que el dueño planifica;
  - dentro del mismo sandbox;
  - sobre un clon limpio;
  - recibe solo la intención (lo mismo que N0–N2) y el procedimiento de `skills/rein-plan/SKILL.md`
    de REIN;
  - el dueño confirma por adelantado y no responde preguntas («decide dentro de los criterios»).
- **Uno por tarea y repetición,** compartido por P2-rK y A-rK. Así, P2 frente a A aísla la
  orquestación.
- **Su coste y su tiempo se suman al total de P2 y al de A.** Se informan también aparte.
- **Si no produce un `tasks.md` que `rein tasks` pueda leer,** se reintenta una vez, y ese coste
  cuenta. Si vuelve a fallar, P2 y A de esa repetición cuentan como fallo del componente de plan.

## 3. Tareas, congeladas antes de ver resultados

**6 duras** (las 8 duras de la criba eran más de 6):

| Tarea | Patrón en N0 | Repos | Qué cubre |
|---|---|---|---|
| mcp-ops-core | Completa, repite el defecto | api | Seguridad y permisos (scopes de plataforma), concurrencia (lote atómico) |
| client-errors | Completa, repite el defecto | api | Observabilidad, privacidad en telemetría, errores |
| cpe-admin | Completa, repite el defecto | admin | Contrato UI–API, reglas de envío |
| pos-availability | Completa, repite el defecto | pos | Sincronización y datos obsoletos |
| new-errors | Incompleta | api + rag | Contrato entre dos repos, concurrencia, errores |
| storefront-tolerance | Incompleta | storefront | Funcionalidad incompleta, otro repo y otro stack |

- **Entran las 4 «completas que repiten el defecto».** Son donde mejor se aísla la auditoría (lo
  pidió el dueño).
- **Entran 2 incompletas:**
  - **new-errors,** por ser la única entre repos y la de menor puntuación parcial;
  - **storefront-tolerance,** porque añade un repo y un stack que ninguna otra cubre.
- **Quedan fuera** cpe-pos (su repo, pos, ya está cubierto) y rag-agent (ya hay observabilidad en
  client-errors, y rag en new-errors).
- **Desviación de CRIBA §8:** api entra 3 veces (el tope era 2). Se acepta para cubrir las 4
  completas y el único contrato entre repos.

**2 fáciles, como control de sobrecoste:** `random.Random(20261029).sample(sorted(fáciles), 2)` =
**widget** y **sale-note-pos**.

Los tests ocultos, los paquetes, la validación, el entorno y la puntuación son los de la criba, sin
cambios. La regla de ruido es la de §12.2, y los reintentos de 3 los de la decisión 5.

## 4. Repeticiones y tandas

- **2 repeticiones por celda:** 8 tareas × {N1, N2, P2, A} × 2 = **64 ejecuciones y 16 planes.**
  N0 ya tiene sus 16.
- **Tanda 1 (validación):** widget, mcp-ops-core y new-errors, × 4 brazos × repetición 1. Son 12
  ejecuciones y 3 planes.
  - Si la instrumentación o las condiciones fallan, se corrige, se anula la tanda y se repite.
  - Si funcionan, **sus ejecuciones cuentan** y el protocolo ya no cambia.
- **Tanda 2:** el resto (52 ejecuciones y 13 planes). Abarca más de una semana de cuota.
- **Orden:** barajado con la semilla `20261029` dentro de cada tanda. Un plan corre antes que los P2
  y A que lo usan.
- **Concurrencia:** como máximo 2 agentes a la vez. A corre **solo**, porque `ship` puede tener 2
  workers o un worker y su auditor. La puntuación va en serie.

## 5. Aislamiento

- **N1, N2, P2 y el plan:** el sandbox de la criba (§15), más:
  - deny de lectura sobre `~/.claude/rein/{audit-reports,runs.jsonl,events.jsonl}`, que guardan
    auditorías y ejecuciones históricas de REIN (ninguna ejecución de N0 los tocó);
  - lectura de `rein` (instalado en `~/.local`).
- **Codex (N2 y P2):** el mismo sandbox, salvo `~/.codex`, del que se deniega la lectura de
  `sessions/`, `archived_sessions/`, `history.jsonl` y `session_index.jsonl`. La función
  `memories` de Codex está apagada.
- **A:** REIN tal como corre hoy. Los clones se registran en Orca como `pilot-<run>-<repo>`, con
  `Repo:` reescrito a ese nombre: **nunca** el nombre de un repo real. Workers y Codex no tienen
  sandbox del sistema operativo.
  - **Un commit de arnés por clon:**
    - `orca.yaml`, para que cada worktree corra el setup de la criba;
    - el `gate.cleanup` de `flow.config.json` apuntando al Postgres del piloto (`rein-pilot-pg`)
      en lugar del tuyo (`proxima-suite-db`).
  - **Un `.env.local` sin seguir,** con el entorno de CI de la criba. El shell de un worker de Orca
    no trae ese entorno, y sin él la API apuntaría a tu base de datos del puerto 5435.
- **Gate de N1, N2 y P2:** `rein gate` nombra su BD de test por el directorio. El hook lo corre
  sobre un enlace con nombre único por ejecución (`.pilot/<run>-<repo>`), para que dos ejecuciones
  en paralelo no compartan base de datos.
- **Escaneo de fugas en todas las transcripciones,** de Claude y de Codex, con la regla de evidencia
  de la criba: una fuga es un resultado que trae contenido de fuera.
  - Una ejecución con fuga se anula y se repite.
  - Una segunda fuga en el mismo brazo para la fase.

## 6. Qué se mide

**Calidad** (la métrica principal es el código correcto, no los hallazgos):

- *Correcta* y *Correcta+* (CRIBA §6);
- tasa de F, de D-spec y de D-impl;
- regresiones confirmadas;
- gate final;
- tests ajenos modificados.

**Por componente:**

| Pregunta | Cómo se mide |
|---|---|
| ¿Detectan los gates algo que N0 no? | Rojos del hook en N1 (qué slot, qué fallaba), y resultado oculto de N1 frente a N0 |
| ¿Encuentra la auditoría defectos reales? | Cada BLOCKING se adjudica **a ciegas**: real (lo confirma un test oculto o un fallo reproducible) o falso positivo |
| ¿Los corrige el implementador? | **Se puntúa también el estado anterior al fix** en N2, P2 y A. *Corregido*: un test oculto pasa de rojo a verde. *No corregido*: sigue rojo. *Regresión del fix*: algo verde pasa a rojo |
| ¿Mejora la cobertura el plan? | Tasa de F de P2 frente a N2 |
| ¿Mejora la calidad la orquestación, o solo añade tiempo? | A frente a P2 y frente a N2: *Correcta*, tiempo y coste |
| ¿Introduce algún componente falsos positivos, regresiones o correcciones innecesarias? | Hallazgos adjudicados falsos, regresiones nuevas tras el fix y cambios fuera del alcance |

**Coste y tiempo, por separado:**

- **equivalente API de Claude:** por fase (plan, implementación, fix, workers) y total;
- **Codex:** tokens de entrada, caché y salida. No se le pone precio (no hay uno fiable);
- **suscripción:** % semanal y de sesión de Claude y de Codex, antes y después de cada tanda. Por
  ejecución no se atribuye, porque hay 2 en paralelo y mi propia sesión;
- **reloj:** total, activo y ocioso, y la parte del plan aparte;
- **esfuerzo humano:**
  - respuestas del dueño simulado en A, cada una cuenta como una intervención;
  - en los brazos N son 0 por diseño, no por mérito;
  - mi tiempo de arnés se informa aparte.

## 7. Reglas de decisión

Las de CRIBA §9.4. Para cada peldaño frente al anterior, y para **N2 frente a A** (H-capa de
[PLAN-PILOTO.md](PLAN-PILOTO.md)): ¿N2 es *Correcta* en ≥ (A − 1) ejecuciones, tarda ≤ 0,7× A en
mediana y cuesta menos?

**Dominancia:** un brazo queda dominado si otro lo iguala o supera en *Correcta* y además es más
barato y más rápido.

Con 12 ejecuciones duras por brazo, una diferencia de ≤ 2 no se distingue del azar, y se dirá.

## 8. Presupuesto y parada

| Límite | Tanda 1 | Acción |
|---|---|---|
| Equivalente API de Claude de la tanda | **$130** | Parar y avisar |
| Semanal de Claude | **≥ 72%** (hoy 48%; se reinicia el lunes a la 1 p. m.) | Parar y avisar |
| Ventana de 5 h de Claude | ≥ 70% | Pausar 10 minutos y volver a mirar |
| Semanal de Codex | ≥ 60% (hoy 19%) | Parar y avisar |
| Parada manual | Archivo `~/Developer/sandbox/rein-pilot/STOP` | No se arranca nada más |
| Problema de aislamiento, contaminación o medición | — | Parar, reportar, y corregir antes de seguir |

El presupuesto de la tanda 2 se fija al cerrar la tanda 1, con el coste real por brazo.

## 9. Desviaciones

**D1 (2026-10-30, 11:33). Error del arnés de A: `flow.config.json` vacío.**

- **Qué pasó.** El commit de arnés de A abría `flow.config.json` para escribir antes de leerlo, así
  que el archivo quedó vacío. REIN no podía leer su configuración: el guard falló en T001 y T003, y
  el gate cayó a un `pytest` genérico.
- **Qué se anula.** Solo `mcp-ops-core-a-r1` (35 min, $6,65): corrió en condiciones distintas. Se
  movió a `phase2-runs/_void/`, y su worktree de Orca se retiró.
- **Qué no se toca.** Las 9 ejecuciones N1, N2 y P2 y los 3 planes no usan ese commit y siguen
  valiendo. Por eso no se anula la tanda entera, a diferencia de lo que dice §4: es decisión del
  dueño.
- **Corrección.**
  - El arnés lee el archivo antes de reescribirlo y comprueba que el JSON resultante es válido.
  - Antes de lanzar, se comprobó que un clon de A conserva `flow.config.json` válido y con
    `rein-pilot-pg`.
  - Se repite `mcp-ops-core-a-r1` con el mismo plan. `widget-a-r1` y `new-errors-a-r1` corren por
    primera vez.
  - El manifiesto se recongela con esta corrección.

**D2 (2026-10-30, 13:05). El dueño detiene la fase 2 después de la tanda 1.**

- La tanda 2 no se ejecuta, y mcp-ops-core A no se repite.
- Análisis posterior, sin ejecuciones nuevas de agentes. Se volvieron a correr tests ocultos sobre
  árboles ya producidos y sobre commits de A anteriores a su fix:
  - new-errors P2 se puntuó con bases de datos limpias y aisladas;
  - el estado de A anterior al fix se puntuó desde git (`widget-a-r1-pre`, `new-errors-a-r1-pre`).
- Se detectaron dos contaminaciones del laboratorio:
  - el hook global `guard-prod-and-secrets.py` quedó bloqueado por el sandbox en la ronda de
    corrección de mcp-ops-core N2;
  - la suite completa de la API es inestable bajo carga.
- Conclusiones en [CIERRE-PROVISIONAL-FASE2.md](CIERRE-PROVISIONAL-FASE2.md).
