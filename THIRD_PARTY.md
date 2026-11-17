# Procedencia

- `src/orca_flashforge/ui/vendor/hls-1.5.6.min.js` es hls.js 1.5.6, obtenido de https://cdn.jsdelivr.net/npm/hls.js@1.5.6/dist/hls.min.js. Se distribuye bajo Apache-2.0; la licencia se incluye en `src/orca_flashforge/ui/vendor/HLS-LICENSE`. Proyecto: https://github.com/video-dev/hls.js/tree/v1.5.6.
- `native/FlashNetwork-3.4.0.h` es una copia sin modificaciones de `src/slic3r/GUI/FlashForge/FlashNetwork.h`, en FlashForge/Orca-Flashforge, commit `ea9a451f89d2be656df0445388d6237319db91d4` (`develop`). El repositorio de origen se distribuye bajo AGPL-3.0.
- Las declaraciones `ctypes` y la secuencia de llamadas se basan en esa cabecera y sus consumidores públicos. El cliente LAN se basa en las operaciones de `src/slic3r/Utils/Flashforge.cpp` de OrcaSlicer 2.5.0.
- `LICENSE` contiene el texto AGPL-3.0 procedente de OrcaSlicer.
- `src/orca_flashforge/ui/flashforge.svg` procede de `resources/images/studio_logo.svg` de FlashForge/Orca-Flashforge, commit `ea9a451f89d2be656df0445388d6237319db91d4`, bajo la licencia AGPL-3.0 del repositorio. Se utiliza para identificar la integración con FlashForge; el proyecto no es un producto oficial del fabricante.
- No se redistribuyen `libFlashNetwork.so`, sus dependencias ni `FLASHNETWORK9.DAT`. Su uso requiere los archivos suministrados por FlashForge y las condiciones que les sean aplicables.
- `.references/` conserva checkouts locales para investigar. No forma parte del paquete ni del repositorio del producto.
