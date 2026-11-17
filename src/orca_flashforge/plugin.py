"""OrcaSlicer 2.5 Pages capability. Network/native work never blocks the UI thread."""

import json
import threading
import base64
import csv
import os
import tempfile
import zipfile
from importlib.resources import files
from pathlib import Path

import orca

from .bridge import SdkBridge, worker_command
from .lan import LanClient
from .model import FlashForgeError
from . import __version__
from .materials import slot_table, suggest_mappings
from .jobs import inspect_gcode, filament_metadata, MAX_FILE_BYTES, MAX_THUMB_BYTES, PNG


class PreviewTemporary:
    """Remove only our known files; avoid fd-based scandir audit prompts."""
    def __init__(self, storage):
        self.name = tempfile.mkdtemp(prefix="preview-", dir=storage)

    def cleanup(self):
        folder = Path(self.name)
        for name in ("project.3mf", "project.3mf.tmp"):
            (folder / name).unlink(missing_ok=True)
        folder.rmdir()

DEFAULTS = {"mode": "cloud", "cloud_backend": "experimental", "sdk_library": "", "sdk_settings": "", "python": "/usr/bin/python3",
            "device_id": "", "host": "", "serial": "", "access_code": "", "profile_bindings": {},
            "remember_account": True, "account_name": ""}


class FlashForgePage(orca.pages.PagesPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self._active = True
        self._busy = threading.Lock()
        self._bridge = None
        self._account = None
        self._devices = []
        self._sliced_plates = {}
        self._plate_details = {}
        self._plate_tokens = {}
        self._plate_inspection_lock = threading.Lock()
        self._selected_plate = None
        self._slicing = False
        self._slice_id = ""
        self._slice_candidate = ""
        self._slice_selection_changed = False
        self._generation = 0
        self._prepared_id = None
        self._prepared_profile = None
        self._prepared_generation = None
        self._prepared_ready = False
        self._prepared_flow_calibration = False
        self._upload_phase = ""
        self._restoring = False

    def get_name(self):
        return "FlashForge"

    def get_ui(self):
        resources = files("orca_flashforge").joinpath("ui")
        icon = "data:image/svg+xml;base64," + base64.b64encode(resources.joinpath("flashforge.svg").read_bytes()).decode()
        return (resources.joinpath("device.html").read_text(encoding="utf-8")
                .replace("__FLASHFORGE_ICON__", icon).replace("__PLUGIN_VERSION__", __version__))

    def get_icon(self):
        return str(files("orca_flashforge").joinpath("ui/flashforge.svg"))

    def get_default_config(self):
        return {**DEFAULTS, "profile_bindings": {}}

    def _settings(self):
        stored = json.loads(self.get_config())
        if not isinstance(stored, dict):
            stored = {}
        config = {key: stored.get(key, value) for key, value in DEFAULTS.items()}
        config["mode"] = "cloud"
        config["cloud_backend"] = "experimental"
        config["sdk_library"] = ""
        config["sdk_settings"] = ""
        pairs = config["profile_bindings"]
        config["profile_bindings"] = {k: v for k, v in pairs.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(pairs, dict) else {}
        config["remember_account"] = config["remember_account"] is True
        return config

    @staticmethod
    def _profile_context():
        # Called only from host UI thread callbacks, never from the SDK worker.
        try:
            preset = orca.host.preset_bundle().current_printer_preset()
            return {"name": preset.name, "model": preset.config_value("printer_model") or "",
                    "nozzles": preset.config_value("nozzle_diameter") or "",
                    "extruder_map": preset.config_value("physical_extruder_map") or "",
                    "printer_agent": preset.config_value("printer_agent") or "",
                    "print_host": preset.config_value("print_host") or "",
                    "plate": orca.host.model().current_plate_index() + 1}
        except Exception:
            return {"name": "", "model": "", "plate": 1}

    @staticmethod
    def _session_filaments():
        # Snapshot the active slicing configuration on the host UI thread.
        try:
            bundle = orca.host.preset_bundle()
            result = {}
            for key in ("filament_type", "filament_colour", "filament_vendor", "filament_settings_id"):
                value = bundle.full_config_value(key) or ""
                result[key] = next(csv.reader([value], delimiter=";", skipinitialspace=True))
            return result
        except Exception:
            return {}

    def on_load(self):
        self._active = True
        config = self._settings()
        stored = json.loads(self.get_config())
        if not isinstance(stored, dict):
            stored = {}
        if (stored.get("cloud_backend") != "experimental" or stored.get("mode", "cloud") != "cloud"
                or stored.get("sdk_library") or stored.get("sdk_settings")):
            if not self.save_config(json.dumps(config)):
                self._post({"command": "error", "error": "No se pudo seleccionar la biblioteca experimental."})
                return
        if config["mode"] == "cloud" and config["remember_account"] and (config["cloud_backend"] == "experimental" or (config["sdk_library"] and config["sdk_settings"])):
            self.on_message({"command": "restore_login"})

    def on_unload(self):
        self._active = False
        if self._bridge:
            self._bridge.shutdown()
            self._bridge = None
        self._account, self._devices, self._prepared_id = None, [], None

    def on_lifecycle_event(self, event, context):
        name = getattr(event, "name", str(event).rsplit(".", 1)[-1])
        code = getattr(context, "code", None)
        ok = getattr(code, "name", str(code).rsplit(".", 1)[-1]) == "Ok"
        if name == "SliceStarted":
            self._generation += 1
            self._prepared_id = None
            if self._selected_plate is None:
                self._sliced_plates.clear()
                self._plate_details.clear()
                self._plate_tokens.clear()
            else:
                self._sliced_plates.pop(self._selected_plate, None)
                self._plate_details.pop(self._selected_plate, None)
                self._plate_tokens.pop(self._selected_plate, None)
            self._slicing = True
            self._slice_id = str(getattr(context, "id", "") or "")
            self._slice_candidate = ""
            self._slice_selection_changed = False
            self._post({"command": "job_invalidated"})
        elif name == "GCodeExportFinished" and self._slicing:
            if ok and self._slice_id and str(getattr(context, "id", "") or "") == self._slice_id:
                self._slice_candidate = getattr(context, "msg", "") or ""
            else:
                self._slice_candidate = ""
        elif name == "SlicingJobComplete" and self._slicing:
            profile = self._profile_context()
            plate = profile.get("plate")
            captured = ""
            if (ok and self._slice_id and str(getattr(context, "id", "") or "") == self._slice_id
                    and self._slice_candidate and not self._slice_selection_changed
                    and profile.get("model") == "Flashforge Creator 5 Pro"
                    and isinstance(plate, int) and plate > 0):
                self._sliced_plates[plate] = self._slice_candidate
                captured = self._slice_candidate
            self._selected_plate = plate
            self._slicing = False
            self._slice_candidate = ""
            self._post({"command": "sliced_plates", "plates": sorted(self._sliced_plates), "slicing": False, "selected": plate})
            if captured:
                token = object()
                self._plate_tokens[plate] = token
                filaments = self._session_filaments()
                threading.Thread(target=self._read_plate_details, args=(plate, captured, token, filaments),
                                 daemon=True, name="FlashForge-plate-details").start()
        elif name == "PlateSelected":
            self._generation += 1
            self._prepared_id = None
            index = getattr(context, "index", -1)
            self._selected_plate = index + 1 if isinstance(index, int) and index >= 0 else None
            if self._slicing:
                self._slice_selection_changed = True
                self._sliced_plates.clear()
                self._plate_details.clear()
                self._plate_tokens.clear()
            self._post({"command": "job_invalidated"})
        elif name in ("NewProject", "ProjectOpened", "ProjectClosed", "ObjectAdded", "ObjectDeleted",
                      "ObjectTransformed", "ObjectChanged", "PresetSelected", "PresetSaved", "PlateCreated", "PlateDeleted"):
            self._generation += 1
            self._prepared_id = None
            self._sliced_plates.clear()
            self._plate_details.clear()
            self._plate_tokens.clear()
            if self._slicing:
                self._slice_selection_changed = True
            self._post({"command": "job_invalidated"})

    def _read_plate_details(self, plate, path, token, session_filaments):
        # File inspection is deliberately off the GUI and slicing threads.
        try:
            if Path(path).stat().st_size > MAX_FILE_BYTES:
                return
            metadata = {}
            with self._plate_inspection_lock:
                if self._plate_tokens.get(plate) is not token:
                    return
                with open(path, "rb") as stream:
                    tools, thumbnail = inspect_gcode(stream, metadata)
            for key in ("filament_type", "filament_colour", "filament_vendor", "filament_settings_id"):
                if not metadata.get(key):
                    metadata[key] = session_filaments.get(key, [])
            details = {"plate": plate, "time": metadata.get("estimated printing time (normal mode)", ""),
                       "weight": metadata.get("total filament used [g]", ""),
                       "filaments": [{"tool": tool, **filament_metadata(metadata, tool)} for tool in tools],
                       "thumbnail": "data:image/png;base64," + base64.b64encode(thumbnail).decode() if thumbnail else ""}
            if self._sliced_plates.get(plate) == path and self._plate_tokens.get(plate) is token:
                if not details["thumbnail"]:
                    details["thumbnail"] = self._plate_details.get(plate, {}).get("thumbnail", "")
                self._plate_details[plate] = details
                self._post({"command": "plate_details", "details": details})
        except (OSError, ValueError, FlashForgeError):
            return

    def _read_project_previews(self, temporary, path, generation, plates):
        try:
            with zipfile.ZipFile(path) as archive:
                for plate in plates:
                    name = f"Metadata/plate_{plate}.png"
                    try:
                        info = archive.getinfo(name)
                        if info.file_size > MAX_THUMB_BYTES:
                            continue
                        data = archive.read(info)
                    except KeyError:
                        continue
                    if not data.startswith(PNG) or generation != self._generation or plate not in self._sliced_plates:
                        continue
                    details = dict(self._plate_details.get(plate, {"plate": plate}))
                    details["thumbnail"] = "data:image/png;base64," + base64.b64encode(data).decode()
                    self._plate_details[plate] = details
                    self._post({"command": "plate_details", "details": details})
            self._post({"command": "previews_done"})
        except (OSError, zipfile.BadZipFile):
            self._post({"command": "previews_error", "error": "No se pudieron leer las vistas del proyecto."})
        finally:
            temporary.cleanup()

    def _post(self, message):
        if self._active:
            self.post_message(message)

    def on_message(self, message):
        if not isinstance(message, dict) or not self._active:
            return
        command = message.get("command")
        if command == "camera_stop":
            bridge = self._bridge
            if bridge:
                def stop_camera():
                    try:
                        bridge.request("camera_stop", wait_timeout=10)
                    except Exception:
                        pass
                threading.Thread(target=stop_camera, daemon=True, name="FlashForge-stop-camera").start()
            return
        if command == "generate_previews":
            if self._slicing or not self._sliced_plates:
                self._post({"command": "previews_error", "error": "Lamina alguna placa antes de generar las vistas."})
                return
            temporary = None
            try:
                # OrcaSlicer permits the plugin's own storage; /tmp deletion from
                # a background thread is rejected by its filesystem audit hook.
                temporary = PreviewTemporary(orca.host.plugin.storage())
                path = str(Path(temporary.name) / "project.3mf")
                generation, plates = self._generation, list(self._sliced_plates)
                orca.host.plater().export_3mf_copy(path)
                threading.Thread(target=self._read_project_previews, args=(temporary, path, generation, plates),
                                 daemon=True, name="FlashForge-plate-previews").start()
            except Exception:
                if temporary:
                    temporary.cleanup()
                self._post({"command": "previews_error", "error": "OrcaSlicer no pudo generar las vistas del proyecto."})
            return
        if command == "refresh_status" or (command == "status" and message.get("background") is True):
            self._start_status_refresh(message)
            return
        if command == "cancel_upload":
            try:
                if self._bridge is None or self._upload_phase != "uploading":
                    raise FlashForgeError("La subida ya terminó o no está activa. Comprueba el estado del trabajo.")
                self._bridge.cancel_upload()
                self._post({"command": "cancel_requested"})
            except FlashForgeError as error:
                self._post({"command": "cancel_error", "error": str(error)})
            return
        message["_profile"] = (self._profile_context() if command != "restore_login"
                               else {"name": "", "model": "", "plate": 1})
        if message.get("command") == "config":
            if not self._slicing:
                self._selected_plate = message["_profile"].get("plate")
            config = self._settings()
            config["has_access_code"] = bool(config.pop("access_code", ""))
            self._post({"command": "config", "config": config, "account": self._account,
                        "devices": self._devices, "profile": message["_profile"],
                        "status_protocol": 1,
                        "restoring": self._restoring,
                        "sliced_plates": sorted(self._sliced_plates), "plate_details": list(self._plate_details.values()), "slicing": self._slicing})
            return
        if not self._busy.acquire(blocking=False):
            self._post({"command": "busy"})
            return
        try:
            self._restoring = command == "restore_login"
            threading.Thread(target=self._run, args=(message,), daemon=True,
                             name="FlashForge-operation").start()
        except Exception:
            self._restoring = False
            self._busy.release()
            self._post({"command": "error", "error": "OrcaSlicer no permitió iniciar el hilo del plugin."})

    def _read_status(self, config, device_id):
        if config["mode"] == "lan":
            return LanClient(config["host"], config["serial"], config["access_code"]).status()
        return self._cloud(config).request("status", device_id=device_id or config["device_id"])

    def _start_status_refresh(self, message):
        # A refresh has its own completion event: it must never release a
        # foreground operation's UI state or rebuild the device selector.
        result = {"command": "status_refresh", "request_id": message.get("request_id"),
                  "device_id": message.get("device_id", "")}
        if not self._busy.acquire(blocking=False):
            self._post({**result, "skipped": True})
            return
        try:
            config = self._settings()
            result["mode"] = config["mode"]
            threading.Thread(target=self._run_status_refresh, args=(config, result), daemon=True,
                             name="FlashForge-status").start()
        except Exception:
            self._busy.release()
            self._post({**result, "error": "No se pudo iniciar la consulta de estado en segundo plano."})

    def _run_status_refresh(self, config, result):
        try:
            if self._active:
                result["status"] = self._read_status(config, result["device_id"])
        except FlashForgeError as error:
            result["error"] = str(error)
        except Exception:
            result["error"] = "No se pudo actualizar el estado de la impresora."
        finally:
            if self._bridge and isinstance(self._bridge.account_state, dict):
                result["account_state"] = self._bridge.account_state
            # Release the SDK operation before notifying the page. A user action
            # deferred during this refresh can then start without being rejected.
            self._busy.release()
            self._post(result)

    def _cloud(self, config):
        if not self._active:
            raise FlashForgeError("El plugin se ha descargado.")
        if self._bridge is None:
            self._bridge = SdkBridge(config["python"], timeout=60)
        return self._bridge

    def _restore_account(self, config):
        restored = self._cloud(config).request("restore_login", library=config["sdk_library"],
                                               settings=config["sdk_settings"], backend=config["cloud_backend"],
                                               expected_username=config["account_name"], wait_timeout=90)
        if not restored.get("connected"):
            self._post({"command": "restore_empty"})
            return
        self._account = restored["profile"]
        config["account_name"] = restored.get("account_name", config["account_name"])
        self.save_config(json.dumps(config))
        self._post({"command": "account", "account": self._account, "restored": True})
        self._devices = self._bridge.request("devices")
        self._post({"command": "devices", "devices": self._devices})

    def _run(self, message):
        command = message.get("command")
        try:
            if not self._active:
                return
            command = message.get("command")
            config = self._settings()
            profile = message.get("_profile", {"name": "", "model": "", "plate": 1})
            if command == "save":
                values = message.get("config", {})
                if not isinstance(values, dict):
                    raise FlashForgeError("Configuración no válida.")
                for key in DEFAULTS:
                    if isinstance(DEFAULTS[key], str) and key in values and isinstance(values[key], str):
                        if key != "access_code" or values[key]:
                            config[key] = values[key].strip()
                config["mode"] = "cloud"
                config["cloud_backend"] = "experimental"
                config["sdk_library"] = ""
                config["sdk_settings"] = ""
                if config["cloud_backend"] not in ("official", "experimental"):
                    raise FlashForgeError("Biblioteca cloud no válida.")
                if config["mode"] not in ("cloud", "lan"):
                    raise FlashForgeError("Modo de conexión no válido.")
                if not self.save_config(json.dumps(config)):
                    raise FlashForgeError("OrcaSlicer no pudo guardar la configuración.")
                if self._bridge:
                    self._bridge.close()
                    self._bridge = None
                self._account, self._devices, self._prepared_id = None, [], None
                restoring = bool(config["mode"] == "cloud" and config["remember_account"] and config["account_name"]
                                 and (config["cloud_backend"] == "experimental" or (config["sdk_library"] and config["sdk_settings"])))
                self._post({"command": "saved", "restoring": restoring})
                if restoring:
                    self._restoring = True
                    self._restore_account(config)
            elif command == "login":
                self._account, self._devices, self._prepared_id = None, [], None
                self._post({"command": "account", "account": None})
                account = self._cloud(config).request("login", library=config["sdk_library"], settings=config["sdk_settings"],
                                                     backend=config["cloud_backend"],
                                                     username=message.get("username", ""), password=message.get("password", ""),
                                                     remember=message.get("remember", config["remember_account"]) is True,
                                                     wait_timeout=90)
                config["remember_account"] = message.get("remember", config["remember_account"]) is True
                config["account_name"] = message.get("username", "") if config["remember_account"] else ""
                if not self.save_config(json.dumps(config)):
                    raise FlashForgeError("La conexión funciona, pero no se pudo guardar la preferencia de cuenta.")
                self._account = account["profile"]
                self._post({"command": "account", "account": self._account})
                self._devices = self._bridge.request("devices")
                self._post({"command": "devices", "devices": self._devices})
            elif command == "restore_login":
                if message.get("force") is True:
                    config["remember_account"] = True
                    if not self.save_config(json.dumps(config)):
                        raise FlashForgeError("No se pudo guardar la preferencia de reconexión.")
                if not config["remember_account"]:
                    self._post({"command": "restore_empty"})
                else:
                    self._restore_account(config)
            elif command == "devices":
                self._devices = self._cloud(config).request("devices")
                self._post({"command": "devices", "devices": self._devices})
            elif command == "bind_profile":
                device_id = message.get("device_id", "")
                if not profile["name"] or not any(d["devId"] == device_id for d in self._devices):
                    raise FlashForgeError("Selecciona un perfil de impresora y un dispositivo de la cuenta.")
                config["profile_bindings"][profile["name"]] = device_id
                if not self.save_config(json.dumps(config)):
                    raise FlashForgeError("No se pudo guardar la asociación con el perfil.")
                self._post({"command": "profile_bound", "profile": profile["name"], "device_id": device_id})
            elif command == "select":
                config["device_id"] = str(message.get("device_id", ""))
                if not self.save_config(json.dumps(config)):
                    raise FlashForgeError("No se pudo guardar la impresora seleccionada.")
                self._prepared_id = None
                self._post({"command": "status", "status": self._cloud(config).request("status", device_id=config["device_id"])})
            elif command == "recordings":
                device_id = message.get("device_id", "")
                result = self._cloud(config).request("recordings", device_id=device_id)
                self._post({"command": "recordings", "device_id": device_id, "recordings": result})
            elif command == "stored_files":
                device_id = message.get("device_id", "")
                result = self._cloud(config).request("stored_files", device_id=device_id)
                self._post({"command": "stored_files", "device_id": device_id, "files": result})
            elif command == "move":
                raise FlashForgeError("El movimiento todavía no está disponible con la biblioteca experimental.")
            elif command == "prepare_stored":
                if profile.get("model") != "Flashforge Creator 5 Pro":
                    raise FlashForgeError("Selecciona el perfil Creator 5 Pro en OrcaSlicer.")
                device = next((d for d in self._devices if d.get("devId") == message.get("device_id")), None)
                if not device or device.get("model", "").strip().casefold() != "creator 5 pro":
                    raise FlashForgeError("Selecciona una Creator 5 Pro de la cuenta.")
                generation = self._generation
                prepared = self._cloud(config).request("prepare_stored", name=message.get("name"),
                                                       device_id=message.get("device_id"), profile=profile["name"])
                status = self._cloud(config).request("status", device_id=message.get("device_id"))
                prepared["status"], prepared["error_code"] = status.get("status", ""), status.get("error_code", "")
                prepared["slots"] = slot_table(status)
                prepared["flow_calibration_available"] = status.get("flow_calibration_available") is True
                if generation != self._generation:
                    raise FlashForgeError("El proyecto cambió durante la preparación. Vuelve a revisar el archivo.")
                self._prepared_id, self._prepared_profile, self._prepared_generation = prepared["id"], profile, generation
                self._prepared_ready = bool(prepared.get("tools") and prepared["slots"])
                self._prepared_flow_calibration = prepared["flow_calibration_available"]
                self._post({"command": "print_prepared", "job": prepared})
            elif command == "prepare_print":
                if config["mode"] != "cloud" or config["cloud_backend"] != "experimental":
                    raise FlashForgeError("La impresión requiere la cuenta y la biblioteca experimental FlashForge.")
                if profile.get("model") != "Flashforge Creator 5 Pro":
                    raise FlashForgeError("Selecciona el perfil Creator 5 Pro en OrcaSlicer.")
                device = next((d for d in self._devices if d.get("devId") == message.get("device_id")), None)
                if not device or device.get("model", "").strip().casefold() != "creator 5 pro":
                    raise FlashForgeError("Selecciona una Creator 5 Pro de la cuenta.")
                if self._slicing:
                    raise FlashForgeError("Espera a que termine el laminado antes de preparar la impresión.")
                plate = message.get("plate", profile["plate"])
                path = message.get("path") or self._sliced_plates.get(plate, "")
                if not path:
                    raise FlashForgeError("Lamina la placa de nuevo para que el plugin pueda identificar su archivo.")
                generation = self._generation
                prepared = self._cloud(config).request("prepare_print", path=path,
                                                       device_id=message.get("device_id", ""), profile=profile["name"],
                                                       plate=plate)
                prepared["automatic_plate"] = not bool(message.get("path"))
                if prepared["automatic_plate"]:
                    prepared["preview"] = self._plate_details.get(plate, {}).get("thumbnail", "")
                if not message.get("path"):
                    for item in prepared.get("filaments", []):
                        detail = next((f for f in self._plate_details.get(plate, {}).get("filaments", [])
                                       if f["tool"] == item["toolId"]), {})
                        for key in ("brand", "preset", "weight", "length_mm"):
                            if not item.get(key) and detail.get(key):
                                item[key] = detail[key]
                    present = {filament["toolId"] for filament in prepared.get("filaments", [])}
                    for filament in self._plate_details.get(plate, {}).get("filaments", []):
                        if (filament["tool"] in prepared.get("tools", []) and filament["tool"] not in present
                                and filament["material"] and filament["color"]):
                            prepared.setdefault("filaments", []).append({"toolId": filament["tool"],
                                "material": filament["material"], "color": filament["color"]})
                status = self._cloud(config).request("status", device_id=prepared["device"]["devId"])
                prepared["status"] = status.get("status", "")
                prepared["error_code"] = status.get("error_code", "")
                prepared["slots"] = slot_table(status)
                prepared["flow_calibration_available"] = status.get("flow_calibration_available") is True
                prepared["suggested_mappings"] = suggest_mappings(prepared.get("filaments", []), prepared["slots"])
                if generation != self._generation:
                    raise FlashForgeError("El proyecto cambió durante la preparación. Vuelve a revisar el archivo.")
                self._prepared_id, self._prepared_profile, self._prepared_generation = prepared["id"], profile, generation
                self._prepared_ready = bool(prepared.get("tools") and prepared["slots"])
                self._prepared_flow_calibration = prepared["flow_calibration_available"]
                self._post({"command": "print_prepared", "job": prepared})
            elif command == "commit_print":
                if (not self._prepared_id or message.get("job_id") != self._prepared_id
                        or profile != self._prepared_profile or self._prepared_generation != self._generation):
                    raise FlashForgeError("El perfil o la preparación cambió. Vuelve a revisar el archivo.")
                if message.get("confirmed") is not True:
                    raise FlashForgeError("Revisa y confirma la impresión antes de continuar.")
                options = message.get("options")
                if not self._prepared_ready or not isinstance(options, dict) or options.get("use_material_station") is not True:
                    raise FlashForgeError("Revisa las herramientas y asigna las ranuras LFS antes de imprimir.")
                if options.get("flow_calibration") is True and not self._prepared_flow_calibration:
                    raise FlashForgeError("La impresora no ha confirmado soporte de calibración de flujo.")
                self._prepared_id = None
                self._upload_phase = "connecting"

                def progress(value):
                    self._upload_phase = value["phase"]
                    self._post({"command": "print_progress", **value})

                result = self._cloud(config).request("commit_print", job_id=message["job_id"],
                                                    device_id=message.get("device_id"), confirmed=True,
                                                    options=message.get("options"), wait_timeout=600, on_progress=progress)
                if not isinstance(result, dict) or result.get("dispatched") is not True:
                    raise FlashForgeError("No se confirmó el envío de la orden. Comprueba la impresora antes de repetir.")
                self._post({"command": "print_dispatched", "result": result})
            elif command == "camera_server":
                result = self._cloud(config).request("camera_server", cloud=config["mode"] == "cloud",
                                                     device_id=message.get("device_id", ""), wait_timeout=90)
                self._post({"command": "camera_server", "url": result["url"], "camera_url": result.get("camera_url", "")})
            elif command == "camera_fallback":
                result = self._cloud(config).request("camera_fallback", device_id=message.get("device_id", ""), wait_timeout=90)
                self._post({"command": "camera_fallback", "url": result["url"]})
            elif command == "light":
                self._cloud(config).request("light", device_id=message.get("device_id", ""), value=message.get("value"), wait_timeout=90)
                self._post({"command": "light_dispatched"})
            elif command == "cancel_print":
                if message.get("confirmed") is not True:
                    raise FlashForgeError("Confirma la cancelación de la impresión.")
                self._cloud(config).request("cancel_print", device_id=message.get("device_id", ""), job_id=message.get("job_id", ""), wait_timeout=90)
                self._post({"command": "cancel_print_dispatched"})
            elif command == "probe":
                self._post({"command": "probe", **self._cloud(config).request("probe", library=config["sdk_library"])})
            elif command == "disconnect":
                # Disable automatic restore before deleting, so even a locked keyring
                # cannot sign the user back in after they explicitly signed out.
                config["remember_account"], config["account_name"] = False, ""
                if not self.save_config(json.dumps(config)):
                    raise FlashForgeError("No se pudo desactivar la reconexión automática. Inténtalo de nuevo.")
                forget_error = None
                try:
                    self._cloud(config).request("forget_account", wait_timeout=30)
                except FlashForgeError as error:
                    forget_error = str(error)
                if self._bridge:
                    self._bridge.close()
                    self._bridge = None
                self._account, self._devices, self._prepared_id = None, [], None
                self._post({"command": "disconnected"})
                if forget_error:
                    self._post({"command": "remember_error", "error": forget_error})
            elif command == "status":
                status = self._read_status(config, message.get("device_id"))
                self._post({"command": "status", "status": status})
            else:
                raise FlashForgeError("Operación no admitida por esta versión.")
        except FlashForgeError as error:
            suffix = " Comprueba la impresora antes de volver a enviar." if self._upload_phase in ("creating", "dispatching") else ""
            self._post({"command": "error", "error": str(error) + suffix})
        except Exception:
            suffix = " Comprueba la impresora antes de volver a enviar." if self._upload_phase in ("creating", "dispatching") else ""
            self._post({"command": "error", "error": "No se pudo completar la operación del plugin." + suffix})
        finally:
            self._restoring = False
            if self._bridge and isinstance(self._bridge.account_state, dict):
                self._post({"command": "account_state", **self._bridge.account_state})
            self._upload_phase = ""
            message.clear()
            self._busy.release()
            self._post({"command": "done"})


@orca.plugin
class FlashForgePlugin(orca.base):
    def register_capabilities(self):
        # Orca audits the full argument list, not just the interpreter path.
        temporary_root = Path(os.environ.get("TMPDIR") or "/tmp")
        slicer_models = temporary_root / f"orcaslicer_{os.getuid()}" / "orcaslicer_model"
        orca.request_permissions(threading=True, process=[str(worker_command(DEFAULTS["python"]))],
                                 fs_read=[str(slicer_models / "*")])
        orca.register_capability(FlashForgePage)
