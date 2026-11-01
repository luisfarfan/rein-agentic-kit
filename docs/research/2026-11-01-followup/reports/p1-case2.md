# Impacto: acceso y aprobación en lote por negocio

La brecha observada está en la visibilidad del panel de `admin`. La API ya contiene una operación de aprobación en lote con controles de negocio, área y clave de autorización. Recomiendo ampliar el acceso al panel para `inventory:manage` y `pricing:manage`, conservando los permisos actuales de visibilidad y sin debilitar la autorización de la API. La evidencia disponible no permite afirmar que el flujo HTTP y la aprobación persistida funcionen de extremo a extremo.

## Capacidades existentes y comportamiento faltante

**Observado.** `admin/menu.py:1` define `VISIBLE_WITH` únicamente con `catalog:manage` y `orders:manage`. Su consumidor, `show_approvals` en `admin/view.py:3-4`, muestra el panel si el usuario tiene cualquiera de esos permisos. Por eso alguien que sólo tenga `inventory:manage` o `pricing:manage` no puede entrar mediante esta condición.

El productor de la solicitud, `approval_request` en `admin/view.py:6-9`, ya acepta varias propuestas y genera un `POST /approvals/batch`, con sus identificadores en `proposal_ids` y la clave en `X-Grant-Key`. `admin/help.md:1-3` confirma que inventario y precios comparten panel y que las mutaciones se autorizan en la API.

En el repositorio separado `api`, `approve_batch` (`api/approvals.py:3-6`) aplica `allowed` a todas las propuestas antes de devolver resultados aprobados. `allowed` (`api/policy.py:3-6`) exige simultáneamente:

- Igualdad entre el negocio del usuario y el de cada propuesta.
- El permiso correspondiente al área: `inventory:manage` o `pricing:manage` (`api/policy.py:1`).
- Igualdad entre la clave recibida y la clave de cada propuesta.

**Inferencia.** La modificación funcional mínima está en la puerta de entrada de `admin`, no en añadir otro mecanismo de aprobación en lote ni otro permiso de API. La visibilidad del menú no concede autorización para mutar.

## Contratos y casos negativos

El cliente produce identificadores y un encabezado; la función de API consume objetos de propuesta y un argumento `grant_key`. No aparece el adaptador que autentica al usuario, resuelve esos identificadores y transmite el encabezado. Ese límite impide verificar la conexión entre ambos contratos.

En el código leído, una propuesta de otro negocio, un área sin permiso o una clave distinta provoca `PermissionError` para todo el lote. Un usuario con permiso sólo de inventario no puede aprobar un lote que incluya precios. Una única clave debe coincidir con todas las propuestas seleccionadas; si sus claves difieren, el lote se rechaza. Los permisos de catálogo y pedidos por sí solos no autorizan aprobaciones en esta política.

Un lote vacío devuelve una lista vacía. Un área desconocida puede producir `KeyError`, pues la política indexa directamente `AREA_PERMISSION`. No se observa validación ni traducción de estas excepciones a respuestas HTTP.

## Estrategia recomendada y motivos

Recomiendo trabajo nativo y acotado en `admin`: ampliar `VISIBLE_WITH` con los dos permisos que la API ya reconoce. Conservar sus entradas actuales evita retirar acceso existente al panel sin una decisión explícita del producto. No implica conceder a esos usuarios capacidad de aprobar: la API sigue siendo la autoridad.

No hay evidencia que justifique cambiar `api/policy.py` o `api/approvals.py`. Tampoco conviene aceptar parcialmente un lote, eliminar la clave o sustituir la comprobación por negocio: cambiaría las garantías observadas. No recomiendo planificación formal para la modificación de visibilidad sustentada por este ejemplo. Sí recomiendo verificación independiente del límite de autorización antes de dar por satisfecho el comportamiento completo.

## Riesgos técnicos y operativos

La ampliación del menú expone a más usuarios el flujo existente. Un error de autorización en la resolución de identificadores podría cruzar negocios aunque el menú fuera correcto; ese adaptador no está disponible. Tampoco se muestran permisos de lectura, carga del listado o tratamiento de selecciones mixtas en el panel.

`approve_batch` devuelve estados, pero no muestra escrituras, transacciones, eventos ni auditoría. Su validación previa de todo el lote no demuestra atomicidad de persistencia. Concurrencia, repetición de solicitudes e idempotencia quedan sin evidencia; no se puede afirmar que estén resueltas ni que falten en el sistema real.

Revertir la ampliación del menú sería una reversión acotada de visibilidad. No desharía aprobaciones realizadas; no aparece un mecanismo de reversión de esas operaciones.

## Evaluación separada

| Dimensión | Valor | Motivo y evidencia |
|---|---|---|
| Complejidad de implementación | low | La brecha demostrada es la lista consumida por `show_approvals` (`admin/menu.py:1`, `admin/view.py:3-4`); solicitud y función de lote ya existen. |
| Riesgo de modificación | high | El acceso habilita una mutación sujeta a límites de negocio, área y clave (`api/policy.py:3-6`); una regresión puede afectar autorización. La edición propuesta es pequeña, pero su consecuencia exige cuidado. |
| Incertidumbre del sistema | high | No se muestra el adaptador HTTP entre `approval_request` y `approve_batch`, ni persistencia o carga del panel. |
| Alcance de repositorios | low | Dos repositorios investigados; cambio recomendado sólo en `admin`, con `api` como contrato que preservar. Ampliar cambios a la API no está justificado por la evidencia. |
| Necesidad de planificación | low | La modificación sustentada tiene una dependencia directa y acotada; las incógnitas deben conservarse, no convertirse en alcance supuesto. |
| Necesidad de verificación independiente | high | Debe corroborarse que la nueva visibilidad mantiene los rechazos por negocio, permiso y clave, y que el adaptador realmente invoca la política. |

## Verificación y límites

Se leyeron completos los cinco archivos de aplicación disponibles en `api` y `admin` citados arriba. Se consultó `rein context .`: no resolvió comandos de verificación ni encontró un plan. No se leyeron tests ni rúbricas de evaluación, informes anteriores o historiales. No se ejecutaron tests de producto ni comprobaciones funcionales; las conclusiones provienen de lectura estática. Se utilizaron tres llamadas de investigación, sin delegados, red ni instalaciones.

Queda por verificar dónde se autentica la petición, cómo se resuelven `proposal_ids`, cómo llega `X-Grant-Key` a la política y qué operación persiste la aprobación. Esa comprobación del adaptador sería la siguiente evidencia necesaria para validar el flujo completo; no está disponible en los archivos inspeccionados y no se amplía la investigación fuera del alcance autorizado. La selección de propuestas con claves distintas también requiere confirmar la semántica real de esa clave antes de proponer cualquier cambio de contrato.
