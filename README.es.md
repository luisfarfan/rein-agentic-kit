# REIN

[English](README.md) | **Español**

**Criterio, verificación y control para desarrollar con agentes.**

REIN es un conjunto de skills y una CLI para trabajar con **Claude Code y Codex**:
entender el impacto de un cambio, convertir requisitos en trabajo verificable,
implementar con el nivel de coordinación adecuado y obtener una revisión
independiente cuando el riesgo lo exige.

La sesión actual es el punto de partida. REIN aporta una guía compartida para
investigar, decidir, ejecutar y comprobar; cuando conviene delegar, utiliza
**Orca** para gestionar workers, worktrees y decisiones del usuario.

[![CI](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/luisfarfan/rein-agentic-kit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Runtime dependencies](https://img.shields.io/badge/runtime%20deps-none-success.svg)](pyproject.toml)

## Qué aporta a tu trabajo

### Entender qué hace falta cambiar

Antes de implementar, REIN orienta al agente a leer código y pruebas, distinguir
capacidades existentes de comportamiento faltante y seguir los consumidores del
cambio. Una petición de interfaz puede depender de una capacidad que el backend ya
tiene; un ajuste pequeño puede afectar permisos, dinero o persistencia.

Si falta una respuesta que puede cambiar la solución, Discover profundiza en esa
pregunta, reutiliza lo ya encontrado y deja explícita la incertidumbre. Una
investigación solicitada como tal conserva su alcance de solo lectura.
Si necesitas conservar un informe estructurado, `discover-check` comprueba la
vigencia de sus referencias a archivos antes de reutilizar esa evidencia.

**Te ayuda a delimitar la solución y a detectar las preguntas que deben resolverse
antes de tocar el código.**

### Convertir requisitos en trabajo que se puede comprobar

Plan descompone el cambio en tareas con criterios de aceptación, repositorio,
dependencias y una verificación ejecutable. Puede usar un `tasks.md` sencillo o una
estructura OpenSpec. El borrador se revisa contigo antes de escribir el plan.

La CLI comprueba aspectos mecánicos del plan, como dependencias inválidas y
problemas en sus verificaciones; el agente evalúa si los criterios realmente
cubren lo pedido.

**Te da una definición concreta de terminado y una forma de comprobar cada parte.**

### Elegir el proceso según el trabajo

Una corrección localizada puede resolverse en la sesión actual. Un cambio con
requisitos y dependencias puede necesitar un plan. Un cambio en autorización o
estado persistente necesita revisión independiente. Las tareas que se benefician
de ejecución paralela o recuperación pueden justificar Ship.

La política de entrada permite tomar esas decisiones desde una petición normal,
una vez adoptada por el proyecto. `rein route` ofrece recomendaciones basadas en
un informe cuando necesitas un registro estructurado; su uso es opcional y no
inicia workers.

**Puedes usar las capacidades por separado y añadir coordinación cuando aporta
algo concreto.**

### Verificar resultados con señales observables

`rein gate` ejecuta las comprobaciones del proyecto y distingue un resultado
correcto, una comprobación fallida y un entorno que no pudo ejecutarla. Una
herramienta ausente o una suite que no llegó a correr conserva su propio diagnóstico.

Los comandos se resuelven desde la configuración del proyecto, sus task runners
o la detección del stack. En ejecución delegada hay verificaciones por tarea y
comprobaciones completas por repositorio; los resultados correctos pueden
reutilizarse mientras coincidan el estado del código, el comando y el entorno.
La política de frontend contempla evidencia del renderizado, que debe obtenerse
y aportarse por separado.

**Puedes saber qué se comprobó, qué falló y qué sigue sin demostrarse.**

### Separar implementación y revisión

La auditoría combina comprobaciones mecánicas con el juicio de una sesión
independiente del implementador. Es parte del método para cambios en seguridad,
permisos, dinero, persistencia, concurrencia o contratos entre servicios.

Los hallazgos bloqueantes requieren un defecto confirmado y evidencia concreta.
La aprobación queda vinculada al estado revisado mediante hashes: al cambiar los
archivos revisados, esa aprobación deja de ser vigente.

**Una revisión tiene un alcance verificable y debe actualizarse cuando cambia el
código que aprobó.**

### Coordinar trabajo delegado sin perder las decisiones

Quick permite encargar explícitamente un cambio pequeño a otro agente. Ship
coordina un plan mediante Orca: respeta dependencias, lanza workers con contexto
nuevo por tarea, ejecuta gates, gestiona reintentos y solicita auditoría.

Ship guarda su estado para poder reanudarlo. Las preguntas, los bloqueos y las
decisiones pendientes llegan mediante watchers y gates de Orca; puedes
consultarlos y responderlos desde la sesión que inició el trabajo. También admite
tareas de revisión humana con una ficha de criterios y evidencia.

**Puedes seguir un trabajo largo, intervenir donde hace falta y recuperar su
estado sin reconstruir toda la conversación.**

### Mantener límites al delegar

REIN comprueba cambios en configuración protegida, vuelve a ejecutar determinados
hooks y detecta actividad fuera del worktree en los checkouts vigilados. Permite
configurar variables por worktree para separar recursos de pruebas, como bases de
datos, y conserva registros de los gates.

La selección de workers usa las observaciones de cuota de Orca y sus límites
configurados. Una cuota desconocida o desactualizada impide autorizar nuevos
workers; seleccionar un proveedor explícitamente conserva esos controles.

**La delegación tiene límites de alcance, capacidad y escalamiento que puedes
inspeccionar.** Estos controles detectan determinadas infracciones al finalizar;
REIN no proporciona un sandbox.

## Cómo usarlo según tu caso

| Situación | Capacidad útil | Resultado esperado |
| --- | --- | --- |
| El alcance está claro y el cambio es localizado | Sesión actual con la guía de entrada | Implementación y verificación pertinentes |
| No sabes si falta una capacidad o dónde está el problema | Discover | Evidencia, incertidumbres y siguiente comprobación |
| Hay requisitos o dependencias que organizar | Plan | Tareas con criterios y verificaciones |
| El cambio toca límites sensibles | Audit, desde otra sesión | Hallazgos y veredicto sobre el estado revisado |
| Quieres encargar un cambio pequeño a otro agente | Quick | Worker en Orca, normalmente en su propio worktree |
| Hay trabajo paralelo o una ejecución que necesita estado recuperable | Ship | Coordinación del plan, gates y auditoría |
| Un trabajo delegado necesita tu decisión | Answer y Watch | Preguntas visibles y respuestas registradas |

Por ejemplo:

- **«El menú no muestra una acción que el usuario puede realizar».** Inspeccionar
  interfaz, permisos y contrato existente; resolver en la sesión actual si la
  evidencia permite una corrección localizada.
- **«Quiero evitar emisiones duplicadas al reintentar un cobro».** Investigar
  concurrencia, persistencia y efectos externos; preparar un plan verificable y
  obtener una auditoría independiente.
- **«Implementa estas tareas independientes y avísame si algo se bloquea».**
  Usar Ship cuando la coordinación de workers aporte una ventaja concreta.

## Empezar

### 1. Exponer las skills a tu agente

Para Claude Code, desde el marketplace:

```sh
claude plugin marketplace add luisfarfan/rein-agentic-kit
claude plugin install rein@rein-agentic-kit --scope project
```

Para actualizar esa instalación:

```sh
claude plugin marketplace update rein-agentic-kit
claude plugin update rein@rein-agentic-kit
```

También puedes cargar el plugin directamente desde un checkout local:

```sh
claude --plugin-dir /ruta/absoluta/rein/plugins/rein
```

Para Codex, este repositorio expone las mismas siete skills mediante
[`.agents/skills`](.agents/skills) y el manifiesto del plugin. Para usarlas en otro
proyecto, sigue la [guía de instalación local y proveedores](docs/native-providers.md).
Ambos proveedores comparten el mismo núcleo en `plugins/rein`, con las mismas
reglas de alcance, verificación y revisión independiente.

### 2. Adoptar la guía de entrada en el proyecto

Integra una sola copia de [`ENTRY.md`](plugins/rein/ENTRY.md) en el `AGENTS.md`
del proyecto, conservando sus instrucciones. Para Claude, añade `@AGENTS.md` al
`CLAUDE.md` vecino cuando sea necesario. Abre una sesión nueva en el checkout o
worktree correcto y describe la tarea normalmente.

**Instalar el plugin expone las skills; adoptar la guía habilita la orientación
compartida para las tareas habituales.** REIN no modifica esos archivos
automáticamente. La guía es una instrucción para el agente, cuyo cumplimiento
depende también del proveedor y del resto de instrucciones del proyecto.

Puedes invocar una capacidad directamente:

| Skill | Claude Code | Codex |
| --- | --- | --- |
| Investigar | `/rein:rein-discover` | `$rein:rein-discover` |
| Planificar | `/rein:rein-plan` | `$rein:rein-plan` |
| Delegar un cambio pequeño | `/rein:quick` | `$rein:quick` |
| Coordinar un plan | `/rein:ship` | `$rein:ship` |
| Auditar | `/rein:rein-audit` | `$rein:rein-audit` |
| Responder preguntas | `/rein:answer` | `$rein:answer` |
| Asignar el rol de una sesión | `/rein:rein-role` | `$rein:rein-role` |

La instalación independiente puede exponer nombres sin prefijo; consulta el
selector de skills si hay ambigüedad.

### 3. Usar la CLI y, si vas a delegar, Orca

La CLI requiere **Python 3.9 o superior** y no tiene dependencias de ejecución.
Para instalarla desde este checkout en un entorno aislado:

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/rein doctor .
```

También puedes ejecutar el código directamente:

```sh
python3 plugins/rein/bin/rein doctor .
```

La CLI instalada está disponible para terminales normales con
`uv tool install rein-agentic-kit` o `pipx install rein-agentic-kit`. Las herramientas
de pruebas y construcción del proyecto se instalan por separado.

Quick y Ship necesitan **Orca**, su CLI y los repositorios registrados allí.
Ship se ejecuta dentro de un terminal de Orca; `rein ship <change> --detach` abre
ese terminal. Las comprobaciones locales, como `doctor`, `detect` y `gate`, pueden
usarse sin Orca.

## Configuración y comprobaciones

REIN respeta esta precedencia: **`flow.config.json` → task runner del proyecto →
detección del stack**. Reconoce Python, Node/TypeScript, Rust y Go, además de
subtipos de frontend e infraestructura. En monorepos con varios candidatos requiere
seleccionar el subproyecto.

La [configuración de ejemplo](plugins/rein/flow.config.example.json) y la
[referencia operativa](docs/reference.md#configuration) describen los comandos,
el entorno por worktree, los límites de ejecución y las protecciones.

```sh
rein doctor .                            # Examinar stack, comandos y configuración
rein gate .                              # Ejecutar comprobaciones y decidir si pasan
rein plan-check tasks.md --root . --gate  # Validar mecánicamente un plan
rein review check                        # Consultar si la revisión sigue vigente
```

`rein gate` devuelve `0` si pasa, `1` si falla y `126` si no puede comprobarlo.
`rein verify` sirve para diagnosticar si los comandos pueden ejecutarse: su código
de salida no certifica que las pruebas hayan pasado. Consulta la
[referencia de comandos](docs/reference.md#cli) para flags y flujos completos.

## Qué está demostrado y qué sigue abierto

El repositorio incluye pruebas de regresión, fixtures de Orca y evaluaciones con
informes y trazas. La [validación de entrada](docs/research/2026-11-01-entry/RESULTS.md)
registra casos acotados de ejecución nativa e investigación selectiva en Codex.
La [matriz de aceptación](docs/research/2026-11-01-evolution/ACCEPTANCE.md) distingue
pruebas automatizadas de integraciones reales; conserva pendientes de Claude y
Quick entre proveedores.

La [comparación con ejecución nativa](docs/research/2026-11-01-followup/RESULTS.md)
obtuvo el mismo comportamiento funcional en una tarea pequeña, con más trabajo de
proceso al añadir informes y routing. Las
[evaluaciones de planificación, auditoría y orquestación](docs/research/2026-10-28-post-sdd/CIERRE-PROVISIONAL-FASE2.md)
orientan el uso selectivo de esas capas. La evidencia disponible no establece una
mejora general de calidad, velocidad o ahorro.

Conviene tener presentes estos límites:

- La auditoría puede omitir defectos; una aprobación se limita al estado y al
  alcance revisados.
- Las protecciones de worktree observan los checkouts vigilados mediante Git.
  Los archivos ignorados y los cambios fuera de esos repositorios quedan fuera
  de esa detección.
- Ship termina en revisión: publicar, abrir una PR, integrar y desplegar quedan
  fuera de su ejecución. `rein done --push` permite únicamente el push opcional
  del commit de archivo del plan, con sus propias restricciones.
- Las cuotas dependen de observaciones utilizables de Orca. REIN puede reanudar
  despachos cuando llegan datos válidos, pero no garantiza que la fuente se
  actualice ni que quede capacidad para terminar toda una tarea.

La medición de consumo sigue disponible mediante `token-report`, `ledger` y
`baseline`. El informe de tokens lee transcripciones de Claude Code; no es una
contabilidad completa de todas las sesiones de Codex. Los estudios históricos
están enlazados en la documentación de investigación.

## Documentación y desarrollo

- [Instalación, proveedores y adopción por proyecto](docs/native-providers.md).
- [Referencia de CLI, configuración y ejecución delegada](docs/reference.md).
- [Estructura del núcleo compartido](plugins/rein/README.md).
- [Decisiones del proyecto](docs/decisions.md).
- [Evolución y registro de evidencia](docs/research/2026-11-01-evolution/README.md).

Para ejecutar la regresión desde la raíz del repositorio:

```sh
python3 -m unittest discover -s tests -q
```

Licencia [MIT](LICENSE).
