"""Cloud upload and MQTT dispatch from the public FlashNetwork 3.4.0 consumer.

Used only by the isolated worker. Never import this module from OrcaSlicer.
"""

import ctypes as c
import hashlib
import threading
from pathlib import Path

from .model import FlashForgeError

S, I, P = c.c_char_p, c.c_int, c.c_void_p
ProgressCallback = c.CFUNCTYPE(I, c.c_longlong, c.c_longlong, P)
StatusCallback = c.CFUNCTYPE(None, I, P)
UpdateCallback = c.CFUNCTYPE(I, c.POINTER(P), P)


class ReadData(c.Structure):
    _fields_ = [("type", I), ("data", P), ("topic", P)]


ReadCallback = c.CFUNCTYPE(None, c.POINTER(ReadData), P)


class MaterialMapping(c.Structure):
    _fields_ = [("toolId", I), ("slotId", I)] + [(k, S) for k in
                ("materialName", "toolMaterialColor", "slotMaterialColor")]


class SendGcode(c.Structure):
    _fields_ = [(k, S) for k in ("gcodeFilePath", "thumbFilePath", "gcodeDstName")] + [
        (k, I) for k in ("printNow", "levelingBeforePrint", "flowCalibration", "firstLayerInspection",
                        "timeLapseVideo", "useMatlStation", "gcodeToolCnt")] + [
        ("materialMappings", c.POINTER(MaterialMapping)), ("callback", ProgressCallback), ("callbackData", P)]


class CloudGcode(c.Structure):
    _fields_ = [(k, S) for k in ("bucketName", "endpoint", "gcodeStorageKey", "gcodeStorageUrl",
                                "thumbStorageKey", "thumbStorageUrl")]


class CloudJob(c.Structure):
    _fields_ = [(k, c.POINTER(S)) for k in ("devIds", "devSerialNumbers", "jobIds")] + [("devCnt", I)] + [
        (k, S) for k in ("gcodeName", "gcodeType", "gcodeMd5")] + [("gcodeSize", c.c_longlong)] + [
        (k, S) for k in ("thumbName", "thumbType", "thumbMd5")] + [("thumbSize", c.c_longlong)] + [
        (k, S) for k in ("bucketName", "endpoint", "gcodeStorageKey", "gcodeStorageUrl", "thumbStorageKey",
                       "thumbStorageUrl", "unionId")] + [(k, I) for k in
        ("plateNo", "printNow", "levelingBeforePrint", "flowCalibration", "firstLayerInspection",
         "timeLapseVideo", "useMatlStation", "gcodeToolCnt")] + [("materialMappings", c.POINTER(MaterialMapping))]


class AddJobResult(c.Structure):
    _fields_ = [("error", I), ("devId", S), ("jobId", S)]


class BindAccountResult(c.Structure):
    _fields_ = [("showUserPoints", I)]


class MqttConfig(c.Structure):
    _fields_ = [("userTopic", S), ("commonTopics", c.POINTER(S)), ("commonTopicCnt", I)]


class ConnectionSettings(c.Structure):
    _fields_ = [("clientId", S), ("statusCallback", StatusCallback), ("statusCallbackData", P),
                ("updateCallback", UpdateCallback), ("updateCallbackData", P),
                ("readCallback", ReadCallback), ("readCallbackData", P)]


class WriteMulti(c.Structure):
    _fields_ = [("type", I), ("datas", c.POINTER(P)), ("topics", c.POINTER(S)),
                ("dataCnt", I), ("topicCnt", I), ("qos", I)]


class WriteResult(c.Structure):
    _fields_ = [("failedIndices", c.POINTER(I)), ("failedCnt", I)]


class CameraControl(c.Structure):
    _fields_ = [("action", S)]


class MoveControl(c.Structure):
    _fields_ = [("axis", S), ("delta", c.c_double)]


class WriteData(c.Structure):
    _fields_ = [("type", I), ("data", P), ("topic", S), ("qos", I)]


def md5(path):
    # These checksums are required by the manufacturer's job contract, not password hashes.
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, lambda: hashlib.md5(usedforsecurity=False)).hexdigest().encode()


class PrintTransport:
    def __init__(self, session):
        self.session, self.lib = session, session.lib
        self.connection = P()
        self.connected = threading.Event()
        self._callbacks = None
        self._bind()

    def _bind(self):
        def bind(name, restype, *args):
            function = getattr(self.lib, name)
            function.restype, function.argtypes = restype, list(args)
        try:
            bind("fnet_bindAccountRelp", I, S, S, S, c.POINTER(c.POINTER(BindAccountResult)), I)
            bind("fnet_freeBindAccountRelpResult", None, c.POINTER(BindAccountResult))
            bind("fnet_getMqttConfig", I, S, S, c.POINTER(c.POINTER(MqttConfig)), I)
            bind("fnet_freeMqttConfig", None, c.POINTER(MqttConfig))
            bind("fnet_createConnection", I, c.POINTER(P), c.POINTER(ConnectionSettings))
            bind("fnet_connectionStop", None, P)
            bind("fnet_freeConnection", None, P)
            bind("fnet_allocString", P, S, I)
            bind("fnet_wanDevSendGcodeClound", I, S, S, c.POINTER(SendGcode), c.POINTER(c.POINTER(CloudGcode)), I)
            bind("fnet_freeCloundGcodeData", None, c.POINTER(CloudGcode))
            bind("fnet_wanDevAddCloundJob", I, S, S, c.POINTER(CloudJob), c.POINTER(c.POINTER(AddJobResult)),
                 c.POINTER(I), I)
            bind("fnet_freeAddCloudJobResults", None, c.POINTER(AddJobResult), I)
            bind("fnet_connectionSendMulti", I, P, c.POINTER(WriteMulti), c.POINTER(c.POINTER(WriteResult)))
            bind("fnet_freeWriteMultiResult", None, c.POINTER(WriteResult))
            for name in ("fnet_freeSyncLoginInfo", "fnet_freeSyncBindInfo", "fnet_freeSyncOnlineInfo",
                         "fnet_freeSliceState", "fnet_freeJobInfo"):
                bind(name, None, P)
        except AttributeError:
            raise FlashForgeError("Esta biblioteca no expone todas las funciones de envío de FlashNetwork 3.4.0.") from None

    def _mqtt_config(self):
        config = c.POINTER(MqttConfig)()
        try:
            self.session._authenticate()
            self.session._check(self.lib.fnet_getMqttConfig(
                self.session.client_id, self.session.access, c.byref(config), 15000))
            if not config:
                raise FlashForgeError("FlashForge no devolvió la configuración MQTT.")
        finally:
            if config:
                self.lib.fnet_freeMqttConfig(config)

    def _connect(self):
        if self.connection:
            if self.connected.is_set():
                return
            self.close()
        profile = self.session.profile()
        if not profile.get("email"):
            raise FlashForgeError("FlashForge no devolvió el correo necesario para preparar el envío.")
        relation = c.POINTER(BindAccountResult)()
        try:
            self.session._check(self.lib.fnet_bindAccountRelp(
                self.session.client_id, self.session.access, profile["email"].encode(), c.byref(relation), 15000))
        finally:
            if relation:
                self.lib.fnet_freeBindAccountRelpResult(relation)
        self._mqtt_config()

        def status(value, _):
            if value == 0:
                self.connected.set()
            else:
                self.connected.clear()

        def update(client_id, _):
            try:
                self._mqtt_config()
                pointer = self.lib.fnet_allocString(self.session.client_id, len(self.session.client_id))
                if not pointer:
                    return 1
                client_id[0] = pointer
                return 0
            except Exception:
                return 1

        def read(pointer, _):
            if not pointer:
                return
            value = pointer.contents
            free = {0: "fnet_freeString", 1: "fnet_freeString", 4: "fnet_freeSyncLoginInfo",
                    5: "fnet_freeSyncBindInfo", 6: "fnet_freeSyncBindInfo", 7: "fnet_freeSyncOnlineInfo",
                    8: "fnet_freeSyncOnlineInfo", 9: "fnet_freeDevDetail", 10: "fnet_freeString",
                    11: "fnet_freeSliceState", 12: "fnet_freeJobInfo", 13: "fnet_freeJobInfo"}.get(value.type)
            try:
                if free and value.data:
                    if value.type == 9:
                        from .sdk import Detail
                        self.lib.fnet_freeDevDetail(c.cast(value.data, c.POINTER(Detail)))
                    else:
                        getattr(self.lib, free)(value.data)
            finally:
                if value.topic:
                    self.lib.fnet_freeString(value.topic)

        self._callbacks = (StatusCallback(status), UpdateCallback(update), ReadCallback(read))
        settings = ConnectionSettings(self.session.client_id, self._callbacks[0], None,
                                      self._callbacks[1], None, self._callbacks[2], None)
        try:
            self.session._check(self.lib.fnet_createConnection(c.byref(self.connection), c.byref(settings)))
            if not self.connection or not self.connected.wait(15):
                raise FlashForgeError("No se pudo establecer la conexión MQTT para enviar el trabajo.")
        except Exception:
            self.close()
            raise
        # No syncLogin is emitted: do not assert exclusive ownership of the user's session.

    def open_camera(self, device_id):
        self.session.devices()
        device = self.session._devices.get(device_id)
        if not device or not device.get("devTopic"):
            raise FlashForgeError("La cuenta no devolvió la ruta de la cámara seleccionada.")
        self._connect()
        try:
            send = self.lib.fnet_connectionSend
        except AttributeError:
            raise FlashForgeError("El SDK no admite abrir la cámara.") from None
        send.restype, send.argtypes = I, [P, c.POINTER(WriteData)]
        control = CameraControl(b"open")
        write = WriteData(19, c.cast(c.pointer(control), P), device["devTopic"].encode(), 1)
        self.session._check(send(self.connection, c.byref(write)))

    def move(self, device_id, axis, delta):
        if axis not in ("x", "y", "z") or type(delta) is not int or delta not in (-1, 1):
            raise FlashForgeError("Movimiento no válido.")
        self.session.devices()
        device = self.session._devices.get(device_id)
        if not device or not device.get("devTopic"):
            raise FlashForgeError("La impresora seleccionada no está disponible.")
        status = self.session.status(device_id)
        if not status.get("move_enabled"):
            raise FlashForgeError("La impresora no permite movimientos en este momento.")
        self._connect()
        send = self.lib.fnet_connectionSend
        send.restype, send.argtypes = I, [P, c.POINTER(WriteData)]
        control = MoveControl(axis.encode(), delta)
        write = WriteData(8, c.cast(c.pointer(control), P), device["devTopic"].encode(), 1)
        self.session._check(send(self.connection, c.byref(write)))

    def send(self, job, options, cancel, emit):
        self.session.devices()  # Refresh membership and routing immediately before any write.
        device = self.session._devices.get(job["device_id"])
        if not device or not device["serialNumber"] or not device["devTopic"]:
            raise FlashForgeError("La cuenta no devolvió los datos de envío de la impresora seleccionada.")
        if options["use_material_station"]:
            slots = {slot["id"]: slot for slot in self.session.status(job["device_id"])["slots"]}
            for mapping in options["material_mappings"]:
                slot = slots.get(mapping["slotId"])
                if not slot or not slot["has_filament"]:
                    raise FlashForgeError("Una ranura seleccionada ya no está disponible con filamento.")
                if mapping["materialName"] != slot["material"] or mapping["slotMaterialColor"] != slot["color"]:
                    raise FlashForgeError("El material de una ranura cambió. Actualiza el estado y revisa el mapeo.")
        if cancel.is_set():
            raise FlashForgeError("Subida cancelada antes de enviar el archivo.")
        self._connect()
        mappings = (MaterialMapping * len(options["material_mappings"]))( *[
            MaterialMapping(m["toolId"], m["slotId"], m["materialName"].encode(),
                            m["toolMaterialColor"].encode(), m["slotMaterialColor"].encode())
            for m in options["material_mappings"]])

        last_progress = [-1.0]

        def progress(now, total, _):
            fraction = now / total if total > 0 else 0
            if fraction - last_progress[0] >= .025 or now == total:
                try:
                    emit({"event": "print_progress", "phase": "uploading", "now": now, "total": total})
                except Exception:
                    return 1
                last_progress[0] = fraction
            return int(cancel.is_set())

        callback = ProgressCallback(progress)
        upload = SendGcode(job["path"].encode(), job["thumbnail"].encode(), job["name"].encode(),
                           1, int(options["bed_leveling"]), 0, 0, int(options["timelapse"]),
                           int(options["use_material_station"]), len(mappings), mappings, callback, None)
        cloud, results, count = c.POINTER(CloudGcode)(), c.POINTER(AddJobResult)(), I()
        created = False
        creation_uncertain = False
        try:
            emit({"event": "print_progress", "phase": "uploading", "now": 0, "total": job["size"]})
            code = self.lib.fnet_wanDevSendGcodeClound(self.session.client_id, self.session.access,
                                                    c.byref(upload), c.byref(cloud), 15000)
            if code == 1 or cancel.is_set():
                raise FlashForgeError("Subida cancelada. No se solicitó iniciar el trabajo.")
            self.session._check(code)
            if not cloud:
                raise FlashForgeError("El SDK no devolvió los datos de la subida.")
            emit({"event": "print_progress", "phase": "creating"})
            ids = (S * 1)(job["device_id"].encode())
            serials = (S * 1)(device["serialNumber"].encode())
            data = CloudJob()
            data.devIds, data.devCnt = ids, 1
            data.gcodeName = job["name"].encode()
            data.gcodeType = Path(job["path"]).suffix[1:].encode()
            data.gcodeMd5, data.gcodeSize = md5(job["path"]), job["size"]
            data.thumbName, data.thumbType = (Path(job["name"]).stem + ".png").encode(), b"png"
            data.thumbMd5, data.thumbSize = md5(job["thumbnail"]), Path(job["thumbnail"]).stat().st_size
            for key, _ in CloudGcode._fields_:
                setattr(data, key, getattr(cloud.contents, key))
            data.unionId, data.plateNo, data.printNow = job["union_id"].encode(), options["plate"] - 1, 1
            data.levelingBeforePrint, data.timeLapseVideo = upload.levelingBeforePrint, upload.timeLapseVideo
            data.useMatlStation, data.gcodeToolCnt, data.materialMappings = upload.useMatlStation, len(mappings), mappings
            creation_uncertain = True
            self.session._check(self.lib.fnet_wanDevAddCloundJob(
                self.session.client_id, self.session.access, c.byref(data), c.byref(results), c.byref(count), 15000))
            if count.value != 1 or not results or results[0].devId != ids[0]:
                raise FlashForgeError("FlashForge devolvió un resultado de creación de trabajo no válido.")
            error = results[0].error
            if error:
                creation_uncertain = False
                labels = {1: "La impresora está ocupada.", 2: "La impresora ya no está disponible en la cuenta.",
                          3: "El servidor FlashForge no pudo crear el trabajo."}
                raise FlashForgeError(labels.get(error, "FlashForge rechazó el trabajo."))
            if not results[0].jobId:
                raise FlashForgeError("FlashForge no devolvió un identificador de trabajo.")
            created = True
            creation_uncertain = False
            jobs = (S * 1)(results[0].jobId)
            data.devSerialNumbers, data.jobIds = serials, jobs
            topics = (S * 1)(device["devTopic"].encode())
            pointers = (P * 1)(c.cast(c.pointer(data), P))
            write = WriteMulti(3, pointers, topics, 1, 1, 1)  # FNET_CONN_WRITE_START_CLOUND_JOB
            sent = c.POINTER(WriteResult)()
            try:
                emit({"event": "print_progress", "phase": "dispatching"})
                self.session._check(self.lib.fnet_connectionSendMulti(self.connection, c.byref(write), c.byref(sent)))
                if not sent or sent.contents.failedCnt != 0:
                    raise FlashForgeError("No se confirmó la entrega de la orden MQTT.")
            finally:
                if sent:
                    self.lib.fnet_freeWriteMultiResult(sent)
            return {"dispatched": True, "device_id": job["device_id"], "job_id": jobs[0].decode(errors="replace"),
                    "name": job["name"], "printer_started_verified": False}
        except FlashForgeError as error:
            if created:
                raise FlashForgeError("El trabajo se creó, pero no se confirmó la orden de inicio. "
                                      "Comprueba la impresora antes de volver a enviar.") from None
            if creation_uncertain:
                raise FlashForgeError("No se pudo confirmar si FlashForge creó el trabajo. "
                                      "Comprueba la impresora antes de volver a enviar.") from None
            raise error
        finally:
            if results:
                self.lib.fnet_freeAddCloudJobResults(results, count.value)
            if cloud:
                self.lib.fnet_freeCloundGcodeData(cloud)

    def close(self):
        if self.connection:
            self.lib.fnet_connectionStop(self.connection)
            self.lib.fnet_freeConnection(self.connection)
            self.connection = P()
        self.connected.clear()
        self._callbacks = None
