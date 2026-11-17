"""Experimental cloud upload, job creation and MQTT dispatch."""

import datetime
import hashlib
import json
import os
import uuid
from pathlib import Path

from .cloud_jobs import (create_job_payload, created_job_id, start_job_payload,
                         existing_job_payload, start_existing_job_payload)
from .cloud_mqtt import publish
from .cloud_storage import put_file, upload_config
from .model import FlashForgeError


def _md5(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()


def send_existing(session, job, options, emit):
    session.devices()
    device = session._devices.get(job["device_id"])
    if not device or not device.get("devTopic"):
        raise FlashForgeError("La cuenta no devolvió el destino de la impresora.")
    session.bind_client()
    broker = session.mqtt_config()
    emit({"event": "print_progress", "phase": "creating"})
    try:
        session._authenticate()
        body = json.dumps(existing_job_payload(job["device_id"], job["name"], options), separators=(",", ":")).encode()
        raw = session.api.request("POST", "/api/v2/external/truck/device/localJob",
            headers={"clientId": session.client_id, "token": session.access, "Content-Type": "application/json",
                     "User-Agent": "OrcaFlashForge/0.1.0"}, body=body)
        response = session._json(raw)
        data = response.get("data") if isinstance(response, dict) and response.get("code") == 0 else None
        if not isinstance(data, dict) or not isinstance(data.get("jobID"), str) or not data["jobID"]:
            raise ValueError("missing job ID")
        job_id, thumbnail = data["jobID"], data.get("thumbPath", "")
        if not isinstance(thumbnail, str):
            raise ValueError("invalid thumbnail")
    except Exception:
        raise FlashForgeError("No se confirmó la creación del trabajo guardado. Comprueba la impresora antes de repetir.") from None
    emit({"event": "print_progress", "phase": "dispatching"})
    try:
        payload = json.dumps(start_existing_job_payload(job["name"], job_id, thumbnail, options), separators=(",", ":")).encode()
        publish(broker, session.client_id, device["devTopic"], payload)
    except Exception:
        raise FlashForgeError("Se creó el trabajo, pero no se confirmó la orden de inicio. Comprueba la impresora antes de repetir.") from None
    return {"dispatched": True, "device_id": job["device_id"], "job_id": job_id,
            "name": job["name"], "printer_started_verified": False}


def send(session, job, options, cancel, emit):
    session.devices()
    device = session._devices.get(job["device_id"])
    if not device or not device.get("serialNumber") or not device.get("devTopic"):
        raise FlashForgeError("La cuenta no devolvió el destino de la impresora.")
    if cancel.is_set():
        raise FlashForgeError("Subida cancelada antes de enviar el archivo.")
    session.bind_client()
    broker = session.mqtt_config()
    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    name = uuid.uuid4().hex
    extension = Path(job["name"]).suffix.lower().lstrip(".")
    file_key = f"slicer-{month}/gcodeFile/{name}.{extension}"
    thumb_key = f"slicer-{month}/thumbFile/{name}.png"
    info = upload_config(session, [file_key, thumb_key])
    host = info["bucket"] + "." + info["endpoint"]
    if not host or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in host):
        raise FlashForgeError("Destino de almacenamiento no válido.")
    from urllib.parse import quote
    storage = {"bucket": info["bucket"], "endpoint": info["endpoint"], "file_key": file_key, "thumb_key": thumb_key,
               "file_url": "https://" + host + "/" + quote(file_key, safe="/"),
               "thumb_url": "https://" + host + "/" + quote(thumb_key, safe="/")}
    details = {**job, "type": extension, "md5": _md5(job["path"]),
               "thumbnail_name": Path(job["name"]).stem + ".png", "thumbnail_md5": _md5(job["thumbnail"]),
               "thumbnail_size": os.path.getsize(job["thumbnail"])}
    total_size = job["size"] + details["thumbnail_size"]
    emit({"event": "print_progress", "phase": "uploading", "now": 0, "total": total_size})
    put_file(info["urls"][file_key], job["path"], cancel,
             lambda now, total: emit({"event": "print_progress", "phase": "uploading", "now": now, "total": total_size}))
    put_file(info["urls"][thumb_key], job["thumbnail"], cancel,
             lambda now, total: emit({"event": "print_progress", "phase": "uploading",
                                      "now": job["size"] + now, "total": total_size}))
    if cancel.is_set():
        raise FlashForgeError("Subida cancelada. No se solicitó iniciar el trabajo.")
    emit({"event": "print_progress", "phase": "creating"})
    try:
        session._authenticate()
        raw = session.api.request("POST", "/api/v2/external/truck/slicer/cloud_job",
            headers={"clientId": session.client_id, "token": session.access, "Content-Type": "application/json",
                     "User-Agent": "OrcaFlashForge/0.1.0"},
            body=json.dumps(create_job_payload(details, options, storage), separators=(",", ":")).encode())
        job_id = created_job_id(session._json(raw), job["device_id"])
    except Exception:
        raise FlashForgeError("No se pudo confirmar la creación del trabajo. Comprueba la impresora antes de repetir.") from None
    emit({"event": "print_progress", "phase": "dispatching"})
    try:
        payload = json.dumps(start_job_payload(details, options, storage, device["serialNumber"], job_id), separators=(",", ":")).encode()
        publish(broker, session.client_id, device["devTopic"], payload)
    except Exception:
        raise FlashForgeError("El trabajo se creó, pero el servidor no confirmó la orden de inicio. Comprueba la impresora antes de repetir.") from None
    return {"dispatched": True, "device_id": job["device_id"], "job_id": job_id,
            "name": job["name"], "printer_started_verified": False}
