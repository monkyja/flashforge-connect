"""Native Orca printer agent. The host routes jobs through the account backend.

The host represents third-party agents as local devices. Its print_host stores
the account's device ID, not a network endpoint. No Bambu command is forwarded.
"""

import json
import threading
import weakref

import orca

from . import __version__
from .materials import ams_payload, color_rgb, slot_table
from .model import FlashForgeError

AGENT_ID = "org.orca-flashforge.cloud"
UNSUPPORTED = -7010
INVALID = -19
CANCELLED = -18
_page = None


def attach_page(page):
    global _page
    _page = weakref.ref(page)


class FlashForgePrinterAgent(orca.printer_agent.PrinterAgentBase):
    def __init__(self):
        super().__init__()
        self._callbacks = {}
        self._selected = ""
        self._slots = {}
        self._stop = threading.Event()
        self._thread = None

    def get_name(self):
        return "FlashForge PrinterAgent"

    def get_agent_info(self):
        return orca.printer_agent.AgentInfo(AGENT_ID, "FlashForge · cuenta y LFS", __version__,
                                            "Envío por cuenta FlashForge y lectura del LFS")

    def _source(self, *, require_account=True):
        page = _page() if _page else None
        if page is None or not page._active or (require_account and not page._account):
            raise FlashForgeError("Conecta la cuenta en la página FlashForge y mantenla activada.")
        if page._settings()["mode"] != "cloud":
            raise FlashForgeError("Este agente utiliza la cuenta FlashForge.")
        return page

    def _target(self, page):
        config = page._settings()
        return config["profile_bindings"].get(page._agent_profile.get("name", ""), config["device_id"])

    def get_user_selected_machine(self):
        try:
            return self._target(self._source(require_account=False))
        except FlashForgeError:
            return ""

    def set_user_selected_machine(self, dev_id):
        try:
            page = self._source(require_account=False)
            if not dev_id or dev_id != self._target(page):
                return INVALID
            if page._account and not any(d["devId"] == dev_id for d in page._devices):
                return INVALID
            self._selected = dev_id
            return 0
        except FlashForgeError:
            return INVALID

    def _emit(self, key, *args):
        callback = self._callbacks.get(key)
        if callback and not self._stop.is_set():
            callback(*args)

    def _publish(self, dev_id, status, *, sync=False, model_id=""):
        slots = slot_table(status)
        previous = self._slots.get(dev_id)
        if previous is not None and [s["id"] for s in previous] != [s["id"] for s in slots] and not sync:
            raise FlashForgeError("Cambió la lista de ranuras LFS. Vuelve a sincronizar los filamentos.")
        # Orca's native dialog requires a storage flag even for third-party
        # account agents. This denotes the adapter's cloud upload path, not a
        # claim about a physical SD card or its free space; upload failures are
        # still reported by the actual cloud transfer.
        payload = {"command": "push_status", "msg": 0, "ams": ams_payload(slots), "sdcard": True}
        if model_id:
            payload["printer_type"] = model_id
        # Only known values are translated. Unknown states remain visibly unknown.
        states = {"ready": "IDLE", "idle": "IDLE", "printing": "RUNNING", "paused": "PAUSE",
                  "completed": "FINISH", "error": "FAILED"}
        payload["gcode_state"] = states.get(status["status"].lower(), "UNKNOWN")
        for source, target in (("progress", "mc_percent"), ("layer", "layer_num"), ("layers", "total_layer_num")):
            if status.get(source) is not None:
                payload[target] = status[source]
        payload["subtask_name"] = status.get("file", "")
        message = {"print": payload}
        if status.get("firmware"):
            message["info"] = {"command": "get_version", "module": [
                {"name": "ota", "sw_ver": status["firmware"]}]}
        # Polling must not silently replace the mapping the user synchronized.
        # A changed spool requires an explicit refresh before sending a job.
        if previous is None or sync:
            self._slots[dev_id] = slots
        self._emit("local_message", dev_id, json.dumps(message))

    def _refresh(self, dev_id, stop=None, *, sync=False):
        page = self._source()
        if not page._busy.acquire(blocking=False):
            return False
        try:
            if dev_id != self._target(page):
                raise FlashForgeError("El destino no coincide con el perfil asociado.")
            status = page._cloud(page._settings()).request("status", device_id=dev_id)
            if stop is not None and stop.is_set():
                return False
            device = next((item for item in page._devices if item.get("devId") == dev_id), {})
            # Flashforge's account model and OrcaSlicer's vendor model_id use
            # different names. Only publish this verified mapping when both the
            # account device and the active printer preset identify Creator 5 Pro.
            model_id = ("Flashforge-Creator-5-Pro"
                        if device.get("model") == "Creator 5 Pro"
                        and page._agent_profile.get("model") == "Flashforge Creator 5 Pro" else "")
            self._publish(dev_id, status, sync=sync, model_id=model_id)
            return True
        finally:
            page._busy.release()

    def connect_printer(self, params):
        if self.set_user_selected_machine(params.dev_id) != 0:
            return INVALID
        self.disconnect_printer()
        self._stop = threading.Event()
        stop = self._stop
        dev_id = params.dev_id

        def poll():
            connected = False
            while not stop.is_set():
                try:
                    page = self._source(require_account=False)
                    if not page._account:
                        if connected:
                            self._slots.pop(dev_id, None)
                            self._emit("local_connect", 2, dev_id, "La cuenta FlashForge está desconectada.")
                            connected = False
                        if stop.wait(1):
                            return
                        continue
                    if self._refresh(dev_id, stop) and not stop.is_set() and not connected:
                        self._emit("local_connect", 0, dev_id, "0")
                        self._emit("printer_connected", dev_id)
                        connected = True
                except FlashForgeError:
                    if stop.is_set():
                        return
                    self._slots.pop(dev_id, None)
                    self._emit("local_connect", 2 if connected else 1, dev_id,
                               "Consulta fallida. Revisa la cuenta en FlashForge.")
                    return
                if stop.wait(5):
                    return
        self._thread = threading.Thread(target=poll, daemon=True, name="FlashForge-native-status")
        self._thread.start()
        return 0

    def disconnect_printer(self):
        self._stop.set()
        self._slots.clear()
        # Do not block the host's UI waiting for a native SDK request.
        return 0

    def on_unload(self):
        self.disconnect_printer()
        self._callbacks.clear()

    def get_filament_sync_mode(self):
        return orca.printer_agent.FilamentSyncMode.Pull

    def fetch_filament_info(self, dev_id, sync_mode=None):
        try:
            return self._refresh(dev_id, sync=True)
        except FlashForgeError:
            self._slots.pop(dev_id, None)
            return False

    def _options(self, params, job, current):
        options = {"plate": job["plate"], "bed_leveling": bool(params.task_bed_leveling),
                   "timelapse": bool(params.task_record_timelapse),
                   "use_material_station": bool(params.task_use_ams), "material_mappings": []}
        if not options["use_material_station"]:
            return options
        previous = self._slots.get(params.dev_id)
        if previous is None or previous != current:
            raise FlashForgeError("El LFS cambió o falta su lectura. Sincroniza los filamentos y revisa las ranuras.")
        try:
            mapping = json.loads(params.ams_mapping)
            info = json.loads(params.ams_mapping_info) if params.ams_mapping_info else []
            if not isinstance(mapping, list) or not isinstance(info, list):
                raise ValueError()
            for tool in job["tools"]:
                index = mapping[tool]
                if type(index) is not int or not 0 <= index < len(previous):
                    raise ValueError()
                slot = previous[index]
                if not slot["has_filament"]:
                    raise ValueError()
                source = info[tool] if tool < len(info) else {}
                if source.get("filamentType") and source["filamentType"].strip().casefold() != slot["material"].strip().casefold():
                    raise FlashForgeError("El material del laminado no coincide con la ranura LFS seleccionada.")
                options["material_mappings"].append({"toolId": tool, "slotId": slot["id"],
                    "materialName": slot["material"], "toolMaterialColor": color_rgb(source.get("sourceColor", "")),
                    "slotMaterialColor": slot["color"]})
        except (ValueError, TypeError, IndexError, KeyError, AttributeError):
            raise FlashForgeError("Asigna una ranura LFS válida a cada filamento en el diálogo de impresión.") from None
        if not job["tools"]:
            raise FlashForgeError("No hay herramientas T identificadas para enviar un mapeo LFS.")
        return options

    def start_print(self, params, update_fn, cancel_fn, wait_fn):
        page = None
        acquired = False
        watcher = None
        finished, uploading, cancelled = threading.Event(), threading.Event(), threading.Event()
        phase = "preparing"
        staged = committed = False
        try:
            page = self._source()
            if params.dev_id != self._target(page):
                raise FlashForgeError("El destino no coincide con la impresora asociada al perfil.")
            acquired = page._busy.acquire(blocking=False)
            if not acquired:
                raise FlashForgeError("Hay otra operación FlashForge activa. Espera a que termine.")
            if cancel_fn and cancel_fn():
                return CANCELLED
            if any(getattr(params, key, False) for key in ("task_flow_cali", "task_vibration_cali", "task_layer_inspect")):
                raise FlashForgeError("Desactiva las calibraciones no admitidas por el adaptador FlashForge.")
            bridge = page._cloud(page._settings())
            generation = page._generation
            page._prepared_id = None
            job = bridge.request("prepare_print", path=params.filename, device_id=params.dev_id,
                                 profile=page._agent_profile.get("name", ""), plate=params.plate_index)
            staged = True
            status = bridge.request("status", device_id=params.dev_id)
            options = self._options(params, job, slot_table(status))
            if generation != page._generation or params.dev_id != self._target(page):
                raise FlashForgeError("El proyecto o destino cambió durante la preparación.")
            if cancel_fn and cancel_fn():
                return CANCELLED

            def watch_cancel():
                try:
                    while not finished.wait(.1):
                        if cancel_fn and cancel_fn() and uploading.is_set():
                            cancelled.set()
                            bridge.cancel_upload()
                            return
                except Exception:
                    # A failed cancellation request is never a confirmed cancellation.
                    return

            def progress(value):
                nonlocal phase
                phase = value["phase"]
                if phase == "uploading":
                    uploading.set()
                    percent = int(100 * value.get("now", 0) / max(1, value.get("total", 1)))
                    if update_fn:
                        update_fn(1, min(100, percent), "Subiendo por cuenta FlashForge")
                else:
                    uploading.clear()
                    if update_fn:
                        update_fn(3, 0, "Creando trabajo" if phase == "creating" else "Entregando orden MQTT")

            watcher = threading.Thread(target=watch_cancel, daemon=True, name="FlashForge-native-cancel")
            watcher.start()
            committed = True
            result = bridge.request("commit_print", job_id=job["id"], device_id=params.dev_id,
                                    confirmed=True, options=options, wait_timeout=600, on_progress=progress)
            if not result.get("dispatched"):
                raise FlashForgeError("No se confirmó la entrega. Comprueba la impresora antes de repetir.")
            if update_fn:
                update_fn(6, 0, "Orden enviada. Recepción por la impresora e inicio sin confirmar.")
            page._post({"command": "print_dispatched", "result": result})
            return 0
        except Exception as error:
            code = CANCELLED if cancelled.is_set() and str(error).startswith("Subida cancelada") else INVALID
            message = str(error) if isinstance(error, FlashForgeError) else "No se pudo completar el envío FlashForge."
            if phase in ("creating", "dispatching"):
                message += " Comprueba la impresora antes de volver a enviar."
            if update_fn:
                update_fn(7, code, message)
            return code
        finally:
            finished.set()
            if watcher:
                watcher.join(timeout=1)
            if acquired:
                if staged and not committed:
                    try:
                        bridge.request("discard_print")
                    except FlashForgeError:
                        pass
                page._busy.release()

    def start_local_print(self, params, update_fn, cancel_fn):
        # Orca's local-agent entry point; the transport remains the account SDK.
        return self.start_print(params, update_fn, cancel_fn, None)

    def start_local_print_with_record(self, params, update_fn, cancel_fn, wait_fn):
        return self.start_print(params, update_fn, cancel_fn, wait_fn)

    def send_message(self, dev_id, json_str, qos, flag):
        # The host requests these read operations when selecting a device. Never
        # pass arbitrary host commands (Bambu dialect) into the FlashForge SDK.
        try:
            message = json.loads(json_str)
            read = (message.get("info", {}).get("command") == "get_version" or
                    message.get("pushing", {}).get("command") == "pushall" or
                    message.get("system", {}).get("command") == "get_access_code")
            if read and dev_id == self._selected:
                return 0 if self._thread and self._thread.is_alive() else INVALID
        except (ValueError, TypeError, AttributeError):
            pass
        return UNSUPPORTED

    send_message_to_printer = send_message

    def start_discovery(self, start, sending):
        return False  # Account destination is configured in the preset, not discovered by SSDP.

    def bind_detect(self, dev_ip, sec_link, detect):
        return UNSUPPORTED

    def bind(self, *args):
        return UNSUPPORTED

    def unbind(self, dev_id):
        return UNSUPPORTED

    def request_bind_ticket(self):
        return UNSUPPORTED, ""

    def start_send_gcode_to_sdcard(self, params, update_fn, cancel_fn, wait_fn):
        # PrintJob uses this entry point as a connection probe before local-agent
        # printing. A real SendJob must never be mistaken for that probe.
        if (getattr(params, "project_name", "") != "verify_job"
                or update_fn is not None or cancel_fn is not None or wait_fn is not None):
            return UNSUPPORTED
        page = None
        acquired = False
        try:
            page = self._source()
            if not params.dev_id or params.dev_id != self._target(page):
                return INVALID
            if not any(device.get("devId") == params.dev_id for device in page._devices):
                return INVALID
            acquired = page._busy.acquire(blocking=False)
            if not acquired:
                return INVALID
            page._cloud(page._settings()).request("status", device_id=params.dev_id)
            return 0
        except (FlashForgeError, AttributeError):
            return INVALID
        finally:
            if acquired:
                page._busy.release()

    def start_sdcard_print(self, *args):
        return UNSUPPORTED

    def set_server_callback(self, fn):
        self._callbacks["server"] = fn
        return 0

    def set_on_ssdp_msg_fn(self, fn):
        self._callbacks["ssdp"] = fn
        return 0

    def set_on_printer_connected_fn(self, fn):
        self._callbacks["printer_connected"] = fn
        return 0

    def set_on_subscribe_failure_fn(self, fn):
        self._callbacks["subscribe_failure"] = fn
        return 0

    def set_on_message_fn(self, fn):
        self._callbacks["message"] = fn
        return 0

    def set_on_user_message_fn(self, fn):
        self._callbacks["user_message"] = fn
        return 0

    def set_on_local_connect_fn(self, fn):
        self._callbacks["local_connect"] = fn
        return 0

    def set_on_local_message_fn(self, fn):
        self._callbacks["local_message"] = fn
        return 0

    def set_queue_on_main_fn(self, fn):
        # Host message callbacks marshal onto the UI thread themselves.
        self._callbacks["queue"] = fn
        return 0
