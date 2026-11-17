"""Private, immutable print preparation. This module never contacts a printer."""

import base64
import hashlib
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from .model import FlashForgeError

# Local resource limits, not limits claimed for FlashForge firmware.
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_THUMB_BYTES = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"
# A neutral fallback, not a preview of the user's model.
EMPTY_THUMBNAIL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNoaGj4DwAFhAKAjM1mJgAAAABJRU5ErkJggg==")


def validate_options(options, tools):
    if not isinstance(options, dict):
        raise FlashForgeError("Opciones de impresión no válidas.")
    result = {}
    for key in ("bed_leveling", "timelapse", "use_material_station", "flow_calibration"):
        value = options.get(key, False)
        if not isinstance(value, bool):
            raise FlashForgeError("Opciones de impresión no válidas.")
        result[key] = value
    plate = options.get("plate", 1)
    if type(plate) is not int or not 1 <= plate <= 2147483647:
        raise FlashForgeError("Selecciona un número de placa válido.")
    result["plate"] = plate
    mappings = options.get("material_mappings", [])
    if not isinstance(mappings, list):
        raise FlashForgeError("El mapeo de materiales no es válido.")
    result["material_mappings"] = []
    if result["use_material_station"]:
        if not tools or len(mappings) != len(tools):
            raise FlashForgeError("Asigna explícitamente una ranura a cada herramienta del archivo.")
        seen = set()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                raise FlashForgeError("El mapeo de materiales no es válido.")
            tool, slot = mapping.get("toolId"), mapping.get("slotId")
            if (type(tool) is not int or tool not in tools or tool in seen or not 0 <= tool <= 2147483647
                    or type(slot) is not int or not 0 <= slot <= 2147483647):
                raise FlashForgeError("El mapeo de herramientas y ranuras no es válido.")
            seen.add(tool)
            clean = {"toolId": tool, "slotId": slot}
            for key in ("materialName", "toolMaterialColor", "slotMaterialColor"):
                value = mapping.get(key, "")
                if not isinstance(value, str) or len(value) > 512 or "\0" in value:
                    raise FlashForgeError("El mapeo de materiales no es válido.")
                clean[key] = value
            result["material_mappings"].append(clean)
    elif mappings:
        raise FlashForgeError("Activa la estación de materiales para aplicar un mapeo de ranuras.")
    return result


def inspect_gcode(stream, metadata=None):
    tools, thumbnail, chunks = set(), None, None
    commands = False
    for line in stream:
        if len(line) > MAX_THUMB_BYTES * 2:
            raise FlashForgeError("El archivo contiene una línea G-code demasiado grande.")
        if re.match(rb"\s*[GM]\d+", line):
            commands = True
        if metadata is not None:
            field = re.match(rb";\s*(filament_type|filament_colour|filament_vendor|filament_settings_id)\s*=\s*(.*?)\s*$", line)
            if field:
                metadata[field[1].decode()] = [part.strip().strip('"') for part in
                                              field[2].decode("utf-8", errors="replace").split(";")]
            summary = re.match(rb";\s*(estimated printing time \(normal mode\)|total filament used \[g\])\s*=\s*(.{1,120}?)\s*$", line)
            if summary:
                metadata[summary[1].decode()] = summary[2].decode("utf-8", errors="replace")
            usage = re.match(rb";\s*filament used \[(g|mm)\]\s*=\s*([0-9., ]+)\s*$", line)
            if usage:
                metadata["filament_used_" + usage[1].decode()] = [v.strip() for v in usage[2].decode().split(",")]
        tool = re.match(rb"\s*T(\d+)\s*(?:;.*)?$", line)
        if tool:
            tools.add(int(tool[1]))
        if re.match(rb";\s*thumbnail(?:_PNG)? begin\b", line):
            chunks = bytearray()
        elif chunks is not None and re.match(rb";\s*thumbnail(?:_PNG)? end\b", line):
            try:
                data = base64.b64decode(chunks, validate=True)
                if data.startswith(PNG) and len(data) <= MAX_THUMB_BYTES:
                    thumbnail = data
            except ValueError:
                pass
            chunks = None
        elif chunks is not None:
            chunks.extend(line.strip().lstrip(b";").strip())
            if len(chunks) > MAX_THUMB_BYTES * 2:
                chunks = None
    if not commands:
        raise FlashForgeError("El archivo no contiene G-code laminado.")
    return sorted(tools), thumbnail


def filament_metadata(metadata, tool):
    result = {}
    for source, target in (("filament_type", "material"), ("filament_colour", "color"),
                           ("filament_vendor", "brand"), ("filament_settings_id", "preset"),
                           ("filament_used_g", "weight"), ("filament_used_mm", "length_mm")):
        values = metadata.get(source, [])
        if tool < len(values) and values[tool]:
            result[target] = values[tool]
    return result


class JobStore:
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="orca-flashforge-jobs-")
        self.prepared = None

    def stage(self, path, *, device_id, profile="", plate=1):
        self.clear()
        if not isinstance(path, str) or not path or "\0" in path:
            raise FlashForgeError("Lamina un modelo o introduce la ruta de un archivo G-code o 3MF laminado.")
        if not isinstance(device_id, str) or not device_id:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        if type(plate) is not int or not 1 <= plate <= 2147483647:
            raise FlashForgeError("Selecciona un número de placa válido.")
        try:
            source = Path(path).expanduser().resolve(strict=True)
            if not source.is_file() or source.suffix.lower() not in (".gcode", ".3mf"):
                raise FlashForgeError("Selecciona G-code o un 3MF que ya contenga el laminado.")
            if any(ord(char) < 32 for char in source.name):
                raise FlashForgeError("El nombre del archivo contiene caracteres de control.")
            if not 0 < source.stat().st_size <= MAX_FILE_BYTES:
                raise FlashForgeError("El archivo supera el límite local de 512 MiB o está vacío.")
            staged = Path(self._temporary.name) / ("job" + source.suffix.lower())
            shutil.copyfile(source, staged)
            if staged.stat().st_size > MAX_FILE_BYTES:
                raise FlashForgeError("El archivo supera el límite local de 512 MiB.")
            thumbnail, metadata = None, {}
            if source.suffix.lower() == ".3mf":
                with zipfile.ZipFile(staged) as archive:
                    name = f"Metadata/plate_{plate}.gcode"
                    info = archive.getinfo(name)
                    if info.file_size > MAX_FILE_BYTES:
                        raise FlashForgeError("La placa supera el límite local de 512 MiB.")
                    with archive.open(info) as gcode:
                        tools, thumbnail = inspect_gcode(gcode, metadata)
                    image = f"Metadata/plate_{plate}.png"
                    if image in archive.namelist() and archive.getinfo(image).file_size <= MAX_THUMB_BYTES:
                        data = archive.read(image)
                        if data.startswith(PNG):
                            thumbnail = data
            else:
                with staged.open("rb") as gcode:
                    tools, thumbnail = inspect_gcode(gcode, metadata)
            thumb = Path(self._temporary.name) / "thumbnail.png"
            thumb.write_bytes(thumbnail or EMPTY_THUMBNAIL)
            with staged.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            self.prepared = {"id": uuid.uuid4().hex, "path": str(staged), "thumbnail": str(thumb),
                             "name": source.name, "size": staged.stat().st_size, "sha256": digest,
                             "device_id": device_id, "profile": str(profile)[:512], "plate": plate,
                             "tools": tools, "has_thumbnail": thumbnail is not None, "union_id": str(uuid.uuid4()),
                             "filaments": [{"toolId": tool, **filament_metadata(metadata, tool)}
                                           for tool in tools if tool < len(metadata.get("filament_type", []))
                                           and tool < len(metadata.get("filament_colour", []))]}
            return {key: value for key, value in self.prepared.items() if key not in ("path", "thumbnail")}
        except (OSError, ValueError, zipfile.BadZipFile, KeyError):
            self.clear()
            raise FlashForgeError("No se pudo preparar el archivo. Comprueba la ruta y que la placa esté laminada.") from None

    def take(self, job_id, device_id, options):
        job = self.prepared
        if not job or job["id"] != job_id or job["device_id"] != device_id:
            raise FlashForgeError("La preparación ha caducado. Revisa de nuevo archivo e impresora.")
        clean = validate_options(options, job["tools"])
        if clean["plate"] != job["plate"]:
            raise FlashForgeError("La placa cambió. Vuelve a preparar el archivo.")
        self.prepared = None  # One attempt only: an uncertain result must never be retried implicitly.
        return job, clean

    def clear(self):
        self.prepared = None
        for path in Path(self._temporary.name).iterdir():
            path.unlink()

    def close(self):
        self._temporary.cleanup()
