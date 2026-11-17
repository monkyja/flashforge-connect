# FlashForge Connect

Plugin para integrar FlashForge Creator 5 Pro en OrcaSlicer, con prioridad en Linux y en la conexión normal mediante cuenta FlashForge.

**Versión 0.1.0, experimental.** Funciona como plugin Pages en OrcaSlicer sin modificar el programa. El envío completo no se ha validado con la impresora real.

## Qué incluye

- Página **FlashForge** como capacidad `Pages` de OrcaSlicer 2.5.0.
- Conexión normal mediante la biblioteca experimental: inicio de sesión, renovación, lista de impresoras y estado.
- **Recordar cuenta** con el llavero de Linux: reconexión al abrir OrcaSlicer, sin guardar la contraseña.
- Cuenta con icono de FlashForge, nombre, correo y avatar devueltos por el SDK; selector de impresoras con su imagen cuando está disponible.
- Asociación del perfil activo de OrcaSlicer con una impresora, guardada por el plugin.
- Revisión propia en la página FlashForge, con asignación explícita de IDs reales de ranura LFS.
- Preparación de G-code o 3MF laminado, revisión del destino y envío cloud con orden de inicio por MQTT. Progreso y cancelación de la subida; inicio físico todavía sin validar.
- Alternativa LAN: consulta autenticada `POST /detail` en el puerto 8898.
- Trabajo actual, progreso, capas, temperaturas, firmware y materiales recibidos.
- Visualización de la URL de cámara devuelta por el firmware o SDK: MJPEG y HLS mediante hls.js cuando el WebView admite Media Source Extensions, con reproducción nativa como alternativa. La reproducción también depende de los códecs disponibles y, para hls.js, de que el servidor permita peticiones CORS. No incluye un conversor de vídeo.
- Modo demostración con datos marcados como simulados.
- CLI para diagnóstico y consultas.
- SDK en un proceso separado: un fallo de la biblioteca no se ejecuta dentro del proceso de OrcaSlicer.

El diálogo usa la biblioteca cloud experimental y mantiene la comunicación en el proceso auxiliar. La subida, creación de trabajo y orden MQTT están probadas con dobles, pero no con una impresión real. El plugin no necesita cambiar la impresora a LAN Only para intentar este modo. La coexistencia efectiva con Flash Maker también está pendiente de validar.

## Instalar en OrcaSlicer

### Desde el catálogo de OrcaSlicer

**Disponible después de que el autor publique el plugin en OrcaCloud.** Necesitas OrcaSlicer con soporte de plugins y una cuenta OrcaCloud; esta cuenta es independiente de tu cuenta FlashForge.

1. Inicia sesión en OrcaCloud desde OrcaSlicer y abre **Archivo > Plugins > Examinar plugins**.
2. Busca **FlashForge Connect**, abre su ficha, comprueba la compatibilidad con tu sistema y pulsa **Subscribe** o **Suscribirse**.
3. Regresa a **Archivo > Plugins**, pulsa **Actualizar** si no aparece y marca **Activar**. Aparecerá la página **FlashForge**.
4. Abre esa página e inicia sesión con tu cuenta FlashForge. OrcaSlicer puede solicitar permisos para red, procesos y archivos necesarios para el plugin.

Consulta la [guía oficial de plugins de OrcaCloud](https://cloud.orcaslicer.com/wiki/#plugins) para ver la interfaz y resolver problemas de suscripción.

### Publicar en OrcaCloud

1. Ejecuta `PYTHONPATH=src python -m unittest discover -s tests -v`, `uv build` y `python scripts/check_distribution.py`.
2. En [OrcaCloud](https://cloud.orcaslicer.com/), abre **Plugins > Shared Plugins**, pulsa **+** y sube `dist/orca_flashforge-<versión>-py3-none-any.whl`. Completa nombre, descripción, versión, tipo y versión compatible de OrcaSlicer. Indica Linux, el carácter experimental y que no es un plugin oficial; no anuncies el envío como validado en una impresora real.
3. Activa **Public Plugin** y crea la ficha cuando esté lista para que otros puedan encontrarla. Hasta entonces, la instalación del catálogo anterior no estará disponible.
4. Para automatizar versiones posteriores, configura un remoto de GitHub y, en **Edit plugin > GitHub publishing**, conecta `propietario/repositorio`. No es necesario guardar tokens: OrcaCloud usa la identidad temporal de GitHub Actions.
5. Actualiza la versión en `pyproject.toml` y `src/orca_flashforge/__init__.py`. Al subir una etiqueta `v<versión>`, GitHub Actions ejecuta las pruebas y crea un borrador de release. Revisa el wheel y las notas; al publicar el release, otro flujo sube el wheel a OrcaCloud. La versión debe superar la versión publicada en OrcaCloud.

La publicación depende de conectar la cuenta y el repositorio; no se realiza por crear esta documentación. También puedes instalar el wheel manualmente desde **Archivo > Plugins > Examinar plugins > Instalar plugin local**.

Requisitos: Linux y Python del sistema **3.12 o posterior**. La biblioteca cloud experimental no necesita `libFlashNetwork.so` ni DAT; el backend oficial opcional sí los necesita y se ejecuta en un proceso separado.

Se ha comprobado la carga de la capacidad y el diagnóstico del SDK 3.4.0 desde OrcaSlicer Flatpak 2.5.0/2.5.1. Las pruebas de envío emplean una biblioteca simulada; no certifican un inicio real en Creator 5 Pro.

1. Construye el paquete con `uv build` o utiliza el `.whl` de `dist/`.
2. Abre el gestor de plugins de OrcaSlicer y usa la instalación local para seleccionar `dist/orca_flashforge-0.1.0-py3-none-any.whl`.
3. Carga y activa la capacidad **FlashForge**. Aparecerá su página en OrcaSlicer.
4. Abre la página FlashForge e inicia sesión con tu cuenta.
5. Selecciona una impresora de la lista y consulta su estado.

El estado se actualiza automáticamente cada 5 segundos, sin activar ninguna casilla. Las lecturas se ejecutan en un hilo separado y mantienen el panel disponible: no desactivan el selector, los formularios ni la revisión de impresión. No se solapan consultas. Si el SDK está ocupado, se omite esa lectura y se reintenta en el siguiente intervalo. En modo cuenta necesitan una sesión conectada y una impresora seleccionada; en LAN, una conexión configurada. **Actualizar estado** permite consultar también de forma manual.

Si pulsas una acción durante una lectura, el plugin conserva el clic y ejecuta la acción al finalizar esa consulta. Las llamadas al SDK siguen serializadas. Cambiar de impresora o de modo descarta la respuesta de la selección anterior. Las lecturas periódicas actualizan los datos sin reconstruir el selector ni las tarjetas de impresora, conservando el foco y los campos que estés editando.

La hora de la última lectura aparece junto al indicador de actualización automática. El panel **Boquillas y filamentos** muestra para Creator 5/5 Pro una tarjeta con temperatura, objetivo, material, color y ranura recibidos de la impresora. Las boquillas sin material aparecen como **Sin filamento**. Cama y cámara permanecen debajo. Si la respuesta no permite asociar materiales con boquillas, los filamentos se mantienen visibles en el mismo panel sin inventar la relación. Esta vista no cambia los materiales del laminado ni la asignación del trabajo. Si sigue apareciendo la casilla **Cada 15 s**, se está mostrando una copia antigua: actualiza el plugin y reinicia OrcaSlicer para cargar sus archivos nuevos.

La primera lectura se solicita al recibir la configuración y recuperar la sesión. La página comprueba que el código cargado admite la actualización independiente; si aún está cargada una versión anterior, pide reiniciar. Una consulta sin respuesta durante 65 segundos libera la espera y vuelve a consultar la configuración. Ese plazo es un límite local de recuperación, no una especificación de FlashForge. Si había una acción pendiente, se descarta y se informa de que no se ejecutó.

OrcaSlicer pedirá permisos para crear un hilo y ejecutar el comando completo del intérprete que aloja el adaptador. El auxiliar descarta sus mensajes nativos dentro de su propio proceso. El acceso de red del SDK ocurre en ese proceso. En LAN, OrcaSlicer puede solicitar permiso de red al realizar la primera consulta. Esos permisos pertenecen al sistema de plugins del laminador.

La primera consulta también puede solicitar permisos para abrir los canales de comunicación con el auxiliar: eventos Python `open` sin ruta de archivo. Como la consulta ocurre en segundo plano, OrcaSlicer puede rechazar ese intento mientras muestra la petición. Después de aceptar, vuelve a pulsar **Comprobar SDK**. Si cambias la ruta de Python, puede solicitar de nuevo permiso para ese comando. Un rechazo de permisos se muestra separado del error de intérprete inexistente.

La configuración no guarda la contraseña de la cuenta ni sus tokens. Con **Recordar cuenta en este equipo**, el auxiliar guarda únicamente el token de renovación en el llavero del sistema mediante Secret Service (`secret-tool`). Al abrir OrcaSlicer renueva la sesión con la biblioteca seleccionada y actualiza el llavero cuando el servidor renueva el token. El nombre de acceso y la preferencia de recordar se guardan en la configuración; el token de acceso solo vive en memoria. El código LAN sí se guarda en la configuración local del plugin; no se vuelve a enviar al formulario.

Para activar esta mejora en una instalación existente, reinicia OrcaSlicer y conecta la cuenta una vez con **Recordar cuenta en este equipo** marcado. Después se recuperará automáticamente. **Recuperar cuenta guardada** permite reintentarlo tras desbloquear el llavero o recuperar la conexión. **Salir y olvidar cuenta** desactiva la reconexión y elimina la entrada del llavero. Si el borrado falla, se informa del error y la reconexión permanece desactivada.

Se necesita `secret-tool` y un servicio de llavero disponible y desbloqueado. Si el guardado falla, la conexión puede seguir en memoria y la página lo indica; no se crea ningún archivo alternativo con credenciales. Una sesión rechazada por el servidor se descarta; un fallo de red conserva la cuenta guardada para reintentar más tarde.

El Flatpak oficial necesita permiso para hablar con Secret Service. Se concede solo a ese servicio:

```bash
flatpak override --user --talk-name=org.freedesktop.secrets com.orcaslicer.OrcaSlicer
```

El permiso se aplica a las nuevas ejecuciones de OrcaSlicer. No requiere modificar el ejecutable ni abrir todo el bus D-Bus.

Los ajustes no secretos descubiertos en este equipo están en `local/sdk.json`, excluido de Git y del paquete. Se usan como referencia para rellenar el formulario; no se modifica automáticamente la configuración de OrcaSlicer.

## Seleccionar destino e imprimir

La cuenta se conecta desde la página **FlashForge** y se restaura desde el llavero. La comunicación experimental ocurre en el auxiliar.

### Enviar desde la previsualización

Lamina el modelo y exporta la placa como G-code o archivo laminado. Abre la página **FlashForge**, comprueba la Creator 5 Pro seleccionada y pulsa **Revisar impresión**. El plugin puede usar el último G-code exportado que detectó; también puedes indicar la ruta de un archivo ya laminado. No modifica el botón de OrcaSlicer.

La revisión muestra las herramientas y las ranuras recibidas. Cambia y actualiza una bobina en la impresora si hace falta, vuelve a revisar y confirma cada asignación. Si faltan herramientas o ranuras, se bloquea el envío. La recepción por la impresora y el inicio físico permanecen sin confirmar tras el acuse MQTT del servidor.

### Enviar desde la página FlashForge

1. Inicia sesión y elige una impresora en la lista o en su tarjeta.
2. Selecciona tu perfil de impresora en OrcaSlicer y pulsa **Vincular perfil a impresora**. El plugin recuerda la pareja; no modifica el perfil del laminador ni sus calibraciones.
3. Lamina el modelo. El plugin recuerda la ruta recibida en `GCodeExportFinished`. También puedes introducir la ruta de un `.gcode` o `.3mf` ya laminado. Un proyecto 3MF sin G-code se rechaza.
4. Selecciona la placa del archivo y pulsa **Revisar impresión**. Se crea una copia privada e inmutable del archivo; todavía no se sube nada.
5. Revisa archivo, impresora, perfil y opciones. La preparación consulta automáticamente los materiales del LFS, sin exigir una consulta de estado previa. Si el G-code declara material y color, propone la ranura solo cuando existe una coincidencia exacta y única. Las coincidencias ambiguas o ausentes quedan pendientes de selección. Las ranuras vacías aparecen desactivadas. Sin estación se envía un mapeo vacío, como en el consumidor público del fabricante.
6. Marca la confirmación y pulsa **Enviar e imprimir**. Se suben archivo y miniatura, se crea el trabajo y se envía la orden MQTT. Si falta una miniatura PNG, se usa una imagen neutra.
7. Actualiza el estado para comprobar el inicio físico. La aceptación de la orden por el SDK no demuestra que la máquina haya empezado.

**Cancelar subida** interrumpe la transferencia antes de crear el trabajo; no cancela una impresión que ya se haya iniciado. Si falla la creación o la entrega de la orden, comprueba la máquina antes de repetir: el plugin no reintenta automáticamente un envío incierto. Cambiar proyecto, placa o perfil invalida la revisión pendiente.

El límite local de preparación es 512 MiB por archivo/placa y 1 MiB por miniatura. Son límites de recursos del plugin, no especificaciones del fabricante. El perfil mostrado es el activo en OrcaSlicer; no se afirma que un archivo externo se haya laminado con ese perfil.

## Desarrollo

```bash
uv build
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m orca_flashforge.cli demo
```

`python scripts/preview.py` abre un servidor de vista previa en localhost y muestra su URL. Añade `--account` para probar una cuenta y una revisión de impresión simuladas. Ninguna opción de esa vista contacta con FlashForge.

Comprueba una biblioteca sin iniciar sesión ni consultar una impresora:

```bash
PYTHONPATH=src python -m orca_flashforge.cli doctor --library /ruta/bin/libFlashNetwork.so
```

Para consultar las impresoras de una cuenta, la contraseña se solicita de forma interactiva:

```bash
PYTHONPATH=src python -m orca_flashforge.cli cloud \
  --library /ruta/bin/libFlashNetwork.so \
  --settings /ruta/resources/data/FLASHNETWORK9.DAT \
  --username usuario@example.com
```

Añade `--device ID` para consultar una impresora concreta. El programa no imprime, mueve ni calienta la máquina.

## Organización

| Componente | Ubicación | Función |
| --- | --- | --- |
| Plugin | `src/orca_flashforge/plugin.py`, `ui/` | Página integrada y configuración |
| Adaptador SDK | `sdk.py`, `sdk_worker.py`, `bridge.py` | Cuenta FlashForge y aislamiento del código nativo |
| Envío | `jobs.py`, `sdk_print.py` | Copia del archivo, mapeo explícito, subida y orden MQTT |
| Cliente LAN | `lan.py`, `model.py` | Consulta HTTP y datos normalizados |
| Referencia nativa | `native/` | Cabecera pública y comprobación del ABI |
| Análisis | `docs/analisis.md` | Evidencias, decisiones, limitaciones y matriz funcional |
| Trabajo pendiente | `docs/roadmap.md` | Hitos y condiciones para completarlos |
| Referencias locales | `.references/` | Checkouts de ambos repositorios, ignorados por Git |

La integración activa usa únicamente la API de plugins de OrcaSlicer.

## Alcance pendiente

Validación real del envío cloud, pausa/reanudación/cancelación de trabajos ya iniciados, controles térmicos y de movimiento, carga/descarga de filamentos, mapeo completo de materiales, archivos de la impresora y consulta de timelapse. La opción de grabar timelapse se conserva al enviar. El envío LAN disponible en OrcaSlicer sigue siendo una función del laminador, independiente de esta página.

Licencia AGPL-3.0-or-later. Procedencia de la cabecera y referencias en `THIRD_PARTY.md`.
