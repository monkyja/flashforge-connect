# Resumen: ingeniería inversa y reimplementación online FlashForge

Fecha: 2026-11-16  
Proyecto: OrcaFlashForge · objetivo indicado: Creator 5 Pro en OrcaSlicer original

## Resumen ejecutivo

El SDK local de FlashForge es `libFlashNetwork.so` 3.4.0. El análisis estático permite documentar parte de su ABI, el formato envolvente de `FLASHNETWORK9.DAT`, la serialización MQTT de varios controles y algunas rutas HTTP. No alcanza todavía para reemplazar el SDK completo: faltan contratos verificables del acceso de cuenta/máquina, selección de servicios y conexión MQTT.

El objetivo acordado es usar OrcaSlicer original con las funciones online de la máquina, sin migrar a Flash Studio, sin incluir el `.so` ni el DAT y sin instalar Python u otras dependencias aparte de OrcaSlicer. La ruta elegida es un plugin Python independiente, usando el CPython que distribuye OrcaSlicer en Windows y Flatpak. El backend actual aún llama al SDK propietario y usa `/usr/bin/python3`; **la reimplementación no está hecha**.

El dominio `api.fdmcloud.flashforge.com` está presente en la configuración local, pero todavía no se ha demostrado estáticamente qué llamadas de máquina lo usan. Los dominios Voxelshare se excluyen del alcance funcional por defecto; si una operación necesaria depende de ellos, se documentará como bloqueo antes de implementarla.

## Objetivo y restricciones del usuario

- Mantener OrcaSlicer original y añadir conexión normal online con la cuenta/máquina FlashForge.
- Centrarse en funciones de máquina: selección, estado, materiales, cámara y envío online.
- No reproducir funciones de tienda, perfil social, puntos o plataforma Voxelshare que no sean imprescindibles para la máquina.
- Reimplementar el comportamiento con código propio; no cargar ni distribuir `libFlashNetwork.so` o `FLASHNETWORK9.DAT`.
- No guardar contraseñas ni tokens en archivos o registros. La política del proyecto permite guardar solo el token de renovación en el llavero del sistema.
- No inventar hosts, rutas, campos, credenciales, comandos, temperaturas, límites ni equivalencias entre herramienta y ranura.
- No ejecutar impresiones, movimiento, calentamiento, firmware ni operaciones contra una impresora real durante desarrollo o pruebas automáticas.
- Separar análisis estático, pruebas simuladas y validación manual con cuenta/máquina.

## Muestras y procedencia

Las muestras analizadas se extrajeron de Flash Studio 1.7.9 sin ejecutar su AppImage. Los hashes de los archivos extraídos coinciden con las copias locales documentadas.

| Artefacto | Observación |
| --- | --- |
| `libFlashNetwork.so` | ELF64, little-endian, x86-64; 2.040.800 bytes; SHA-256 `7c7afb73c4f27fe34a243404e9536ccb3ad67877eeebfa9674ea6183431d68a6`; Build ID `98819a919765829d7d4b4ee2186eab90cdee606d` |
| `FLASHNETWORK9.DAT` | 7.482 bytes; SHA-256 `889d2f9a3bb1801b37a1d2a13e8914aa9d9fe1b1fb5de34bb2a684c0fb3d88ce` |
| Cabecera ABI | `native/FlashNetwork-3.4.0.h`; SHA-256 `812671c84b57a69190c81f5c5966b35dfee516cbf4c95f042d6e457678e25174` |
| Consumidor fijado | Orca-Flashforge `develop`, commit `ea9a451f89d2be656df0445388d6237319db91d4`; requiere 3.4.0 |

La referencia `main` de Orca-Flashforge corresponde a FlashNetwork 3.0.0; no se usó para deducir el ABI local. Los repositorios de referencia están fijados en `upstreams.lock.json` y `.references/` no debe modificarse.

## Hallazgos de ingeniería inversa

### Versión y superficie ABI

**Hechos observados:**

- La versión 3.4.0 se construye estáticamente en `fnet_getVersion` (VA `0x6abf0`) a partir de escrituras en el constructor `_GLOBAL__sub_I_FlashNetwork.cpp` (`0x62910`, escrituras `0x62a2d` y `0x62a37`).
- La biblioteca exporta 119 funciones C; la cabecera 3.4.0 declara las mismas 119, sin ausentes ni extras.
- La biblioteca depende, entre otras, de libcurl, OpenSSL, zlib, libstdc++ y libc. Esto es inventario ELF, no prueba de que las dependencias se resuelvan en cualquier equipo.

**Límite:** coincidir en versión y nombres no demuestra todos los layouts, ownership, precondiciones o comportamientos del ABI. Los layouts se calcularon desde la cabecera local y se contrastaron con ensamblador solo en partes relevantes.

### `FLASHNETWORK9.DAT`

**Hechos observados en lectura estática:**

- Encabezado `FNETDAT-1.2.0`; el formato del DAT no es la versión del SDK.
- El lector `ServerSettingRW::readSettings` está en VA `0x119190`. Reconstruye una carga de 314 bytes a partir de 1.783 enteros de relleno MT19937 intercalados; el parser independiente consumió exactamente los 7.482 bytes.
- `aseEncode` (VA `0x119a50`) usa AES-256-OFB. Las llamadas de descifrado aparecen en `0x119bae`, `0x119bcb` y `0x119be3`.
- `dataToSettings` empieza en VA `0x119770`; solo se había documentado parcialmente el esquema.

**Seguimiento posterior, offline:** se descifró la carga en memoria para enumerar hostnames y luego no se guardó el texto descifrado. La salida depurada de hostnames fue:

- `api.fdmcloud.flashforge.com`
- `api.voxelshare.com`
- `auth.voxelshare.com`
- `report.voxelshare.com`
- `desktop.voxelshare.com`
- además apareció el fragmento `:8083/mqtt`, sin asociación confirmada con un hostname.

La primera lista compartida con el usuario omitió `auth.voxelshare.com`; esta es la lista corregida. La mera presencia de un hostname en configuración no demuestra qué llamada lo usa, si continúa activo ni si forma parte del flujo de máquina. No se incluyen claves, credenciales ni el contenido completo descifrado.

### Contratos MQTT y controles

**Hechos observados en cabecera/ensamblador, no enviados a una impresora:**

- `fnet_connectionSend` (VA `0xa0b50`) delega en `MqttConn::send` (`0x10ded0`), que llama al serializador `ConnDataProc::writeData2Str` (`0x10df35`).
- El consumidor público usa el topic provisto por la sesión/dispositivo y QoS 1. No hay base para inventar topics a partir del ID de impresora.
- Se localizaron serializadores para temperatura, luz, ventiladores, movimiento, homing, materiales, impresión, trabajo, cámara y configuración de materiales. El informe estático contiene sus nombres de comando, claves JSON, VAs y referencias a cadenas.
- La cabecera documenta `job_ctrl.action` como `pause`, `continue`, `cancel`; cámara como `open`/`close`; y acciones de estación de material como carga/descarga. Son contratos de representación observados, no prueba de soporte físico en Creator 5 Pro.
- La cabecera menciona campos escalares y vectores de temperatura. El serializador redondea varios escalares, pero trata los elementos del vector como `double`. No se dedujeron límites térmicos.

**Discrepancia reproducible en ensamblador:** `fnet_connectionSendMulti` (VA `0xa0bd0`) delega en `MqttConn::sendMulti` (`0x10e460`). La implementación acepta `dataCnt == 1` o `dataCnt == topicCnt`; no acepta todas las combinaciones permitidas por el comentario de la cabecera. El retorno cero tampoco basta: se debe inspeccionar `failedCnt` y liberar el resultado con `fnet_freeWriteMultiResult`.

**Inferencia, no hecho probado en ejecución:** `ConnDataProc::str2ReadData` (VA `0x65670`) compara dos veces `job_download` para ramas de tipos 12 y 13. La segunda condición parece inalcanzable y podría impedir `FNET_CONN_READ_JOB_UNZIP`; no se ejecutó el parser para confirmarlo.

### Consultas HTTP localizadas

El informe estático enlaza dos funciones públicas a rutas leídas en `.rodata` y llamadas HTTP:

| Función | VA | Ruta observada |
| --- | ---: | --- |
| `fnet_getWanDevGcodeList` | `0x7f4b0` | `/api/v2/external/truck/device/local_files?id=%s` |
| `fnet_getWanDevTimeLapseVideoList` | `0x802a0` | `/api/v2/external/truck/device/delay_video/list?id=%s&currentPage=1&pageSize=%d` |

Esto no proporciona el host, autenticación completa ni una especificación estable del servicio. No se llamaron estas rutas. La configuración de paginación de timelapse es lo que muestra esa función; el valor 15 procede de un consumidor particular y no es un máximo universal.

### ABI, ownership y materiales

- El informe registra tamaños/offsets de las estructuras públicas de escritura, datos G-code/timelapse y callbacks más usados. Los valores corresponden al host x86-64 analizado.
- Los punteros devueltos requieren las funciones de liberación correspondientes. En MQTT, la conexión se detiene antes de liberar y callbacks/datos deben seguir vivos durante su uso.
- La estructura real `fnet_conn_read_data_t` tiene `topic`; un comentario de cabecera habla de `devId`. Debe prevalecer el layout y uso observado.
- `toolId` y `slotId` son campos distintos. No se debe inferir equivalencia entre herramienta G-code y ranura física.
- No se confirmó soporte de todos los controles, RFID, topología o límites para Creator 5 Pro.

## Lo que sigue sin conocerse

Para reemplazar el SDK en la parte online de máquina aún hacen falta contratos reproducibles de:

1. Inicio de sesión/renovación y respuesta, incluidos los pasos exactos necesarios para autorizar operaciones de máquina.
2. Relación entre los hostnames de configuración y cada operación. En particular, seguir estáticamente `api.fdmcloud.flashforge.com`; excluir servicios Voxelshare salvo dependencia imprescindible demostrada.
3. Broker MQTT: host, puerto, TLS, autenticación, credenciales de sesión, topic, suscripciones y reconexión.
4. Estructuras completas de estados/eventos de Creator 5 Pro y relación de materiales/ranuras.
5. Flujo online completo de subida, creación de trabajo, despacho y estados de error, además de cómo distinguir despacho de inicio físico.
6. Dependencias y credenciales de aplicación que un cliente independiente puede usar legítimamente. No se copiarán claves secretas del fabricante ni se eludirá autenticación.

El análisis en `/docs/sdk-flashnetwork-3.4.0-analisis-estatico.md` documenta coordenadas, bytes y comandos de reproducción. Las muestras y exportaciones grandes están en `local/sdk-analysis-2026-11-15/` y no deben empaquetarse. REA/Ghidra MCP no estaba conectado; el trabajo se hizo principalmente con binutils y evidencia de cabecera/consumidores.

## Arquitectura y distribución acordadas

El sistema de plugins fijado de OrcaSlicer usa `.py`/`.whl`, `PythonPluginBridge` y capacidades enlazadas mediante pybind. No existe en esa versión un instalador de plugins C++ puro. Como OrcaSlicer distribuye Python con el instalador Windows y con Flatpak/AppImage, el plan cambió a reimplementar en Python usando el runtime incluido, sin pedir al usuario que instale Python aparte.

- El instalador de Windows coloca el runtime en `<directorio de OrcaSlicer>\\python` y contiene `python.exe`.
- OrcaSlicer Flatpak 2.5.1 contiene CPython 3.12.13 bajo `/app/libpython`.
- AppImage también empaqueta runtime según el layout fijado.
- Un paquete FHS simple puede no incluir runtime; no se usará como requisito compatible salvo que se confirme su Python incluido.
- El `bridge.py` actual lanza `/usr/bin/python3`, así que **debe cambiarse** para localizar el runtime de OrcaSlicer o eliminar el subprocess si resulta seguro y soportable.
- Las dependencias serán biblioteca estándar o módulos compatibles empaquetados en el wheel con licencia/procedencia. No se instalarán en el equipo mediante `pip`/`uv`.
- Se conserva IPC/aislamiento si puede arrancarse el worker con el Python incluido; la alternativa es ejecutar el cliente dentro del intérprete del plugin, con la decisión documentada.

## Seguridad, legalidad y validación

La conversación revisó el EULA publicado por FlashForge y la excepción de interoperabilidad de la Directiva 2009/24/CE, artículo 6. La excepción europea puede permitir descompilación para interoperabilidad bajo condiciones (tener derecho a usar copia, información no disponible fácilmente, análisis limitado a lo imprescindible, uso limitado a interoperabilidad). No es una conclusión legal sobre esta implementación concreta. El EULA no autoriza a redistribuir binarios; el proyecto no los incluirá. Referencias: [EULA FlashForge](https://eu.flashforge.com/es/pages/terms-of-service) · [Directiva 2009/24/CE](https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:32009L0024).

Validación lograda y pendiente deben permanecer separadas:

| Nivel | Estado resumido |
| --- | --- |
| Análisis estático | Hecho para ELF, símbolos, DAT, serializadores MQTT, rutas HTTP seleccionadas y ABI; sin llamada a la nube |
| Pruebas simuladas | Existen pruebas previas de la integración con un SDK falso; no verifican el protocolo real ni completan la reimplementación |
| Validación real | No se ha probado login, nube, MQTT, cámara ni máquina con una cuenta/impresora real en este análisis |

No se ejecutarán acciones físicas automáticamente. Login/lista/estado son la primera validación manual futura; cámara y luego trabajos requieren pasos separados y confirmación explícita.

## Plan de reimplementación resumido

El plan detallado está en [`roadmap.md`](roadmap.md), hito 6:

1. Mapear rutas, servicios y estructuras con símbolo/VA/offset y nivel de confianza.
2. Definir modelos neutrales y transporte HTTP/MQTT en Python; probar solo contra dobles locales.
3. Implementar autorización necesaria para la máquina, después lista y estado de solo lectura.
4. Añadir MQTT/eventos/cámara cuando broker y topics estén demostrados.
5. Añadir subida y creación de trabajo por fases, sin reintentos ambiguos.
6. Sustituir backend por grupos, retirar dependencia del SDK/DAT, usar Python empaquetado con Orca, ejecutar tests/build y revisar el wheel.
7. Hacer validación manual separada con Windows/Flatpak; no imprimir, mover ni calentar durante desarrollo.

### Estado al crear este resumen

El código de integración existente todavía contiene el adaptador `ctypes` al SDK oficial y necesita los archivos locales del fabricante para su modo de cuenta. El plan de reescritura está documentado; **ningún módulo del SDK propietario ha sido reemplazado todavía**. Las modificaciones de este resumen son documentación.
