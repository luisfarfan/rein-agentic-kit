# Smoke del piloto: resultados (fase 0 y fase 1)

Fecha: 2026-10-28/29 · REIN `rein 2.1.0` (repo `80b1732`) · Claude Code 2.1.312 · codex-cli 0.164.0.
Diseño: [PLAN-PILOTO.md](PLAN-PILOTO.md). Harness: `pilot/` (`harness.py`, `watch_a.py`, `intents.py`,
`tasks.json`, `manifest.json`). Los datos en bruto están en
`~/Developer/sandbox/rein-pilot/runs/<run>/meta/`.

> **Con n = 2 tareas y 1 repetición nada de esto es concluyente.** Este smoke valida el método;
> no decide la arquitectura.

---

## 1. Qué se ejecutó

- **Tareas:**
  - **widget** (`support-v3-widget`, proxima-auth): una tarea, frontend, con **1 BLOCKING histórico**.
  - **obs-cors** (`ai-support-p0-obs-cors`, proxima-api): una tarea, orden de middlewares,
    **sin BLOCKING**.
- **Brazos:** N0, N1, N2, P2 y A, todos con **Claude Sonnet 5.5 como implementador** y **Codex
  gpt-6.1-sol como auditor**.
- **Ejecuciones:** 10 válidas. Otras 3 se anularon por fallos del harness (§4).

## 2. Fase 0: validación

| Comprobación | Resultado |
|---|---|
| Repos productivos sin modificar | ✅ Mis operaciones sobre ellos fueron de solo lectura (`git archive`, `repack` hacia mis copias). `proxima-api` cambió durante la sesión por **tus** commits (`feat(mcp)…`, 23:11–23:16, según el reflog) |
| Sin acceso a las soluciones | ✅ Los clones solo tienen la historia hasta la base (verificado: los commits finales no existen en ellos) y un stub de hooks en lugar de `proxima-engineering`. Las sesiones nativas, con `deny` de lectura sobre productos, oráculos y transcripciones. **Escáner de fugas: 0 coincidencias en las 10 ejecuciones** |
| Los tests ocultos reproducen lo esperado | ✅ **widget funcional:** fallan 7 de 9 en la base y pasan los 9 en el pre-fix y en el final. **widget defecto:** falla en la base y en el pre-fix y pasa en el post-fix. **obs-cors:** fallan 4 de 15 en la base y pasan los 15 en el final |
| **El BLOCKING histórico era un defecto real** | ✅ **Demostrado por primera vez:** en el commit anterior al fix, la lectura antigua pisa el mensaje recién enviado (`expected … to contain 'Sigue sin salir'`); en el posterior, no |
| Repetibilidad | ✅ Los tests ocultos dan el mismo resultado en dos ejecuciones. La suite de proxima-api da 10 fallos estables y 0 intermitentes en dos ejecuciones, con BD aislada |
| Presupuesto y parada | ✅ `--max-budget-usd 8` por sesión, timeout de 60 min por fase, 90 min para A, archivo `STOP` y presupuesto total del smoke de $60 |
| Registro | ✅ Tiempo real, tokens por tipo (lectura y escritura de caché, entrada, salida), coste equivalente API, sesiones, gates, rondas, intervenciones, cuota antes y después, y resultado funcional |

## 3. Resultados por brazo

Estado definitivo de los tests ocultos, tras las correcciones de validez de §4. Tiempos en minutos
de reloj; el tiempo ocioso es el wall-clock sin ningún agente ni gate activo.

### widget (con BLOCKING histórico)

| Brazo | ¿Correcta? | Funcional | Defecto histórico | Wall | Ocioso | Claude eq. API | Codex (entrada / caché / salida) | Turnos Claude | Sesiones C/X | Gates (rojos) | Rondas de fix | Intervenciones |
|---|---|---|---|---:|---:|---:|---|---:|---|---|---:|---:|
| N0 nativo | ✅ | 9/9 | ✅ | 3,7 | 0,0 | $2,29 | — | 38 | 1/0 | 0 | 0 | 0 |
| N1 + gates | ✅ | 9/9 | ✅ | 4,0 | 0,0 | $2,27 | — | 43 | 1/0 | 1 (0) | 0 | 0 |
| N2 + auditoría | ❌ (¹) | 8/9 | ✅ | 4,8 | 0,1 | $1,95 | 40k / 309k / 2,6k | 29 | 1/1 | 1 (0) | 0 (APPROVED) | 0 |
| P2 + plan | ✅ | 9/9 | ✅ | 9,0 (²) | 0,0 | $3,68 | 56k / 473k / 5,4k | 60 | 1/1 | 2 (0) | 1 | 0 |
| A REIN `ship` | ❌ | 9/9 | **❌ reproduce la carrera** | 7,4 | 0,8 | $2,66 | 90k / 728k / 5,9k | 52 | 3/2 | 4 (0) | 1 | 0 |

### obs-cors (sin BLOCKING histórico)

| Brazo | ¿Correcta? | Funcional | Wall | Ocioso | Claude eq. API | Codex (entrada / caché / salida) | Turnos | Sesiones C/X | Gates | Rondas de fix | Intervenciones | Alcance del diff |
|---|---|---|---:|---:|---:|---|---:|---|---|---:|---:|---|
| N0 | ✅ | 16/16 | 8,4 | 0,1 | $1,58 | — | 45 | 1/0 | 0 | 0 | 0 | 3 archivos |
| N1 | ✅ (³) | 16/16 | 9,3 | 0,1 | $2,03 | — | 56 | 1/0 | 2 | 0 | 0 | 4 |
| N2 | ✅ | 16/16 | 42,5 ⚠ | 0,1 | $3,59 | 61k / 1,30 M / 6,3k | 95 | 1/1 | 3 | 1 ⚠ | 0 | 4 |
| P2 | ✅ | 16/16 | 39,1 ⚠ | 0,1 | $2,51 | 86k / 2,14 M / 8,6k | 74 | 1/1 | 4 | 1 ⚠ | 0 | 2 |
| A | ✅ | 16/16 | 58,1 ⚠ | 3,6 | $4,67 | 56k / 654k / 4,3k | 124 | 6/1 | 6 (14,5 min) | 2 tareas GATE ⚠ | **2** | **9 archivos, 5 tests ajenos** ⚠ |

(¹) F2 falla porque el indicador «Revisando…» sigue visible después de llegar la respuesta. Los
criterios no lo dicen explícitamente: es un **caso ambiguo**, con probable defecto menor de UX. Se
informa aparte.

(²) Descontados los 0,9 min de hueco del harness (§4, problema 4).

(³) Adjudicado: la «regresión» `test_rein_gate_cleanup` fue un timeout de `DROP DATABASE` por
**Postgres saturado por el propio piloto**. No viene del cambio de N1.

⚠ = **comparación no válida**: el entorno piloto de proxima-api tiene 10 tests que ya fallan en la
base (problema 7). Los brazos con gate de suite completa los persiguieron.

**Aporte marginal de la auditoría** (estado puntuado antes del fix):

| Brazo | Antes del fix | Después del fix | Qué encontró la auditoría |
|---|---|---|---|
| P2 | Ya era correcto | Correcto | Un bug plausible de *badge* en conversaciones fuera de pantalla, **no cubierto** por los tests ocultos y no validado |
| A | Ya tenía la carrera | Sigue teniéndola | **Otro** bug plausible (el polling se detenía si fallaba la lectura del detalle), que corrigió. **La carrera, no la vio** |
| N2 | — | — | APPROVED sin hallazgos. No cazó el problema de F2 |

**Verificaciones nativas que hizo N0 sin que se le forzara** (cuenta de comandos en la transcripción):

| Tarea | Tests | Typecheck | Lint | `git diff` de autorrevisión | Commit |
|---|---:|---:|---:|---:|---|
| widget | 12 | 6 | — | 3 | ✅ |
| obs-cors | 8 | 2 | 6 | 4 | ✅ |

Es un uso competente de Claude Code, no un N0 limitado artificialmente.

**Coste total del smoke:**

| Concepto | Consumo |
|---|---|
| **Equivalente API, Claude** | $27,23 en los 10 brazos válidos; menos de $0,50 en los 3 anulados y en las pruebas del sandbox con Haiku |
| **Codex** | ~0,39 M de tokens de entrada, ~5,6 M de lectura de caché y ~33k de salida. Sin precio equivalente fiable |
| **Suscripción** | Claude semanal 33% → 34%, Codex semanal 15% → 17%. La ventana de sesión de Claude (2% → 9%) incluye mi propia sesión Opus orquestando, así que no se atribuye |
| **Tiempo humano tuyo** | 0 (el dueño simulado fue un guion) |
| **Tiempo de preparación del harness** | Lo hizo un agente, unas 3 h de reloj. Construir y validar los tests ocultos de 2 tareas, con dos correcciones de validez, fue la parte más cara |

**Coste de planificar** (P2 y A recibieron el plan congelado, sin pagarlo): en el histórico, la
mediana de `/rein-plan` fue de 10 min y 8,7 M de tokens Opus, unos **$3 en equivalente API** por
change. Debe sumarse a P2 y A al comparar el coste total.

## 4. Problemas metodológicos detectados (y qué se hizo)

| # | Problema | Efecto | Acción |
|---|---|---|---|
| 1 | La suite de proxima-api usaba la **BD de test compartida** `proxima_test`, cuyo esquema el setup borra y recrea | Línea base inestable (1.205 fallos → 10). **Pudo chocar con tests tuyos** entre las 23:12 y las 23:24 | `PROXIMA_TEST_DB=proxima_test_pilot_<run>` en toda ejecución; las BDs del piloto se borran al final (hecho) |
| 2 | Los hooks del repo llaman a `../proxima-engineering/scripts/hooks/*`, que no existe en los clones | **Todo Bash bloqueado**: N0, N1 y A del widget (r1) no hicieron nada | Stub con **solo** esos dos scripts, enlazado como hermano. Las 3 ejecuciones se anularon y se repitieron (r2) |
| 3 | El repo piloto en Orca no tenía `worktreeBaseRef` | El primer worker de A no arrancó (1 intervención causada por el harness) | `orca repo set-base-ref develop` antes de lanzar. Además, `orca.yaml` (commit de harness en los clones de A) para que sus worktrees tengan las dependencias |
| 4 | El parser de hallazgos esperaba texto y REIN guarda `{severity, text}` | P2 widget no ejecutó su ronda de fix a tiempo | Parser corregido. La ronda se ejecutó después, con el hueco (0,9 min) descontado |
| 5 | El test del defecto **podía pasar vacuamente**, y después quedó **acoplado** a «hacer polling con `get`» | Falsos aprobados y falsos suspensos posibles | Se endureció (la lectura debe estar en curso) y se generalizó (la lista anuncia el cambio). Se revalidó contra la base, el pre-fix y el post-fix, y **se volvieron a puntuar todos los brazos** con la versión final (hashes en `manifest.json`) |
| 6 | El guion del dueño respondió con el texto literal «(free text)» | El worker GATE1 de A obs-cors recibió una respuesta vacía | Corregido en `watch_a.py`. Esa ejecución queda marcada |
| 7 | **El entorno de proxima-api no equivale al de producción**: sin `.env.prod` ni `POSTGRES_*`, 10 tests fallan ya en la base. *Corrección 2026-10-29: la causa era mi `.env.local` copiado del ejemplo, sin las variables que exporta el CI; con el entorno del CI los 10 pasan ([CRIBA.md](CRIBA.md) §3)* | Los brazos con suite completa (el gate de repo de A y el auditor de N2/P2) los trataron como rojos: rondas de fix, +30–50 min y, en A, **5 archivos de test ajenos modificados** | **Las comparaciones de tiempo y coste de obs-cors para N2, P2 y A no son válidas.** Para la fase 2: solo tareas cuya suite base esté verde en el entorno piloto, o un entorno de test completo y saneado |
| 8 | Concurrencia de 4–5 agentes más la puntuación | Postgres saturado, una falsa regresión y tiempos de reloj con ruido | Fase 2: como máximo 2 a la vez, y la puntuación en serie |
| 9 | La adjudicación no fue ciega (yo sabía qué brazo era cada uno) | Posible sesgo en N2/F2 y en N1 | Se publican el dato bruto y el adjudicado. Fase 2: adjudicación ciega |
| 10 | Confusiones declaradas | N usa `claude -p` (no puede preguntar) y A usa workers interactivos de Orca. A tiene MCP y N no (`--strict-mcp-config`, por seguridad). A hace hasta 2 rondas, N2/P2 una | Se declaran. Las intervenciones de N son 0 por diseño, no por mérito |
| 11 | El gate de REIN en A nombra la BD por el worktree (`proxima_test_ai_support_p0_obs_cors*`) | **Reutilizó BDs de test anteriores al piloto** que tenían ese nombre (por OID, son de antes de esta noche, probablemente del run del 27-oct) y recreó su esquema | No las he borrado porque no son mías. Fase 2: nombres de worktree únicos para el piloto |

**¿Aíslan las comparaciones la variable deseada?**

- **N0 → N1 (gates):** sí. Solo cambia el hook `Stop`.
- **N1 → N2 (auditoría):** sí, en widget. En obs-cors la auditoría arrastra el problema 7.
- **N2 → P2 (plan):** sí. Mismo arnés, con el plan congelado añadido.
- **P2 → A (orquestación):** **no aísla una sola variable.** Cambian a la vez:
  - el modo del agente (interactivo frente a `-p`);
  - el brief de REIN;
  - los workers múltiples;
  - el gate de repo;
  - el número de rondas.

  Es «el paquete REIN» frente a «la sesión nativa con el mismo plan», y así hay que leerlo.

## 5. Qué parece aportar valor (provisional, n = 2)

| Componente | Señal en el smoke | Lectura |
|---|---|---|
| **Gates deterministas** (N1 frente a N0) | Sin diferencia: ambos correctos en las dos tareas, mismo coste. N0 ya corre tests, typecheck y lint por su cuenta | **Efecto techo.** Con tareas que un Sonnet competente resuelve, el gate no puede mostrar valor |
| **Auditoría entre proveedores** (N2 frente a N1) | No cambió ningún resultado oculto. Encontró bugs plausibles no cubiertos (P2, A), pero **no vio la carrera real** que A introdujo, ni el F2 de N2. En obs-cors costó una ronda entera por fallos de entorno | **Sin evidencia de valor en este smoke, y con un coste real de latencia** cuando la suite es lenta o inestable. Su valor histórico (84 hallazgos, el primero ahora validado) sigue en pie, pero no se reprodujo aquí |
| **Plan** (P2 frente a N2) | widget: P2 correcto y N2 no (por el F2 ambiguo). P2 cuesta más: el doble de turnos y $3,68 frente a $1,95. A eso hay que sumar unos $3 de planificar | Señal débil y ambigua |
| **Orquestación `ship`** (A frente a P2) | widget: **A reprodujo el defecto histórico**, P2 no. obs-cors: A fue el más lento, el único con intervenciones y el único que modificó tests ajenos (con el entorno como agravante) | **Ninguna ventaja observada en tareas de una sola tarea de plan.** Riesgo de alcance arrastrado por su gate de suite completa |
| **Nativo** (N0) | Correcto en ambas tareas, el más rápido (3,7 y 8,4 min) y de los más baratos | Para tareas de este tamaño, el nativo competente ya entrega |

**Sobre tu hipótesis** (capa ligera de gates y auditoría, más orquestación solo cuando haga falta):

- El smoke **no la confirma ni la refuta**.
- **Sí es coherente** con «la orquestación pesada no aporta en tareas de una sola unidad».
- **No aporta evidencia** de que gates y auditoría mejoren a un Sonnet competente en tareas que este
  ya resuelve.
- Para medir su valor hacen falta **tareas donde N0 falle**.

## 6. ¿Seguir con el benchmark completo?

**Sí, pero no con las 64 ejecuciones tal como estaban diseñadas.** Cambios necesarios antes:

1. **Criba de dificultad**, que es lo más importante: correr **solo N0** una vez sobre unos 12
   candidatos y quedarse con las tareas donde N0 falla o queda al límite, más 2 controles fáciles.
   Si N0 lo resuelve todo, gates y auditoría no pueden demostrar valor. Coste: unos 12 × $2–4.
2. **Entorno equivalente:** solo tareas cuya suite base esté verde en el entorno piloto (§4.7), o
   preparar un entorno de test completo y saneado para proxima-api.
3. **Concurrencia de 2 agentes como máximo**, puntuación en serie y nombres de worktree y BD únicos.
4. **Al menos 2 repeticiones por celda.** En el widget, N2 falló donde N0 y N1 no; con n = 1 no se
   distingue el azar del efecto.
5. **Adjudicación ciega** de cada fallo oculto (un revisor sin la etiqueta del brazo).
6. **Ahorrar brazos:** dejar P2 solo donde la tarea tenga ≥2 tareas de plan (en las de una, el plan
   equivale a la intención).

**Coste revisado de la fase 2** (con la criba): unos 8 × $3 de criba, más unas 40 ejecuciones × $2–5,
**unos $150–250 en equivalente API** de Claude, más Codex. El tiempo humano real está en construir y
validar los tests ocultos: **unas 1,5 h por tarea** según este smoke, por los dos fallos de validez
que hubo que corregir.

## 7. Limpieza y reversión

**Ya hecho:**

- todas las BDs `proxima_test_pilot_*` y `proxima_test_obs_cors_p2_r1*` borradas;
- procesos del piloto parados.

**Queda (lo decides tú):**

- `~/Developer/sandbox/rein-pilot/` (fixtures, oráculos, ejecuciones y pruebas del sandbox);
- worktrees de Orca `~/orca/workspaces/pilot-*`;
- los **3 repos `pilot-*` registrados en Orca** (`pilot-widget-a-r1`, `pilot-widget-a-r2`,
  `pilot-obs-cors-a-r1`). El CLI no tiene `repo remove`: se quitan desde la UI de Orca;
- las BDs `proxima_test_ai_support_p0_obs_cors*`, anteriores al piloto pero con el esquema recreado
  por A;
- las sesiones en `~/.claude/projects/*rein-pilot*` y `~/.codex/sessions`.

**Nada de REIN ni de tu configuración global se modificó.**

> **Corrección del 2026-10-29: tests ocultos de widget.** Al portarlos a la criba se encontraron más
> problemas de validez:
>
> - polling atado a `setInterval`;
> - un test de parada vacuo;
> - un estado inventado por el final histórico;
> - `SupportApi` mockeado.
>
> Los veredictos del smoke en widget, incluido «A reintrodujo la carrera», quedan como
> **ambiguos** hasta repuntuarlos con los tests nuevos (`screen/widget/task/`). Las cifras de
> coste y tiempo no cambian.
