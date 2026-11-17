"""Private JSON-lines worker. Native failures stay outside OrcaSlicer's process."""

import json
import os
import queue
import sys
import threading
import uuid

from .model import FlashForgeError


def main():
    # Keep RPC separate from any printf performed by the native vendor library.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    from .sdk import CloudSession, probe
    from .cloud_session import IndependentSession
    from .jobs import JobStore, validate_options
    from .sdk_print import PrintTransport
    from .credentials import CredentialError, SessionVault
    from .camera_server import CameraServer
    session = None
    vault = SessionVault()
    jobs = JobStore()
    stored_job = None
    camera = None
    requests = queue.Queue()
    output_lock = threading.Lock()

    def emit(value):
        with output_lock:
            output.write(json.dumps(value, allow_nan=False) + "\n")
            output.flush()

    def read_requests():
        pending_cancel = None
        try:
            for line in sys.stdin:
                if len(line) > 65536:
                    break
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError()
                except ValueError:
                    requests.put(({"command": "invalid"}, threading.Event()))
                    continue
                if request.get("command") == "cancel_upload":
                    if pending_cancel:
                        pending_cancel.set()
                    continue  # One final response belongs to the original print request.
                cancelled = threading.Event()
                if request.get("command") == "commit_print":
                    pending_cancel = cancelled
                if request.get("command") == "close" and pending_cancel:
                    pending_cancel.set()
                requests.put((request, cancelled))
                request = line = None
        finally:
            if pending_cancel:
                pending_cancel.set()
            requests.put(({"command": "close"}, threading.Event()))

    threading.Thread(target=read_requests, daemon=True, name="FlashForge-IPC-reader").start()
    try:
        while True:
            request, cancelled = requests.get()
            try:
                command = request.get("command")
                if command == "probe":
                    _, version = probe(request["library"])
                    result = {"sdk_version": version, "supported": version == "3.4.0"}
                elif command == "login":
                    jobs.clear()
                    stored_job = None
                    if session:
                        session.close()
                        session = None
                    factory = IndependentSession if request.get("backend") == "experimental" else CloudSession
                    session = factory(request["library"], request["settings"])
                    vault.attach(session, request["username"], request.get("remember") is True)
                    result = session.login(request["username"], request["password"])
                    if "remember" in request and request["remember"] is False:
                        try:
                            vault.forget()
                        except CredentialError:
                            vault.warning = "La sesión anterior no se pudo borrar del llavero. La reconexión automática está desactivada."
                    result.update(vault.info())
                elif command == "restore_login":
                    jobs.clear()
                    stored_job = None
                    if session:
                        session.close()
                        session = None
                    factory = IndependentSession if request.get("backend") == "experimental" else CloudSession
                    session, result = vault.restore(factory, request["library"], request["settings"],
                                                    request.get("expected_username", ""))
                    result.update(vault.info())
                elif command == "forget_account":
                    jobs.clear()
                    stored_job = None
                    if session:
                        session.close()
                        session = None
                    vault.forget()
                    result = {"forgotten": True}
                elif command == "camera_server":
                    if request.get("cloud"):
                        if not session:
                            raise FlashForgeError("Inicia sesión en FlashForge.")
                        if isinstance(session, IndependentSession):
                            from .cloud_controls import dispatch
                            dispatch(session, request.get("device_id", ""), "camera", "open")
                        else:
                            if session._transport is None:
                                session._transport = PrintTransport(session)
                            session._transport.open_camera(request.get("device_id", ""))
                    if camera is None:
                        camera = CameraServer()
                    result = {"url": camera.url, "camera_url": session.status(request.get("device_id", "")).get("camera_url", "") if session else ""}
                elif command == "camera_stop":
                    if camera:
                        camera.stop_streams()
                    result = {"stopped": True}
                elif command == "camera_fallback":
                    if not isinstance(session, IndependentSession):
                        raise FlashForgeError("Inicia sesión en FlashForge.")
                    if camera is None:
                        camera = CameraServer()
                    status = session.status(request.get("device_id", ""))
                    result = {"url": camera.fallback(status.get("camera_url", ""))}
                elif command == "close":
                    break
                elif session and command == "devices":
                    result = session.devices()
                elif session and command == "status":
                    result = session.status(request.get("device_id", ""))
                elif session and command == "recordings":
                    result = session.recordings(request.get("device_id", ""))
                elif session and command == "stored_files":
                    if not isinstance(session, IndependentSession):
                        raise FlashForgeError("Esta acción requiere la biblioteca experimental.")
                    result = session.stored_files(request.get("device_id", ""))
                elif session and command == "light":
                    if not isinstance(session, IndependentSession):
                        raise FlashForgeError("Esta acción requiere la biblioteca experimental.")
                    from .cloud_controls import dispatch
                    result = dispatch(session, request.get("device_id", ""), "light", request.get("value"))
                elif session and command == "cancel_print":
                    if not isinstance(session, IndependentSession):
                        raise FlashForgeError("Esta acción requiere la biblioteca experimental.")
                    from .cloud_controls import cancel_print
                    result = cancel_print(session, request.get("device_id", ""), request.get("job_id", ""))
                elif session and command == "move":
                    if isinstance(session, IndependentSession):
                        raise FlashForgeError("El movimiento aún no está disponible con la biblioteca experimental.")
                    if session._transport is None:
                        session._transport = PrintTransport(session)
                    session._transport.move(request.get("device_id", ""), request.get("axis"), request.get("delta"))
                    result = {"dispatched": True}
                elif session and command == "discard_print":
                    jobs.clear()
                    stored_job = None
                    result = {"discarded": True}
                elif session and command == "prepare_stored":
                    if not isinstance(session, IndependentSession):
                        raise FlashForgeError("Esta acción requiere la biblioteca experimental.")
                    device_id = request.get("device_id", "")
                    device = next((d for d in session.devices() if d["devId"] == device_id), None)
                    if not device:
                        raise FlashForgeError("La impresora seleccionada no pertenece a la sesión actual.")
                    entries = [entry for entry in session.stored_files(device_id) if entry["name"] == request.get("name")]
                    if len(entries) != 1 or not entries[0]["can_repeat"]:
                        raise FlashForgeError("No hay datos suficientes o el archivo ya no está disponible.")
                    jobs.clear()
                    entry = entries[0]
                    stored_job = {"id": uuid.uuid4().hex, "device_id": device_id, "name": entry["name"],
                                  "tools": [f["toolId"] for f in entry["filaments"]], "plate": 1}
                    result = {**stored_job, "filaments": entry["filaments"], "size": None,
                              "profile": request.get("profile", ""), "device": device,
                              "has_thumbnail": bool(entry["thumbnail"]), "preview": entry["thumbnail"],
                              "stored_file": True}
                elif session and command == "prepare_print":
                    stored_job = None
                    device_id = request.get("device_id", "")
                    device = next((d for d in session.devices() if d["devId"] == device_id), None)
                    if not device:
                        raise FlashForgeError("La impresora seleccionada no pertenece a la sesión actual.")
                    result = jobs.stage(request.get("path", ""), device_id=device_id,
                                        profile=request.get("profile", ""), plate=request.get("plate", 1))
                    result["device"] = device
                elif session and command == "commit_print":
                    if request.get("confirmed") is not True:
                        raise FlashForgeError("Confirma archivo, impresora y opciones antes de imprimir.")
                    if stored_job and stored_job["id"] == request.get("job_id") and stored_job["device_id"] == request.get("device_id"):
                        job = stored_job
                        stored_job = None
                        options = validate_options(request.get("options"), job["tools"])
                        if options["plate"] != job["plate"]:
                            raise FlashForgeError("La preparación ha cambiado. Revisa de nuevo el archivo.")
                        from .cloud_send import send_existing
                        result = send_existing(session, job, options, emit)
                        response = {"ok": True, "result": result, "account_state": vault.info()}
                        request.clear()
                        request = None
                        emit(response)
                        continue
                    stored_job = None
                    job, options = jobs.take(request.get("job_id"), request.get("device_id"), request.get("options"))
                    try:
                        if isinstance(session, IndependentSession):
                            from .cloud_send import send
                            result = send(session, job, options, cancelled, emit)
                        else:
                            if session._transport is None:
                                session._transport = PrintTransport(session)
                            result = session._transport.send(job, options, cancelled, emit)
                    finally:
                        jobs.clear()
                else:
                    raise FlashForgeError("Inicia sesión en FlashForge.")
                response = {"ok": True, "result": result}
            except FlashForgeError as error:
                response = {"ok": False, "error": str(error)}
            except Exception:
                response = {"ok": False, "error": "El adaptador SDK no pudo completar la operación."}
            response["account_state"] = vault.info()
            # Passwords and raw native responses never cross the return channel.
            request.clear()
            request = None
            emit(response)
    finally:
        if camera:
            camera.close()
        if session:
            session.close()
        jobs.close()


if __name__ == "__main__":
    main()
