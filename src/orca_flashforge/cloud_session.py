"""Experimental read-only account client, independent of FlashNetwork."""

import json
import time
import urllib.parse
import uuid

from .cloud_http import HttpsTransport
from .cloud_protocol import ACCOUNT_CLIENT_ID
from .model import FlashForgeError, camera_url, normalize, number, text
from .sdk import AuthenticationError


class IndependentSession:
    def __init__(self, library=None, settings=None, *, auth=None, api=None):
        self.auth = auth or HttpsTransport("https://auth.voxelshare.com")
        self.api = api or HttpsTransport("https://api.voxelshare.com")
        self.client_id = str(uuid.uuid4())
        self.access = self.refresh = ""
        self.deadline = 0
        self._token_sink = None
        self._devices = {}
        self._account_name = ""

    @staticmethod
    def _json(data):
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise FlashForgeError("FlashForge devolvió una respuesta no válida.") from None

    def _token(self, fields):
        query = urllib.parse.urlencode(fields)
        try:
            response = self._json(self.auth.request("POST", "/oauth/token?" + query,
                headers={"Accept": "application/json", "Accept-Language": "es",
                         "User-Agent": "OrcaFlashForge/0.1.0"}, body=b""))
        except FlashForgeError as error:
            if "HTTP 400" in str(error) or "HTTP 401" in str(error):
                raise AuthenticationError("FlashForge rechazó las credenciales de la cuenta.") from None
            raise
        access, refresh, expires = response.get("access_token"), response.get("refresh_token"), response.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(expires, (int, float)) or expires <= 0:
            raise FlashForgeError("FlashForge no devolvió una sesión válida.")
        self.access = access
        self.refresh = refresh if isinstance(refresh, str) else ""
        self.deadline = time.monotonic() + expires
        if self._token_sink and self.refresh:
            self._token_sink(self.refresh.encode())

    def login(self, username, password):
        if not username or not password:
            raise FlashForgeError("Introduce el usuario y la contraseña de FlashForge.")
        self._token({"grant_type": "password", "scope": "*", "password_type": "password",
                     "client_id": ACCOUNT_CLIENT_ID, "username": username, "password": password})
        self._account_name = username
        return {"connected": True, "profile": {"nickname": username, "email": "", "avatar_url": ""}}

    def restore(self, refresh_token, username):
        self._token({"grant_type": "refresh_token", "client_id": ACCOUNT_CLIENT_ID, "refresh_token": refresh_token})
        self._account_name = username
        return {"connected": True, "profile": {"nickname": username, "email": "", "avatar_url": ""}}

    def _authenticate(self):
        if not self.access:
            raise FlashForgeError("Inicia sesión en FlashForge.")
        if time.monotonic() >= self.deadline - 60:
            if not self.refresh:
                raise AuthenticationError("Sesión caducada; inicia sesión de nuevo.")
            self._token({"grant_type": "refresh_token", "client_id": ACCOUNT_CLIENT_ID, "refresh_token": self.refresh})

    def _get(self, path, params):
        self._authenticate()
        response = self._json(self.api.request("GET", path + "?" + urllib.parse.urlencode(params),
            headers={"clientId": self.client_id, "token": self.access, "User-Agent": "OrcaFlashForge/0.1.0"}))
        if response.get("code") != 0 or not isinstance(response.get("data"), dict):
            raise FlashForgeError("FlashForge rechazó la consulta o devolvió datos incompletos.")
        return response["data"]

    def mqtt_config(self):
        """Fetch broker settings; credentials remain inside the worker."""
        self._authenticate()
        response = self._json(self.api.request(
            "GET", "/api/v2/external/truck/device/mqtt/config",
            headers={"clientId": self.client_id, "token": self.access, "clientType": "pc",
                     "User-Agent": "OrcaFlashForge/0.1.0"}))
        data = response.get("data")
        if response.get("code") != 0 or not isinstance(data, dict):
            raise FlashForgeError("FlashForge no devolvió la configuración MQTT.")
        if not all(isinstance(data.get(key), str) and data[key] for key in ("server", "username", "password")):
            raise FlashForgeError("La configuración MQTT está incompleta.")
        return data

    def bind_client(self):
        """Register the account client, without changing machine ownership."""
        self._authenticate()
        if not self._account_name:
            raise FlashForgeError("La sesión no contiene el correo de la cuenta.")
        response = self._json(self.api.request(
            "POST", "/api/v2/external/shop-center/apps/user_relation",
            body=json.dumps({"email": self._account_name}).encode(),
            headers={"clientId": self.client_id, "token": self.access,
                     "Content-Type": "application/json", "User-Agent": "OrcaFlashForge/0.1.0"}))
        if response.get("code") != 0:
            raise FlashForgeError("FlashForge rechazó la relación del cliente con la cuenta.")

    def devices(self):
        result, routes, page = [], {}, 1
        while page <= 10:
            data = self._get("/api/v2/external/truck/device/list", {"currentPage": page, "pageSize": 100, "appId": 31, "platform": 15})
            items = data.get("detail")
            if not isinstance(items, list):
                raise FlashForgeError("Lista de impresoras no válida.")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("deviceID"), str):
                    routes[item["deviceID"]] = {"serialNumber": item.get("sn", ""), "devTopic": item.get("gTopic", ""),
                                                 "model": item.get("model", "")}
                    result.append({"devId": item["deviceID"], "name": item.get("name", ""), "model": item.get("model", ""),
                                   "status": item.get("status", ""), "image_url": camera_url(item.get("image"))})
            if not data.get("next"):
                break
            page += 1
        else:
            raise FlashForgeError("La lista de impresoras supera el límite de páginas.")
        self._devices = routes
        return result

    def status(self, device_id):
        if device_id not in self._devices:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        data = self._get("/api/v2/external/truck/device/details", {"id": device_id})
        detail = data.get("detail")
        if not isinstance(detail, dict):
            raise FlashForgeError("FlashForge no devolvió el detalle de la impresora.")
        names = {"printerName": "name", "progress": "printProgress", "fileName": "printFileName",
                 "targetLayer": "targetPrintLayer", "duration": "printDuration", "estimateTime": "estimatedTime",
                 "rightTemperature": "rightTemp", "rightTargetTemperature": "rightTargetTemp",
                 "leftTemperature": "leftTemp", "leftTargetTemperature": "leftTargetTemp",
                 "platformCurTemperature": "platTemp", "platformTargetTemperature": "platTargetTemp",
                 "chamberTemperature": "chamberTemp", "chamberTargetTemperature": "chamberTargetTemp",
                 "hlsStream": "cameraStreamUrl", "jobID": "jobId", "light": "lightStatus"}
        return normalize({names.get(k, k): v for k, v in detail.items()}, mode="cloud",
                         account_model=self._devices[device_id].get("model", ""))

    def recordings(self, device_id):
        if device_id not in self._devices:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        data = self._get("/api/v2/external/truck/device/delay_video/list",
                         {"id": device_id, "currentPage": 1, "pageSize": 15})
        videos = data.get("videos")
        if not isinstance(videos, list) or len(videos) > 100:
            raise FlashForgeError("La lista de grabaciones no es válida.")
        return [{"name": str(v.get("filename", ""))[:512], "url": camera_url(v.get("videoUrl")),
                 "thumbnail": camera_url(v.get("thumbnail"))}
                for v in videos if isinstance(v, dict)]

    def stored_files(self, device_id):
        """Files stored on the printer; this is not a completed-job history."""
        if device_id not in self._devices:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        self._authenticate()
        raw = self.api.request("GET", "/api/v2/external/truck/device/local_files?" + urllib.parse.urlencode({"id": device_id}),
            headers={"clientId": self.client_id, "token": self.access, "User-Agent": "OrcaFlashForge/0.1.0"})
        response = self._json(raw)
        entries = response.get("data")
        if response.get("code") != 0 or not isinstance(entries, list) or len(entries) > 1000:
            raise FlashForgeError("FlashForge no devolvió una lista válida de archivos guardados.")
        result = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("file_name"), str):
                continue
            name = text(entry["file_name"])
            if not name or any(ord(c) < 32 for c in name):
                continue
            image = entry.get("file_obj") if isinstance(entry.get("file_obj"), dict) else {}
            detail = entry.get("file_detail") if isinstance(entry.get("file_detail"), dict) else {}
            values = detail.get("gcodeToolDatas")
            expected = detail.get("gcodeToolCnt")
            tools = []
            if isinstance(values, list) and type(expected) is int and expected == len(values) and 0 < expected <= 64:
                for item in values:
                    if not isinstance(item, dict) or type(item.get("toolID")) is not int or item["toolID"] < 0:
                        tools = []
                        break
                    tools.append({"toolId": item["toolID"], "material": text(item.get("materialName")),
                                  "color": text(item.get("materialColor")), "weight": number(item.get("filamentWeight"))})
                if len({item["toolId"] for item in tools}) != len(tools):
                    tools = []
            result.append({"name": name, "thumbnail": camera_url(image.get("url")),
                           "seconds": number(detail.get("printingTime")), "weight": number(detail.get("totalFilamentWeight")),
                           "filaments": tools, "can_repeat": bool(tools)})
        return result

    def close(self):
        self.access = self.refresh = ""
        self._token_sink = None
        self._account_name = ""
        self._devices.clear()
