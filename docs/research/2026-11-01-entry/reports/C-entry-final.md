El checkout ya reenvía `EUR` al adaptador en [payments.py:7](/Users/luisfarfan/Developer/labs/agents/rein/.rein/p0-live-entry/project4/payments.py:7). **El soporte real para euros sigue sin confirmar**: el proveedor y su contrato no están incluidos.

Ejecuté 4 pruebas locales con adaptadores simulados; todas pasaron:

- EUR e importe se transmiten sin conversión.
- Se conserva el resultado del adaptador.
- USD mantiene su comportamiento.
- Los rechazos del proveedor se propagan; también se reenvían monedas desconocidas.

No modifiqué archivos: falta conocer las monedas admitidas y las unidades del importe antes de definir un cambio correcto. El siguiente paso requiere ese contrato y, si cambia la lógica monetaria, una revisión independiente.

Usé 5 llamadas, sin red, instalaciones, workers, commits ni push.
