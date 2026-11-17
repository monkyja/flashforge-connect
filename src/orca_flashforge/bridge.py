"""Bounded, serialized IPC with an isolated Python/FlashNetwork worker."""

import json
import os
import selectors
import subprocess
import threading
import time
from pathlib import Path

from .model import FlashForgeError


def worker_command(python):
    """Keep the launch arguments and OrcaSlicer's audited command identical."""
    return [python, "-u", "-m", "orca_flashforge.sdk_worker"]


class SdkBridge:
    def __init__(self, python="/usr/bin/python3", *, timeout=25):
        self.python = python
        self.timeout = timeout
        self.process = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._buffer = b""
        self._stopped = threading.Event()
        self.account_state = {}

    def _start(self):
        if self._stopped.is_set():
            raise FlashForgeError("El adaptador se ha cerrado.")
        if self.process and self.process.poll() is None:
            return
        self.close()
        environment = os.environ.copy()
        # The plugin's extracted wheel is not installed in the system interpreter.
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        try:
            self.process = subprocess.Popen(
                worker_command(self.python),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                env=environment, bufsize=0,
            )
            if self._stopped.is_set():
                self.close()
                raise FlashForgeError("El adaptador se ha cerrado.")
        except PermissionError:
            raise FlashForgeError(
                "No se pudo iniciar el adaptador por falta de permisos. Revisa los permisos del plugin "
                "para ejecutar Python y abrir los canales de comunicación. "
                "Si acabas de concederlos, vuelve a intentarlo."
            ) from None
        except FileNotFoundError:
            raise FlashForgeError("No se pudo iniciar el adaptador. Revisa la ruta de Python 3.12 o posterior.") from None
        except OSError as error:
            raise FlashForgeError(f"El sistema no pudo iniciar el adaptador (código {error.errno}).") from None

    def request(self, command, *, wait_timeout=None, on_progress=None, **arguments):
        with self._lock:
            self._start()
            process = self.process
            try:
                payload = (json.dumps({"command": command, **arguments}) + "\n").encode()
                with self._write_lock:
                    process.stdin.write(payload)
                    process.stdin.flush()
                deadline = time.monotonic() + (self.timeout if wait_timeout is None else wait_timeout)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        while b"\n" not in self._buffer:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or not selector.select(remaining):
                                raise FlashForgeError("El SDK tardó demasiado. La sesión se ha cerrado.")
                            chunk = os.read(process.stdout.fileno(), 65536)
                            if not chunk:
                                raise FlashForgeError("El proceso del SDK terminó. Inicia sesión de nuevo.")
                            self._buffer += chunk
                            if len(self._buffer) > 2 * 1024 * 1024:
                                raise FlashForgeError("Respuesta del SDK demasiado grande.")
                        line, self._buffer = self._buffer.split(b"\n", 1)
                        response = json.loads(line)
                        if response.get("event") == "print_progress":
                            if on_progress:
                                on_progress(response)
                            continue
                        break
            except (OSError, ValueError, FlashForgeError) as error:
                self.close()
                if isinstance(error, FlashForgeError):
                    raise
                raise FlashForgeError("No se pudo comunicar con el adaptador SDK.") from None
            self.account_state = response.get("account_state", {})
            if not response.get("ok"):
                raise FlashForgeError(response.get("error", "Error del SDK."))
            return response["result"]

    def cancel_upload(self):
        process = self.process
        if process is None or process.poll() is not None:
            raise FlashForgeError("No hay una subida activa.")
        try:
            with self._write_lock:
                process.stdin.write(b'{"command":"cancel_upload"}\n')
                process.stdin.flush()
        except (OSError, ValueError):
            raise FlashForgeError("No se pudo solicitar la cancelación. Comprueba la impresora.") from None

    def shutdown(self):
        self._stopped.set()
        self.close()

    def close(self):
        process, self.process = self.process, None
        self._buffer = b""
        if process is None:
            return
        if process.poll() is None:
            try:
                with self._write_lock:
                    process.stdin.write(b'{"command":"close"}\n')
                    process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=.3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=.3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=.5)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
