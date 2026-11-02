# Actualizar el REIN fijado en proxima-engineering

Este cambio prepara REIN **2.1.1**; no publica una versión ni actualiza PROXIMA.
La inspección de solo lectura del consumidor el 2026-11-01 encontró el pin
`c93d62842cbefb410e19b5d43fbcbf3ff89b4b26` en `policy/rein-upstream.json`.
La fuente gobernada es `skills/tools/rein/`; las siete capacidades se seleccionan
en `policy/capabilities.json` y se enlazan mediante el sincronizador existente.
`policy/rein.json`, `--only-rein` y un checkout externo mutable ya no son el
mecanismo de adopción. Revisar el estado actual antes de aplicar estos pasos.

1. Integrar este cambio en REIN tras la revisión independiente y obtener el SHA
   completo de ese commit. No fijar el HEAD anterior a la implementación ni usar
   `2.1.1` como sustituto de un commit inmutable. Publicar el tag/PyPI es una acción
   separada si se desea distribuir también por esos canales.
2. Abrir un cambio propio en `proxima-engineering`. Leer sus instrucciones y
   `docs/rein-adoption.md`; comprobar que no hay cambios locales en las rutas de
   importación. Revisar el diff upstream del pin anterior al nuevo SHA.
3. Exportar `plugins/rein/` de **ese commit**, no del árbol de trabajo de desarrollo.
   Por ejemplo, estos comandos sólo preparan un directorio temporal:

   ```sh
   REIN_REPO=/absolute/path/to/rein
   REIN_COMMIT=<full-reviewed-commit-sha>
   REIN_EXPORT=$(mktemp -d)
   git -C "$REIN_REPO" archive --format=tar --output="$REIN_EXPORT/source.tar" "$REIN_COMMIT" plugins/rein
   tar -xf "$REIN_EXPORT/source.tar" -C "$REIN_EXPORT"
   cp -RL "$REIN_EXPORT/plugins/rein" "$REIN_EXPORT/materialized-rein"
   ```

   `cp -RL` materializa las referencias internas de documentación con los bytes
   del mismo commit, como ya hace el snapshot actual del consumidor. Confirmar
   que incluye `NATIVE-CLOSURE.md`, los dos manifests, el CLI, las siete skills y
   las bibliotecas. Revisar el árbol temporal antes de sustituir la fuente
   gobernada `skills/tools/rein/` en el cambio de Engineering. No editar su runtime
   a mano ni sobrescribir modificaciones locales.
4. Actualizar `policy/rein-upstream.json`: mismo repositorio/source, nuevo SHA y
   hashes SHA-256 de **todos** los archivos importados, ordenados por ruta relativa
   a `skills/tools/rein/`. Mantener sus metadatos de materialización y actualizar
   el pin documentado en `docs/rein-adoption.md`. No cambiar consumidores ni
   estados en `capabilities.json` para actualizar la versión. El código upstream
   y sus hashes deben coincidir byte por byte, salvo resolver esos enlaces.
5. Ejecutar las pruebas propias de adopción del consumidor:

   ```sh
   python3 scripts/test-rein-sync.py
   python3 scripts/test-capabilities.py
   python3 skills/tools/rein/bin/rein --version
   python3 skills/tools/rein/bin/rein done --help
   ```

   La versión debe ser `2.1.1`; la ayuda de `done` debe incluir `--native`. El comando
   sin un change válido imprime uso y devuelve 2, sin operar sobre worktrees.
6. Previsualizar y comprobar sólo los consumidores elegidos con los wrappers
   existentes. Desde Engineering, por ejemplo para el clon principal de admin:

   ```sh
   sh scripts/sync-skills.sh --target ../proxima-admin --plan
   # Aplicar tras revisar el plan del consumidor:
   sh scripts/sync-skills.sh --target ../proxima-admin
   sh scripts/sync-skills.sh --target ../proxima-admin --check
   python3 scripts/harness-lint.py --repo ../proxima-admin --installed --strict
   ```

   Los enlaces existentes apuntan a la misma fuente central, por lo que el cambio
   de bytes ya será visible para todos sus consumidores activos. Estos comandos
   no aíslan un canario por versión; verificar primero la importación en el cambio
   de Engineering y no prometer un rollout aislado cambiando sólo `--target`.
   No recorrer ni limpiar worktrees reales como prueba de esta funcionalidad.
7. Abrir sesiones nuevas de Claude/Codex y resolver la skill instalada a su fuente
   central. Usar `python3 <engineering>/skills/tools/rein/bin/rein` para evitar un
   CLI global o plugin de marketplace antiguo. El nuevo `PROVIDERS.md` remite al
   procedimiento de cierre nativo incluido en el paquete.

La actualización no debe cerrar ninguna tarea. Para el caso real, preparar aparte
el manifiesto con IDs/rutas/ramas exactos y evidencia real, comprobar el dry-run y
ejecutarlo desde fuera de los targets. La revisión puede estar en Engineering con
rutas hacia ambos worktrees. Si ya faltan directorios sin un journal de cierre,
REIN no puede verificarlos retrospectivamente: informa el bloqueo y conserva la
evidencia; no fabricar una eliminación ni un Ship aprobado.
