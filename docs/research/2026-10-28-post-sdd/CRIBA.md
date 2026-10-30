# Criba de dificultad con N0: diseño pre-registrado

Fecha: 2026-10-29 · REIN fijado en `80b1732` · Antecedentes: [RESULTADOS-SMOKE.md](RESULTADOS-SMOKE.md), §6.

> **Estado:** diseño y candidatos listos; fase 0 de la criba (fixtures y baselines) en curso; **ninguna
> ejecución de agente lanzada.**
>
> Este documento se **congela** (con su sha256 en `pilot/manifest-criba.json`) antes de la primera
> ejecución de N0. Cualquier cambio posterior va a §11 *Desviaciones*, con fecha y motivo, y los
> resultados se publican con el criterio original y con el corregido.

## 1. Para qué sirve la criba

El smoke tuvo **efecto techo**: Claude Code nativo (N0) resolvió las dos tareas, así que ni los
gates ni la auditoría ni la orquestación tenían margen para demostrar nada. La criba responde a una
sola pregunta:

> **¿En qué tareas reales falla o queda al límite N0, y en cuáles acierta con holgura?**

Con eso se elige el conjunto de la fase 2: tareas donde una pieza del harness *puede* aportar valor,
más controles donde N0 acierta, para medir lo que esa pieza **cuesta** cuando no hace falta.

La criba **no compara brazos**: solo corre N0. No dice nada todavía sobre REIN.

## 2. Candidatos: 13 tareas reales con su evidencia de dificultad

### 2.1 De dónde salen

- **Universo:** las 77 ejecuciones de `rein ship` aprobadas.
- **Requisitos:**
  - el commit base es recuperable;
  - el plan (`tasks.md`) está en git, porque hace falta para P2 y A.
- **Señales de dificultad a priori**, calculadas con `bench/screen_signals.py`:
  1. **BLOCKING histórico**, mejor si el fix trae un **test testigo**. Señala defectos que la
     auditoría *sí* vio.
  2. **Defecto escapado:** un `fix` del dueño, posterior o durante la ejecución, que toca los
     archivos del cambio y arregla algo que el cambio debía cubrir. Revisado a mano commit por
     commit; el recuento automático tiene ruido.
  3. **Clase de riesgo:** concurrencia, sincronización offline, contrato entre repos, OAuth.

**Sesgo de selección declarado:**

- Los candidatos con BLOCKING favorecen a la auditoría (allí ya encontró algo).
- Los que tienen defectos escapados la desfavorecen (allí no lo encontró).
- Hay de los dos tipos, y los resultados se publican separados por tipo.

### 2.2 Lista

Detalle por candidato (base y evidencia) en `pilot/screen.json`.

| id | change | repos (stack) | tareas del plan | prior | Evidencia principal |
|---|---|---|---:|---|---|
| client-errors | obs-client-error-reasons | api (py+pg) | 1 | alta | 3 BLOCKING, **los 3 corregidos dentro del run** (claves del cliente sin redactar, 4xx directos sin motivo, 413 en streaming duplicado); 0 escapes |
| pos-availability | availability-p1-pos | pos (Angular) | 1 | alta | 2 BLOCKING (en el store Rust de escritorio, sin test posible con `ng test`); 2 escapes de sync offline (filas sin el campo nuevo, catálogo vacío durante el snapshot) |
| storefront-tolerance | availability-p1-storefront-tolerance | storefront (Astro/TS) | 1 | alta | 3 BLOCKING corregidos en el run; el «escape» de líneas *scheduled/confirm* topadas a stock 0 probablemente vino de commits posteriores (por decidir) |
| mcp-ops-core | mcp-ops-core | api | 2 | alta | BLOCKING con testigo: carrera (comprobación de staleness fuera de la transacción) y herramientas listadas sin mirar scopes; ambos corregidos en el run |
| new-errors | support-v3-new-errors | api + rag-api | 2 | alta | 4 BLOCKING; carrera: dos posts del mismo fingerprint crean dos issues (corregida en el run); 1 escape en rag-api (atributo de estado de SigNoz); contrato entre repos |
| cpe-admin | cpe-delivery-p0-admin | admin (Angular) | 2 | alta | 2 BLOCKING corregidos en el run (canales por índice en vez de id, envío sin receptor aceptado); 0 escapes |
| sale-note-pos | sale-note-pos | pos | 2 | media | 0 BLOCKING, pero el dueño arregló una nota offline mal etiquetada que la auditoría no vio |
| widget | support-v3-widget | auth (Vite/TS) | 1 | media | Smoke: tests ocultos ya validados; carrera real; N0 1/1 bien, A la reintrodujo |
| rag-agent | support-v3-agent | rag-api (py) | 2 | media | 3 BLOCKING (1 testigo) corregidos en el run; 2 escapes: todo turno real devolvía 502, atributo de SigNoz equivocado |
| oauth-public | mcp-oauth-public-clients | api | 2 | media | 1 BLOCKING; OAuth (redirect URIs, RFC 9207) |
| obs-cors | ai-support-p0-obs-cors | api | 1 | baja | Smoke: tests ya validados; todos los brazos bien |
| cpe-pos | cpe-delivery-p0-pos | pos | 2 | baja | 0 BLOCKING, 0 rondas de auditoría |
| cpe-storefront | cpe-delivery-p0-storefront | storefront | 1 | baja | 0 BLOCKING, 0 rondas de auditoría |

**Corrección del 2026-10-29, antes de cualquier ejecución.** La evidencia y 5 de las 13 bases se
auditaron commit a commit (`screen/BASES-AUDIT.md`).

- *Bases.* client-errors, pos-availability, cpe-admin, rag-agent y new-errors (rag-api) tenían
  como base el propio primer commit de la implementación. El minado tomaba el `launch_head` de la
  tarea T001, y REIN lo sobrescribe cada vez que reintenta la tarea. Ya están corregidas, y sus
  fixtures y baselines se reconstruyeron.
- *Evidencia.* Varios «escapes» eran en realidad correcciones de la propia auditoría dentro del run.
  La tabla de arriba ya está corregida.
- *Efecto sobre el sesgo de §2.1.* Con defectos realmente escapados quedan pos-availability,
  sale-note-pos, rag-agent y new-errors (rag-api). Con BLOCKING corregidos dentro del run, la
  mayoría.

**Mezcla:**

- prior: 6 alta, 4 media, 3 baja;
- stacks: 5 Python y 8 TypeScript; 1 tarea entre dos repos;
- 7 tareas tienen plan de ≥2 tareas, lo que hace falta para medir P2 frente a A.

### 2.3 Exclusión, decidida antes de ver a N0

Un candidato sale de la criba, y se anota el motivo, si cumple cualquiera de estas condiciones:

1. su suite base no queda **verde 2/2** en el entorno piloto (§3) y no se puede arreglar el entorno
   sin tocar el código del repo;
2. no se logra un test oculto **F** válido (§4) para al menos el 70% de sus criterios de
   aceptación;
3. la tarea necesita servicios externos reales (SUNAT, Linear, LLM) que no se pueden simular de
   la misma forma para todos los brazos.

Las sustituciones salen de la lista de `bench/screen_signals.py`, con el mismo prior y el mismo stack.

## 3. Baseline limpio y entorno equivalente

En el smoke, los 10 tests de proxima-api que fallaban en la base **no eran del repo, eran de mi
entorno**: un `.env.local` copiado del ejemplo y sin las variables `POSTGRES_*` que pone el CI.
Con el entorno del CI pasan los 27 tests de esos archivos.

| Regla | Cómo se cumple |
|---|---|
| Mismo entorno que el CI del repo | Las variables y los comandos salen de `.github/workflows/ci.yml` y de `mise.toml`: proxima-api sin `.env.local`, con `POSTGRES_*`, `SECRET_KEY` y `ENVIRONMENT=test`, y la suite unitaria **en serie** como en CI (en paralelo fallan 5 tests, según el propio CI) |
| BD aislada | Contenedor propio, `rein-pilot-pg` (pgvector pg16 en tmpfs, puerto 56543, igual que el CI), y una BD por ejecución vía `PROXIMA_TEST_DB`. **Ya no se toca `proxima-suite-db`**, tu BD local |
| Dependencias de la base | `uv sync --frozen`, `pnpm install --frozen-lockfile` o `npm ci` sobre el lockfile **del commit base**, no del HEAD actual (en el smoke se copió `node_modules` del HEAD) |
| Sin historia futura | El fixture es un clon con solo `develop` en la base, reflog expirado y `gc --prune`; `git rev-list --all --not <base>` debe quedar vacío |
| Reproducible | Suite completa **dos veces** en una copia limpia. Verde 2/2 o el candidato sale (§2.3). Un test que cambia entre corridas se **pone en cuarentena para todos los brazos por igual** y se anota |

El estado de los baselines está en §12. Se rellena con `pilot/screen.py report`.

## 4. Tests ocultos: comportamiento, no implementación

### 4.1 Tipos

| Tipo | De dónde sale | Qué prueba |
|---|---|---|
| **F**, funcional | Cada criterio de aceptación («Done when») del plan | Lo pedido, por su interfaz pública: HTTP, componente renderizado por rol o `data-testid` ya existentes, comandos o eventos |
| **D-spec**, defecto | Un BLOCKING histórico o un defecto escapado **que viola un criterio de la intención** | Que el defecto concreto no esté |
| **D-impl**, defecto implícito | Un defecto escapado que viola una propiedad que cualquier senior daría por supuesta: no perder datos, no duplicar efectos con concurrencia, no romper el flujo principal | Ídem, pero **puntúa aparte** |
| (excluido) | Un defecto cuya «corrección» fue una preferencia posterior del dueño que no se deduce de la intención | Solo se informa |

La clase de cada test (F, D-spec, D-impl) se decide y se anota **antes** de correr N0.

### 4.2 Reglas de admisibilidad

1. **Solo interfaces que el brazo puede conocer:** las de la base, o las que la intención nombra.
   Si un test necesita un nombre que no está en ninguna de las dos, ese nombre se añade a la
   intención **para todos los brazos**, o el test se reescribe, o se descarta.
2. **Sin acoplarse a una implementación:** nada de mocks de funciones internas, ni de orden de
   llamadas, ni de textos que la intención no fija. El smoke enseñó las dos formas de fallar:
   un test que pasaba vacío y otro atado a una forma concreta de hacer polling.
3. **Determinista:** relojes falsos solo donde la intención habla de tiempo; nada de red real.

### 4.3 Validación obligatoria, por test, contra versiones conocidas

| Versión | F | D |
|---|---|---|
| Base (sin el cambio) | **falla** (si pasa, es un guardián de regresión y se anota aparte) | sin requisito |
| Final histórico del cambio | **pasa** | **falla** si el defecto escapó; pasa si se corrigió dentro de la ejecución |
| Versión con el defecto (pre-fix) | sin requisito | **falla** |
| Versión corregida (post-fix) | **pasa** | **pasa** |
| Anti-vacuidad: implementación nula o *stub* | **falla** | **falla** |

Cada test se corre **3 veces** sobre cada versión, y debe dar el mismo resultado.

Los tests, la intención y el plan se congelan con su sha256 en el manifest **antes** de la primera
ejecución.

### 4.4 Si un test resulta inválido después

Se corrige solo si el fallo es de validez (vacío, acoplado, no determinista), y se demuestra
contra las versiones de §4.3, nunca contra el resultado de un brazo. Después:

- se re-puntúan **todas** las ejecuciones de esa tarea;
- se anota en §11, con los resultados antes y después.

## 5. Protocolo de la criba

| Parámetro | Valor |
|---|---|
| Brazo | **N0** exactamente como en el smoke: `claude -p` con `claude-sonnet-5-5`, el perfil lean, el mismo prompt de «senior engineer» con Claude Code, sin REIN y sin hooks de gate |
| Contexto | La intención (Why + Scope + **todos** los criterios + las decisiones que los criterios citan) y el repo en la base, con su `CLAUDE.md` y `AGENTS.md` |
| Repeticiones | **2 por tarea**: 13 × 2 = 26 ejecuciones |
| Concurrencia | **Como máximo 2 agentes a la vez**; tests ocultos y suite **en serie**, de una en una |
| Orden | Aleatorio, con semilla fija `20261029`, intercalando tareas para que la hora del día no se confunda con la tarea |
| Aislamiento | Clon desde el fixture; *deny* de lectura sobre productos, Orca, oráculo, fixtures y transcripciones; escaneo de fugas sobre todas las llamadas a herramientas |
| Topes por ejecución | `--max-budget-usd 8`, 60 min de reloj; si salta un tope, la ejecución cuenta como **fallo por tope**, no se repite |

**Lo que *no* se reutiliza:** las ejecuciones de la criba **nunca** entran como datos de N0 en la
fase 2. Si se eligen tareas porque N0 falló una vez, esa misma ejecución está sesgada hacia el
fallo, y es la regresión a la media. En la fase 2, N0 se vuelve a correr desde cero, igual que los
demás brazos.

## 6. Métricas, definidas antes de ejecutar

### 6.1 Resultado (primarias)

- **Correcta:** todos los tests F y D-spec pasan, **0 regresiones confirmadas**, y el
  lint, el typecheck y el build rápido del repo están en verde.
- **Correcta+:** además pasan los D-impl. Se publica al lado de *Correcta*, nunca mezclada.
- **Puntuación parcial:** fracción de tests F y D que pasan. Solo es descriptiva y sirve para
  graduar la dificultad.
- **Regresión confirmada:** un test que estaba verde 2/2 en la base, falla en el árbol del brazo y
  vuelve a fallar en **dos** re-ejecuciones aisladas, en serie y sin carga.
  - **Se excluye** si el archivo de ese test está entre los que el cambio histórico modificó **y**
    un adjudicador ciego confirma que la intención exige ese cambio de comportamiento.
- **Manipulación de tests:** borrar, saltar o debilitar un test existente para que pase cuenta
  como **defecto**, aunque la suite quede verde. Lo juzga un revisor ciego.
- **Cambios fuera de alcance:** archivos tocados que ni la intención ni el plan justifican, como
  los 5 tests ajenos que A modificó en el smoke. Cuentan como señal negativa de calidad y se
  revisan a ciegas.

### 6.2 Tiempo

| Métrica | Definición |
|---|---|
| Reloj | Del arranque del proceso al fin de la sesión (con varias sesiones, la última) |
| Tiempo hasta correcta | El reloj si el árbol final es *Correcta*; si no, censurado. Se publica como tasa de éxito y mediana del reloj **de las correctas** |
| Activo | Suma de los turnos del modelo más las herramientas en ejecución |
| Ocioso | Huecos de más de 60 s sin modelo ni herramienta activos; en N0 debería ser ~0 |

El tiempo de la máquina (gates, suites) se cuenta dentro del reloj, porque es tiempo que el dueño
espera.

### 6.3 Coste

| Métrica | Fuente | Nota |
|---|---|---|
| Tokens Claude por modelo: input, output, *cache write* 5 min y 1 h, *cache read* | Transcripciones `~/.claude/projects` de la ejecución | Brutos, siempre publicados |
| **Equivalencia API (Claude)** | Precios congelados en la tabla de abajo | Una comparación de volumen de cómputo, no una factura |
| Tokens Codex: input, *cached*, output | `~/.codex/sessions` | Sin precio API documentado en el skill: **no se convierte a $**, se publican los tokens |
| **Suscripción** | `orca account list`: % semanal y de la ventana de 5 h de Claude y Codex, antes y después | Granularidad del 1%; solo sirve para el total del lote, nunca por ejecución |
| **Tiempo humano** | Minutos de intervención durante la ejecución (N0: 0 por diseño) **y**, aparte, horas de preparar y adjudicar | No se mezclan: lo primero es coste del brazo, lo segundo coste del experimento |

Precios congelados, en $/MTok y según el skill claude-api, caché del 2026-10-06:

| Modelo | Input | Output | Cache read | Cache write |
|---|---:|---:|---:|---|
| Sonnet 5.5 | 2 | 10 | 0,20 | ×1,25 del input (5 min), ×2 (1 h) |
| Opus 5.5 | 4 | 20 | 0,20 | ídem |

### 6.4 Lo que **no** se usa como medida de calidad

El número de auditorías, rondas de fix, gates ejecutados, tests escritos por el agente o líneas de
test **solo se describe**. Ninguno entra en *Correcta* ni en la calidad. Que un brazo trabaje más
no prueba que el resultado sea mejor: solo lo prueban los tests ocultos, las regresiones y la
revisión ciega.

## 7. Clasificación de dificultad (resultado de la criba)

| Clase | Regla (N0, 2 ejecuciones) |
|---|---|
| **Dura** | 0/2 *Correcta* |
| **Límite** | 1/2 *Correcta*, o 2/2 *Correcta* pero alguna con 0/1 en D-impl |
| **Fácil** | 2/2 *Correcta+* |
| **Inválida** | Algún test resultó inválido según §4.4 y no se pudo arreglar: la tarea sale |

Dentro de cada clase se ordena por la puntuación parcial media, de peor a mejor.

## 8. Regla de selección para la fase 2, decidida ahora

1. **Objetivo: 8 tareas.**
2. Entran **todas las duras y límite**, hasta 6. Si hay más de 6, se eligen por diversidad: un máximo
   de 2 por repo, luego al menos 2 de las que tienen plan de ≥2 tareas, y luego las de menor
   puntuación parcial.
3. Entran **como mínimo 2 fáciles**, y hasta completar 8. Se eligen al azar con la semilla
   `20261029` entre las fáciles válidas. Son el control de «coste cuando no hace falta».
4. **Si hay menos de 3 duras o límite:** ese resultado **es un hallazgo**, porque N0 resuelve la
   mayoría de las tareas reales de este tamaño. La fase 2 se reduce entonces a medir el
   **sobrecoste** de cada pieza en 4 tareas, y la conclusión por defecto es simplificar.

## 9. Lo que cambia en la fase 2, también pre-registrado

### 9.1 Brazos y repeticiones

- **Brazos:** N0, N1, N2 y A en todas las tareas; P2 solo donde el plan tiene ≥2 tareas.
- **Repeticiones:** 2 por celda. Unas 8 × 4,5 × 2 ≈ 72 ejecuciones, como máximo.

### 9.2 Prerrequisitos de A

- **Guard:** debe estar resuelto antes de correr A. El diseño `confined` de
  [GUARD-AISLAMIENTO.md](GUARD-AISLAMIENTO.md) tiene que estar implementado en REIN (un cambio
  aparte que tienes que aprobar), y **REIN se fija en ese commit** para toda la fase 2.
- **Sin guard resuelto, no corre A.** Los brazos N y P2 no dependen del guard.

### 9.3 Adjudicación ciega

Cada fallo oculto, regresión, cambio fuera de alcance o manipulación de tests lo juzga un revisor
que no ve la etiqueta del brazo. Los árboles se renombran con un id aleatorio. Las ambigüedades se
resuelven **por tarea**, nunca por brazo.

### 9.4 Reglas de decisión, con n pequeño y sin p-valores de adorno

Cada peldaño se compara con el anterior: N1 frente a N0 (gates), N2 frente a N1 (auditoría),
P2 frente a N2 (plan) y A frente a P2 o N2 (orquestación).

| Veredicto | Cuándo |
|---|---|
| **Aporta valor** | Suma **≥2 ejecuciones *Correcta* más** en las tareas duras y límite, **y** no pierde ninguna en las fáciles |
| **Solo sobrecoste** | Sin ganancia de *Correcta*, **y** la mediana del reloj o de la equivalencia API sube ≥25% |
| **Ambiguo** | Cualquier otro caso. Se publica así, sin redondear hacia ningún lado |

**Confirmado frente a ambiguo:** un resultado es *confirmado* solo si se repite en las 2
repeticiones o en ≥2 tareas distintas. Un caso aislado se publica como *observación*.

**Lo que este n no puede ver:** con unas 16 ejecuciones por brazo, una diferencia menor que unos
3–4 aciertos sobre 16 no se distingue del azar. Se dirá explícitamente.

**Si REIN no supera a N0 en un tipo de tarea, la recomendación será simplificar** para ese tipo, no
buscar otra métrica que le favorezca.

## 10. Presupuesto y parada

| Límite | Valor | Acción |
|---|---|---|
| Por ejecución | $8 eq. API de Claude, 60 min | Corte; cuenta como fallo por tope |
| Total de la criba | **$120 eq. API** (previsto: 26 × $2–4 ≈ $50–100) | Parar y avisarte |
| Ventana de 5 h de Claude | ≥70% | Pausar hasta que se reinicie |
| Semanal de Claude | ≥60% (hoy: **35%**) | Parar y avisarte. Esta conversación de diseño usa la misma suscripción |
| Semanal de Codex | Sin uso en la criba (hoy: 18%) | — |
| Parada manual | Crear el archivo `~/Developer/sandbox/rein-pilot/STOP` | El lanzador no arranca nada más; las 2 ejecuciones en curso terminan o se cortan por su tope |
| Problema metodológico | Cualquier fuga, test inválido o entorno roto | **Parar la criba**, corregir, anotar en §11, y repetir lo afectado |

## 11. Desviaciones

*(Vacío. Cada entrada lleva fecha, qué cambió, por qué y qué resultados afecta.)*

## 12. Estado de la fase 0 de la criba

Cerrada el 2026-10-29, antes de cualquier ejecución de agente. Los 13 fixtures están construidos y
los 13 candidatos siguen en la criba; no hubo que sustituir ninguno.

| Candidato | Repo | Suite base (2 corridas) | Tests | Gate |
|---|---|---|---|---|
| client-errors | proxima-api | base corregida (d6aa26f3): 1 verde + 1 fallo intermitente real, en cuarentena (nota 4) | ~13.400 | verde |
| mcp-ops-core | proxima-api | 1 verde + ruido | ~13.300 | verde |
| new-errors | proxima-api + proxima-rag-api | api 1 verde + ruido; rag con base corregida (2b87907c), verde 2/2 | ~13.500 + 2.168 | verde |
| oauth-public | proxima-api | 1 verde + ruido | ~13.300 | verde |
| obs-cors | proxima-api | 1 verde + ruido (también con SSL desactivado) | ~13.100 | verde |
| pos-availability | proxima-pos | base corregida (6f7de61): verde 2/2 | — | verde |
| sale-note-pos | proxima-pos | verde 2/2 | 553 | verde |
| cpe-pos | proxima-pos | verde 2/2 | 574 | verde |
| cpe-admin | proxima-admin | base corregida (a82375bc): verde 2/2 | — | verde |
| storefront-tolerance | proxima-storefront-v2 | verde 2/2 | 5.110 | rojo preexistente, ver nota 3 |
| cpe-storefront | proxima-storefront-v2 | verde 2/2 | 5.237 | verde |
| widget | proxima-auth | verde 2/2 | 234 | verde |
| rag-agent | proxima-rag-api | base corregida (667747ae): verde 2/2 | — | verde |

**Regla de las stacks.** Para cada repo, la referencia es su propia validación de CI en el commit
base (`mise run ci` / `ci.yml`), separada en dos partes:

- la *suite* de tests, que se corre dos veces;
- el *gate* de lint, typecheck y build, que se corre una vez.

Ambas usan las variables de entorno de CI. Configuración en `pilot/screen.json`.

**Nota 1 — ruido de conexión en proxima-api.** En 6 de las 12 corridas de proxima-api falló
exactamente 1 test de unas 13.000. Fue un test distinto cada vez, por un error de conexión con el
Postgres de Docker Desktop. Primero salió como `rejected SSL upgrade`; con `PGSSLMODE=disable`
salió como `TimeoutError`. El servidor no registra nada.

- *Decisión, tomada antes de cualquier ejecución:* es ruido del entorno, no un defecto del repo.
  Un test cuenta como regresión solo si falla también en 2 reejecuciones aisladas (§6), y este
  fallo no se repite.
- *Coste:* habrá alguna reejecución aislada de más por run. Los 5 candidatos de proxima-api se
  mantienen.

**Nota 2 — código generado.** Los clientes de API generados no están versionados:

- storefront los genera con `gen:api`;
- admin los genera con `orval`, desde su hook `pretest`.

Ambos parten del `contracts/openapi.json` que está dentro del propio repo, así que el setup ejecuta
esa misma generación. Sin ella, los tests y el typecheck fallaban.

**Nota 3 — gate rojo preexistente en storefront-tolerance.** `prettier --check` falla en el commit
base por un único archivo versionado, `flow.config.json`.

- *Regla para este candidato, decidida antes de ejecutar:* el gate cuenta como superado si
  `format:check` solo marca archivos que ya fallaban en el baseline y que la ejecución no tocó.
- El resto del gate (lint, typecheck y build) se exige en verde.
- Se aplica igual a todos los brazos.

**Nota 4 — cuarentena.** Con la base corregida, client-errors dio un fallo real pero intermitente
en 1 de 2 corridas:
`tests/unit/inventory/test_lot_model.py::test_switching_off_needs_every_unit_back_in_the_unassigned_lot`
(`DID NOT RAISE`).

- No tiene relación con la tarea.
- Queda en cuarentena para todos los brazos (§3); está anotado en `screen.json`, bajo
  `quarantine`.

### 12.1 Paquetes de tests ocultos (2026-10-29)

Los 13 están construidos y validados con `pilot/screen.py validate`: cada test se corre 3 veces en
cada versión y se comprueban automáticamente las reglas de §4.3. Cada paquete vive en
`screen/<cid>/task/`, con estos archivos:

- `plan.md` y `intent.md`;
- `hidden/`;
- `hidden.json` (versiones y clase de cada test);
- `validation.json`;
- `NOTES.md` (mapa de criterio a test, defectos y ambigüedades).

Además de §4.3, los paquetes de las últimas tareas pasaron dos comprobaciones manuales:
implementaciones alternativas correctas que deben pasar y versiones mutadas que deben fallar.

| Candidato | F | guardianes | D-spec | D-impl | Defectos (en el run / escapados) | Líneas en `intent_extra` |
|---|---:|---:|---:|---:|---|---:|
| obs-cors | 4 | 13 | 0 | 0 | — | 0 |
| client-errors | 11 | 0 | 6 | 0 | 3 / 0 | 0 |
| pos-availability | 6 | 3 | 1 | 1 | 0 testeables / 2 (los 2 BLOCKING Rust, sin runner) | 0 |
| storefront-tolerance | 14 | 1 | 3 | 0 | 3 / 0 (e3c2320 excluido: no es de este cambio) | 0 |
| oauth-public | 12 | 6 | 0 | 0 | BLOCKING excluido (ubicación de tests, no comportamiento) | 0 |
| sale-note-pos | 12 | 4 | 1 | 0 | 0 / 1 | 0 |
| cpe-admin | 22 | 5 | 3 | 0 | 2 / 0 (49f1f19d excluido: convención) | 2 |
| widget | 15 | 3 | 1 | 0 | 1 / 0 | 0 |
| cpe-pos | 14 | 1 | 0 | 0 | — | 0 |
| cpe-storefront | 7 | 2 | 0 | 0 | — (fix de gate sin efecto visible) | 2 |
| mcp-ops-core | 19 | 2 | 1 | 0 | 1 / 0 (la carrera no es testeable: el fix era inerte) | 0 |
| rag-agent | 23 | 0 | 2 | 2 | 3 / 0 (2 escapes excluidos: dependen del entorno real) | 4 |
| new-errors | 17 | 0 | 2 | 1 | 3 / 0 (a32e4c3 excluido: hecho del SigNoz real) | 2 |

Las cifras son casos de test. «Guardianes» son los F que ya pasaban en la base: se exigen, pero no
discriminan.

**Hallazgos de validez corregidos antes de ejecutar:**

- *obs-cors:* con el entorno de CI, 6 tests se saltaban. Se fija `CORS_ALLOW_ORIGINS` en el comando
  del test oculto.
- *widget:* los tests del smoke tenían más fallos:
  - polling atado a `setInterval`;
  - dos tests vacuos;
  - un estado inventado por el final histórico;
  - `SupportApi` mockeado.

  Se reescribieron. **Esto afecta a la lectura del smoke en widget** (anotado en
  RESULTADOS-SMOKE).
- *Bases:* ver la corrección de §2.2.

**Herramienta:**

- reintentos de `uv venv` en rag-api;
- hashes de validación por `repo/archivo`;
- puntuación multi-repo con copias hermanas;
- cuarentena;
- `freeze` y `verify_frozen`: el lanzador se niega a correr si algo cambió tras congelar.

La puntuación se probó de punta a punta inyectando el final histórico como si fuera el resultado
de un brazo:

- obs-cors sale *Correcta+*;
- new-errors (dos repos) sale *Correcta+*. Su suite de proxima-api tuvo un fallo
  (`test_triage::test_marking_a_duplicate…`) que pasó en las 2 reejecuciones aisladas, así que no
  cuenta como regresión: la regla funciona.

**Revalidación independiente (`screen/revalidate.log`):** repetí `validate` de los 13 paquetes
desde cero.

- 12 salen `OK`.
- mcp-ops-core sale con un único problema: `test_c07_the_batch_and_grant_spec_files_exist_and_pass`
  dio pasa/falla/pasa en `final`. El fallo fue un `TimeoutError` al abrir la conexión a Postgres
  dentro del spec que el test ejecuta. Es el mismo ruido de conexión de la nota 1 de §12, no un
  problema de lógica del test. Con la regla de la decisión 5 queda cubierto.
- mcp-ops-core se revalida de todos modos tras la decisión 1.

### 12.2 Decisiones del dueño (antes de congelar)

El dueño aprobó las 7 recomendaciones el 2026-10-29. La tabla de §12.1 ya refleja los cambios en
mcp-ops-core y cpe-storefront.

| # | Tarea | Pregunta | Recomendación |
|---|---|---|---|
| 1 | mcp-ops-core | `intent_extra` da el diseño histórico (`BatchExecutor` y sus hooks) para poder probar los criterios 12 y 13 | **Quitar esa línea y sus 8 casos.** Regalar el diseño abarata la tarea; la cobertura sigue por encima del 70% |
| 2 | rag-agent | Los brazos no reciben `eval-cases.md`, así que los criterios 7 y 8 no tienen test (cobertura de 7/9) | **Aceptar así.** Los criterios 7 y 8 pasan a revisión ciega |
| 3 | cpe-storefront | El criterio 1 pide copiar el contrato desde proxima-api, que ningún brazo tiene | **Añadir a `intent_extra`, para todos:** «proxima-api no está disponible: añade el campo al contrato a mano» |
| 4 | oauth-public | Los criterios 1 y 9 exigen los specs en `tests/integration/…`; el repo los pone en `tests/unit` | **Aceptar las dos ubicaciones** |
| 5 | varios | Tests ocultos que ejecutan los specs del propio brazo (client-errors, oauth-public, mcp-ops-core, rag-agent, new-errors) pueden fallar por un spec inestable del brazo | **Regla:** un test oculto que falla se reejecuta 2 veces y solo cuenta como fallo si falla las 3. Igual para todos los brazos |
| 6 | varios | Clasificaciones dudosas entre D-spec y D-impl (detalle en cada `NOTES.md`) | **Mantener la clasificación de cada paquete**, que se decidió antes de ver resultados |
| 7 | pos-availability | Los 2 BLOCKING del store Rust no tienen test, porque no hay runner de cargo | **Aceptar.** Se anota como límite |

**Cómo se aplicaron:**

1. **mcp-ops-core.** Se quitó la línea de `intent_extra` y los 8 casos que la necesitaban: c12 ×6,
   c13 y el caso de `grant` de c14. Quedan 19 F, 2 guardianes y 1 D-spec, sin `intent_extra`. Los
   criterios 12 y 13 pasan a revisión ciega.
2. **rag-agent.** Los criterios 7 y 8 pasan a revisión ciega. No hubo cambios en el paquete.
3. **cpe-storefront.** `intent_extra` añade, para todos los brazos: «proxima-api is not available in
   this workspace: … add `invoice` and `OrderTrackingInvoiceRead` to `contracts/openapi.json` by
   hand, with the shape above.»
4. **oauth-public.** c1 y c9 aceptan el spec en `tests/integration/mcp/` o en `tests/unit/mcp/`.
   Basta con uno, y todos los que existan deben pasar.
5. **`screen_run.py score`.** Si algún test oculto no pasa, se reejecuta el archivo oculto entero,
   como máximo 2 veces más. Un test cuenta como fallo solo si no pasó en ninguna de las 3. Cada
   ejecución queda en `hidden_runs` del `score.json`. La regla se comprobó con una simulación de 5
   casos: pasa a la primera, falla 1 vez, falla 2 veces, falla 3 de 3 y caída sin resultados.
6. **Clasificación D-spec / D-impl.** Se mantiene la de cada paquete.
7. **pos-availability.** Sin cambios. Queda anotado como límite.

**Regla de ruido en la validación (consecuencia de la decisión 5).** En la revalidación, c07 de
mcp-ops-core volvió a dar pasa/pasa/falla, esta vez en `prefix`. Fue otra vez un `TimeoutError` de
conexión dentro del spec que ejecuta. `screen.py validate` ahora acepta una diferencia entre
ejecuciones como ruido, y no como test no determinista, solo si se cumple todo esto:

- se esperaba «pasa»;
- el test pasó en al menos una ejecución;
- en cada ejecución fallida, el fallo es ruido de conexión (`TimeoutError` o «rejected SSL
  upgrade»). Si el test ejecuta specs, *todos* los specs fallidos deben haber fallado por eso.

Un test que debe fallar y pasa aunque sea una vez sigue siendo un problema. Los casos de ruido se
listan en `validation.json` → `noise`. Se comprobó contra los XML de las 13 validaciones: solo
encaja el caso de c07. Los de new-errors en `prefix:contract` fallan siempre, así que la regla no
los toca.

En cada paquete se guardaron las versiones anteriores como `*.before-d1`, `*.before-d3` y
`*.before-d4`. Los criterios en revisión ciega (mcp-ops-core 12–13 y rag-agent 7–8) no cuentan
para *Correcta* en la criba. Se informan aparte.

## 13. Antes de lanzar la criba

1. ~~Baselines verdes 2/2, o candidatos sustituidos (§2.3, §3).~~ Hecho: ver §12.
2. ~~Por candidato: intención, tests ocultos F y D clasificados, validación de §4.3 superada.~~
   Hecho: ver §12.1 y §12.2. Los 13 `validation.json` están en `ok` y coinciden con los tests
   actuales.
3. ~~El lanzador: orden aleatorio con semilla, 2 a la vez, chequeo de cuota antes de cada arranque
   y archivo STOP.~~ Hecho: `pilot/screen_run.py`, probado de punta a punta en obs-cors y new-errors.
4. ~~Congelar este documento y los paquetes.~~ Hecho el 2026-10-29: `pilot/manifest-criba.json`
   guarda el sha256 de este documento, del código (`screen.json`, `screen.py`, `screen_run.py`,
   `harness.py`, `intents.py`) y de cada archivo de los 13 paquetes. `screen_run.py launch` se
   niega a correr si algo cambió. Cualquier cambio posterior exige descongelar a la vista: borrar
   el manifest, anotarlo aquí y volver a congelar.
5. ~~Decisiones del dueño sobre los paquetes~~ (§12.2, aprobadas el 2026-10-29).
6. **Pendiente: tu orden de lanzar las 26 ejecuciones de N0.** Comando:
   `python3 pilot/screen_run.py launch`. Se detiene con
   `touch ~/Developer/sandbox/rein-pilot/STOP`.

## 14. Parada metodológica del 2026-10-29 (22:21)

La criba arrancó a las 21:53 y se detuvo sola a las 22:21, tras 3 ejecuciones, por una de sus
condiciones de parada (fuga detectada). Al revisarla aparecieron tres problemas. Ninguno era del
agente. Los tres se corrigieron antes de seguir, como pide el protocolo.

1. **Fuga falsa en `mcp-ops-core-n0-r1`.** El agente ejecutó `ls openspec/changes/archive | grep -i
   mcp` dentro de su propio clon. El patrón genérico `openspec/changes/archive` de `harness.py` lo
   marcó como fuga.
   - El clon termina en la base y el lanzador comprueba que no tenga commits futuros. Su archivo
     solo contiene cambios anteriores, historia que cualquier desarrollador tiene. Allí solo había
     `mcp-oauth` y `mcp-oauth-public-clients`, y el agente no abrió ningún archivo.
   - Se comprobó en los 13 candidatos y en sus 14 repos: ninguna base contiene su propio cambio en
     `openspec/changes`, ni en curso ni archivado.
   - **Corrección:** en la criba, el archivo solo cuenta como fuga si la ruta nombra el cambio de la
     ejecución (`forbidden_rx` en `screen_run.py`). `harness.py` y los demás patrones no cambian.
2. **Error del puntuador en las reejecuciones.**
   - Un test dentro de una clase (`…test_sale_note_document.TestTheDocument…`) se convertía en una
     ruta inexistente, y pytest abortaba la reejecución entera sin ejecutar nada.
   - Un test ausente de la reejecución contaba como «falla otra vez». Así, los 13 fallos de
     `oauth-public-n0-r1` quedaron como regresiones confirmadas sin haberse reejecutado.
   - **Corrección:** el módulo se resuelve buscando el archivo real y la clase se añade al node id.
     Una reejecución que no recoge ningún test ahora es un error de puntuación, nunca una
     confirmación.
3. **Postgres sin memoria de locks.** Esos 13 fallos eran `out of shared memory` (*HINT: increase
   max_locks_per_transaction*). Ocurrían en el `TRUNCATE` de unas 300 tablas mientras los dos
   agentes y el puntuador usaban a la vez el mismo contenedor, con el valor por defecto de 64.
   - **Corrección:** `rein-pilot-pg` se recreó con la misma imagen, puerto y tmpfs, y con
     `-c max_locks_per_transaction=1024 -c max_connections=300`. Es capacidad del entorno, no
     cambia la semántica de ningún test.

**Las 3 ejecuciones ya hechas siguen siendo válidas.** Ninguno de los problemas afectaba al trabajo
del agente:

| Ejecución | Minutos | Coste eq. API |
|---|---:|---:|
| `oauth-public-n0-r1` | 10,8 | $1,91 |
| `mcp-ops-core-n0-r1` | 17,6 | $5,45 |
| `client-errors-n0-r2` | 29,1 | $2,68 |

Se recalcularon sus métricas con la regla nueva (0 fugas) y se volvieron a puntuar desde cero. La
primera puntuación quedó en `meta/score-v1/` y `score-v1/`.

**Recongelación.** Se borró `pilot/manifest-criba.json` y se volvió a congelar con este documento y
`screen_run.py` corregidos. Los paquetes de tests no cambiaron.

## 15. Segunda parada metodológica (2026-10-29, 23:48): aislamiento por sistema operativo

La criba se reanudó a las 22:52 y se detuvo sola a las 23:48 por una fuga en `cpe-pos-n0-r1`. Para
entonces llevaba 18 de 26 ejecuciones.

**Qué pasó.** El intent de cpe-pos menciona proxima-admin. Buscándolo, el agente ejecutó
`cd ~/Developer && grep -rIl "invoicing/.*delivery" .` y obtuvo **nombres** de archivos de los
repos de producción y de `proxima-engineering/.rein`. Después intentó `cat` sobre uno, y la regla
`deny` de Claude Code lo bloqueó.

- Las reglas `deny` de Claude Code bloquean `Read` y comandos con una ruta prohibida explícita.
  No bloquean un `grep` o `find` recursivo lanzado desde la carpeta padre con rutas relativas.
- El escáner de fugas solo buscaba rutas absolutas prohibidas.

**Auditoría de las 18 ejecuciones.** Se revisaron todos los comandos de todas, buscando rutas
absolutas, `~`, `$HOME`, `cd ..`, `ls ../..` y accesos a red o a GitHub.

- Ningún agente leyó contenido de producción, del oráculo ni de otra ejecución.
- Dos vieron nombres de fuera de su clon:
  - `cpe-pos-n0-r1`: nombres de archivos de admin, api y `.rein` con «delivery»;
  - `rag-agent-n0-r1`: el listado de `rein-pilot`, donde se ven `oracle` y `screen`, y nombres de
    worktrees `support-v3-*`.
- Varios hicieron `ls ..` o `ls ../..` y vieron los nombres de las demás ejecuciones, sin entrar.
- Ninguno usó GitHub. Sin embargo, la máquina tiene `gh` autenticado y una llave SSH: un
  `git clone` habría traído commits futuros. Era una vía abierta, aunque no se usó.

**Corrección: sandbox del sistema operativo.** Cada agente corre bajo `sandbox-exec`, con un perfil
por ejecución en `meta/sandbox.sb`. La restricción la aplica el núcleo a todo proceso que lance el
agente.

- **Se deniega lectura y escritura de:**
  - `~/Developer`, salvo su propia ejecución (lectura y escritura) y el stub de ingeniería (solo
    lectura);
  - Orca, Codex, `~/.ssh` y `~/.config/gh`;
  - las demás transcripciones de Claude (`~/.claude/projects`, `file-history`, `plans`, `todos` e
    `history.jsonl`);
  - los temporales de otras sesiones;
  - Documents, Desktop, Downloads y las demás carpetas de proyectos del home.
- **Se deniega escribir en su propio `meta/`.**
- **Excepción:** los dos hooks globales de `~/.claude/settings.json` (el guardrail de QA,
  `block-qa-access.py`, y el hook de Orca) corrieron en todas las ejecuciones anteriores. Cada uno
  puede leer *solo* su propio script, así que el entorno nativo no cambia.
- **Entorno del agente:** sin `SSH_AUTH_SOCK`, `gh` sin sesión, `GIT_CONFIG_NOSYSTEM=1`, un
  `gitconfig` propio sin ayudante de credenciales y `GIT_TERMINAL_PROMPT=0`.

**Verificación antes de relanzar.** Sobre una ejecución de prueba (`_tests/obs-cors-n0-r9`):

- 18 accesos que deben fallar, todos bloqueados: `ls ~/Developer`, `grep -r` y `find` desde el
  padre, `ls ../..`, oráculo, producto vía Python, docs de REIN, otras transcripciones, temporales
  de esta sesión, Orca, Codex, `~/.ssh`, `gh`, git por SSH y por HTTPS, escribir en `meta/`, los
  vecinos del hook y escribir fuera.
- 8 cosas que deben funcionar, todas funcionan: leer su repo, el stub, `git commit`, `uv` con
  pytest y Postgres, su carpeta de transcripción, el hook de QA, `pnpm` y la red a PyPI.
- Dos llamadas mínimas reales a Claude dentro del sandbox ($0,21). Se autentica, su transcripción
  queda donde la busca el lanzador y `ls ~/Developer` devuelve `Operation not permitted`.

**Escáner.** Se registra toda llamada que nombre un patrón prohibido o un directorio denegado. Una
llamada cuyo resultado muestra el bloqueo cuenta como `blocked_attempts`, que se informa pero no
detiene la criba. Una no bloqueada es una fuga y la detiene. Con el escáner nuevo, sobre las 18
ejecuciones, solo `cpe-pos-n0-r1` y `rag-agent-n0-r1` tienen fugas.

**Ejecuciones invalidadas.** `cpe-pos-n0-r1` y `rag-agent-n0-r1` se movieron a
`screen-runs/_invalid/` y se repiten con el sandbox. Por prudencia: solo vieron nombres. Su coste
sigue contando en el presupuesto.

**Test oculto corregido (pos-availability).** Las dos ejecuciones fallaban `zzF search scheduled`.

- El intent pide «A pedido · {ship_by}», y el agente mostraba `A pedido · 2026-11-27`.
- El test buscaba `\b27\b`, y en el `textContent` de la fila la fecha queda pegada al precio
  (`2026-11-27S/ 10.10`). La «S» elimina el límite de palabra.
- El propio comentario del test dice que «2026-11-27» debe pasar: es un fallo de validez que la
  validación no detectó, porque no probó un formato de fecha seguido de texto sin espacio.
- **Corrección:** `(?<!\d)27(?!\d)` en los dos archivos. Sigue rechazando un día corrido y «127».
- El paquete se revalidó, las dos ejecuciones se repuntuaron y la versión anterior quedó como
  `*.before-d8`.
- Se revisaron los demás `\b` de los tests TypeScript. Los que deben coincidir (cpe-admin
  `Emitido`, `qr` y cpe-pos `imprimir`) se aplican a elementos sueltos o ya pasaron en las
  ejecuciones, y los demás son negativos.

**Revisado y no corregido (fallo real):** `new-errors-n0-r1` falla los 12 tests ocultos porque
`python -m app.support.new_errors` revienta con `no such table: reported_errors` sobre una base
nueva.

- El agente crea la tabla solo en el `init_db()` del arranque de la app.
- El intent pide que el job se pueda correr a mano con ese comando.
- En el repo, los módulos que corren solos y usan SQLite (`app/ingest/ingest.py` y
  `scripts/whatsapp_flows.py`) inicializan su base.
- Se cuenta como fallo del agente, de un solo punto. Se anotará así en los resultados.

## 16. Tercera parada (2026-10-30, 00:24): falso positivo del escáner y barrido del disco

Con el sandbox activo, la criba se detuvo por «2 fugas» en la repetición de `rag-agent-n0-r1`:

- **`find / -name "support-v3*"` y `find / -name "eval-cases*"`** sobre todo el disco, con errores
  a `/dev/null`. Pasó a segundo plano. Su salida completa solo contenía el `ls` de su propia
  ejecución: el sandbox no dejó ver nada fuera.
- **`git log --all`.** Lo denegó una regla de Claude Code («has been denied»). Además, el clon no
  tiene commits futuros.

Ninguno trajo nada. El escáner contaba como fuga toda llamada cuyo resultado no mostrara el
bloqueo. Con `2>/dev/null` no hay mensaje de bloqueo, aunque tampoco hay datos.

**Corrección del escáner.** Una llamada que nombra algo prohibido es **fuga** solo si lo que volvió
al agente trae algo de fuera: su resultado y, si pasó a segundo plano, la salida de esa tarea.

- Cuenta como algo de fuera:
  - un patrón prohibido;
  - una ruta absoluta denegada;
  - una ruta relativa reveladora (`products/proxima/`, `proxima-engineering/.rein`,
    `rein-pilot/screen|oracle|fixtures|…`, `orca/workspaces`, `.claude/projects/-…` u otra
    ejecución).
- Las líneas de error de bloqueo no cuentan.
- El resto queda como intento y se informa en `blocked_attempts`.
- Aplicado a las 20 ejecuciones vigentes: 0 fugas. La repetición de `rag-agent-n0-r1` tiene 2
  intentos sin resultado. Las dos ejecuciones de `_invalid/` siguen saliendo como fuga.

**Barrido desde dentro del sandbox.** Se buscaron los marcadores del oráculo (`zz_hidden` y
`zz-hidden`) en todo lo que el agente puede leer.

- Un barrido de todo `~` se cortó a los 26 minutos sin hallazgos.
- Un barrido dirigido cubrió `~/.claude`, `~/.config`, `~/.local/share`, los datos de la app de
  Claude, `~/Library/Logs`, `/private/tmp` y `/private/var/folders`. Encontró los logs del
  vigilante de archivos de Raycast (`~/Library/Logs/com.raycast.macos`), que registran **rutas**
  de archivos modificados, incluidos los nombres de los tests ocultos, pero no su contenido.
- Ningún agente leyó `~/Library/Logs`, según las transcripciones de las 20 ejecuciones.
- `~/Library/Logs` se añadió a lo denegado. También contiene los logs de la app de escritorio de
  Claude, que no usa `claude -p`.
- Repetido el barrido dirigido: sin hallazgos, y `ls ~/Library/Logs` da `Operation not permitted`.

Se recongela y se relanzan las 6 pendientes.

## 17. Postgres sin espacio (2026-10-30, 01:19)

La tanda final terminó las 26 ejecuciones a las 01:39, pero Postgres había caído a las 01:19:55 con
`No space left on device`.

- El contenedor usa un tmpfs de 4 GB, y cada ejecución, validación y puntuación dejaba sus bases.
- Los primeros errores de espacio son del mismo segundo de la caída, así que nada anterior se vio
  afectado. Solo proxima-api usa Postgres.

**Afectadas:**

- **`obs-cors-n0-r1`:** el agente trabajaba de 01:09 a 01:21, y la caída le quitó la base en su
  último minuto y medio. Él mismo lo reporta: «I couldn't get a clean full-suite run on the final
  commit». Su puntuación dio todo en rojo por conexión rechazada. **Se invalida**
  (`_invalid/obs-cors-n0-r1-dbcrash`) y se repite.
- **`new-errors-n0-r2`:** el agente terminó a las 01:14, antes de la caída, así que su trabajo es
  válido. Sus tests ocultos corrieron antes de la caída, pero su suite cayó en ella (`rc=4` sin
  JUnit). Se repuntúa completa; la puntuación anterior queda en `score-v1`.

**Corrección:**

- Postgres se recreó con un tmpfs de 8 GB (Docker tiene 16 GB).
- `score()` ahora borra, al terminar, todas las bases de la ejecución (`drop_run_dbs`): la del
  agente, la del puntuador y las copias de specs. Las bases borradas quedan en
  `score.json` → `dropped_dbs`.

**Revisado y sin cambio:** la regresión de `new-errors-n0-r1`
(`test_the_application_layer_does_not_import_the_api_layer`) es real. El agente importó un esquema
de la capa API desde la capa de aplicación, y el test de arquitectura del repo lo detecta, también
en las 2 reejecuciones aisladas.
