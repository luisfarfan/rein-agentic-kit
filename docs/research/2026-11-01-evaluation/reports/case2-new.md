# Impacto: acceso al panel y aprobación por lotes

## Conclusión y estrategia recomendada

La capacidad de aprobar lotes ya existe en la API. La discrepancia comprobada está en la visibilidad del panel: el administrador admite `catalog:manage` y `orders:manage`, mientras la API autoriza por `inventory:manage` o `pricing:manage`, según el área de cada propuesta.

Recomiendo un cambio acotado en `admin`: ampliar los permisos de entrada con los dos permisos de gestión ya reconocidos por la API, conservando los permisos actuales de visibilidad. Mantener la autorización por propuesta en la API. Esto permite entrar a quienes hoy quedan excluidos sin crear un permiso nuevo ni ampliar sus facultades de aprobación. Conservar catálogo y pedidos evita retirar accesos existentes; esos permisos, por sí solos, no autorizan mutaciones en la API observada.

La ejecución nativa y acotada resulta suficiente para el cambio identificado; no se justifica delegación ni un plan separado con la evidencia disponible. Sí se recomienda verificación independiente de la frontera de permisos antes de integrar. Esta es una recomendación de estrategia, no un plan de implementación.

## Evidencia y capacidades existentes

Rutas relativas a `/Users/luisfarfan/Developer/labs/agents/rein/.rein/p0-live-evaluation/new/case2`:

- `admin/menu.py:1`, `VISIBLE_WITH`: sólo catálogo y pedidos pueden habilitar la visibilidad.
- `admin/view.py:3–4`, `show_approvals`: basta la intersección con esos permisos; no examina propuestas ni negocio. Un usuario con sólo inventario o precios no ve el panel.
- `admin/view.py:6–9`, `approval_request`: produce un POST a `/approvals/batch`, con IDs seleccionados en `proposal_ids` y una cabecera `X-Grant-Key`. Ya existe la construcción de una petición por lote.
- `admin/help.md:1–3`: documenta un panel compartido por inventario y precios y sitúa la autorización de mutaciones en la API. Es documentación, no prueba de funcionamiento integral.
- `api/approvals.py:3–6`, `approve_batch`: consume un usuario, propuestas completas y una clave; comprueba todas las propuestas antes de devolver estados `approved`. Si alguna comprobación devuelve falso, lanza `PermissionError('forbidden')` para el lote entero.
- `api/policy.py:1–6`, `AREA_PERMISSION` y `allowed`: exige coincidencia entre el negocio del usuario y el de la propuesta, el permiso correspondiente al área y la clave de concesión de la propuesta.

## Comportamiento faltante y contratos

Falta habilitar el acceso al panel para inventario y precios en la política de visibilidad. La API ya expresa los permisos solicitados; no hay evidencia que justifique cambiarlos.

El productor de la solicitud es `admin.view.approval_request`. El consumidor de dominio observado es `api.approvals.approve_batch`, que delega en `api.policy.allowed`. Son repositorios distintos según el alcance facilitado. El contrato HTTP visible incluye ruta, IDs y cabecera; el contrato de dominio requiere objetos completos. No se muestra el adaptador que autentica al usuario, resuelve los IDs, obtiene propuestas y traduce la cabecera al argumento `grant_key`. Por ello no está demostrada la conexión de extremo a extremo.

No se observan almacenamiento, migraciones, escritura de inventario o precios, eventos ni otros efectos secundarios. La función devuelve diccionarios; eso no prueba una aprobación persistida. Tampoco se observa el origen ni el filtrado por negocio de la lista presentada en el panel.

## Riesgos técnicos y operativos

La visibilidad y la autorización son controles diferentes. Abrir el panel no debe sustituir las comprobaciones del servidor. Un usuario de inventario que seleccione una propuesta de precios sin ese permiso será rechazado; con ambos permisos puede superar la comprobación de áreas. Una propuesta de otro negocio o con una clave distinta rechaza el lote completo. Como se envía una sola clave, un lote con claves diferentes no supera la política actual, aunque todas sus propuestas pertenezcan al mismo negocio.

Estas conclusiones proceden de lectura de código, no de ejecución. Además, `all([])` permite que un lote vacío devuelva `[]`; áreas desconocidas o campos ausentes pueden producir excepciones de acceso en lugar de `PermissionError`. No se observa su traducción a respuestas HTTP. No recomiendo modificar estos comportamientos incidentalmente al ajustar la visibilidad.

El rechazo previo a construir los resultados evita resultados parciales en esta función, pero no demuestra atomicidad de almacenamiento, idempotencia ni protección frente a cambios concurrentes de permisos o propuestas. Esas garantías permanecen desconocidas. El impacto financiero efectivo de una aprobación de precios tampoco puede establecerse sin el consumidor persistente.

Como inferencia operativa, revertir el ajuste de visibilidad restauraría el acceso anterior, pero no desharía aprobaciones que un sistema real hubiera persistido. No se ha observado un mecanismo de reversión de dichas aprobaciones.

## Clasificaciones

| Dimensión | Evaluación | Motivo y evidencia |
| --- | --- | --- |
| Complejidad de implementación | Baja para el cambio identificado | `VISIBLE_WITH` y `show_approvals` concentran el acceso; la API ya comprueba ambos permisos y procesa lotes. No equivale a demostrar que toda la integración esté completa. |
| Riesgo de modificación | Alto | Cambia quién accede a una función de aprobación. `allowed` protege negocio, área y clave; preservar esta frontera y los accesos existentes es esencial. |
| Incertidumbre del sistema | UNKNOWN | Faltan adaptador HTTP, carga de propuestas, listado por negocio y persistencia. Pueden cambiar el alcance necesario para garantizar el resultado integral. |
| Alcance de repositorios | Bajo para la modificación recomendada | Dos repositorios investigados; el cambio observado corresponde a `admin/menu.py`, con `admin/view.py` como consumidor. No se justifica modificar `api`; su contrato debe verificarse entre ambos. |
| Necesidad de planificación | Baja con el alcance observado | Hay una discrepancia concreta y localizada; no se observan migraciones ni necesidad de cambiar contratos. Si aparece una carencia en el adaptador o listado, esta conclusión debe revisarse. |
| Necesidad de verificación independiente | Alta | La condición de negocio y los permisos por área de `api/policy.py` deben seguir siendo obligatorios aunque cambie el acceso al panel; el rechazo por lote de `approve_batch` es parte del comportamiento a preservar. |

## Verificación y límites

Se leyeron completos los cinco archivos disponibles indicados arriba, la skill asignada y `PROVIDERS.md`. Se consultó el CLI canónico con `rein context .`; su contexto corresponde a la carpeta de sesión, no a los repositorios `api` y `admin`, y no acredita comandos de verificación para estos. Se emplearon tres llamadas de investigación, dentro del límite temporal. No se leyeron tests, rúbricas, Git, informes anteriores ni historiales; no se ejecutaron tests, red, instalaciones ni delegados. No hubo cambios de producto.

La verificación relevante debería cubrir acceso con inventario y precios por separado, conservación del acceso actual, rechazo sin permisos, lotes autorizados y rechazo íntegro con otra área sin permiso, otro negocio o clave incorrecta. Esto es cobertura recomendada, no resultados ejecutados. El lote mixto requiere permisos para cada área y una clave válida para todas las propuestas según el contrato actual.

La pregunta pendiente que puede cambiar riesgo y alcance es: ¿el adaptador real resuelve los IDs y el usuario de forma confiable, limita el listado al negocio y persiste las aprobaciones con las garantías necesarias? El siguiente examen sería leer ese adaptador, el origen del listado y la escritura persistente. Esos componentes no están en la evidencia disponible; se detiene la investigación conservando la limitación, sin afirmar que el ajuste de menú demuestre por sí solo el funcionamiento integral. No hace falta una decisión del usuario para recomendar el ajuste de visibilidad observado.
