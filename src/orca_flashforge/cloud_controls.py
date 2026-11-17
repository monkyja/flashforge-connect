"""Experimental MQTT controls, based on the FlashNetwork 3.4.0 serializers."""

import json

from .cloud_mqtt import publish
from .model import FlashForgeError


def dispatch(session, device_id, control, value):
    commands = {"light": ("lightControl_cmd", "status"), "camera": ("streamCtrl_cmd", "action")}
    if control not in commands or value not in ("open", "close"):
        raise FlashForgeError("Acción de impresora no válida.")
    session.devices()
    device = session._devices.get(device_id)
    if not device or not device.get("devTopic"):
        raise FlashForgeError("La cuenta no devolvió el destino de la impresora.")
    session.bind_client()
    broker = session.mqtt_config()
    command, field = commands[control]
    payload = json.dumps({"cmd": command, "args": {field: value}}, separators=(",", ":")).encode()
    try:
        publish(broker, session.client_id, device["devTopic"], payload)
    except Exception:
        raise FlashForgeError("El servidor no confirmó la orden. Comprueba el estado antes de repetir.") from None
    return {"broker_confirmed": True, "device_confirmed": False}


def cancel_print(session, device_id, job_id):
    if not isinstance(job_id, str) or not job_id:
        raise FlashForgeError("La impresora no ha comunicado el identificador del trabajo.")
    session.devices()
    device = session._devices.get(device_id)
    if not device or not device.get("devTopic"):
        raise FlashForgeError("La cuenta no devolvió el destino de la impresora.")
    status = session.status(device_id)
    if status.get("job_id") != job_id or status.get("status", "").lower() not in ("printing", "pause", "pausing", "heating"):
        raise FlashForgeError("El trabajo o el estado cambió. Actualiza la impresora antes de cancelar.")
    session.bind_client()
    payload = json.dumps({"cmd": "jobCtl_cmd", "args": {"jobID": job_id, "action": "cancel"}}, separators=(",", ":")).encode()
    try:
        publish(session.mqtt_config(), session.client_id, device["devTopic"], payload)
    except Exception:
        raise FlashForgeError("El servidor no confirmó la cancelación. Comprueba la impresora antes de repetir.") from None
    return {"broker_confirmed": True, "device_confirmed": False}
