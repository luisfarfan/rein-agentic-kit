Añadí `inventory:manage` y `pricing:manage` a la visibilidad del panel, conservando los accesos existentes. No cambié la autorización para aprobar.

Verificación: `python3 -B -m unittest test_panel -q` pasó los 3 tests, incluidos controles de permiso, negocio y clave.

Sólo cambié el ejemplo local; sin commits ni push.
