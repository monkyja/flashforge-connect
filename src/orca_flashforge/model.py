"""Public status model. Missing values remain None, never simulated measurements."""

import math
from urllib.parse import urlsplit


class FlashForgeError(Exception):
    """An error safe to display without credentials or raw printer responses."""


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def text(value):
    return value[:512] if isinstance(value, str) else ""


def camera_url(value, local_host=None):
    """Allow only web streams; LAN URLs must stay on the configured printer."""
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return ""
        if local_host and url.hostname.lower() != local_host.lower():
            return ""
        return value
    except ValueError:
        return ""


def normalize(detail, *, mode, local_host=None, account_model=""):
    if not isinstance(detail, dict):
        raise FlashForgeError("La respuesta no contiene un estado válido.")
    station = detail.get("matlStationInfo", detail.get("MatlStationInfo", {}))
    station = station if isinstance(station, dict) else {}
    slots = station.get("slotInfos", station.get("SlotInfos", []))
    nozzles = detail.get("nozzleTemps")
    targets = detail.get("nozzleTargetTemps")
    temperatures = []
    if isinstance(nozzles, list):
        for index, current in enumerate(nozzles[:16]):
            target = targets[index] if isinstance(targets, list) and index < len(targets) else None
            temperatures.append({"name": f"Boquilla {index + 1}", "current": number(current), "target": number(target)})
    else:
        for label, key in (("Derecha", "right"), ("Izquierda", "left")):
            if key + "Temp" in detail:
                temperatures.append({"name": label, "current": number(detail.get(key + "Temp")),
                                     "target": number(detail.get(key + "TargetTemp"))})
    for label, key in (("Cama", "plat"), ("Cámara", "chamber")):
        if key + "Temp" in detail:
            temperatures.append({"name": label, "current": number(detail.get(key + "Temp")),
                                 "target": number(detail.get(key + "TargetTemp"))})
    normalized_slots = [{"id": slot.get("slotId"), "material": text(slot.get("materialName")),
                         "color": text(slot.get("materialColor")),
                         "has_filament": slot.get("hasFilament") in (True, 1, "1")}
                        for slot in slots[:64] if isinstance(slot, dict)] if isinstance(slots, list) else []
    # FFUtils.hpp identifies C5/C5P as 0x0028/0x0029. For these models,
    # SlotSelectWnd::setupNozzles pairs nozzle i+1 with slotInfos[i], while
    # NewTempInputPanel::UpdateTempatrue reads nozzleTemps[i] for that nozzle.
    # Preserve array order and real slot IDs; this is a display association,
    # never a G-code tool/slot mapping. Incomplete or ambiguous data stays separate.
    slot_ids = [slot["id"] for slot in normalized_slots]
    known_model = (type(detail.get("pid")) is int and detail["pid"] in (0x0028, 0x0029)) or (
        detail.get("pid") is None and isinstance(account_model, str) and account_model.strip().casefold() == "creator 5 pro")
    if (known_model
            and isinstance(nozzles, list) and 0 < len(nozzles) <= 16
            and isinstance(slots, list) and len(nozzles) == len(slots) == len(normalized_slots)
            and all(type(slot_id) is int and slot_id >= 0 for slot_id in slot_ids)
            and len(set(slot_ids)) == len(slot_ids)):
        for temperature, slot in zip(temperatures, normalized_slots):
            temperature["filament_slot_id"] = slot["id"]
    progress = number(detail.get("printProgress"))
    return {
        "mode": mode, "name": text(detail.get("name")), "firmware": text(detail.get("firmwareVersion")),
        "move_enabled": detail.get("moveCtrl") == 1 and type(detail.get("moveCtrl")) is int,
        "status": text(detail.get("status")), "file": text(detail.get("printFileName")),
        "job_id": text(detail.get("jobId")),
        "job_thumbnail": camera_url(detail.get("printFileThumbUrl"), local_host),
        "machine_info": {
            "model": text(account_model),
            "nozzle_model": text(detail.get("nozzleModel")),
            "build_volume": text(detail.get("measure")),
            # The manufacturer's WAN UI treats cumulativePrintTime as hours
            # and cumulativeFilament as metres; LAN uses a different time unit.
            "print_hours": number(detail.get("cumulativePrintTime")) if mode == "cloud" else None,
            "filament_metres": number(detail.get("cumulativeFilament")) if mode == "cloud" else None,
            "free_storage_gb": number(detail.get("remainingDiskSpace")),
        },
        "light_status": text(detail.get("lightStatus")),
        "flow_calibration_available": type(detail.get("lidar")) is int and detail.get("lidar") == 1,
        "progress": progress * 100 if progress is not None and 0 <= progress <= 1 else None,
        "layer": number(detail.get("printLayer")), "layers": number(detail.get("targetPrintLayer")),
        "duration_seconds": number(detail.get("printDuration")),
        "estimated_seconds": number(detail.get("estimatedTime")),
        "temperatures": temperatures,
        "slots": normalized_slots,
        "camera_url": camera_url(detail.get("cameraStreamUrl"), local_host),
        "error_code": text(detail.get("errorCode")),
    }


def demo_status():
    result = normalize({
        "name": "Creator 5 Pro · DEMOSTRACIÓN", "firmwareVersion": "Simulado", "pid": 0x0029,
        "status": "printing", "printFileName": "ejemplo-simulado.gcode", "printProgress": .42,
        "printLayer": 84, "targetPrintLayer": 200, "nozzleTemps": [210, 25, 25, 25],
        "nozzleTargetTemps": [210, 0, 0, 0], "platTemp": 60, "platTargetTemp": 60,
        "chamberTemp": 25, "chamberTargetTemp": 0,
        "matlStationInfo": {"slotInfos": [
            {"slotId": 1, "hasFilament": 1, "materialName": "PLA simulado", "materialColor": "#eeeeee"},
            {"slotId": 2, "hasFilament": 1, "materialName": "PETG simulado", "materialColor": "#ed4c5c"},
            {"slotId": 3, "hasFilament": 0, "materialName": "", "materialColor": ""},
            {"slotId": 4, "hasFilament": 0, "materialName": "", "materialColor": ""}]},
    }, mode="demo")
    return result
