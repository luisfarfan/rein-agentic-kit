# Resultados de la criba N0 (2026-10-30)

Claude Code nativo (Sonnet 5.5), 13 tareas reales de Proxima, 2 ejecuciones por tarea, tests
ocultos validados, aislamiento por sandbox del sistema operativo. Diseño en [CRIBA.md](CRIBA.md).
Las paradas e incidencias del proceso están en sus §14–§17.

## Resultado por tarea

**Correcta+** significa: todos los tests funcionales (F) y de defecto (D) ocultos pasan, sin
regresiones y con el gate en verde.

| Clase | Tarea | Correcta+ | F | D (defectos históricos) | Min | $ eq. API |
|---|---|---|---|---|---|---|
| Fácil | sale-note-pos | 2/2 | 16/16, 16/16 | 1/1, 1/1 | 6–7 | 4,1–4,8 |
| Fácil | widget | 2/2 | 18/18, 18/18 | 1/1, 1/1 | 4 | 2,2–2,7 |
| Fácil | oauth-public | 2/2 | 17/17, 17/17 | — | 11 | 1,9–2,0 |
| Fácil | obs-cors | 2/2 | 15/15, 15/15 | — | 10–14 | 2,2–3,3 |
| Fácil | cpe-storefront | 2/2 | 9/9, 9/9 | — | 4–7 | 1,9–2,3 |
| Dura | mcp-ops-core | 0/2 | 19/19, 19/19 | 0/1, 0/1 | 14–18 | 5,5–5,6 |
| Dura | cpe-admin | 0/2 | 19/19, 19/19 | 0/3, 1/3 | 9–10 | 5,3–5,5 |
| Dura | pos-availability | 0/2 | 9/9, 9/9 | 0/2, 0/2 | 3 | 1,8–1,9 |
| Dura | client-errors | 0/2 | 11/11, 11/11 | 1/6, 0/6 | 11–29 | 2,6–2,7 |
| Dura | cpe-pos | 0/2 | 12/15, 13/15 | — | 8 | 3,9–4,6 |
| Dura | rag-agent | 0/2 | 17/19, 13/19 | 3/4, 4/4 | 15–17 | 4,9–5,2 |
| Dura | storefront-tolerance | 0/2 | 13/15, 14/15 | 0/3, 0/3 | 7 | 3,1–3,5 |
| Dura | new-errors | 0/2 | 8/17, 9/17 | 0/3, 1/3 | 9–11 | 3,9–4,3 |

No salió ninguna tarea límite: el nativo resuelve una tarea las 2 veces o no la resuelve nunca.

## Lo que muestra

1. **Tareas pequeñas y claras (5 de 13):** el nativo las resuelve solo, en 4–14 minutos y por
   $2–5. Ahí REIN no puede aportar calidad, solo coste.
2. **Tareas donde el nativo cumple lo pedido pero repite el defecto histórico (4 de 13):**
   mcp-ops-core, cpe-admin, pos-availability y client-errors.
   - Pasan todos los tests funcionales.
   - Fallan exactamente en los defectos que encontró la auditoría de REIN en la implementación
     original: permisos de plataforma mal filtrados, datos privados en la telemetría, rechazos sin
     registrar, filas obsoletas y un texto de envío que falta.
   - **Aquí es donde una auditoría puede aportar valor.**
3. **Tareas donde el nativo no completa la funcionalidad (4 de 13):** cpe-pos, rag-agent,
   storefront-tolerance y new-errors. Le faltan criterios del intent, y en new-errors además
   rompe el comando manual.
   - **Aquí podría ayudar la planificación o la verificación.**
   - Falta comprobar si REIN lo consigue.

## Coste de la criba

- 26 ejecuciones válidas: $91,53 en equivalente API.
- Total con 4 ejecuciones invalidadas y repetidas y las pruebas del sandbox: $106,64 de $120.
- Cuota semanal de Claude al terminar: 48%.

## Pregunta abierta para la fase 2

En las 8 tareas duras, ¿atrapa la auditoría de REIN los defectos que el nativo deja pasar? ¿Y
completa REIN lo que al nativo le falta? ¿A qué coste? Con 8 duras (más de 6), la regla de §8 elige
6 por diversidad y añade 2 fáciles como control de sobrecoste.
