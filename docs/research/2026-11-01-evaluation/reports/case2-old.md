# Informe de impacto: acceso y aprobación de propuestas en lote

## Resultado y estrategia recomendada

La evidencia favorece ampliar la visibilidad del panel en **admin**, conservando la autorización existente en **api**. No hay evidencia que justifique relajar la política del servidor ni crear un mecanismo nuevo de aprobación en lote.

`admin/menu.py:1` permite entrar únicamente con `catalog:manage` u `orders:manage`. `admin/view.py:3-4` usa esa lista mediante una intersección de permisos. En cambio, `api/policy.py:1-6` autoriza propuestas de inventario con `inventory:manage` y de precios con `pricing:manage`, siempre del mismo negocio y con coincidencia de `grant_key`. Por tanto, alguien con sólo uno de estos permisos de gestión puede satisfacer la política de aprobación de su área y aun así no ver el panel.

La estrategia recomendada es incorporar ambos permisos a la visibilidad, preservando los accesos actuales. La API debe seguir siendo la autoridad para cada propuesta. Esto respeta el contrato descrito en `admin/help.md:1-3`: inventario y precios comparten panel; la visibilidad permite llegar a él y la API controla las mutaciones. Es una recomendación de alcance, no un plan de implementación.

## Lo que existe y sus límites

- **Entrada y consumidor:** `show_approvals` depende de `VISIBLE_WITH` (`admin/view.py:1-4`). `approval_request` construye un POST a `/approvals/batch`, con `X-Grant-Key` y una lista de `proposal_ids` (`admin/view.py:6-9`). Ya existe una representación de selección y envío en lote; no se observa aquí una pantalla interactiva completa.
- **Autorización por negocio y área:** `allowed` exige simultáneamente igualdad de `business_id`, el permiso correspondiente a `proposal['area']` y coincidencia de la clave (`api/policy.py:3-6`). Tener inventario no concede precios, ni viceversa.
- **Semántica del lote:** `approve_batch` comprueba todos los elementos antes de devolver resultados y lanza `PermissionError` si alguno no está autorizado (`api/approvals.py:3-6`). Un lote de ambas áreas exige ambos permisos; una propuesta de otro negocio o con clave distinta bloquea el lote entero. Estas consecuencias se deducen de `all(allowed(...))`. Un lote vacío devuelve una lista vacía por la semántica de `all`.
- **Resultado:** el código devuelve pares de ID y estado `approved` (`api/approvals.py:6`). No muestra escritura en almacenamiento ni aplicación de cambios de inventario o precios.

## Impacto y motivos

**Alcance funcional.** El desajuste observado se concentra en el acceso al panel. La selección y la petición en lote ya están representadas (`admin/view.py:6-9`). No hay evidencia suficiente para afirmar que haga falta cambiar el flujo de selección o permitir resultados parciales. Conservar el rechazo completo evita introducir una semántica distinta a `api/approvals.py:4-6`.

**Complejidad de implementación.** La corrección de visibilidad parece acotada a una configuración consumida directamente por `show_approvals` (`admin/menu.py:1`, `admin/view.py:1-4`). Es una inferencia sobre este ejemplo, no una garantía de que una aplicación completa carezca de otras barreras de acceso.

**Seguridad y aislamiento entre negocios.** Es la dimensión más sensible. Mostrar el panel no debe sustituir ni ampliar la autorización de la API. La política actual combina negocio, permiso de área y clave (`api/policy.py:3-6`). La petición del admin sólo envía IDs y clave; no aporta los objetos que recibe `approve_batch` (`admin/view.py:6-9`, `api/approvals.py:3`). Se desconoce cómo se autentica al usuario, se resuelven esos IDs, se entregan las claves y se filtran las propuestas que se muestran. La seguridad de esas conexiones no queda demostrada por el ejemplo.

**Contratos entre repositorios.** Aunque api y admin son repositorios distintos, la evidencia no exige modificar ambos. El contrato del cliente es un POST con IDs y cabecera; la función del servidor recibe usuario, propuestas y clave. Falta el adaptador HTTP que conecta ambas formas. Mantener ruta, cabecera y cuerpo reduce el impacto contractual; cambiarlos sin esa evidencia no está justificado (`admin/view.py:6-9`, `api/approvals.py:3`).

**Persistencia y migraciones.** No se observa necesidad de migración para ampliar `VISIBLE_WITH`. Las funciones leídas consultan estructuras y producen resultados, sin escrituras persistentes (`admin/view.py:3-9`, `api/approvals.py:3-6`). El almacenamiento real y la transición durable de estado son UNKNOWN, porque no aparecen en el código disponible.

**Concurrencia e idempotencia.** UNKNOWN para una aprobación real. `approve_batch` no verifica estado previo, versión, transacción ni repetición; sólo autoriza y devuelve estados (`api/approvals.py:3-6`). La comprobación completa previa al resultado no demuestra atomicidad de una escritura que no está representada.

**Riesgo operativo y efectos laterales.** La ampliación propuesta hará accesible el panel a más usuarios, por la intersección de permisos de `admin/view.py:4`. No se observan colas, notificaciones ni integraciones en ese recorrido. Los efectos posteriores a una aprobación quedan sin determinar; no corresponde calificarlos como inexistentes a partir de estas funciones.

**Dinero.** La política reconoce explícitamente el área de precios (`api/policy.py:1`). Dar acceso puede habilitar el uso de una capacidad sensible ya autorizada, pero no se observan importes, redondeos ni aplicación de precios en `api/approvals.py:6`. No hay base para proponer cambios monetarios ni para descartar consecuencias económicas aguas abajo.

**Incertidumbre.** La causa de la exclusión del panel está respaldada por el código. La integración HTTP, la lectura de propuestas y su persistencia son las fronteras sin evidencia que impiden afirmar que todo el recorrido de producción esté verificado.

## Verificación disponible y cierre

La inspección permite derivar comprobaciones relevantes: acceso con cada permiso de gestión por separado; conservación de los accesos actuales; rechazo sin permisos; autorización sólo del negocio y área correspondientes; rechazo completo si un elemento falla; y envío de IDs y `X-Grant-Key` con el formato existente. Son criterios de verificación derivados de las funciones citadas, no resultados de pruebas ejecutadas.

No se leyeron tests ni rúbricas, ni se ejecutaron tests de producto. La cobertura existente queda UNKNOWN. El CLI canónico `rein context .`, ejecutado en case2, no resolvió comandos de test, lint o typecheck y no encontró un plan; esto describe el contexto disponible, no demuestra ausencia de pruebas.

Como siguiente paso apropiado, recomiendo una corrección nativa y acotada en admin, con revisión independiente centrada en permisos y aislamiento antes de incorporarla. No se justifica orquestación por el mero hecho de existir dos repositorios, ni un rediseño del servidor. Si el alcance exigiera modificar el adaptador HTTP o la persistencia, haría falta evidencia adicional antes de decidir esa ampliación.

No quedan decisiones de producto imprescindibles para recomendar esta corrección mínima. Las lagunas indicadas son límites de evidencia técnica y se conservan sin suponer una respuesta. Investigación cerrada sin implementación, plan, delegación, red ni instalación; sólo se escribió este informe.
