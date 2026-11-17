"""Remember refresh tokens through Linux Secret Service, never plugin files.

Only the SDK worker uses this module. Secret values travel on secret-tool's stdin
and captured stdout, never in its command line or the page's IPC response.
"""

import json
import subprocess

from .model import FlashForgeError


class CredentialError(FlashForgeError):
    pass


class SecretStore:
    def __init__(self, *, runner=None, purpose="refresh-session"):
        self._run = runner or subprocess.run
        self._attributes = ["application", "org.orca-flashforge", "purpose", purpose]

    def _request(self, action, payload=None):
        command = ["/usr/bin/secret-tool", action]
        if action == "store":
            command += ["--label=FlashForge · OrcaSlicer"]
        command += self._attributes
        try:
            result = self._run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise CredentialError("No se pudo acceder al llavero del sistema. Comprueba que esté disponible y desbloqueado.") from None
        if action in ("lookup", "clear") and result.returncode == 1 and not result.stderr:
            return None
        if result.returncode:
            raise CredentialError("No se pudo acceder al llavero. En Flatpak, comprueba el permiso org.freedesktop.secrets.")
        return result.stdout

    def load(self):
        value = self._request("lookup")
        if value is None:
            return None
        try:
            if len(value) > 65536:
                raise ValueError()
            record = json.loads(value)
            if (not isinstance(record, dict) or record.get("version") != 1
                    or not isinstance(record.get("username"), str) or not record["username"]
                    or not isinstance(record.get("refresh_token"), str) or not record["refresh_token"]):
                raise ValueError()
            return {"username": record["username"], "refresh_token": record["refresh_token"]}
        except (ValueError, UnicodeError):
            raise CredentialError("La sesión guardada no es válida. Olvida la cuenta y vuelve a conectar.") from None

    def save(self, username, refresh_token):
        if not refresh_token:
            raise CredentialError("FlashForge no devolvió un token de renovación; esta sesión no se puede recordar.")
        data = json.dumps({"version": 1, "username": username,
                           "refresh_token": refresh_token.decode("utf-8")}).encode("utf-8")
        if len(data) > 65536:
            raise CredentialError("La sesión supera el tamaño admitido por el almacenamiento local.")
        self._request("store", data)

    def clear(self):
        self._request("clear")


class SessionVault:
    def __init__(self, store=None):
        self.store = store or SecretStore()
        self.remembered = False
        self.warning = ""

    def attach(self, session, username, remember):
        self.remembered, self.warning = False, ""
        if not remember:
            session._token_sink = None
            return

        def rotated(refresh_token):
            try:
                self.store.save(username, refresh_token)
                self.remembered, self.warning = True, ""
            except (CredentialError, UnicodeError):
                self.remembered = False
                self.warning = "La conexión funciona, pero no se pudo guardar la sesión en el llavero."
                # If the new token could not replace an old account/token, do not keep
                # a stale credential that could restore the wrong account later.
                try:
                    self.store.clear()
                except CredentialError:
                    self.warning += " No se pudo eliminar una sesión anterior; usa Salir y olvidar cuenta para reintentarlo."

        session._token_sink = rotated

    def forget(self):
        self.store.clear()
        self.remembered, self.warning = False, ""

    def restore(self, factory, library, settings, expected_username=""):
        from .sdk import AuthenticationError
        record = self.store.load()
        if record is None:
            return None, {"connected": False}
        if expected_username and record["username"] != expected_username:
            raise CredentialError("La cuenta del llavero no coincide con la última cuenta elegida. Olvídala y vuelve a conectar.")
        session = factory(library, settings)
        self.attach(session, record["username"], True)
        self.remembered = True
        try:
            result = session.restore(record["refresh_token"], record["username"])
            result["account_name"] = record["username"]
            return session, result
        except AuthenticationError:
            session.close()
            try:
                self.forget()
            except CredentialError:
                self.remembered = False
                self.warning = "La sesión caducó y no se pudo eliminar del llavero."
            raise AuthenticationError("La sesión guardada caducó. Vuelve a conectar tu cuenta.") from None
        except Exception:
            session.close()
            # An offline server or unavailable SDK does not revoke a saved credential.
            raise

    def info(self):
        return {"remembered": self.remembered, "remember_warning": self.warning}
