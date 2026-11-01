# Impacto: emisión sin duplicados

## Alcance y evidencia

Se leyeron completos los seis archivos de `case1`: `api/router.py`, `api/service.py`, `api/store.py`, `api/gateway.py`, `api/schema.sql` y `admin/invoice.ts`. Las referencias siguientes son relativas a esa carpeta. `api` y `admin` son repositorios distintos. Se aplicó exclusivamente la skill Discover indicada y su recurso `PROVIDERS.md`; se consultó el CLI canónico con `rein context .`. Ese contexto corresponde al fixture actual, no acredita configuración ni verificaciones de los dos repositorios examinados.

No se leyeron tests, rúbricas, Git, otros informes ni historiales. No se ejecutaron tests de producto, instalaciones, llamadas de red ni trabajadores. Se usaron cuatro llamadas de investigación a comandos, más una consulta al reloj, dentro de los límites fijados. Este documento es un informe de descubrimiento y recomendación, sin implementación ni plan de tareas.

## Capacidades existentes y comportamiento faltante

**Observado.** El punto de entrada `emit_invoice` exige `invoicing:manage`, obtiene el negocio del usuario y devuelve `201` con el resultado (`api/router.py:1–5`). Recibe `headers`, pero no los utiliza. El servicio reserva un número por negocio, solicita la emisión externa y después guarda y devuelve una fila (`api/service.py:7–14`). El almacenamiento mostrado mantiene filas y secuencias en memoria; no ofrece transacción, reserva de operación ni exclusión de solicitudes repetidas (`api/store.py:1–14`).

La tabla SQL declara unicidad de `(business_id, number)` (`api/schema.sql:1–9`), pero no existe conexión visible entre esa tabla y `Store`. Además, esa unicidad no identifica una misma intención: dos llamadas pueden reservar números distintos y ambas ser válidas. No evita dos comprobantes por doble pulsación.

`admin` produce solicitudes a `/invoices/emit` y repite el POST si recibe `error.status === 0` (`admin/invoice.ts:1–7`). No transmite una clave de operación ni comparte una promesa pendiente entre invocaciones. El manejador real del botón y la implementación de `http` no están incluidos, por lo que su comportamiento adicional queda desconocido.

**Inferencia del flujo observado.** Dos llamadas a la función pueden producir dos números y dos llamadas fiscales. Si el emisor acepta el comprobante y se pierde su respuesta, o falla el guardado posterior, un reintento no tiene evidencia local para recuperar la emisión previa. Si el corte ocurre después de guardar y antes de recibir la respuesta en admin, tampoco hay una identidad que permita devolver el resultado anterior.

## Estrategia recomendada y motivos

Recomiendo idempotencia explícita y duradera por operación, con una clave opcional para mantener compatibles los clientes actuales. Para las solicitudes con clave, la API debe asociar atómicamente `(business_id, clave)` a una huella del contenido, un número reservado, el estado de la emisión y el resultado recuperable. La exclusión debe funcionar entre procesos y sobrevivir reinicios; un bloqueo en memoria o consultar antes de insertar no resuelve la carrera. Los reintentos con igual clave y contenido deben recuperar la misma operación; reutilizarla con otro contenido debe producir un conflicto explícito. La autorización y el aislamiento por negocio deben aplicarse también a las recuperaciones.

Admin debe identificar la intención de emitir y conservar su clave durante el doble clic y todos los reintentos de esa intención. Crear una clave en cada invocación de `emitInvoice` permitiría que dos clics siguieran emitiendo dos veces. Compartir la operación pendiente y deshabilitar el botón ayudan a la experiencia, pero la garantía debe residir en la API. Una nueva emisión legítima, aunque tenga idéntico importe y moneda, necesita una identidad distinta: deduplicar únicamente por contenido impediría operaciones válidas. Si se requiere recuperación tras recargar la página, la identidad pendiente también debe sobrevivir a esa recarga.

Para clientes sin clave, deben conservarse el contrato actual de entrada y la respuesta exitosa `201` con su cuerpo. La clave opcional permite adoptar la protección sin romper esos clientes. No puede prometerse deduplicación de clientes antiguos que no aporten una identidad estable: el código no permite distinguir su reintento de otra emisión legítima. Esa limitación debe ser explícita, sin introducir una deduplicación heurística que cambie su comportamiento.

La idempotencia local por sí sola es insuficiente frente al emisor fiscal. La misma identidad debe llegar al proveedor si éste admite idempotencia; alternativamente, debe existir una consulta o conciliación fiable por referencia estable que permita resolver una respuesta perdida antes de volver a emitir. Un timeout debe conservarse como resultado incierto, sin tratarlo automáticamente como fracaso definitivo ni disparar otra emisión. No es posible hacer atómica una transacción local con el POST externo sólo envolviendo el servicio en una transacción de base de datos.

## Dependencias, contratos y riesgos

- **Admin → API:** hoy sólo envía el payload. La nueva identidad debe cubrir llamadas concurrentes y reintentos, con contrato definido para contenido distinto, operación pendiente y reproducción del resultado. Los códigos y cuerpos nuevos deben limitarse al modo con clave; la respuesta de éxito existente debe preservarse.
- **Router → servicio → almacenamiento:** debe propagarse la identidad sin sustituir el negocio autenticado. La persistencia real, sus transacciones y migraciones no están demostradas por el `Store` de memoria ni por el SQL aislado. Número e identidad deben quedar asociados de forma estable; la política sobre huecos de numeración no consta.
- **Servicio → emisor → transporte:** `FiscalIssuer.issue` sólo envía negocio, número y payload (`api/gateway.py:5–8`). No muestra una clave, consulta de estado ni garantía de unicidad del proveedor. El comentario delega timeouts y reintentos al despliegue, cuya configuración no está disponible. Reintentos ocultos del transporte pueden duplicar efectos incluso con una sola ejecución del servicio.
- **Límite de confianza:** en `gateway.py:7`, `**payload` aparece después de `business` y `number`, de modo que esas claves pueden sobrescribir los valores calculados si llegan en el payload. No se ve validación previa. Esto amenaza el aislamiento y la asociación del número con la operación; el contrato debe reservar esos campos y usar los valores confiables del servidor.
- **Operación y reversión:** expirar registros demasiado pronto permite que un reintento tardío emita otra vez. Deben definirse retención y recuperación de estados pendientes. Revertir una versión no debe eliminar identidades ya aceptadas ni convertir operaciones inciertas en nuevos POST. Los comprobantes externos duplicados no se deshacen mediante rollback local.

## Clasificaciones independientes

| Dimensión | Clasificación | Motivo y evidencia |
|---|---|---|
| Complejidad de implementación | Alta | Requiere coordinar identidad en admin, exclusión y recuperación duraderas en API y resolución del efecto externo; `invoice.ts:1–7`, `service.py:7–14`, `gateway.py:5–8`. |
| Riesgo de modificación | Alto | Afecta emisión fiscal, numeración, aislamiento por negocio y compatibilidad; `router.py:2–5`, `schema.sql:8` y sobrescritura potencial en `gateway.py:7`. |
| Incertidumbre del sistema | UNKNOWN | No constan contrato del proveedor, reintentos efectivos, almacenamiento desplegado ni manejador del botón. Pueden cambiar la viabilidad y el alcance. |
| Alcance de repositorios | Alto: dos repositorios | El productor está en `admin/invoice.ts` y el consumidor y efecto fiscal en `api`. No implica necesidad de delegar. |
| Necesidad de planificación | Alta | La solución exige acuerdos sobre compatibilidad, estados inciertos, retención y proveedor antes de implementar. Se recomienda planificación selectiva posterior; aquí no se escribe un plan. |
| Necesidad de verificación independiente | Alta | La concurrencia, persistencia y contratos entre servicios pueden producir efectos fiscales irreversibles; conviene una revisión independiente de esas garantías. |

## Verificación y límites pendientes

La evidencia es lectura estática, no una prueba ejecutada. No se leyeron tests y no se afirma cobertura existente. Una verificación significativa tendría que demostrar que solicitudes simultáneas con la misma clave producen una sola emisión; que un corte antes o después de guardar recupera la operación; que un reinicio no pierde la identidad; que otra carga con igual clave se rechaza; que otros negocios y usuarios sin permiso no pueden recuperar resultados ajenos; y que clientes sin clave mantienen su contrato. También debe comprobarse que dos intenciones legítimas iguales siguen pudiendo emitir por separado y que el payload no sustituye negocio ni número.

La pregunta decisiva pendiente es: **¿qué garantía documentada ofrece el proveedor para reconocer una emisión ya aceptada cuando se pierde la respuesta, y qué reintentos realiza el transporte desplegado?** La siguiente comprobación necesaria sería revisar ese contrato y configuración, junto con el adaptador persistente real, cuando estén disponibles y autorizados. No están en la evidencia proporcionada; se conserva UNKNOWN y se detiene la investigación. Hasta resolverlo, la estrategia recomendada reduce duplicados locales, pero no acredita una garantía completa de emisión única tras un corte externo.
