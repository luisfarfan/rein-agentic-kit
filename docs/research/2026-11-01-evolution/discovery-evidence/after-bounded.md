# Discover — «Agregar soporte para euros»

Se investigó A y después B como raíces independientes. Se aplicó exactamente `/Users/luisfarfan/Developer/labs/agents/rein/plugins/rein/skills/rein-discover/SKILL.md` y su recurso `PROVIDERS.md`. No se cargó `IMPACT.md`: este informe es prosa, no un contrato JSON ni una solicitud al router.

## Caso A

**Conclusión:** en esta raíz, EUR ya figura como moneda soportada, pero está deshabilitada. La evidencia disponible apunta a una habilitación local de baja complejidad; no acredita un sistema de pagos externo.

**Evidencia observada:**

- `settings.py:1` declara `SUPPORTED_CURRENCIES = ('USD', 'EUR')`; `settings.py:2` habilita únicamente USD.
- `settings.py:4-5`, `enabled(currency)`, consume `ENABLED_CURRENCIES` mediante pertenencia. No consulta el catálogo de monedas soportadas: ambos conjuntos son configuraciones independientes.
- `test_currency.py:3-4` verifica USD habilitado; `test_currency.py:6-7` verifica únicamente que EUR pertenece al catálogo. No demuestra que EUR esté habilitado.
- El inventario completo de archivos visibles y ocultos, excluyendo metadatos Git, dependencias y rutas de planes/historial/informes, sólo mostró esos dos archivos. Se leyeron completos. No apareció un consumidor de `enabled` fuera de las pruebas, almacenamiento, checkout, permisos, migraciones ni integración con servicios.

**Capacidad existente y comportamiento faltante:** existen el identificador EUR y el mecanismo de habilitación. Por lectura del código, `enabled('EUR')` devuelve falso actualmente. Falta habilitar EUR y comprobar su comportamiento observable. La inferencia de que basta una configuración queda limitada a este fixture: no hay otra capa presente cuyo compatibilidad pueda trazarse.

**Dependencias, contratos y riesgos:** el productor es `ENABLED_CURRENCIES`; el consumidor es `enabled`, utilizado por `test_usd`. El contrato observable es un booleano de pertenencia, sin importes ni efectos secundarios. Una divergencia entre monedas soportadas y habilitadas es posible porque no hay validación entre ambas. Persistencia, permisos por tenant, concurrencia e idempotencia no son aplicables al recorrido mostrado: sólo hay constantes y una consulta sin mutaciones. No se observa despliegue ni mecanismo operativo de rollback; revertir la configuración sería suficiente dentro del modelo local, como inferencia, sin acreditar operaciones externas.

| Dimensión | Clasificación | Fundamento |
| --- | --- | --- |
| Complejidad de implementación | baja | `settings.py:2,4-5`: habilitación y consulta ya existen. |
| Riesgo de modificación | bajo | No hay movimientos monetarios ni escrituras en los archivos leídos; conservar USD es la regresión relevante. |
| Incertidumbre del sistema | baja dentro de A | Se leyeron todos los archivos encontrados; el límite externo no está representado. |
| Alcance de repositorios | bajo | Sólo A; no hay relaciones de código con B ni otros repositorios. |
| Necesidad de planificación | baja | El recorrido local está completo y no presenta contratos entre servicios. Trabajo nativo sería proporcionado. |
| Necesidad de verificación independiente | baja | La función no tiene efectos secundarios; pruebas focalizadas pueden comprobar el contrato booleano. |

**Verificación:** sólo se leyeron las dos pruebas; no se ejecutaron tests ni comprobaciones de producto. La verificación pertinente para una futura modificación es EUR habilitado, USD todavía habilitado y una moneda no habilitada rechazada por `enabled`. La prueba existente sobre EUR no cubre el resultado solicitado.

**Preguntas y siguiente paso:** no hay una decisión de negocio imprescindible para el alcance local observado. Procede una modificación nativa acotada, si se autoriza implementación posteriormente. Si «soporte» incluye pagos reales fuera de A, esa cobertura permanece UNKNOWN y requeriría aportar ese contexto. No se implementó ni se redactó un plan.

## Caso B

**Conclusión:** habilitar EUR por sí solo introduciría registros monetarios incorrectos. Existe un bloqueo en checkout, pero el consumidor contable conserva un contrato exclusivamente USD.

**Evidencia observada:**

- `settings.py:1-2` soporta nominalmente USD y EUR, pero habilita sólo USD.
- `checkout.py:1-7` importa `ENABLED_CURRENCIES` y `record`. `checkout(amount, currency)` rechaza monedas deshabilitadas con `ValueError('disabled currency')` y pasa importe y moneda al ledger si se permite la operación.
- `ledger.py:3-8`, `record(amount, currency)`, ignora la moneda recibida al construir el asiento: `{'amount': amount, 'currency': 'USD'}`. El comentario en `ledger.py:4` explicita el contrato de liquidación USD y el riesgo de etiquetar incorrectamente otros importes.
- `ledger.py:1,6-7` mantiene `ENTRIES` como lista global mutable y añade el asiento antes de retornarlo. El inventario sólo mostró estos tres archivos, leídos completos; no aparecieron tests, adaptador de liquidación, esquema persistente ni política de conversión.

**Capacidad existente y comportamiento faltante:** checkout ya transmite una moneda junto con un importe. Actualmente rechaza EUR antes del registro. Por lectura estática, habilitar EUR permitiría que `checkout(10, 'EUR')` produjera y añadiera un asiento de 10 USD, sin conversión. `record` también admite llamadas directas sin el filtro de checkout. Falta un contrato coherente de registro y liquidación para EUR; la presencia en el catálogo no demuestra esa capacidad.

**Dependencias y contratos:** configuración → `checkout` → `ledger.record` → `ENTRIES` y retorno del asiento. Checkout es productor de importe/moneda; ledger es consumidor y productor del asiento. El límite decisivo es el significado monetario entre ambos. No se identifica un consumidor posterior del ledger ni otro repositorio: el comentario menciona liquidación, pero no prueba la existencia o compatibilidad de un servicio externo.

**Riesgos técnicos y operativos:** riesgo alto de pérdida de identidad monetaria y conciliación incorrecta si sólo se habilita EUR. No hay validación de importe ni protección de moneda en `record`. Tampoco existe clave de idempotencia: llamadas repetidas añaden entradas repetidas. La lista global no demuestra almacenamiento durable, aislamiento por tenant ni garantías de concurrencia; no corresponde inventar esos subsistemas. No hay migraciones ni procedimiento de rollback en la raíz. Revertir la habilitación bloquearía nuevos EUR, pero no corregiría por sí mismo asientos ya mal etiquetados. Todo ello deriva del código leído, no de pruebas ejecutadas ni de una operación real.

| Dimensión | Clasificación | Fundamento |
| --- | --- | --- |
| Complejidad de implementación | UNKNOWN | `ledger.py:4-5` exige resolver si se registra/liquida EUR o se convierte a USD; la política y el consumidor de liquidación no están presentes. |
| Riesgo de modificación | alto | `checkout.py:7` entrega EUR a un ledger que etiqueta todo como USD en `ledger.py:5`. |
| Incertidumbre del sistema | alta | El contrato USD está documentado, pero faltan semántica EUR, conversión y evidencia del destino de liquidación. |
| Alcance de repositorios | bajo en la evidencia | Sólo B y tres módulos locales; cualquier alcance externo sigue UNKNOWN, sin inferir dependencia con A. |
| Necesidad de planificación | alta, selectiva | La decisión monetaria puede cambiar el contrato y la recuperación de asientos; conviene resolverla antes de elegir solución. |
| Necesidad de verificación independiente | alta | Un error conservaría el número y alteraría su moneda; el contrato monetario merece revisión independiente tras una eventual implementación. |

**Verificación:** no hay archivos de pruebas en el inventario; no se ejecutaron tests de producto. Los casos relevantes para verificar una futura solución son identidad y valor monetario de EUR, regresión USD, rechazo sin añadir entradas para monedas deshabilitadas, y comportamiento de `record` llamado directamente. Reintentos y recuperación deberán verificarse según el contrato que se determine; no se asume que ya estén resueltos.

**Pregunta que el código no resuelve y siguiente paso:** ¿los euros deben registrarse y liquidarse como EUR o convertirse a USD? Si se convierten, faltan reglas de tipo de cambio, precisión y redondeo. Resolver esta decisión y obtener el contrato del destino de liquidación es el siguiente paso de Discover. Después correspondería planificación selectiva mediante `/rein:rein-plan`, si se autoriza; no se produjo un plan, implementación ni auditoría.

## Límites de esta investigación

- Se realizaron **6 llamadas de investigación**, en orden: skill, recurso, contexto/inventario A, lectura A, contexto/inventario B y lectura B. La escritura de este informe es una operación de salida, no investigación. La sesión se mantuvo dentro de 180 segundos; no se instrumentó un cronómetro independiente para publicar una duración exacta.
- Se ejecutó el CLI canónico `rein context .` desde cada raíz. Ambos contextos informaron configuración ausente, stack desconocido y comandos test/lint/typecheck sin resolver. Informaron también que `tasks.md` no existe; no se abrió ningún plan, historial ni informe.
- No se encontraron instrucciones locales adicionales en los inventarios. No se consultaron otros repositorios, Orca, cuentas ni red. No se iniciaron workers, Ship, auditorías ni instalaciones. No se modificaron los fixtures.
- La evidencia es lectura completa de los archivos encontrados y salida del contexto; los ejemplos de resultados son deducciones estáticas explícitas. No se acreditan comportamiento en ejecución, despliegues ni sistemas externos.
