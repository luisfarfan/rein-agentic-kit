# Reproducibilidad

Todos los scripts usan solo la biblioteca estándar de Python, no modifican ningún repositorio de
trabajo y escriben únicamente en el directorio `--out` que reciben. Los experimentos con modelo
lanzan `claude -p` y consumen cuota de la suscripción, poca pero real. Los análisis observacionales
no consumen nada.

## Scripts (en `bench/`)

| Script | Qué hace | ¿Cuota? | Tiempo |
|---|---|---|---|
| `analyze_workers.py --out DIR` | Recorre `~/.claude/projects/*/*.jsonl` y `~/.codex/sessions/**`, se queda con los transcripts cuyo brief contiene el preámbulo de un worker de `rein ship` y calcula las métricas por worker y los agregados | No | ~3 s |
| `analyze_states.py DIR` | Agrega los archivos `.rein/ship/**/*.json` de `~/Developer` y `~/orca/workspaces`: dispatches, intentos, causas de fallo y rondas de auditoría | No | <1 s |
| `ground_truth.py REPO NAME [--exclude-tests] [--json]` | Calcula por AST la verdad de referencia «quién llama a NAME», o con `--candidates` lista funciones aptas para preguntas | No | ~10 s en proxima-api |
| `e1_startup_context.sh SRC OUT [REPS]` | E1: el contexto del primer turno de un worker Claude según la configuración de lanzamiento. Usa Haiku y una respuesta «OK» | ~12 llamadas Haiku | ~2 min |
| `e2_context.py --repos DIR --out DIR [--only Qn] [--strategies A,B,C,D,E,BF,CF,H] [--reps N] [--model haiku] [--seed N]` | E2/E3: estrategias de contexto sobre preguntas con verdad de referencia y un bug sembrado. BF y CF fuerzan el uso del grafo; H añade el handoff. Reanuda las celdas que ya están hechas | 1 sesión `claude -p` por celda | 0,2–1,5 min por celda |
| `summarize_e2.py RESULTS.jsonl` | Medianas y rangos por (pregunta, estrategia). Vuelve a puntuar Q4 desde los worktrees que se conservan | No | <1 s |

Comandos exactos usados:

```bash
python3 -I e2_context.py --repos $S/repos --out out/e2  --reps 2 --only Q1,Q2,Q3,Q4,Q6            # Haiku, A..E
python3 -I e2_context.py --repos $S/repos --out out/e2f --reps 2 --only Q3,Q6 --strategies BF,CF --seed 11
python3 -I e2_context.py --repos $S/repos --out out/e2s --reps 2 --only Q6 --strategies A,BF --model sonnet --seed 13
python3 -I e2_context.py --repos $S/repos --out out/e3  --reps 3 --only Q5 --strategies A,H
```

Los resultados crudos están en `data/*_results.jsonl` y la tabla agregada en `data/e2_summary.md`.
Las primeras 11 celdas de Q4 se puntuaron además con la suite completa. Después se quitó esa
puntuación, porque la suite ya falla en el clon (ver Limitaciones), y `summarize_e2.py` vuelve a
puntuar todas las celdas de Q4 igual: tests de cuota más diff.

## Preparación usada

```bash
S=<scratch>; mkdir -p $S/repos && cd $S/repos
git clone -q --depth 1 file:///Users/luisfarfan/Developer/labs/agents/rein rein                # 1c0f9ff
git clone -q --depth 1 file:///Users/luisfarfan/Developer/products/proxima/proxima-api api        # b1f4105
cp -R rein rein-bug && sed -i '' 's/used >= max_pct/used > max_pct/' rein-bug/plugins/rein/lib/quota.py
for r in rein api rein-bug; do (cd $r && codegraph init . && graphify update .); done
python3 -I ground_truth.py $S/repos/rein brake_reason --exclude-tests --json > out/gt_q1.json
python3 -I ground_truth.py $S/repos/api resolve_effective_sale_price --exclude-tests --json > out/gt_q2.json
python3 -I ground_truth.py $S/repos/api invalidate_website_composition --exclude-tests --json > out/gt_q3.json
```

`gt_q5.json` (llamadas que pasan `ctx`) y `gt_q6.json` (impacto a dos niveles) se generaron con los
fragmentos AST que hay en `bench/gt_extra.py`.

## Controles aplicados

- **Mismo modelo y misma configuración** en todas las estrategias de E2: Haiku y el perfil lean de E1
  (`--strict-mcp-config --disable-slash-commands --tools Bash,Read,Edit,Write,Grep,Glob`, con los
  plugins de usuario desactivados). Las estrategias solo se diferencian en el prompt o en
  `--append-system-prompt`.
- **Mismo commit**: los clones están congelados y los índices se construyeron una vez antes de los
  runs.
- **Orden aleatorio con semilla** (`--seed 7`) e intercalado de estrategias, para repartir la deriva
  temporal del modelo.
- **Caché**: una llamada de calentamiento antes de la matriz. Cada celda es una sesión nueva
  (`--no-session-persistence`). Los tokens se reportan separando `cache_read` y `cache_write`.
- **Bug sembrado**: cada repetición trabaja sobre una copia nueva del repo con el bug.
- **Puntuación determinista**: precisión y recall por archivo contra el AST, o los tests de cuota más
  el diff exacto.
- **Contaminación**: los índices `.codegraph/` y `graphify-out/` existen en todos los clones, así que
  la estrategia A *podría* descubrirlos. Se registra si se usaron (columnas `codegraph_calls` y
  `graphify_calls`).

## Limitaciones conocidas

- **E1 mide en modo `-p`.** Los workers reales corren en modo interactivo dentro de Orca: 68,7k frente
  a 59,0k en `-p`, con más hooks. Las reducciones absolutas se trasladan por analogía, no se midieron
  en interactivo. La validación interactiva es el primer paso del roadmap (P0-1).
- **E2 usa Haiku**, no Sonnet ni Codex, para no gastar cuota. Las conclusiones de techo y no uso son
  robustas. Las de magnitud no se pueden extrapolar sin la confirmación con Sonnet (§4.3).
- **Pocas repeticiones** (n=2 por celda). Solo se afirma una diferencia cuando no se solapan los
  rangos.
- **La suite completa de REIN ya falla en un clon `--depth 1`** (13 fallos y 1 error por entorno:
  corpus, historial de git, Orca). Para el bug sembrado se usan los tests de cuota y el diff, no la
  suite.
- **La consumición de cuota no se puede observar** a la granularidad de un experimento:
  `orca account list` da porcentajes enteros. Se reportan tokens y el coste equivalente en API (solo
  como proxy; la suscripción no factura por API).
- **Los clasificadores de fallos** y de «editó / no editó» del análisis observacional son heurísticas
  con regex. Las cifras indican el orden de magnitud.

## Diseño propuesto: `rein benchmark` (ligero, determinista)

El objetivo es responder de forma continua a «¿este cambio en REIN, en el brief o en el perfil de
lanzamiento mejora algo?», sin LLM en el bucle de decisión y con un coste acotado.

```
rein benchmark observe [--since 2026-10-01]   # 0 cuota: analyze_workers + analyze_states → ledger
rein benchmark startup [--profile worker]     # 1-2 llamadas Haiku: contexto de arranque del perfil (detecta el trampa de 533k)
rein benchmark suite --tier pilot|full        # tareas con verdad de referencia, N reps, A/B de una variable
```

- **`observe`** debería ejecutarse siempre al terminar `rein ship`. Hoy `token_report` solo lee
  Claude; el parser de Codex ya está en `analyze_workers.py`. Para cada ejecución añade al ledger:
  - el contexto del primer turno;
  - turnos y tokens por rol;
  - dispatches por tarea y causa de reintento;
  - rondas de auditoría.
- **`startup`** es la red de seguridad de P0-1. Si el contexto inicial del perfil supera un umbral
  (por ejemplo 30k), `rein doctor` falla.
- **`suite`** son las preguntas de verdad de referencia de E2, más una tarea de implementación
  sembrada por stack. Compara dos variantes (`--a`, `--b`), corre por defecto con Haiku y reporta
  medianas y rangos. Solo se ejecuta bajo petición.
