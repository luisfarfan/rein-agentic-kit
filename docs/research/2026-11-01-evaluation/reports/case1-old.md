# Informe de impacto: evitar comprobantes duplicados

## Conclusión y estrategia recomendada

Recomiendo idempotencia optativa en la API, respaldada por almacenamiento durable y una clave estable por intención de emisión en admin. La API debe seguir aceptando peticiones sin clave con su contrato actual. La protección ante un resultado fiscal incierto requiere además conocer las garantías del proveedor: con la evidencia disponible no se puede prometer una emisión única de extremo a extremo.

Es un cambio entre dos repositorios, `api` y `admin`, con concurrencia, persistencia y un efecto fiscal externo. Su complejidad es material; deshabilitar el botón o recordar una respuesta en memoria no cubre reinicios, solicitudes simultáneas ni un corte después de emitir. Esta es una recomendación de diseño, no un plan de implementación.

## Qué existe y qué demuestra el código

Referencias relativas a `/Users/luisfarfan/Developer/labs/agents/rein/.rein/p0-live-evaluation/old/case1`:

- `admin/invoice.ts:1-8`, `emitInvoice`: envía `POST /invoices/emit` y repite el mismo POST cuando `error.status === 0`. No proporciona una identidad de operación. El manejador del botón y sus consumidores no están entre los archivos observados; no se puede confirmar cómo se producen dos invocaciones desde la interfaz.
- `api/router.py:1-5`, `emit_invoice`: exige `invoicing:manage`, deriva el negocio de `user['business_id']` y devuelve `201` con el resultado del servicio. Recibe `headers` pero no los utiliza.
- `api/service.py:7-14`, `InvoiceService.emit`: cada llamada reserva otro número, invoca al emisor y sólo después guarda el comprobante. No consulta operaciones previas. Dos llamadas exitosas generan dos números y dos emisiones. Si el proveedor emite pero se pierde su respuesta, no se alcanza el guardado local y un reintento puede emitir otra vez. Este último escenario es una inferencia del orden de operaciones; no se ejecutó contra un proveedor.
- `api/store.py:1-14`, `Store`: secuencias y comprobantes viven en un diccionario y una lista de la instancia. No hay persistencia durable ni reserva de operación. Ese estado no coordina instancias ni sobrevive al reinicio. No hay un `await` dentro de `next_number`, pero eso no proporciona coordinación entre procesos.
- `api/schema.sql:1-9`: describe una tabla de comprobantes con `UNIQUE(business_id, number)`. Esta restricción no impide dos comprobantes de una misma intención con números distintos. No se muestra una conexión de `Store` con ese esquema; no puede suponerse que la restricción se esté aplicando en ejecución.
- `api/gateway.py:5-8`, `FiscalIssuer.issue`: realiza un POST con negocio, número y payload. La política de timeout y reintento depende del despliegue. No se muestra un contrato de idempotencia ni una consulta de estado del proveedor.

## Alcance y motivos del diseño recomendado

En **admin**, la clave debe pertenecer a una intención de emisión, compartida por el doble clic y todos sus reintentos. Crear una clave nueva en cada invocación de `emitInvoice` sólo protegería el reintento interno y dejaría abierto el doble clic. La identidad debe sobrevivir al corte que se pretenda cubrir, y una nueva emisión deliberada debe recibir otra clave. La ubicación y duración exactas de ese estado son UNKNOWN: falta el consumidor de `emitInvoice`. Bloquear el botón puede mejorar la experiencia, pero no aporta por sí solo la garantía (`admin/invoice.ts:1-8`).

En **api**, recomiendo admitir una clave opcional en cabecera y resolverla después de autenticar y autorizar. Para solicitudes con clave, una reserva atómica y durable por negocio, operación y clave debe identificar un único propietario de la emisión; las solicitudes concurrentes deben recuperar el mismo resultado o un estado pendiente definido, sin repetir el efecto. La reserva debe asociar el contenido relevante de la petición: reutilizar una clave con datos distintos debe producir un conflicto explícito. No conviene deduplicar por igualdad del payload, porque dos comprobantes legítimos pueden tener el mismo importe y moneda (`router.py:1-5`; `service.py:7-14`).

El resultado final y el número asignado deben quedar ligados a la operación para poder responder al reintento tras perderse la respuesta. La numeración necesita coordinación durable con esa reserva. Una transacción local no puede hacer atómico el POST fiscal: si el proveedor acepta y el proceso cae antes del guardado, queda una operación de resultado incierto. Recomiendo reutilizar una identidad aceptada por el proveedor si éste garantiza idempotencia; alternativamente, reconciliar mediante una consulta autoritativa de estado. Mientras el resultado sea desconocido, no se debe volver a emitir a ciegas. Si el proveedor no ofrece ninguna de esas capacidades, la garantía completa permanece sin resolver (`service.py:8-13`; `gateway.py:5-8`).

Para **clientes existentes**, conservar ausencia de clave, permiso, negocio autenticado, cuerpo exitoso y `201` evita convertir el nuevo mecanismo en un requisito obligatorio. El contrato para claves nuevas debe definir también petición en curso, conflicto de contenido y reproducción del resultado. La API debe tolerar clientes antiguos durante despliegues separados de ambos repositorios. Mantener el comportamiento de clientes sin clave implica que éstos seguirán expuestos a duplicados: el servidor no puede distinguir con certeza su reintento de otra emisión legítima. Exigir protección universal y conservar exactamente ese comportamiento requeriría resolver esa incompatibilidad de producto (`router.py:5`; `admin/invoice.ts:3-5`).

## Impactos y riesgos

**Persistencia y migraciones.** El diseño requiere estado durable de operaciones, unicidad por negocio y clave, estados de ejecución y resultado recuperable. El esquema actual sólo contempla comprobantes, y `Store` es volátil. La tecnología real de base de datos, mecanismo de migración, retención de claves y despliegue son UNKNOWN. El vencimiento de una clave puede reabrir la posibilidad de duplicar un reintento tardío (`schema.sql:1-9`; `store.py:2-11`).

**Seguridad y separación de negocios.** La nueva búsqueda y reproducción deben mantener el permiso actual y usar el negocio autenticado en todas las reservas y consultas; una clave no debe permitir recuperar resultados de otro negocio. Existe además una frontera a revisar: en `gateway.py:7`, `**payload` aparece después de `business` y `number`, por lo que puede sobrescribir esos campos si llegan en el payload. El router mostrado no los filtra; no se puede afirmar que otra capa lo haga. Esto condiciona que la identidad y numeración enviadas al proveedor sean las reservadas por la API (`router.py:2-4`; `gateway.py:7`).

**Dinero y efectos laterales.** No se observa cálculo de impuestos, cobros o conversión monetaria. Sí se observa almacenamiento de importe y moneda, consumo de numeración y emisión fiscal externa: la duplicación puede producir dos obligaciones documentales aunque no haya dos cargos. El impacto económico concreto y las reglas de anulación son UNKNOWN (`service.py:8-13`; `schema.sql:4-7`).

**Operación e incertidumbre.** Reintentos automáticos del transporte podrían repetir el efecto incluso dentro de una sola llamada a `issue`; su configuración no está disponible. La recuperación de operaciones pendientes y la visibilidad de resultados inciertos son necesarias para evitar bloqueos permanentes o reemisiones inseguras. La garantía del proveedor es la principal incertidumbre que puede cambiar el diseño y su alcance (`gateway.py:8`).

## Verificación disponible y límites

Se realizó lectura estática de los seis archivos de código/esquema enumerados y se consultó el CLI canónico con `rein context` sobre la carpeta autorizada. El contexto no encontró configuración ni comandos resueltos de test, lint o typecheck. No se ejecutaron tests de producto ni se inspeccionaron tests o rúbricas de evaluación; la cobertura existente es UNKNOWN.

La verificación relevante para un cambio posterior sería demostrar una sola emisión ante doble clic y concurrencia, reproducción tras pérdida de respuesta y reinicio, aislamiento entre negocios, rechazo de clave con contenido distinto, conservación del contrato sin clave y recuperación del corte entre aceptación fiscal y guardado local. Son propiedades a comprobar, no resultados ya demostrados.

La investigación se detiene donde falta evidencia: contrato y reintentos del proveedor, almacenamiento real y consumidor del botón. El siguiente paso recomendado es resolver esas fronteras antes de comprometer una garantía completa; por tocar estado durable y efecto fiscal, el cambio resultante merece revisión independiente. No se implementó ni se escribió un plan. Se usaron tres llamadas de investigación, sin delegación, red ni instalación; la única escritura fue este informe.
