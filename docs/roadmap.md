# Hitos

## 0. Base entregada

Página Pages, modo demostración, consulta LAN, puente aislado a FlashNetwork 3.4.0, login normal, renovación, lista de impresoras y consulta de detalles. Wheel local, CLI, pruebas y análisis fijado por commits.

La cuenta se puede recordar en el llavero de Linux guardando solo el token de renovación. Se restauran sesiones al abrir el plugin, se actualizan los tokens rotados y se permite salir olvidando la cuenta. Probado con Secret Service real y credenciales ficticias, y con el SDK simulado; falta observar la caducidad y restauración de una sesión FlashForge real.

## 1. Validar la conexión normal en la Creator 5 Pro

- Cargar el wheel en OrcaSlicer 2.5.0.
- Usar las rutas del SDK y DAT de la misma instalación de Flash Studio.
- Iniciar sesión manualmente; registrar únicamente códigos de error y versión, nunca credenciales ni tokens.
- Confirmar que aparecen las impresoras vinculadas y que el estado cambia durante una impresión iniciada por el usuario.
- Si el servidor lo requiere, completar el flujo público de perfil y relación de cuenta antes de listar.
- Comprobar que Flash Maker sigue conectado y documentar cualquier exclusión de sesiones.
- Recibir la URL real de cámara y verificar el formato reproducible en WebKitGTK. Si es HLS no admitido, decidir una dependencia local de reproducción.

Completado cuando la carga nativa, la consulta real, la renovación y la coexistencia se hayan observado. No basta una prueba simulada.

## 2. Controles WAN y eventos

Implementar `fnet_createConnection`, callbacks con sus tiempos de vida, suscripciones y liberación correcta de cada tipo de evento. Probar primero lecturas. Añadir pausa/reanudación/cancelación con el `jobId` actual y confirmaciones apropiadas. Rechazar comandos incompatibles usando las capacidades reales del dispositivo.

Después: luz, ventiladores, temperaturas, homing, movimientos y carga/descarga de materiales. Mantener los controles peligrosos fuera de las pruebas automáticas contra el equipo.

## 3. Impresión integrada

Implementado en la página del plugin: identidad de cuenta, selección del destino, asociación con el perfil activo, preparación de G-code/3MF laminado, copia inmutable para revisión, mapeo explícito de ranuras, subida, creación del trabajo y entrega de la orden MQTT. Se conservan nivelado y timelapse; se permite cancelar la subida. Hay pruebas con un SDK simulado, sin impresiones reales.

Pendiente: validar el recorrido con la máquina y comprobar la compatibilidad efectiva del archivo y los materiales. La cuenta se muestra en FlashForge; no es un proveedor de cuenta del monitor nativo. La asociación de perfil se guarda en la configuración del plugin.

Usar las firmas y secuencias de `WanDevSendGcodeThd.cpp` para subida, miniatura y creación de trabajo. Conservar mapeo de herramientas y ranuras, nivelado y timelapse. Verificar cancelación de la transferencia y distinguir subida de inicio de impresión.

Implementada la capacidad `PrinterAgent` con ID `org.orca-flashforge.cloud`: reutiliza sesión, SDK aislado y envío; traduce progreso y errores, permite cancelar la subida y rechaza operaciones no admitidas. El botón **Usar botón Imprimir de OrcaSlicer** configura el agente y el destino del perfil mediante una transacción del host. El LFS publica materiales, colores y ranuras mediante sincronización `Pull`, con tabla inversa para preservar IDs originales y rechazo de mapeos obsoletos.

Pendiente de validación nativa: preferencia experimental de agentes, selección de dispositivo, sincronización del LFS en el laminador y diálogo de impresión con el archivo exportado por OrcaSlicer. La prueba de arranque aislada de Flatpak 2.5.1 abortó antes de cargar plugins. Las pruebas del contrato del agente usan un doble del host; no se ha ejecutado ninguna impresión real.

## 4. Gestión de archivos y materiales

Lista de archivos, miniaturas, historial/timelapse y edición de ranuras. Implementadas la lectura del LFS y su publicación para sincronización nativa; falta validarlas en el equipo. La página prepara automáticamente la lista de ranuras y propone coincidencias exactas únicas por material y color del G-code. Las funciones no soportadas por Creator 5 Pro deben aparecer como no disponibles según sus capacidades.

## 5. Distribución y mantenimiento

Validar ABI 3.4.1 y 3.4.3 contra sus cabeceras antes de admitirlos. Añadir CI Linux, pruebas con OrcaSlicer real y matriz de firmware. Automatizar releases del wheel. El usuario aporta los archivos del SDK; no empaquetar binarios del fabricante.

## 6. Reescritura independiente de la parte de máquina del SDK

Objetivo: reimplementar en Python las funciones de máquina de `libFlashNetwork.so`, manteniendo OrcaSlicer original y sin pedir la instalación de Python ni de dependencias por separado. El alcance es autorización necesaria para la máquina, selección de impresora, estado/materiales, cámara y envío online. No incluye funciones de slicer, tienda ni servicios Voxelshare ajenos a ese flujo.

OrcaSlicer 2.5.1 Flatpak, fijado y probado en este proyecto, incluye CPython 3.12.13 en su entorno `/app/libpython` y ejecuta plugins Python con el runtime integrado. Por tanto, el usuario no debe instalar Python aparte para este paquete. El puente actual (`bridge.py`) lanza `/usr/bin/python3`; esa ruta sí crea un requisito externo y se cambiará para usar el runtime de Orca o, si la API del host lo permite, ejecutar el backend dentro del intérprete del plugin.

Arquitectura objetivo: plugin `.whl` compatible con `PythonPluginBridge`; cliente independiente del SDK propietario, implementado en Python; cliente HTTP y MQTT aislados detrás de interfaces sustituibles; y llamadas de red mantenidas en el auxiliar si se puede resolver la ruta del intérprete incluido. Las dependencias necesarias se limitarán a la biblioteca estándar o se empaquetarán dentro del wheel, con licencias y procedencia documentadas. No se instalarán con `pip`/`uv` en el equipo del usuario.

**Límite de distribución:** el soporte de Python incluido varía por formato de OrcaSlicer. La referencia fijada indica runtime en el instalador Windows, Flatpak y AppImage; una instalación FHS simple puede no incluirlo. El objetivo es soportar Windows y OrcaSlicer Flatpak. El lanzador localizará el runtime de Orca según la plataforma y rechazará formatos sin runtime compatible, sin buscar silenciosamente Python del sistema.

### Fase 1: mapa verificable del protocolo

- Trazar cada función pública que usa el consumidor fijado hasta su HTTP, configuración MQTT y parser de respuesta. Prioridad: login/renovación, lista de dispositivos, detalle de máquina, materiales/cámara y flujo de envío.
- Para cada llamada, registrar símbolo/VA, offsets de las cadenas y estructuras, dirección de los datos y consumidor de referencia. Separar hechos, inferencias y huecos en el informe estático.
- Confirmar qué servidor recibe las operaciones de máquina. La configuración local contiene `api.fdmcloud.flashforge.com`, `api.voxelshare.com`, `report.voxelshare.com`, `desktop.voxelshare.com` y `:8083/mqtt`; su mera presencia no asigna operaciones. Tratar `api.fdmcloud.flashforge.com` solo como candidato hasta seguir sus referencias.
- Excluir por defecto los tres dominios Voxelshare. Si una operación imprescindible depende de uno, marcarla como dependencia externa y dejarla sin implementar hasta resolver su necesidad y alcance.

**Salida/gate:** matriz de contratos suficiente para implementar una operación sin adivinar host, ruta, cabeceras, campos ni credenciales. No avanzar una función si falta cualquiera de esos datos.

### Fase 2: cliente Python sin dependencias externas

- Base implementada: `cloud_http.py` ofrece HTTPS estándar inyectable, plazo, límite de respuesta y errores sin cuerpos remotos. Rechaza URLs con credenciales y rutas externas; no fija host, rutas ni cabeceras FlashForge. Todavía no se conecta a operaciones del producto.
- Crear modelos neutrales de sesión, impresora, estado, materiales, cámara y errores; sin estructuras ABI ni nombres de tipos del fabricante.
- Implementar transporte HTTP/TLS con biblioteca estándar e inyectable. Para MQTT, elegir entre una implementación Python empaquetada en el wheel con licencia compatible o código propio basado en el contrato documentado.
- Añadir plazos, límites de tamaño, validación de esquema, cancelación y redacción de secretos. Mantener el auxiliar y el IPC JSON versionado si se puede lanzar con el Python incluido; si no, ejecutar las operaciones dentro del plugin y registrar la decisión de aislamiento.
- No declarar `/usr/bin/python3`, paquetes del sistema, `pip` o `uv` como requisito de ejecución. No se cargarán `libFlashNetwork.so` ni el DAT.
- Por petición expresa del usuario se incorpora el `client_id` de aplicación observado en Flash Studio. No incorporar claves privadas ni secretos de aplicación. Las credenciales personales se introducen en el formulario y solo el token de renovación puede guardarse en el llavero.

**Salida/gate:** pruebas simuladas del cliente y del IPC pasan usando el Python de Orca; el wheel y su ejecución no dependen de Python del sistema, instalación de paquetes, `ctypes`, `.so` del SDK ni DAT.

### Fase 3: cuenta y operaciones de solo lectura

- Implementar solo los pasos de autorización que el mapa confirme como necesarios para acceder a la máquina, renovar sesión y cerrar/olvidar cuenta.
- Conservar el manejo de token de renovación exclusivamente mediante el llavero del sistema. No registrar contraseña, token de acceso/renovación, URL firmada ni cuerpos de autenticación.
- Añadir lista de máquinas y consulta de detalle; normalizar temperaturas ausentes como `null`, conservar IDs originales de materiales y validar host/esquema de URLs de cámara.
- Dejar fuera perfil social, puntos, tienda, telemetría ajena a la máquina y reportes Voxelshare.

**Salida/gate:** respuestas sintéticas cubren éxito, expiración, error de servidor, respuesta incompleta y datos malformados; consultas simuladas no salen a Internet.

### Fase 4: MQTT de máquina y cámara

- Confirmar broker, puerto/TLS, autenticación, topic y suscripción desde el contrato observado. No inferirlos de `:8083/mqtt` ni construir topics desde IDs.
- Implementar primero conexión y recepción de eventos de lectura. Después, si el contrato lo respalda, apertura/cierre de cámara y los controles de máquina por tipo, uno a uno.
- Mantener eventos, callbacks, estado de conexión y reconexión bajo límites explícitos. No reportar que una orden tuvo efecto hasta observar confirmación de estado.

**Salida/gate:** pruebas MQTT locales reproducen conexión, desconexión, mensajes incompletos, eventos desconocidos y cierre limpio. Ninguna acción física en pruebas.

### Fase 5: subida y trabajos

- Reproducir el flujo confirmado de subida, creación de trabajo y despacho MQTT como fases separadas. Preservar el archivo inmutable, destino, perfil, placa y mapeo explícito de ranuras ya validados por el plugin.
- Aplicar idempotencia/cancelación solo cuando el protocolo observado las defina; no reintentar escrituras ambiguas automáticamente.
- Entregar progreso por IPC y distinguir “subida completa”, “trabajo creado”, “orden despachada” e “impresión iniciada” como estados distintos.

**Salida/gate:** pruebas simuladas de cada corte/fallo aseguran que no se duplica una escritura ni se afirma un inicio físico sin evidencia.

### Fase 6: migración del backend y retirada del SDK

- Cambiar el lanzador de `bridge.py` para encontrar el Python distribuido con OrcaSlicer: `<directorio de Orca>/python/python.exe` en Windows y la ruta del runtime del paquete en Flatpak. Sin fallback silencioso al Python del sistema. Mantener el auxiliar aislado y el IPC existente si el runtime incluido proporciona un ejecutable utilizable.
- Migrar por grupos: cuenta/lecturas, eventos/cámara y finalmente trabajos. Mantener contratos de UI/agente documentados y versionados.
- Tras cada grupo, ejecutar `PYTHONPATH=src python -m unittest discover -s tests -v` y `uv build`; verificar instalación y ejecución desde OrcaSlicer Flatpak y Windows limpios, sin Python del host ni dependencias preinstaladas.
- Quitar selector de `.so`, diagnóstico ABI, cabecera vendorizada y referencias a DAT de los requisitos de ejecución solo cuando no exista ruta activa que los necesite. No borrar evidencia histórica de análisis.
- Verificar artefactos de distribución: sin requisito de Python del sistema, sin dependencias a instalar por separado, y sin `.so`, DAT, secretos ni muestras del fabricante.

### Fase 7: validación manual separada

- Primero validar login, lista y estado con una cuenta propia y sin emitir comandos de escritura.
- Después validar cámara; luego, con confirmación explícita, una subida y despacho escogidos por el usuario. No automatizar impresiones, movimientos, calentamiento ni firmware.
- Registrar versión, resultados y errores sin secretos. La simulación, el análisis estático y la validación real se reportan por separado.

No se inventarán hosts, rutas, cabeceras, credenciales, comandos, límites ni mapeos. Los dominios observados no se tratarán como endpoints activos hasta verificar su uso estático; no se publicarán secretos del DAT. Si el acceso de máquina requiere una operación Voxelshare excluida o credenciales de cliente no disponibles, se documentará como bloqueo concreto en vez de ampliar silenciosamente el alcance.
