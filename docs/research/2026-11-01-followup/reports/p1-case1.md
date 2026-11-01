# Impacto y estrategia recomendada

La recomendación es introducir una identidad de operación opcional en la API, persistir su estado antes del efecto fiscal y conservarla en el administrador durante los dobles clics y reintentos de una misma emisión. La protección completa frente a cortes requiere además idempotencia o reconciliación en el proveedor fiscal. Ese contrato no está demostrado por el código disponible; por tanto, no se puede prometer todavía que un reintento ambiguo nunca produzca otro comprobante.

## Evidencia y capacidades existentes

En `admin/invoice.ts:1–7`, `emitInvoice` envía `POST /invoices/emit` y, si recibe `error.status === 0`, vuelve a enviarlo una vez con el mismo payload. No transporta una clave de operación ni comparte una solicitud en curso. Dos invocaciones pueden producir dos solicitudes independientes. No hay código de botón o formulario en los archivos disponibles: el doble clic es un escenario de la solicitud, no una interacción de interfaz que se haya observado directamente.

En `api/router.py:1–5`, `emit_invoice` exige `invoicing:manage`, obtiene el negocio del usuario autenticado y devuelve `201` con el resultado del servicio. Recibe `headers`, pero no los utiliza. Esto permite considerar un contrato aditivo para clientes que opten por la nueva protección, manteniendo la ruta y respuesta de quienes no envíen la nueva identidad.

En `api/service.py:7–14`, cada invocación asigna un número, llama al emisor fiscal y guarda el comprobante después de recibir la respuesta. No consulta una operación previa ni registra que haya una emisión pendiente. En `api/store.py:1–14`, números y comprobantes viven en memoria; `save` añade filas sin deduplicación. El filtrado de consulta sí se hace por negocio.

`api/schema.sql:1–9` define una tabla y una restricción única por `(business_id, number)`. Esa restricción evita repetir un número dentro del negocio en una base que la aplique; no identifica una misma intención de emisión que recibió dos números diferentes. Tampoco demuestra persistencia efectiva: el servicio mostrado usa el almacén en memoria y no hay conexión visible con ese esquema.

`api/gateway.py:5–8` transmite negocio, número y payload al proveedor. No presenta una clave idempotente, consulta de estado o mecanismo de reconciliación. El comentario atribuye los timeouts y reintentos del transporte a configuración de despliegue que no está disponible. El payload se expande después de los campos `business` y `number`, por lo que podría sobrescribirlos si llega con esos nombres; no se observa validación previa. Esto debe aclararse para confiar en la correspondencia entre negocio autenticado, número reservado y emisión externa.

## Comportamiento que falta y contratos afectados

El administrador sería el productor de una identidad estable para una intención de emisión; la API sería su consumidor y responsable de decidir si crea, espera o reproduce el resultado. La identidad debe sobrevivir al corte contemplado y conservarse entre invocaciones simultáneas de la misma acción. Generar una clave nueva en cada llamada no protege el doble clic. Una emisión deliberadamente nueva debe recibir otra identidad: deduplicar sólo por importe y moneda bloquearía comprobantes legítimos iguales.

La API debería asociar la identidad al negocio autenticado, con unicidad durable y adquisición atómica antes de asignar el número y llamar al proveedor. Debe vincularla también al contenido efectivo de la solicitud: reutilizarla con datos distintos debe producir un conflicto definido, sin emitir. La autorización debe verificarse también al recuperar resultados anteriores; conocer una clave no concede acceso a otro negocio.

Una operación completada debe devolver el resultado original, incluido su número e identificador fiscal, sin repetir efectos. Una operación concurrente o pendiente necesita una respuesta acordada con el administrador que no provoque otra emisión. El número reservado y el estado de la operación deben conservarse; un bloqueo en memoria o desactivar el botón no cubren reinicios, varias instancias ni pérdidas de respuesta.

La API sería a su vez productora de una identidad estable ante el proveedor, si éste admite ese contrato. Si la emisión externa tuvo éxito pero se perdió la respuesta o falló el guardado local, una transacción local no puede deshacerla. Sin idempotencia externa o consulta autoritativa, el estado debe permanecer ambiguo y requerir reconciliación; reenviar a ciegas reintroduce el duplicado. Tampoco basta con eliminar el reintento automático del administrador: reduce una causa, pero deja el doble clic y la pérdida de respuesta sin resolver.

## Compatibilidad y riesgos

La opción recomendada es aditiva: solicitudes sin identidad mantienen el contrato existente; las solicitudes con identidad reciben las nuevas garantías. Con ello los clientes antiguos siguen funcionando, aunque conservan su exposición a duplicados. Extender la garantía a clientes que no identifican una intención distinta no está justificado por la evidencia disponible y puede alterar su comportamiento.

El impacto abarca dos repositorios, `api` y `admin`, y un contrato externo fiscal. Incluye persistencia y concurrencia, asignación de números, permisos y transporte. La clave no debería convertirse en campo fiscal por pasar indiscriminadamente el payload al proveedor.

El riesgo operativo principal es duplicar una emisión real o dejar una emisión real sin registro local. Los estados ambiguos necesitan trazabilidad y reconciliación, con una retención de claves coherente con la ventana de reintentos: borrar una clave demasiado pronto permite volver a emitir. Una reversión que retire la protección mientras quedan operaciones pendientes también puede duplicarlas; conservar el registro durable es parte de la compatibilidad operativa.

## Valoración y verificación

La **complejidad de implementación es alta**: el orden de efectos de `InvoiceService.emit` exige coordinar exclusión durable, número, llamada externa y recuperación, además del ciclo de identidad en `emitInvoice`. El **riesgo de modificación es alto**, porque afecta comprobantes e importes y hoy el efecto externo precede al guardado. La **incertidumbre del sistema es alta**: quedan UNKNOWN las garantías del proveedor, los reintentos del transporte y la conexión real del esquema SQL con ejecución. El **alcance de repositorios es alto**, pues intervienen `api` y `admin`, aunque eso no exige delegación. La **necesidad de planificación es alta** por esos contratos y estados de fallo; esta valoración no constituye un plan y no se ha escrito ninguno. La **necesidad de verificación independiente es alta** por los efectos fiscales y la compatibilidad de clientes.

Se leyeron íntegramente los seis archivos de producto citados y se consultó `rein context .`, que no resolvió comandos de comprobación. No se leyeron tests ni rúbricas, no se ejecutaron tests de producto y no se verificó comportamiento en ejecución. La verificación relevante tendría que demostrar una sola emisión ante solicitudes simultáneas, repetición tras respuesta perdida, reinicio y fallo de guardado; además, separación entre negocios, rechazo de una clave con contenido distinto, emisiones nuevas legítimas y compatibilidad de solicitudes sin clave. Son criterios pendientes de evidencia, no resultados obtenidos.

La pregunta decisiva pendiente es qué garantía ofrece el proveedor ante una emisión aceptada cuya respuesta se pierde, y si el transporte ya reintenta ese POST. También quedan sin demostrar la persistencia efectiva y el ciclo de vida de la intención en la interfaz. No hay más código disponible para resolverlos dentro de la carpeta autorizada. Se detuvo la investigación tras leer el recorrido completo, con tres llamadas de investigación y dentro del límite temporal; no hubo implementación, delegación, red ni instalación.
