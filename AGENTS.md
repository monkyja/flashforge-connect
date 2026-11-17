# OrcaFlashForge

Extensión para OrcaSlicer, orientada a FlashForge Creator 5 Pro y Linux.

- Mantener `caveman` activo en las respuestas hasta que el usuario indique lo contrario.
- Leer `docs/analisis.md` antes de cambiar el protocolo o la integración.
- La conexión normal con la cuenta FlashForge tiene prioridad; LAN es una alternativa.
- El SDK oficial se ejecuta en un proceso separado. No cargarlo dentro de OrcaSlicer.
- No guardar contraseñas. Por petición explícita del usuario, se permite recordar la sesión guardando únicamente su token de renovación en el llavero del sistema; nunca en configuración, archivos propios, argumentos ni registros. No incluir binarios ni archivos DAT del fabricante en los paquetes.
- No inventar endpoints, comandos, límites de temperatura ni equivalencias entre herramientas y ranuras.
- Diferenciar implementación, pruebas simuladas y validación con la impresora real.
- No ejecutar impresiones, movimientos, calentamiento ni cambios de firmware al desarrollar o probar.
- Ejecutar `PYTHONPATH=src python -m unittest discover -s tests -v` y `uv build` al cambiar código.
- `.references/` contiene los dos repositorios fijados en `upstreams.lock.json`; no modificar su código.
