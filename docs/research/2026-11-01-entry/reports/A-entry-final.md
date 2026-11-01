EUR quedó habilitado en `settings.py`, manteniendo USD. Agregué una prueba para `enabled('EUR')`.

Verificado localmente: 3 pruebas pasan, las monedas habilitadas están soportadas, una moneda desconocida se rechaza y `git diff --check` pasa.

Alcance limitado a configuración: este checkout no contiene conversión, cálculos ni pagos para verificar. Sin red, instalaciones, workers, commits ni push.
