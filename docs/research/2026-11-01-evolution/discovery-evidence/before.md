# Discover: «Agregar soporte para euros»

Investigación independiente, primero A y después B. Se aplicó exclusivamente `.rein/discovery-comparison/before/skills/rein-discover/SKILL.md`, junto con sus recursos `PROVIDERS.md` e `IMPACT.md`. Las referencias de cada caso son relativas a su propia raíz. No se implementó ni se escribió un plan.

## Caso A

**Conclusión:** EUR ya figura como moneda soportada, pero está deshabilitada. En este ejemplo, la petición parece reducirse a habilitar una configuración existente; no hay evidencia de un flujo monetario adicional.

**Evidencia observada:** `settings.py:1` declara USD y EUR en `SUPPORTED_CURRENCIES`; `settings.py:2` habilita únicamente USD. El consumidor `settings.enabled(currency)` (`settings.py:4–5`) consulta directamente esa lista. `test_currency.py:3–4` comprueba USD habilitado; `test_currency.py:6–7` comprueba solamente que EUR está soportado, sin comprobar su habilitación. El inventario local mostró esos dos archivos.

| Dimensión | Evaluación y evidencia |
| --- | --- |
| Alcance funcional | Bajo: habilitación de EUR mediante el contrato booleano de `settings.enabled` (`settings.py:1–5`). |
| Complejidad | Baja: una lista controla el único consumidor observado (`settings.py:2–5`). Es una inferencia sobre este ejemplo, no sobre un sistema externo. |
| Riesgo operativo | Bajo en el código entregado: consulta en memoria sin llamadas externas ni escrituras (`settings.py:4–5`). No hay evidencia de despliegue. |
| Incertidumbre | Baja para habilitar EUR en esta API; UNKNOWN para comportamientos externos no incluidos en la carpeta. La petición no define conversión ni pagos. |
| Persistencia / migraciones | No aplicable al recorrido observado: configuración y consulta en memoria, sin almacenamiento (`settings.py:1–5`). |
| Seguridad / permisos por tenant | No aplicable al recorrido observado: `enabled` recibe una moneda y no maneja usuarios o tenants (`settings.py:4–5`). |
| Concurrencia / idempotencia | Riesgo bajo: consulta sin efectos sobre estado (`settings.py:4–5`). |
| Contratos | Impacto bajo: cambia la respuesta para EUR, conservando la firma y el booleano de `enabled` (`settings.py:4–5`). |
| Cobertura | Insuficiente para aceptar la petición: `test_supported_eur` verifica soporte declarado, no `enabled('EUR')` (`test_currency.py:6–7`). |
| Efectos secundarios | Bajos: el único efecto funcional observado es el resultado de pertenencia a la lista (`settings.py:5`). |
| Dinero | Bajo en este ejemplo: se maneja un código de moneda, sin importes, conversión o redondeo (`settings.py:1–5`). |

**Cambio probable, sin implementarlo:** habilitar EUR y verificar el comportamiento público de `enabled` para EUR, preservando USD. La verificación disponible es `test_currency.py`; no se ejecutó. REIN no resolvió comandos de test, lint o typecheck.

**Siguiente paso:** trabajo nativo acotado si se autoriza implementar la habilitación. La evidencia no justifica planificación adicional, delegación ni auditoría independiente. No queda una decisión de producto necesaria para esa interpretación limitada.

## Caso B

**Conclusión:** habilitar EUR únicamente en configuración permitiría registrar importes de euros etiquetados como dólares. Hay un contrato monetario incompatible en el consumidor; no basta con activar la moneda.

**Recorrido y evidencia observada:** `settings.py:1–2` declara EUR soportado pero deshabilitado. La entrada `checkout.checkout(amount, currency)` valida la moneda habilitada (`checkout.py:4–6`) y pasa importe y moneda a `ledger.record` (`checkout.py:7`). `ledger.record` ignora la moneda recibida al construir la entrada: fija `currency` a `USD` (`ledger.py:3–5`), añade la entrada a `ENTRIES` y la devuelve (`ledger.py:6–7`). El comentario de `ledger.py:4` declara expresamente que el contrato de liquidación es solo USD. El inventario mostró únicamente estos tres archivos.

| Dimensión | Evaluación y evidencia |
| --- | --- |
| Alcance funcional | Alto respecto de una simple activación: afecta la admisión en checkout y la representación monetaria en ledger (`checkout.py:4–7`, `ledger.py:3–7`). |
| Complejidad | UNKNOWN hasta decidir el contrato: preservar EUR y convertir a USD son comportamientos diferentes; el código actual solo demuestra etiquetado fijo (`ledger.py:4–5`). |
| Riesgo operativo | Alto: una activación aislada abriría un camino que añade entradas monetarias mal etiquetadas (`checkout.py:5–7`, `ledger.py:5–6`). Es una inferencia directa del flujo, no un fallo ejecutado. |
| Incertidumbre | Alta: no se especifica si EUR debe conservarse o convertirse a USD; el comentario de liquidación hace material esa decisión (`ledger.py:4`). |
| Persistencia / migraciones | No se observa persistencia durable: `ENTRIES` es una lista global en memoria (`ledger.py:1,6`). Migraciones durables no aplican al ejemplo; cualquier sistema externo permanece UNKNOWN. |
| Seguridad / permisos por tenant | No aplicable al recorrido mostrado: checkout y record reciben importe/moneda, sin identidad ni tenant (`checkout.py:4–7`, `ledger.py:3–7`). No acredita controles de un sistema mayor. |
| Concurrencia / idempotencia | Hay estado compartido y cada llamada añade otra entrada, sin clave de deduplicación (`ledger.py:1,3–7`). UNKNOWN si existen reintentos o ejecución concurrente; no puede inferirse seguridad transaccional. |
| Contratos | Alto: record acepta currency pero devuelve y almacena siempre USD; modificar ese comportamiento debe reconciliarse con el contrato declarado de liquidación (`ledger.py:3–7`). |
| Cobertura | Insuficiente en la carpeta entregada: el inventario no mostró tests; no hay verificación observada del paso checkout→ledger. Esto no prueba ausencia de tests externos. |
| Efectos secundarios | Alto por su contenido monetario: se muta la lista global y se devuelve la misma entrada creada (`ledger.py:5–7`). No se observan llamadas a proveedores externos. |
| Dinero | Alto: se conserva el importe recibido pero se fuerza su denominación a USD (`ledger.py:5`). No hay conversión o redondeo en ese recorrido. |

**Cambio probable, sin elegir solución:** además de habilitar EUR, habrá que reconciliar el contrato entre checkout y ledger con el significado acordado de un importe en euros. No existe evidencia para afirmar que basta con sustituir el literal USD ni para introducir conversión automáticamente.

**Verificación disponible:** inspección del recorrido completo mostrado. No se encontraron tests en el inventario local ni comandos de verificación resueltos por REIN. La futura verificación deberá distinguir rechazo actual de EUR, aceptación acordada, moneda e importe efectivamente registrados y continuidad de USD. No se ejecutaron tests ni se mutó `ENTRIES`.

**Decisión que el código no resuelve:** ¿los cobros en EUR deben conservar EUR en el registro o liquidarse en USD mediante conversión? Si deben convertirse, la fuente del tipo de cambio y las reglas de redondeo también quedan sin definir.

**Siguiente paso:** aclarar ese contrato monetario antes de implementar; después corresponde planificación selectiva mediante `/rein:rein-plan` y revisión independiente del cambio monetario y contractual. No se invocó ninguno de esos flujos.

## Límites de la investigación

Se realizaron seis llamadas de investigación y una llamada exclusiva para escribir este informe, dentro del máximo de ocho y de los 180 segundos. Se leyeron los recursos indicados de la skill y los archivos enumerados de A y B, sin cruzar contexto entre casos. No se consultaron otros informes, historial o planes existentes; REIN informó que no había `tasks.md` en ninguna raíz.

Se usó el CLI canónico local para contexto. En A, `rein context --help` produjo contexto en lugar de ayuda: esta peculiaridad se observó directamente; resolvió A como raíz y no encontró configuración ni plan. En B se ejecutó `rein context .`, con el mismo resultado de comandos sin resolver y plan inexistente. No se ejecutó el router: su recomendación estructurada es opcional y la evidencia basta para los siguientes pasos descritos.

No se ejecutaron tests de producto, red, Orca, consultas de cuentas, trabajadores, Ship, auditorías, instalaciones o cambios de código/configuración. El único archivo escrito es este informe. Las valoraciones se limitan a los ejemplos entregados y no acreditan comportamiento de servicios, despliegues o almacenamiento externos.
