# Discover: «Agregar soporte para euros»

Se investigaron A y después B como raíces independientes. Se aplicó exclusivamente `/Users/luisfarfan/Developer/labs/agents/rein/plugins/rein/skills/rein-discover/SKILL.md`, junto con sus recursos `PROVIDERS.md` e `IMPACT.md`. No se infiere relación entre ambos ejemplos.

## Caso A

Raíz: `/Users/luisfarfan/Developer/labs/agents/rein/.rein/p0-live-_sl9us7c/.rein/discovery-comparison/A`.

**Capacidad existente y comportamiento faltante — observado.** `settings.py:1` declara USD y EUR como soportados; `settings.py:2` habilita sólo USD. El consumidor `enabled(currency)` devuelve pertenencia a esa lista (`settings.py:4-5`). Por tanto, EUR está declarado, pero `enabled('EUR')` resulta falso por lectura del código. `test_currency.py:3-7` comprueba USD habilitado y EUR soportado; no comprueba EUR habilitado ni una moneda desconocida rechazada.

**Dependencias y límites.** El productor `ENABLED_CURRENCIES` alimenta al consumidor local `enabled`; el test importa `settings` (`test_currency.py:1`). El inventario completo, incluidos archivos ocultos e ignorados y excluyendo metadatos Git, contiene únicamente esos dos archivos. Su lectura completa no muestra almacenamiento, migraciones, importes, liquidación, permisos, tenants, llamadas externas ni otros consumidores. No se identificó un contrato entre repositorios. La afirmación de suficiencia se limita a esta API local, no a un producto externo.

**Conclusión — inferencia.** Para el comportamiento representado en A, habilitar la capacidad EUR existente parece suficiente. La modificación no requiere introducir cálculo monetario ni un nuevo flujo. El riesgo funcional visible consiste en confundir “soportada” con “habilitada”, algo que el test actual de EUR no detectaría.

| Clasificación | Nivel | Razón y evidencia |
|---|---|---|
| Complejidad de implementación | low | Configuración existente consumida directamente por `enabled`; `settings.py:1-5`. |
| Riesgo de modificación | low | Efecto local de pertenencia; no procesamiento de dinero ni efectos persistentes en los dos archivos completos. |
| Incertidumbre del sistema | low | Productor, consumidor y todos los tests locales inspeccionados; inventario de dos archivos. |
| Alcance de repositorios | low | Un único contexto; el único import observado es `test_currency.py:1` → `settings`. |
| Necesidad de planificación | low | La capacidad ya existe y el límite afectado es directo; `settings.py:1-5`. No se justifica un plan formal. |
| Necesidad de verificación independiente | low | No se observan cambios de contrato entre servicios, seguridad, concurrencia ni estado durable; cuerpo completo de `enabled` y tests. La comprobación funcional local sigue siendo necesaria. |

**Riesgo operativo.** Bajo dentro del ejemplo: no hay escrituras ni efectos externos. Restituir la lista anterior revertiría la exposición local de EUR (inferencia de `settings.py:2,4-5`). Persistencia, migraciones y liquidación no aplican a lo representado. No se acredita ningún mecanismo operativo de despliegue.

**Verificación y siguiente paso.** Se leyeron los dos tests, sin ejecutarlos. Una futura verificación relevante debe distinguir EUR habilitado de EUR soportado, conservar USD y rechazar monedas no habilitadas. Siguiente paso recomendado: trabajo nativo acotado, si se autoriza implementar. No quedan preguntas que cambien esa recomendación dentro de esta raíz; cualquier consumidor externo queda fuera de la evidencia disponible.

## Caso B

Raíz: `/Users/luisfarfan/Developer/labs/agents/rein/.rein/p0-live-_sl9us7c/.rein/discovery-comparison/B`.

**Capacidad existente — observado.** `settings.py:1-2` también declara EUR soportado y sólo USD habilitado. La entrada `checkout(amount, currency)` consume `ENABLED_CURRENCIES`, rechaza monedas deshabilitadas con `ValueError` y entrega importe y moneda a `record` (`checkout.py:1-7`). Actualmente, EUR se rechaza antes de registrar; USD atraviesa ese filtro (conclusión estática, no prueba ejecutada).

**Comportamiento faltante y contrato — observado.** El productor `checkout` pasa la moneda a `ledger.record`, pero este consumidor ignora ese valor al construir `{'amount': amount, 'currency': 'USD'}` y añade la entrada a `ENTRIES` (`ledger.py:1-7`). El comentario de `ledger.py:4` explicita un contrato de liquidación exclusivamente USD. No hay conversión ni validación adicional de moneda o importe en ese cuerpo. Habilitar EUR únicamente en configuración permitiría que un importe EUR entrase al registro con etiqueta USD: inferencia directa del flujo `checkout.py:5-7` → `ledger.py:5-6`. Una llamada directa a `record` tampoco pasa por el filtro de checkout.

**Dependencias y límites.** Flujo local: `settings.ENABLED_CURRENCIES` → `checkout` → `ledger.record` → lista `ENTRIES` y entrada devuelta al llamador. El inventario completo contiene tres archivos, todos leídos. No se observa un consumidor posterior de esa lista, servicio de liquidación, base de datos, migraciones, permisos, aislamiento por tenant ni protección de idempotencia. `ENTRIES` es estado mutable en memoria; no es evidencia de persistencia durable. No hay tests en esta raíz. La ausencia de código de liquidación impide determinar por qué el contrato debe seguir siendo USD o cómo debería evolucionar.

**Riesgos técnicos y operativos.** Alto riesgo monetario y de contrato: habilitar la moneda sin resolver el registro produciría datos semánticamente incorrectos. Reintentar llamadas puede añadir entradas repetidas porque `record` siempre hace `append` (`ledger.py:6`); no se encontró un contrato de idempotencia. No se acredita una garantía de concurrencia. Deshabilitar EUR de nuevo bloquearía nuevas llamadas EUR por checkout, pero no corregiría entradas ya añadidas y no impediría invocaciones directas de `record` (inferencia de ambos cuerpos). El efecto observado es local y en memoria; no se afirma exposición a producción ni liquidación real.

| Clasificación | Nivel | Razón y evidencia |
|---|---|---|
| Complejidad de implementación | UNKNOWN | Una corrección local y una conversión a USD serían soluciones distintas; `ledger.py:4-5` no permite elegir el contrato EUR requerido. |
| Riesgo de modificación | high | El importe conservaría su valor con moneda USD aunque llegase EUR; `checkout.py:7`, `ledger.py:4-6`. Afecta dinero y estado mutable. |
| Incertidumbre del sistema | high | Contrato USD explícito, pero sin consumidor de liquidación ni reglas EUR en los tres archivos completos. |
| Alcance de repositorios | low | Los imports de `checkout.py:1-2` se resuelven dentro de esta raíz; no hay evidencia de otro repositorio involucrado. |
| Necesidad de planificación | high | Hace falta acordar el comportamiento monetario del límite checkout/registro y su reversión antes de elegir solución; `checkout.py:7`, `ledger.py:4-6`. Se justifica planificación selectiva tras resolver la pregunta de contrato. |
| Necesidad de verificación independiente | high | El cambio afectaría el contrato monetario de registro y un efecto de escritura en memoria; `ledger.py:4-6`. Una revisión independiente está justificada por ese riesgo, aunque el código sea pequeño. |

**Verificación y cuestión abierta.** No hay tests para leer ni se ejecutaron tests de producto. Las verificaciones relevantes deberán cubrir coherencia importe/moneda en EUR y USD, rechazo antes de mutar para monedas no habilitadas y la entrada directa a `record`; la política de reintentos depende del contrato que se acuerde. Pregunta que el código no responde: ¿las entradas deben conservar EUR o la liquidación debe seguir siendo USD mediante una conversión definida? La siguiente comprobación es obtener la regla de negocio de ese contrato y su consumidor previsto. No se inventan proveedor de cambio, redondeo ni repositorios externos.

**Siguiente paso.** Resolver esa decisión y, con ella, solicitar planificación selectiva; recomendar revisión independiente del cambio monetario resultante. No basta con habilitar una opción. Este informe no autoriza ni inicia implementación, planificación o auditoría.

## Límites y ejecución observada

Se realizaron siete llamadas de investigación a `exec_command` y una consulta al reloj: ocho llamadas en total bajo el conteo conservador. Se usó el CLI canónico para contexto en ambas raíces. En A, `context --help` devolvió contexto en lugar de ayuda; en B se ejecutó `context .`. Ambos resultados indicaron configuración ausente, comandos de verificación no resueltos y ningún `tasks.md` existente; no se leyó contenido de planes, historial o informes. No se consultó Orca ni cuentas.

La evidencia es lectura estática con referencias de archivo y línea. No se ejecutaron tests, `discover-check` ni comprobaciones de comportamiento; no se generó un artefacto JSON v2 ni se afirma validación semántica o de frescura mediante ese checker. No hubo delegados, red, instalaciones, Ship, auditoría ni cambios en las raíces. La única escritura es este informe solicitado.

Tiempo observado desde la consulta inicial al reloj hasta preparar esta escritura: 88.8 segundos; el primer comando de lectura duró menos de un segundo y precedió a esa consulta. Límite total: 180 segundos.
