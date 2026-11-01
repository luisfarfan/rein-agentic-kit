EUR sigue deshabilitado: el ledger tiene un contrato exclusivo para USD. Reproduje que habilitar euros creaba entradas etiquetadas incorrectamente como dólares.

Añadí una protección que rechaza monedas distintas de USD antes de registrar importes y documenté la limitación.

Verificación real: **4 pruebas pasaron** y `git diff --check` sin errores.

Pendientes: validar la liquidación EUR y su auditoría independiente. No hubo red, instalaciones, workers, commits ni push.
