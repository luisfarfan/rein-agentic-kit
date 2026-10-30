# Guard: aislar al worker en lugar de culparlo

Fecha: 2026-10-28 · Evidencia: §9.4 de [INFORME.md](INFORME.md) y las pruebas de abajo, reproducibles
con los comandos de §3. **No se ha cambiado REIN.**

## 1. El problema, con la causa raíz

- **Qué hace hoy el guard.** Al lanzar un worker, `outside_snapshot` (`guard.py:770`) fotografía los
  checkouts del plan: HEAD, refs y el contenido de cada ruta sucia o sin seguir. Al terminar,
  `outside_changes` (`guard.py:890`) y `_head_moves` (`guard.py:827`) atribuyen **al worker** todo lo
  que cambió allí:
  - un commit hecho en ese checkout con la identidad de git del checkout;
  - cualquier archivo modificado.
- **Por qué falla con el trabajo real.** Tú, otras sesiones tuyas y el worker comparten **la misma
  identidad de git**. Un cambio de archivo no dice qué proceso lo hizo. La atribución es, por
  construcción, imposible de hacer bien después del hecho.
- **Medido:**
  - en 26 de 26 rechazos no había escritura del worker (§9.4);
  - y se volvió a ver durante este piloto: mientras yo trabajaba, `proxima-api` recibió tres commits
    tuyos (`feat(mcp)…`, 23:11–23:16, según el reflog).

  Si hubiera estado corriendo un `rein ship` sobre ese repo, esos commits habrían tumbado sus tareas.

Ignorar los cambios externos no es una solución, porque deja sin vigilancia justo lo que el guard
debe vigilar. Lo robusto es **impedir** que el worker escriba fuera. Así, lo que cambie fuera no es
suyo por construcción, y el guard pasa de «culpar» a «avisar».

## 2. Prevención por el sistema operativo: probada

Se probó con un repo y un worktree desechables (`~/Developer/sandbox/rein-pilot/guardtest`). Se usó
`claude -p` con Haiku, en `bypassPermissions` como los workers de Orca, con `--settings` en cada
variante.

| Variante | Commit en su worktree | `git -C ../main commit` | Bash escribe en `main/` | Write/Edit escribe en `main/` |
|---|---|---|---|---|
| Sin sandbox (lo de hoy) | ✅ | ✅ (**sin bloqueo**) | ✅ (**sin bloqueo**) | ✅ (**sin bloqueo**) |
| E: solo el sandbox de Claude Code | ✅ | ✅ (**sin bloqueo**: el `.git` compartido es escribible) | ❌ bloqueado | ✅ (**sin bloqueo**: Write/Edit no pasan por el sandbox) |
| F: sandbox más `deny Edit(main/**)` | ❌ también rompe el commit legítimo | ❌ | ❌ | ❌ |
| **I: sandbox, más `denyWrite` del índice, HEAD y rama de `main`, más un hook `PreToolUse` de ruta** | ✅ | ❌ bloqueado | ❌ bloqueado | ❌ bloqueado (hook) |

La primera fila se infiere de la variante E y del comportamiento por defecto de Claude Code; no se
ejecutó como prueba aparte.

**Configuración ganadora (I)**, que va en el `.claude/settings.local.json` que
`launch_profile.py` **ya escribe** en el worktree de cada implementador:

```json
{
  "sandbox": {
    "enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
    "filesystem": {"denyWrite": ["<cada checkout vigilado>/.git/index", "<…>/.git/index.lock",
                                  "<…>/.git/HEAD", "<…>/.git/refs/heads/<su rama>", "<…>/.git/refs/heads/<su rama>.lock"]}
  },
  "hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit",
                             "hooks": [{"type": "command", "command": "<rein>/only-worktree.py"}]}]}
}
```

`only-worktree.py` son unas 10 líneas: rechaza con `exit 2` cualquier ruta cuyo `realpath` esté
fuera de `$CLAUDE_PROJECT_DIR`. Está probado en `guardtest/only-worktree.py`.

**Lo que se aprendió por el camino:**

- El sandbox de Claude Code solo cubre Bash, no Read/Edit/Write.
- En un worktree, el sandbox abre el `.git` compartido entero. Eso permitía commitear en la rama de
  `main` hasta añadir el `denyWrite`.
- Una regla `deny Edit` sobre una carpeta **también** se convierte en una denegación de escritura del
  sandbox, y rompe el commit legítimo. Por eso, para las herramientas, se usa un hook y no una regla
  de permisos.

## 3. Cómo cambia el guard (propuesta; no implementada)

1. **Lanzamiento.**
   - Si el worker es Claude y su perfil incluye el aislamiento, Rein guarda en el estado de la tarea
     `confined: true`.
   - Si el sandbox no puede arrancar, Claude se niega a iniciar (`failIfUnavailable`) y la tarea va
     al dueño como `setup`. Nunca se degrada en silencio.
2. **Al terminar.**
   - **Tarea `confined`:** lo que cambió fuera no puede ser del worker. `outside_changes` se registra
     igual, con todo su detalle, pero sale como **aviso al dueño** («actividad externa durante T003:
     commits X, archivos Y»). No es un rojo, no consume un reintento y no abre un gate bloqueante.
     Si lo externo toca un archivo que el diff del worker también toca, se marca para la auditoría.
   - **Tarea no `confined`** (Codex, Antigravity o perfil por defecto): **el comportamiento actual,
     sin cambios.**
3. **Codex.**
   - `--sandbox workspace-write` con `writable_roots` limitados a su gitdir de worktree confinaría
     las escrituras.
   - Pero no admite un `denyWrite` dentro del `.git` compartido, así que un `git -C ../main commit`
     seguiría siendo posible. Esa parte la sigue vigilando el guard actual.
   - **Hay que probarlo aparte** antes de marcar a Codex como `confined`.

**Por qué no debilita nada:**

- Hoy el guard **detecta después**. Con el cambio, **impide antes** en los workers Claude, que hicieron el
  78% de las tareas del corpus (184 de 236 con agente asignado), y la detección sigue entera donde no hay prevención.
- El único caso que pierde severidad es «cambió algo fuera mientras un worker confinado trabajaba».
  En el corpus, ese caso fue siempre actividad del dueño.

**Coste estimado:**

- unas 40 LOC en `launch_profile.py` (las rutas `denyWrite` salen de `watched_checkouts`, que ya
  existe);
- el hook de ~10 LOC;
- una rama de ~30 LOC en `ship.py`/`guard.py` para `confined`;
- sus tests.

Es menos que cualquiera de las rondas «review 6a–6d» del guard.

**Riesgo que hay que medir antes:** el sandbox también confina la **red**. Por defecto solo sale lo
que esté en `allowedDomains`, y los drivers de base de datos no atraviesan el proxy.

- Los tests de `proxima-api` usan Postgres en `localhost:5435`. Hace falta
  `sandbox.network.allowLocalBinding: true` (macOS) y comprobar que pasan.
- `npm ci`/`uv sync` dentro del worker necesitan sus registros en `allowedDomains`.
- **Piloto:** un change real con `ship.launchProfile` en una variante «confinada», detrás de un flag
  y con el perfil actual como vuelta atrás.

## 4. Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Ignorar las rutas del plan, o todo `openspec/**`, en el check de fuera | Deja una puerta que el worker podría usar y no resuelve los commits concurrentes |
| Atribuir con la transcripción (QW2) | Es mejor que hoy, pero no es prueba: una escritura por un script o una ruta relativa no aparece literal |
| Identidad git distinta por worker | Distingue commits, no escrituras de archivos, y ensucia la autoría del historial |
| Prohibir al dueño trabajar en los checkouts durante un `ship` | Frágil: depende de la disciplina, y el reflog de hoy muestra que no es realista |
| Monitor de sistema de archivos por proceso (`fs_usage`, EndpointSecurity) | Necesita root o una extensión del sistema; es desproporcionado |
