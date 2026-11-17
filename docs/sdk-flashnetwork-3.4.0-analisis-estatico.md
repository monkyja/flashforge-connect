# FlashNetwork 3.4.0: análisis estático de SDK y DAT

Fecha: 2026-11-15. Objetivo: documentar contratos pendientes de OrcaFlashForge para interoperabilidad con FlashForge Creator 5 Pro. Esta investigación continúa `docs/analisis.md`; no sustituye su historial de implementación y validación.

## Alcance y resultado

Se inspeccionaron bytes, símbolos, ensamblador, cabeceras y consumidores de referencia. Se reconstruyó el contenedor del DAT mediante un lector independiente en memoria. No se cargó ni ejecutó `libFlashNetwork.so`, tampoco mediante `ctypes`, `ldd` o un depurador. No hubo llamadas a FlashForge, cuentas ni impresoras. No se modificó código de integración ni `.references/`.

Resultados principales:

- La biblioteca examinada identifica estáticamente su versión como **3.4.0** y exporta las **119 funciones C** declaradas en la cabecera fijada.
- Se localizaron serializadores de los controles pendientes, las consultas de archivos y timelapse, y sus contratos de memoria.
- `sendMulti` implementa una restricción más estrecha que el comentario de la cabecera.
- El parser de eventos compara dos veces `job_download`; la segunda rama intenta generar `FNET_CONN_READ_JOB_UNZIP`. Es un probable defecto del SDK, identificado por análisis estático.
- `FLASHNETWORK9.DAT` contiene un encabezado `FNETDAT-1.2.0`, relleno verificable con MT19937 y una carga cifrada de 314 bytes. La biblioteca usa AES-256-OFB en su lector. No se descifró ni publicó el contenido de configuración.

No se atribuye a estos resultados validación física, compatibilidad de todos los controles con Creator 5 Pro ni reconstrucción completa del protocolo cloud.

## 1. Identidad y procedencia

Los archivos locales están en:

- `/home/monky/Impresora3D/squashfs-root/bin/libFlashNetwork.so`
- `/home/monky/Impresora3D/squashfs-root/resources/data/FLASHNETWORK9.DAT`

También se extrajeron únicamente esos dos miembros de `/home/monky/.local/opt/flash-studio/FlashStudio-1.7.9.AppImage`, usando `7z`, sin ejecutar el AppImage. **Ambas parejas tienen hashes idénticos**. El nombre observado del DAT es `FLASHNETWORK9.DAT`.

| Artefacto | Tamaño | SHA-256 |
| --- | ---: | --- |
| `libFlashNetwork.so` | 2.040.800 bytes | `7c7afb73c4f27fe34a243404e9536ccb3ad67877eeebfa9674ea6183431d68a6` |
| `FLASHNETWORK9.DAT` | 7.482 bytes | `889d2f9a3bb1801b37a1d2a13e8914aa9d9fe1b1fb5de34bb2a684c0fb3d88ce` |
| `native/FlashNetwork-3.4.0.h` | — | `812671c84b57a69190c81f5c5966b35dfee516cbf4c95f042d6e457678e25174` |

La cabecera vendorizada y `.references/Orca-Flashforge/src/slic3r/GUI/FlashForge/FlashNetwork.h` tienen el mismo SHA-256.

Los HEAD observados coinciden con `upstreams.lock.json`:

| Repositorio | Commit |
| --- | --- |
| OrcaSlicer | `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19` |
| Orca-Flashforge, `develop` | `ea9a451f89d2be656df0445388d6237319db91d4` |

La referencia antigua `main` corresponde al SDK 3.0.0; no se utilizó para deducir el ABI de esta biblioteca.

### Evidencia ELF

`file` y `readelf` identifican ELF64, little endian, x86-64, tipo `DYN`, con tablas de símbolos conservadas. Build ID: `98819a919765829d7d4b4ee2186eab90cdee606d`.

Dependencias `DT_NEEDED`: `libcurl.so.4`, `libssl.so.3`, `libcrypto.so.3`, `libz.so.1`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`. Esto es inventario estático; no se comprobó su resolución por el cargador.

**Convención de direcciones:** todas las direcciones de funciones e instrucciones de este informe son VA del ELF antes de ASLR. En los segmentos de código y `.rodata` de este archivo, `p_vaddr == p_offset`: esas direcciones son también offsets de archivo. Esto no se extiende a los datos mutables ni a `.bss`. La fórmula general es `offset = VA - p_vaddr + p_offset`; una dirección en `.bss` no tiene bytes de archivo correspondientes.

Evidencia completa local: `local/sdk-analysis-2026-11-15/evidence/elf.txt`, `symbols.txt`, `disassembly.txt`, `inventory.json` y `api-inventory.tsv`. Este último relaciona cada función pública con su VA y línea de declaración.

## 2. Versión y compatibilidad observada

**Hecho confirmado en ensamblador.** `fnet_getVersion`, VA `0x6abf0`, devuelve el puntero de `g_versionStr`, VA `0x1a1a50`, situado en `.bss`. El constructor `_GLOBAL__sub_I_FlashNetwork.cpp`, VA `0x62910`, prepara esa cadena: instrucciones `0x62a2d` y `0x62a37` escriben las constantes `0x2e342e33` y `0x30`. Interpretadas en little endian forman `3.4.0\0`.

Reproducción de esa conversión, sin invocar el SDK:

```python
import struct
print(struct.pack('<IH', 0x2e342e33, 0x30))  # b'3.4.0\x00'
```

El consumidor fijado exige `strcmp(getVersion(), "3.4.0") == 0` en `FlashNetworkIntfc.cpp:145`. Se compararon nombres declarados mediante `FNET_API` con los símbolos dinámicos definidos: 119 en cada conjunto, sin ausentes ni extras.

**Límite:** versión y nombres coincidentes no prueban por sí solos todos los tamaños, ownership, precondiciones o comportamientos del ABI. Los layouts siguientes se obtuvieron del compilador local; solo algunos accesos fueron contrastados además con ensamblador.

## 3. Contrato del archivo DAT

### Estructura observada y reconstruida

| Offset del DAT | Contenido | Evidencia |
| --- | --- | --- |
| `0x00` | Encabezado de 20 bytes; prefijo ASCII `FNETDAT-1.2.0`, seguido de ceros en esta muestra | Bytes del archivo; lectura y comparación en `0x1192ff–0x119340` |
| `0x14` | Entero de 8 bytes little endian; el lector usa sus 32 bits bajos como semilla | Lectura en `0x119360–0x119365`; uso en `0x1193ac` |
| `0x1c` | Longitud de la carga reconstruida, entero de 8 bytes: **314** | Lectura en `0x119374–0x119379` |
| `0x24` en adelante | Bloques de valores de MT19937 intercalados con fragmentos de la carga | Bucle de `readSettings`, especialmente `0x1194c7`, `0x1195d0–0x1195df`, `0x1195ed–0x119634` |

`fnet::ServerSettingRW::readSettings`, VA `0x119190`, construye un MT19937 con la semilla. Por bloque, el byte bajo de una salida determina cuántos enteros de 32 bits leer y comparar con las siguientes salidas del generador. Otra salida, enmascarada con `0x3f`, determina los bytes de carga que se leen a continuación, sin superar la longitud restante.

**Comprobación independiente sobre bytes:** se verificaron los **1.783 enteros de relleno**, se reconstruyeron los **314 bytes** y se consumieron exactamente los **7.482 bytes** del archivo, sin sobrantes. SHA-256 de la carga aún cifrada: `1dad23148a784158944edf21e2ef681a583140fe3d37ce2e35d56fd5c4c85555`. Los 13 bloques y sus offsets constan en `evidence/dat-framing.json`.

**Hecho confirmado en ensamblador:** `readSettings` llama a `aseEncode(..., false)` en `0x11946f`, después a `dataToSettings` en `0x11947e`. `aseEncode`, VA `0x119a50`, llama a `EVP_aes_256_ofb` en `0x119b97` y a `EVP_DecryptInit_ex`, `EVP_DecryptUpdate` y `EVP_DecryptFinal_ex` en `0x119bae`, `0x119bcb` y `0x119be3`.

`dataToSettings`, VA `0x119770`, comienza leyendo una longitud de 32 bits y copiando esa cantidad de bytes a una cadena (`0x119791–0x1197ca`); continúa con más campos. No se ha asignado significado a todos ellos.

**Inferencia sustentada:** el DAT es un contenedor de configuración consumido por el SDK; el código analizado no lo trata como biblioteca ejecutable. `1.2.0` identifica su formato, mientras que la biblioteca identifica `3.4.0`.

**Pendiente:** significado y orden completos de los campos, selección regional y direcciones de servicios. No se deducen dominios ni credenciales a partir de cadenas aisladas. No se guardó texto descifrado ni se incorporaron constantes criptográficas a la integración.

## 4. Controles pendientes: ruta MQTT y serialización

**Hechos de cabecera y ensamblador.** El punto público es `fnet_connectionSend`, VA `0xa0b50`, que llama a `MqttConn::send` en `0xa0b54`. `MqttConn::send`, VA `0x10ded0`, llama al serializador `ConnDataProc::writeData2Str` en `0x10df35`. Este despachador, VA `0x636f0`, admite los tipos del enum hasta `0x15` inclusive.

En la referencia, `ComWanConn.cpp:199–377` construye `fnet_conn_write_data_t` con el tipo, un puntero a la estructura, el topic recibido y QoS `1`. Los topics proceden de la sesión/dispositivo; no se debe reconstruir su texto por conjetura.

La tabla documenta los nombres literales enlazados desde los serializadores, no una ejecución de los controles. Las coordenadas completas instrucción → cadena se conservaron en `evidence/field-refs.txt`.

| Tipo C / valor del enum | Serializador / VA | `cmd` observado | Claves observadas en argumentos |
| --- | --- | --- | --- |
| `TEMP_CTRL` / 4 | `JsonProc::tempCtrl2Obj` / `0xd8920` | `temperatureCtl_cmd` (`0x15ca1e`) | `platform`, `rightNozzle`, `leftNozzle`, `chamber`, `nozzles` |
| `LIGHT_CTRL` / 5 | `JsonProc::lightCtrl2Obj` / `0xd8e30` | `lightControl_cmd` (`0x15ca58`) | `status` |
| `AIR_FILTER_CTRL` / 6 | `JsonProc::airFilterCtrl2Obj` / `0xd8fa0` | `circulateCtl_cmd` (`0x15ca69`) | `internal`, `external` |
| `CLEAR_FAN_CTRL` / 7 | `JsonProc::clearFanCtrl2Obj` / `0xd9190` | `clearFan_cmd` (`0x15ca7a`) | `status` |
| `MOVE_CTRL` / 8 | `JsonProc::moveCtrl2Obj` / `0xd9300` | `moveCtrl_cmd` (`0x15ca87`) | `axis`, `delta` |
| `EXTRUDE_CTRL` / 9 | `JsonProc::extrudeCtrl2Obj` / `0xd94f0` | `extrudeCtrl_cmd` (`0x15ca9f`) | `axis`, `delta` |
| `HOMING_CTRL` / 10 | `JsonProc::homingCtrl2Obj` / `0xd96e0` | `homingCtrl_cmd` (`0x15caaf`) | Objeto `args`; sin estructura de entrada |
| `MATL_STATION_CTRL` / 11 | `JsonProc::matlStationCtrl2Obj` / `0xd97d0` | `ms_cmd` (`0x15cabe`) | `slot`, `action` |
| `INDEP_MATL_CTRL` / 12 | `JsonProc::indepMatlCtrl2Obj` / `0xd99c0` | `ipdMs_cmd` (`0x15caca`) | `action` |
| `PRINT_CTRL` / 13 | `JsonProc::printCtrl2Obj` / `0xd9b30` | `printerCtl_cmd` (`0x15cad4`) | `zAxisCompensation`, `speed`, `chamberFan`, `coolingFan`, `coolingLeftFan` |
| `JOB_CTRL` / 14 | `JsonProc::jobCtrl2Obj` / `0xd9ee0` | `jobCtl_cmd` (`0x15cae9`) | `jobID`, `action` |
| `CAMERA_STREAM_CTRL` / 19 | `ConnDataProc::cameraStreamCtrl2Str` / `0x653c0` | `streamCtrl_cmd` (`0x15a5e3`) | `action` |
| `MATL_STATION_CONFIG` / 20 | `JsonProc::matlStationConfig2Obj` / `0xda710` | `msConfig_cmd` (`0x15cb47`) | `slot`, `mt`, `rgb` |
| `INDEP_MATL_CONFIG` / 21 | `JsonProc::indepMatlConfig2Obj` / `0xda970` | `ipdMsConfig_cmd` (`0x15cb5b`) | `mt`, `rgb` |

Los nombres de enum de esta tabla llevan el prefijo `FNET_CONN_WRITE_` en la cabecera.

### Pausa, reanudación y cancelación

`native/FlashNetwork-3.4.0.h:303` declara dos punteros: `jobId` y `action`; documenta `pause`, `continue`, `cancel`. El serializador referencia `jobID` en `0xd9f24`, `action` en `0xd9f97` y `args` en `0xda00b`. El consumidor `ComWanConn::sendJobCtrl`, `ComWanConn.cpp:299`, usa el tipo 14 y QoS 1.

**Implicación:** existe contrato para añadir controles de trabajo al auxiliar mediante la API pública. El retorno de envío no demuestra que la impresora haya cambiado de estado; haría falta observar posteriormente su estado. Esta investigación no envió órdenes.

### Temperaturas: diferencias de representación

En `tempCtrl2Obj`, los cuatro escalares pasan por `round` y conversión a entero: `0xd8a94/0xd8a99`, `0xd8b12/0xd8b17`, `0xd8b98/0xd8b9d`, `0xd8c1e/0xd8c23`. Los elementos de `nozzlesTemp` se leen como `double` y se añaden al array (`0xd8994–0xd8a07`). El array se recorre cuando su puntero es distinto de cero y `nozzlesCnt > 0` (`0xd8953–0xd8963`).

Los cuatro escalares se incorporan al objeto; en esa ruta no se observó una condición que los omita por tener valor cero. **No interpretar una estructura inicializada a cero como “no cambiar nada”.** No se ha deducido ningún límite térmico ni un valor centinela universal. El consumidor `ComTempCtrl`, `ComCommand.hpp:382`, construye los cuatro campos y añade el vector por separado.

En `printCtrl2Obj`, `zAxisCompensation` se conserva como `double` (`0xd9bb3`); velocidad y los tres ventiladores pasan por `round` (`0xd9c3c`, `0xd9cc9`, `0xd9d49`, `0xd9dc0`). La cabecera documenta mm para compensación Z y porcentajes para velocidad/ventiladores, pero no establece aquí rangos seguros del equipo.

### Materiales y cámara

La cabecera `:286–293` documenta `action=0` para cargar y `action=1` para descargar. Se conserva el `slotId` suministrado; las claves `slot`, `mt` y `rgb` no prueban equivalencia con herramientas G-code ni disponibilidad de RFID. La función de cámara documenta `open`/`close` (`:325`), pero no permite deducir el formato del vídeo o la validez de su URL.

## 5. Discrepancia de `connectionSendMulti`

**Hecho confirmado en ensamblador.** `fnet_connectionSendMulti`, VA `0xa0bd0`, delega en `MqttConn::sendMulti`, VA `0x10e460`. Tras comprobar la conexión, esta última exige:

```text
topicCnt > 0
y (dataCnt == 1 o dataCnt == topicCnt)
```

Prueba: lectura de `topicCnt` en offset `0x1c` y comparación en `0x10e4cd–0x10e4d3`; lectura de `dataCnt` en offset `0x18`, comparación con 1 en `0x10e4d9–0x10e4e0`, comparación entre ambos contadores en `0x10e4f2–0x10e4f4`. La rama inválida prepara `0xbb9`, decimal **3001**, en `0x10e63a`, correspondiente a `FNET_CONN_SEND_ERROR`.

El comentario de la cabecera `:240–246` también permite `topicCnt == 1` con varios datos. Esa combinación **no pasa esta implementación** cuando `dataCnt > 1`.

Además, la función construye `failedIndices` y `failedCnt` y retorna cero en la ruta normal (`0x10e508–0x10e61e`). No basta comprobar el retorno: hay que inspeccionar el resultado y liberarlo con `fnet_freeWriteMultiResult`, VA `0xa0d60`.

**Impacto actual:** `src/orca_flashforge/sdk_print.py` usa `dataCnt=1`, `topicCnt=1` y comprueba `failedCnt`; esa llamada cumple la restricción observada. La discrepancia afecta a futuras operaciones agrupadas. No se reprodujo mediante llamadas nativas.

## 6. Eventos: probable defecto en la rama de descompresión

`ConnDataProc::str2ReadData`, VA `0x65670`, consulta `eventType`; para `device_action` (`0x659b1`) lee `payload.action_type` (`0x659c4`, `0x659da`).

**Hecho confirmado en ensamblador:**

1. `0x65a5b` carga la cadena de `0x15a4c4`, `job_download`. La comparación se llama en `0x65a65`.
2. Si coincide, prepara tipo `0x0c` y salta a `getJobInfo`.
3. Si no coincide, `0x65a73` carga **la misma dirección** y `0x65a7d` llama **al mismo comparador**, con el mismo objeto de cadena.
4. Esa segunda rama prepara tipo `0x0d` en `0x65a82` y también intenta llamar a `getJobInfo` en `0x65a95`.

La cabecera `:77–78` asigna 12 a `FNET_CONN_READ_JOB_DOWNLOAD` y 13 a `FNET_CONN_READ_JOB_UNZIP`.

**Inferencia:** con una cadena estable y el comparador normal, la segunda comparación no puede pasar después de fallar la primera. La rama de tipo 13 parece inalcanzable por este camino. No se ha probado que no exista otra ruta productora del evento ni se ha ejecutado el parser.

**Implicación:** no basar la confirmación de progreso o finalización exclusivamente en `JOB_UNZIP`. Reproducir después con un mensaje ficticio en un entorno aislado, sin conexión, antes de atribuir un fallo observado a firmware o integración.

## 7. Archivos y timelapse: contratos de lectura localizados

Los siguientes paths fueron leídos en `.rodata` y enlazados desde llamadas HTTP; no se consultaron. Son paths de esta versión, no URLs completas ni una especificación estable del servicio.

| Operación | Función pública / VA | Referencia comprobada |
| --- | --- | --- |
| Listar G-code | `fnet_getWanDevGcodeList` / `0x7f4b0` | `0x7fbf1` referencia `0x15b037`: `/api/v2/external/truck/device/local_files?id=%s`; llamada `HttpIO::get` en `0x7fe82` |
| Listar timelapse | `fnet_getWanDevTimeLapseVideoList` / `0x802a0` | `0x809e1` referencia `0x15b067`: `/api/v2/external/truck/device/delay_video/list?id=%s&currentPage=1&pageSize=%d`; llamada `HttpIO::get` en `0x80e07` |

Ambas firmas reciben `clientId`, `accessToken`, `devId`, punteros de salida y timeout. El listado de vídeos añade `maxVideoCnt`. El consumidor `ComCommand.hpp:224` solicita **15** vídeos: es una elección de ese consumidor, no un máximo del SDK o de la impresora. El path fija `currentPage=1`; la firma no ofrece parámetro de página.

### Datos y liberación

- `parseWanDevGcodeList`, VA `0xb5ad0`, referencia `code`, `data`, `file_name`, `file_obj`, `url`; llama a `getWanGcodeDetail` en `0xb5f96`.
- `getWanGcodeDetail`, VA `0xc6b80`, referencia `file_detail`, `printingTime`, `totalFilamentWeight`, `useMatlStation`, `gcodeToolCnt`, `gcodeToolDatas`. Los identificadores internos se leen con claves **`toolID`** (`0xc6d6a`) y **`slotID`** (`0xc6db1`). No se presupone que sean iguales.
- La salida se declara en `FlashNetwork-3.4.0.h:517–533`; tiempos en segundos y pesos en gramos según la cabecera. Se libera con `fnet_freeGcodeList(array, count)`, VA `0x6c540`.
- `parseWanDevTimeLapseVideoList`, VA `0xb6180`, referencia `data.videos`, `jobID`, `filename`, `videoUrl`, `thumbnail`; llama a `getWanTimeLapseVideoResolution`, VA `0xc70a0`, en `0xb689d`.
- La salida de vídeo contiene `jobId`, `fileName`, `videoUrl`, `thumbUrl`, `width`, `height` (`FlashNetwork-3.4.0.h:535`). Se libera con `fnet_freeTimeLapseVideoList(array, count)`, VA `0x81220`.

**Próxima extensión recomendada:** integrar primero estas consultas de lectura usando las funciones públicas y el auxiliar existente. Mantener las URLs recibidas en memoria y aplicar las restricciones de host/esquema del proyecto. No deducir que un error, un campo ausente o una primera página vacía describan todo el contenido de la impresora.

## 8. ABI y propiedad de memoria

Clang calculó estos layouts desde la cabecera fijada en el host x86-64, con `#pragma pack(push, 8)`. No se compiló ni ejecutó un programa de prueba. Salida completa: `evidence/abi-layouts.txt`.

| Estructura | Tamaño / alineación | Offsets decimales relevantes |
| --- | --- | --- |
| `fnet_conn_write_data` | 32 / 8 | `type=0`, `data=8`, `topic=16`, `qos=24` |
| `fnet_conn_write_multi_data` | 40 / 8 | `datas=8`, `topics=16`, `dataCnt=24`, `topicCnt=28`, `qos=32` |
| `fnet_conn_write_multi_result` | 16 / 8 | `failedIndices=0`, `failedCnt=8` |
| `fnet_conn_read_data` | 24 / 8 | `type=0`, `data=8`, `topic=16` |
| `fnet_temp_ctrl` | 48 / 8 | escalares `0/8/16/24`, `nozzlesTemp=32`, `nozzlesCnt=40` |
| `fnet_job_ctrl` | 16 / 8 | `jobId=0`, `action=8` |
| `fnet_gcode_tool_data` | 32 / 8 | `toolId=0`, `slotId=4`, cadenas `8/16`, `filemanetWeight=24` |
| `fnet_gcode_data` | 48 / 8 | cadenas `0/8`, tiempos/pesos `16/24`, `useMatlStation=32`, `gcodeToolCnt=36`, `gcodeToolDatas=40` |
| `fnet_time_lapse_video_data` | 40 / 8 | cadenas `0/8/16/24`, `width=32`, `height=36` |

Los offsets de contadores de envío múltiple y del vector de temperaturas también aparecen en el ensamblador citado. No se ha contrastado cada campo de todas las estructuras del SDK.

La cabecera del callback de lectura (`:93`) menciona `readData->devId`, pero la estructura real (`:674`) contiene **`topic`**. El consumidor libera `event.readData.topic` con `freeString` en `MultiComMgr.cpp:1062`. Por tanto, para esa propiedad debe seguirse la estructura y el uso real, no el comentario desactualizado.

El callback de renovación exige reservar el nuevo `clientId` con `fnet_allocString`; el SDK lo liberará (`FlashNetwork-3.4.0.h:89–91`, `ComWanConn.cpp:388–404`). Los callbacks y sus datos asociados deben mantenerse vivos durante la conexión.

El consumidor detiene la conexión, espera su trabajo pendiente y después la libera (`ComWanConn.cpp:45–60`). Direcciones públicas: `fnet_connectionStop=0xa0ad0`, `fnet_freeConnection=0xa0a30`. La integración debe conservar el aislamiento en proceso auxiliar y evitar liberar callbacks mientras puedan ser invocados. No se midieron aquí carreras ni plazos de cierre.

## 9. Herramientas y límites de esta sesión

Se utilizaron GNU binutils 2.47 (`readelf`, `nm`, `objdump`), Clang 23.1.5, Python 3.14.10, `file`, `7z` y los scripts de inventario de la skill. No hubo decompilación a C: las conclusiones sobre la biblioteca proceden del ensamblador y sus referencias a datos.

No se expusieron herramientas MCP de REA, Ghidra, IDA o radare2. El detector de la skill no encontró Ghidra, `analyzeHeadless`, rizin ni radare2. `command -v rea ghidra analyzeHeadless java` solo encontró Java, en `/usr/lib/jvm/java-17-openjdk/bin/java`.

Para una continuación con Ghidra/REA faltan un backend Ghidra instalado, un JDK compatible con esa versión y el servidor REA registrado y conectado en el cliente MCP. El detector de la skill señala JDK 21+ para Ghidra 12; actualmente detecta JDK 17. Antes de instalar, comprobar requisitos de las versiones concretas elegidas. No se instalaron herramientas ni se cambió la configuración MCP en esta investigación.

La biblioteca conserva símbolos suficientes para continuar con binutils mientras tanto. Las rutinas prioritarias para decompilar después son `ServerSettingRW::dataToSettings`, `ConnDataProc::str2ReadData`, `MqttConn::sendMulti` y los parsers de detalles y materiales.

## 10. Incertidumbres y próximos pasos

### Continuación: cuenta y lectura de máquina

Inspección estática adicional del mismo ELF, sin llamadas de red: `fnet_getTokenByPassword` referencia en `0x76c03` la ruta `/oauth/token?grant_type=password&scope=*&password_type=password`, añade fragmentos `client_id`, `username` y `password` en `0x76c71`, `0x76d73` y `0x76ec3`, y llama a `HttpIO::post` en `0x76fe5`. La posición de los datos en la petición y todas las cabeceras aún requieren confirmación; no se debe registrar la URL construida, pues contiene la contraseña.

En `0x76b6c` y `0x76b73` el código carga `g_userComUrl`; en `0x76c1c` y `0x76c23` carga `g_userComClientId` antes de añadirlo a la URL de autorización. Por tanto, la ruta aislada no basta para implementar el acceso: faltan el origen de usuario y un identificador de cliente independiente autorizado. El plan excluye incorporar credenciales extraídas de la configuración del fabricante. No se ha determinado si ese identificador es público o si existe uno disponible para clientes independientes.

Actualización: el usuario autorizó expresamente usar el identificador observado, incorporado en `cloud_protocol.py`. Una lectura local de `ServerSettingRW::readSettings` sobre la configuración instalada devolvió `https://auth.voxelshare.com` como `g_userComUrl` y `https://api.voxelshare.com` como `g_busComUrl`. Las referencias a esos campos se comprueban en `fnet_initlize` (`0x6a964`, `0x6a97a`) y en las operaciones de login (`0x76b6c`), lista (`0x7dbf4`) y detalle (`0x7efc4`). Esto demuestra que el recorrido de cuenta y máquina de esta instalación usa esos dominios. No se realizaron solicitudes ni se usaron credenciales. Los esquemas completos y el comportamiento del servidor siguen sin validarse.

Continuación estática para impresión independiente: `UploadFileClound::getCloundUploadInfo` (`0x11b9c0`) referencia `/api/v2/external/truck/device/upload/config`; `fnet_wanDevAddCloundJob` (`0x82e90`) referencia `/api/v2/external/truck/slicer/cloud_job`; `MqttConn::getMqttConfig` (`0x110ce0`) referencia `/api/v2/external/truck/device/mqtt/config`. Las tres rutas usan cabeceras asociadas a `clientId` y `token`. La presencia de estas rutas no documenta sus cuerpos, autenticación del almacenamiento, MQTT ni resultados de error. No se ha hecho ninguna solicitud de subida, creación o impresión durante este análisis.

Las referencias estáticas en `makeGetCloundUploadInfo` (`0xaea20`) incluyen `deviceID`, `fileName`, `printNow`, `leveling`, `flowCalibration`, `firstLayerInspection`, `timeLapseVideo`, `useMs`, `tCount`, `opType`, `keys` y `ms`. El parser de configuración MQTT (`0xbcea0`) referencia `server`, `username`, `password`, `uTopic` y `commonTopics`. El serializador de inicio (`0x644b0`) referencia `newJob_cmd`, `jobInfo`, `filename`, `fileMd5` y `filepath`. Las cadenas identifican campos posibles; por sí solas no establecen estructura, tipos o precondiciones completos.

Se invocaron después, en un proceso local con estructuras y datos ficticios, `makeWanDevAddCloundJob` y `startCloundJob2Str`, sin sesión ni acceso de red. Sus salidas confirmaron las estructuras implementadas en `cloud_jobs.py`: `deviceID`, metadatos de archivo y miniatura, opciones y mapeos para la creación; `newJob_cmd`, `jobInfo` y `args` para el despacho. Los mapeos contienen `tNum`, `slot`, `mt`, `sRgb` y `tRgb`. La autenticación y la subida de archivos siguen sin validarse; los serializadores aún no despachan trabajos.

`fnet_getWanDevList` referencia `/api/v2/external/truck/device/list` en `0x7d548`, parámetros de paginación en `0x7d5f4` y llama a `HttpIO::get` en `0x7dcc7`. `fnet_getWanDevProductDetail` referencia `/api/v2/external/truck/device/details?id=%s` en `0x7ee01` y llama a `HttpIO::get` en `0x7f092`. Las dos funciones también referencian las cadenas de cabecera `clientId: %s` y `token: %s`. Esto no confirma todavía el origen seleccionado, todas las cabeceras ni el esquema completo de respuesta. No habilita una implementación cloud segura por sí solo. No se ha establecido que estas operaciones dependan de Voxelshare.

1. **Comportamiento real de eventos:** confirmar con entradas ficticias la rama de `JOB_UNZIP`. Mantenerlo como probable defecto estático hasta reproducirlo sin red.
2. **Precondiciones y errores:** elaborar casos simulados para contadores de `sendMulti`, salidas vacías, errores parciales y liberación. El contrato observado no justifica reintentos automáticos de escrituras.
3. **DAT:** terminar el esquema de `dataToSettings` y el uso de cada campo. La reconstrucción del contenedor no valida autenticación, regiones ni configuración remota.
4. **Lecturas pendientes:** diseñar bindings de listas de G-code y timelapse usando los layouts y frees registrados. Cualquier implementación posterior exige las pruebas y el build indicados por `AGENTS.md`.
5. **Capacidades C5 Pro:** correlacionar `pid`, `nozzleCount`, estado y materiales con evidencia ya fijada. No inferir RFID, temperaturas máximas o equivalencias entre herramientas y ranuras por presencia de símbolos.
6. **Sesión y coexistencia:** siguen sin verificarse en esta sesión login, renovación real, reconexión MQTT, convivencia con Flash Maker y respuestas del servidor.

| Tipo de validación | Realizado en esta investigación |
| --- | --- |
| Análisis estático | Hashes, ELF, símbolos, funciones seleccionadas, referencias a cadenas, cabeceras y consumidores |
| Reconstrucción independiente de formato | Lectura offline del DAT; comprobación de relleno y carga cifrada, sin ejecutar código del fabricante |
| Pruebas simuladas del SDK/integración | Ninguna nueva; las de `docs/analisis.md` son antecedentes |
| Carga/ejecución nativa del SDK | Ninguna nueva |
| Validación con cuenta/impresora real | Ninguna |

## Apéndice: reproducción local

Las muestras y salidas grandes están bajo `local/sdk-analysis-2026-11-15/`, excluido por `.gitignore`. No se añadieron binarios o DAT a paquetes ni se generaron paquetes.

Desde la raíz del repositorio, estos comandos leen la biblioteca y la cabecera:

```bash
sdk_so='/home/monky/Impresora3D/squashfs-root/bin/libFlashNetwork.so'
sdk_dat='/home/monky/Impresora3D/squashfs-root/resources/data/FLASHNETWORK9.DAT'
sha256sum "$sdk_so" "$sdk_dat" native/FlashNetwork-3.4.0.h
readelf -W -h -l -S -d -n "$sdk_so"
nm -D -n -C "$sdk_so"
objdump -d -C -Mintel --start-address=0x62a18 --stop-address=0x62a4b "$sdk_so"
objdump -d -C -Mintel --start-address=0x65a5b --stop-address=0x65a9f "$sdk_so"
objdump -d -C -Mintel --start-address=0x10e4cd --stop-address=0x10e508 "$sdk_so"
objdump -d -C -Mintel --start-address=0x119190 --stop-address=0x119c90 "$sdk_so"
clang -x c -Xclang -fdump-record-layouts-complete -fsyntax-only native/FlashNetwork-3.4.0.h
```

Para comparar API declarada/exportada, sin cargar la biblioteca:

```python
from pathlib import Path
import re, subprocess
h = Path('native/FlashNetwork-3.4.0.h').read_text()
s = subprocess.check_output(['nm', '-D', '--defined-only',
    '/home/monky/Impresora3D/squashfs-root/bin/libFlashNetwork.so'], text=True)
declared = set(re.findall(r'FNET_API\s+[^;]*?\b(fnet_\w+)\s*\(', h))
exported = set(re.findall(r' T (fnet_\w+)$', s, re.M))
print(len(declared), len(exported), declared - exported, exported - declared)
# Resultado observado: 119 119 set() set()
```

Reconstrucción independiente del contenedor DAT; ejecutar con `python3 -I`. No descifra ni guarda su contenido:

```python
from pathlib import Path
import hashlib, random, struct
b = Path('/home/monky/Impresora3D/squashfs-root/resources/data/FLASHNETWORK9.DAT').read_bytes()
assert b[:20] == b'FNETDAT-1.2.0'.ljust(20, b'\0')
seed, length = struct.unpack_from('<QQ', b, 20)
state = [seed & 0xffffffff]
for i in range(1, 624):
    state.append((1812433253 * (state[-1] ^ (state[-1] >> 30)) + i) & 0xffffffff)
r = random.Random()
r.setstate((3, tuple(state + [624]), None))
pos, checked = 36, 0
payload = bytearray()
while len(payload) < length:
    count = r.getrandbits(32) & 255
    for _ in range(count):
        assert struct.unpack_from('<I', b, pos)[0] == r.getrandbits(32)
        pos += 4
        checked += 1
    size = min(r.getrandbits(32) & 63, length - len(payload))
    assert pos + size <= len(b)
    payload.extend(b[pos:pos + size])
    pos += size
assert pos == len(b)
print(len(payload), checked, pos, hashlib.sha256(payload).hexdigest())
# 314 1783 7482 1dad23148a784158944edf21e2ef681a583140fe3d37ce2e35d56fd5c4c85555
```

Este lector reproduce la muestra identificada; no es un parser endurecido para DAT arbitrarios ni una propuesta de código para producción.
