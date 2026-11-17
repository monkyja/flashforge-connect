"""Small, injectable HTTPS transport for the independent cloud client.

This module contains transport rules only. It does not define FlashForge hosts,
routes, headers, authentication, or response schemas.
"""

import urllib.error
import urllib.parse
import urllib.request

from .model import FlashForgeError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class HttpsTransport:
    def __init__(self, base_url, *, timeout=15, max_response_bytes=2 * 1024 * 1024, opener=None):
        parsed = urllib.parse.urlsplit(base_url) if isinstance(base_url, str) else None
        if (parsed is None or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise FlashForgeError("La URL base cloud debe ser HTTPS y no incluir credenciales ni parámetros.")
        if timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout and max_response_bytes must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, *, headers=None, body=None):
        if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                or any(ord(char) < 32 for char in path)):
            raise FlashForgeError("Ruta cloud no válida.")
        url = self.base_url + path
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise FlashForgeError(f"El servicio cloud devolvió HTTP {response.status}.")
                data = response.read(self.max_response_bytes + 1)
                if len(data) > self.max_response_bytes:
                    raise FlashForgeError("Respuesta cloud demasiado grande.")
                return data
        except urllib.error.HTTPError as error:
            raise FlashForgeError(f"El servicio cloud devolvió HTTP {error.code}.") from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise FlashForgeError("No se pudo completar la solicitud HTTPS cloud.") from None
