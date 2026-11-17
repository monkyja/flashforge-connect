"""Bounded upload to URLs signed by the machine service."""

import http.client
import json
import os
import urllib.parse

from .model import FlashForgeError


def upload_config(session, keys):
    if len(keys) != 2 or any(not isinstance(key, str) or not key for key in keys):
        raise FlashForgeError("Las rutas de subida no son válidas.")
    session._authenticate()
    body = json.dumps({"opType": "common", "keys": keys}, separators=(",", ":")).encode()
    response = session._json(session.api.request(
        "POST", "/api/v2/external/truck/device/upload/config", body=body,
        headers={"clientId": session.client_id, "token": session.access,
                 "Content-Type": "application/json", "User-Agent": "OrcaFlashForge/0.1.0"}))
    data = response.get("data")
    if response.get("code") != 0 or not isinstance(data, dict) or data.get("method") != "PUT":
        raise FlashForgeError("FlashForge no autorizó la subida.")
    urls = data.get("keyMap")
    if not isinstance(urls, dict) or any(not valid_signed_url(urls.get(key)) for key in keys):
        raise FlashForgeError("FlashForge devolvió rutas de subida no válidas.")
    if not all(isinstance(data.get(key), str) and data[key] for key in ("bucket", "endpoint")):
        raise FlashForgeError("FlashForge no devolvió los datos de almacenamiento.")
    return {"bucket": data["bucket"], "endpoint": data["endpoint"], "urls": {key: urls[key] for key in keys}}


def valid_signed_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return False
    try:
        url = urllib.parse.urlsplit(value)
        return bool(url.scheme == "https" and url.hostname and not url.username and not url.password
                    and not url.fragment and url.port in (None, 443) and url.query)
    except ValueError:
        return False


def put_file(url, path, cancel, progress, *, timeout=30, connection_factory=http.client.HTTPSConnection):
    if not valid_signed_url(url):
        raise FlashForgeError("La URL de subida no es válida.")
    parsed = urllib.parse.urlsplit(url)
    connection = connection_factory(parsed.hostname, parsed.port or 443, timeout=timeout)
    try:
        total = os.path.getsize(path)
        with open(path, "rb") as stream:
            connection.putrequest("PUT", urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, "")))
            connection.putheader("Content-Type", "application/zip")
            connection.putheader("Cache-Control", "no-cache")
            connection.putheader("Content-Length", str(total))
            connection.endheaders()
            sent = 0
            while sent < total:
                if cancel.is_set():
                    raise FlashForgeError("Subida cancelada. No se solicitó iniciar el trabajo.")
                chunk = stream.read(min(65536, total - sent))
                if not chunk:
                    raise FlashForgeError("El archivo cambió durante la subida.")
                connection.send(chunk)
                sent += len(chunk)
                progress(sent, total)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise FlashForgeError(f"El almacenamiento rechazó la subida (HTTP {response.status}).")
    except (OSError, http.client.HTTPException):
        raise FlashForgeError("No se pudo completar la subida al almacenamiento.") from None
    finally:
        connection.close()
