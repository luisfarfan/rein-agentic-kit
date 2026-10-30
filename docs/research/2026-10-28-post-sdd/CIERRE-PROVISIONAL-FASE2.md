# Fase 2: cierre provisional (2026-10-30)

La fase 2 se detiene después de la tanda 1, por decisión del dueño. Este documento resume lo que la
tanda 1 permite concluir y lo que no.

- **Tanda 1:** 3 tareas (widget, mcp-ops-core, new-errors), 4 brazos y **1 ejecución por celda**.
- **Brazos:**
  - N1: el nativo más gates;
  - N2: N1 más una auditoría de Codex y una ronda de corrección;
  - P2: N2 más un plan;
  - A: REIN completo sobre el mismo plan que P2.
- **N0:** viene de la criba de la fase 1.
- **Diseño:** [FASE2.md](FASE2.md).

Ninguna diferencia entre brazos es estadísticamente distinguible del azar. Lo que sigue son
**señales y mecanismos observados**, no efectos medidos.

## 1. Resultado por celda (tests ocultos, con el puntuador congelado)

- **Correcta:** todos los tests F y D-spec pasan, sin regresiones y con el gate en verde.
- **Correcta+:** además pasan los D-impl.

| Tarea | N0 (criba, ×2) | N1 | N2 | P2 (+ plan) | A (+ plan) |
|---|---|---|---|---|---|
| widget | Correcta+ ×2, 4 min | Correcta+, 4 min, $2,2 | Correcta+, 9 min, $2,9 | Correcta+, 8+2 min, $3,0+1,7 | Correcta+, 13+2 min, $4,9+1,7 |
| mcp-ops-core | F ok, falla el D-spec ×2 | igual que N0, 36 min, $6,0 | igual, 39 min, $9,0 | igual (+1 regresión), 21+4 min, $4,9+3,8 | **anulada** (error del arnés) |
| new-errors | incompleta ×2 | incompleta, 17 min, $4,3 | incompleta, 26 min, $6,3 | antes del fix: **Correcta**. Final: ver §2. 54+2 min, $12,7+2,0 | F ok; falla el D-spec de la carrera. 65+2 min, $12,4+2,0 |

**Gasto de la tanda 1:** $82,76 en equivalente API de Claude. Incluye los 3 planes ($7,42) y la A
anulada ($6,65). Codex no tiene precio fiable: van 2–2,5 M de tokens por auditoría, casi todos en
caché.

## 2. Resultados inválidos, contaminados o inconclusos

1. **mcp-ops-core A: inválida.** Mi arnés vació `flow.config.json`, y REIN corrió sin su
   configuración (FASE2 §9, D1). No se repitió.
2. **new-errors P2, resultado final: contaminación de los tests ocultos, no una regresión.**
   - La ronda de corrección añadió una tabla de «claims» en la base de datos con commit explícito.
     Los tests ocultos reutilizan las mismas huellas en cada test y dan por hecho que la base de
     datos vuelve atrás. Desde el segundo test, la API responde «ya existe».
   - Se volvieron a correr con bases de datos nuevas y aisladas:
     - el archivo completo da 10 fallos;
     - **cada test por separado: 9 de los 10 pasan.**
   - El que sigue fallando es `d_queue` (un error se pierde tras un envío fallido; es D-impl). Falla
     3 de 3 veces tanto **antes** como **después** de la corrección.
   - Conclusión:
     - el fix no rompió la funcionalidad;
     - **tampoco corrigió el defecto que la auditoría le pidió corregir**;
     - el estado final, evaluado de forma aislada, es equivalente al de antes: Correcta, no
       Correcta+.
   - Con el protocolo congelado sigue contando como fallo. Se informa de las dos lecturas.
3. **mcp-ops-core N2, ronda de corrección: contaminada.**
   - Un hook de la configuración global de Claude (`guard-prod-and-secrets.py`, en `products/`)
     quedó bloqueado por el sandbox. Ese archivo de configuración se modificó a las 11:03, con la
     tanda en marcha.
   - El implementador se quedó sin shell durante 4 rondas del hook Stop, unos 17 min.
   - El resultado oculto no cambia (igual antes y después), pero su tiempo y su coste están
     inflados.
4. **El gate completo de la API es inestable en este laboratorio.**
   - Con 18 workers de xdist sobre un Postgres compartido y la máquina cargada, aparecen timeouts y
     caídas de SSL. En la línea base, sin cambios, ya fallaba 1 de cada 2 veces.
   - Infla el tiempo de N1 y N2 en mcp-ops-core y el de A en new-errors (§3). **Los tiempos de esas
     celdas no son representativos de tu entorno real.**
5. **`usd_by_phase`** atribuía todo el coste de N2 y P2 a la corrección. Recalculado por marcas de
   tiempo en §3; el total ya era correcto.

## 3. Tiempo útil frente a overhead

Por ejecución, recalculado con las marcas de tiempo de las transcripciones:

| Celda | Implementación | Auditoría (Codex) | Corrección | Overhead de infraestructura del laboratorio |
|---|---|---|---|---|
| mcp-ops-core N1 | 12 min | — | — | **23 min** persiguiendo rojos del gate inestable |
| mcp-ops-core N2 | 18 min | 3 min | 17 min | casi todo el tramo de la corrección (hook bloqueado y gate inestable) |
| new-errors N2 | 16 min, $5,2 | 5 min | 5 min, $1,1 | — |
| new-errors P2 | 14 min, $3,6 | 5 min | **35 min, $9,0** | parte del tramo de corrección: el gate rojo cuenta como BLOCKING |
| new-errors A | unos 12 min (T001–T004) | 2 auditorías | unos 5 min | **unos 39 min** (GATE1 ×2 y GATE2) |
| widget N2 / P2 / A | 5 / 5 / unos 8 min | 2–3 min | menos de 1 min | — |

**Lo que estas cifras muestran:**

- **new-errors A:** de sus 65 min, unos 39 se fueron en workers que «arreglaban» la suite. Tocaron
  `tests/db_setup.py`, limitaron xdist y modificaron otros 2 tests de seguridad ajenos. Esos
  cambios **quedaron en el commit final**, fuera del alcance de la tarea.
- **Overhead del experimento en sí** (mi tiempo de arnés, aparte): unos 4 días entre fase 1 y fase
  2, sobre todo en incidencias del laboratorio. La tanda 1 duró unas 4 h de reloj.

## 4. Evidencia por componente

### Gates como hook Stop (N1): **desfavorable en este entorno**

- **Bloquea correctamente.** Los rojos se le muestran al agente y no le dejan terminar hasta verde:
  - mcp-ops-core N1: 3 pasadas, 2 rojas;
  - mcp-ops-core N2: 6 pasadas, 4 rojas;
  - las demás: verde a la primera.
- **Ninguno de los 6 rojos era un fallo del código del agente.**
  - Todos vinieron de la suite inestable: la suite pasaba sola.
  - En mcp-ops-core N2, además, del hook bloqueado.
  - El agente lo diagnosticó y reintentó hasta que la suite salió verde por azar. Por eso «no
    corregía» nada: no había nada suyo que corregir.
- **No cambió el resultado oculto en ninguna tarea.** El defecto de mcp-ops-core no es algo que el
  gate pueda ver.
- **Coste:** hasta +23 min por ejecución.

### Auditoría de Codex: **detecta defectos reales, pero su ronda de corrección es poco fiable**

| Auditoría | BLOCKING | Clasificación (por evidencia de tests y snapshots) |
|---|---|---|
| widget P2 | 1: una lectura pendiente pisa un envío más nuevo | **Real** (es el D-spec oculto). Antes del fix fallaba, después pasa: **corregido** |
| widget A | 1: el mismo defecto | **Real**. Antes del fix fallaba, después pasa: **corregido** |
| widget N2 | 1: una respuesta tardía de `mine()` pisa el resumen | El estado previo ya era Correcta+. **No verificable** con los tests ocultos; corrección inocua |
| mcp-ops-core N2 | 1: `_no_commit` rompe los savepoints | Plausible (el auditor lo reprodujo), no cubierto por los tests. **No vio el defecto de permisos** |
| mcp-ops-core P2 | 0 | **Falso negativo:** el defecto de permisos estaba presente |
| new-errors N2 | 1: se pierden errores diferidos entre ejecuciones | **Real** (área de `d_queue`), **no corregido**: falla antes y después |
| new-errors P2 | 4 | 1 «gate rojo», que es infraestructura (**falso positivo**). 1 real (`d_queue`), **no corregido**. 2 no verificables (paginación, idempotencia durable); su fix generó la contaminación de §2 |
| new-errors A | 4, luego aprobado | **2 reales y corregidos.** Contrato `trace_ids` entre repos: un F pasa de rojo a verde. Reintentos: `d_queue` pasa de rojo a verde. 2 no verificables (tope de spans, `route` nulo). **No vio la carrera** (D-spec), que sigue roja |

- **Detecta:** encontró el área del defecto oculto en widget (2 de 2) y en new-errors (3 de 3).
- **No ve:** el defecto de permisos de mcp-ops-core, 0 de 2.
- **Corrige:**
  - con una sesión y una sola ronda (N2/P2): **1 de 3** defectos reales;
  - en A, con un worker por hallazgo: **3 de 3** (widget, más 2 en new-errors).
- **Efectos laterales:**
  - convierte un gate rojo de infraestructura en BLOCKING;
  - empuja correcciones grandes (35 min y $9 en new-errors P2) que amplían el alcance.

### Plan (P2 frente a N2): **favorable, la señal más clara**

- **new-errors:** completa la funcionalidad **solo** en los brazos con plan (P2 y A, 2 de 2), frente
  a 0 de 4 sin plan (N0 ×2, N1 y N2).
- **Coste:** $2–4 y 2–4 min.
- **Riesgo observado:** en mcp-ops-core, el plan **fijó como decisión de diseño el comportamiento
  defectuoso** («el token de plataforma ve todas las herramientas»). Ni la auditoría ni la
  orquestación lo corrigieron después.

### Orquestación (A frente a P2, con el mismo plan): **insuficiente y mixta**

- **widget:** las dos son Correcta+; A tarda 13 min frente a 8 y cuesta $4,9 frente a $3,0.
- **new-errors:** A deja completa la funcionalidad, pero falla el D-spec de la carrera. P2 pasaba
  ese D-spec antes de su fix.
- **mcp-ops-core:** sin dato válido.
- **Coste específico de A:**
  - el gate completo con workers de corrección automática consume la mayor parte del tiempo cuando
    la suite es inestable;
  - mete cambios fuera de alcance en el commit.
- **Ventaja observable:** su ronda de corrección sí arregló lo que la auditoría señaló (§6). Pero
  en new-errors parte de lo que arregló lo había introducido el propio reparto en workers: un
  contrato `trace_ids` desalineado entre los dos repos, que P2 en una sola sesión no tuvo. Saldo
  neto frente a P2 en new-errors:
  - A: falla 1 D-spec;
  - P2 antes de su fix: falla 1 D-impl.

## 5. Simplificaciones que se pueden aplicar sin perder garantías

1. **Sin gate de suite completa con corrección automática por defecto.**
   - Mantener lint, typecheck y los tests afectados como gate barato.
   - Si la suite completa está roja en la base o es intermitente, clasificarla como
     **infraestructura** y parar, sin despachar workers.
   - Es la regla que el dueño pide para el benchmark, y el dato dice que vale igual para el uso
     real.
2. **Plan solo para tareas con varios criterios o varios repos.** No para las pequeñas.
   - Añadir una revisión de las *Decisions* del plan contra seguridad y permisos. Ahí entró el
     defecto de mcp-ops-core.
3. **Auditoría opcional, no por defecto.** Recomendada para cambios con concurrencia, estado
   durable o contratos entre servicios.
   - Que un «gate rojo» nunca cuente como BLOCKING.
   - Acotar la ronda de corrección a los hallazgos que se apoyan en un criterio de aceptación.
4. **Orquestación completa (A) fuera del modo por defecto,** mientras no haya evidencia de que
   supera a P2.

## 6. Estado previo al fix en A (puntuado desde el historial de git, sin nuevas ejecuciones)

| Celda | Antes del fix | Después del fix |
|---|---|---|
| widget A | falla el D-spec de lectura obsoleta | Correcta+ |
| new-errors A | falla 1 F (`trace_ids` entre repos), `d_queue` (D-impl) y la carrera (D-spec) | F completo y `d_queue` en verde; **la carrera sigue roja** |
| widget P2 (snapshot) | falla el D-spec | Correcta+ |
| new-errors P2 (snapshot) | Correcta; falla `d_queue` | aislado: igual que antes |
| new-errors N2 (snapshot) | incompleta, falla `d_queue` | igual |

La auditoría con varias rondas de A es la única variante cuyas correcciones movieron tests ocultos
de rojo a verde en new-errors.

## 7. La única prueba adicional con más valor

**Hipótesis:** con el mismo plan, la orquestación de A (un worker por tarea, auditoría y varias
rondas) deja código Correcta más a menudo que P2 (una sesión, una auditoría y una ronda).

- **Tarea:** new-errors.
  - Es la única donde el plan cambió el resultado.
  - Las dos variantes difieren justo en la fase de corrección, donde P2 falló el `d_queue` que le
    señalaron.
  - Es la tarea donde A y P2 divergen de verdad.
- **Diseño:**
  - **3 ejecuciones por brazo**, con **el mismo plan ya generado** para las 6, para quitar la
    variación del plan.
  - Entorno estable:
    - máquina sin otras sesiones con tests;
    - Postgres de tests dedicado;
    - gate completo de A sin la inestabilidad: el mismo límite de workers de xdist en el fixture
      para los dos brazos.
  - Tests ocultos corregidos para limpiar entre tests la tabla de claims. Se aplica a los dos
    brazos y se documenta antes de correr.
- **Regla, fijada antes de correr:** si A no es Correcta en más ejecuciones que P2 (de 3), A no
  vuelve a ser el modo por defecto. Si las dos empatan, gana la más barata.
- **Coste estimado:** unos $80 y 5 h de reloj, con A corriendo sola.
