"""LAN HTTP implementation based on OrcaSlicer 2.5.0 Utils/Flashforge.cpp."""

import http.client
import ipaddress
import json
import re

from .model import FlashForgeError, normalize


def validate_host(host):
    if not isinstance(host, str) or not host or len(host) > 253:
        raise FlashForgeError("Introduce una IP o nombre de host, sin puerto ni URL.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
            raise FlashForgeError("Introduce una IP o nombre de host, sin puerto ni URL.") from None
    return host


class LanClient:
    def __init__(self, host, serial, access_code, *, port=8898, timeout=5):
        self.host = validate_host(host)
        self.serial = serial
        self.access_code = access_code
        self.port = port
        self.timeout = timeout
        if not all(isinstance(v, str) and v and not any(ord(c) < 32 for c in v)
                   for v in (serial, access_code)):
            raise FlashForgeError("Faltan número de serie o código de acceso LAN válidos.")

    def status(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            body = json.dumps({"serialNumber": self.serial, "checkCode": self.access_code})
            connection.request("POST", "/detail", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise FlashForgeError(f"La impresora devolvió HTTP {response.status}.")
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise FlashForgeError("Respuesta de la impresora demasiado grande.")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise FlashForgeError("Respuesta JSON de la impresora no válida.")
            code = result.get("code", result.get("err"))
            if code is not None and str(code) != "0":
                raise FlashForgeError("La impresora rechazó la consulta. Revisa el acceso LAN.")
            detail = result.get("detail")
            if not isinstance(detail, dict):
                raise FlashForgeError("La respuesta no contiene detail.")
            return normalize(detail, mode="lan", local_host=self.host)
        except (OSError, http.client.HTTPException):
            raise FlashForgeError("No se pudo consultar la impresora por LAN.") from None
        except (ValueError, UnicodeError):
            raise FlashForgeError("Respuesta JSON de la impresora no válida.") from None
        finally:
            connection.close()
