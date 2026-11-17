import ctypes
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from orca_flashforge import sdk
from orca_flashforge import sdk_print
from orca_flashforge.bridge import SdkBridge
from orca_flashforge.camera_server import CameraServer
from orca_flashforge.jobs import JobStore
from orca_flashforge.credentials import SessionVault
from orca_flashforge.lan import LanClient, validate_host
from orca_flashforge.model import FlashForgeError, camera_url, demo_status, normalize

ROOT = Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def test_launch_errors_distinguish_permissions_from_missing_python_without_leaking_details(self):
        for error, expected in ((PermissionError("synthetic-sensitive-detail"), "falta de permisos"),
                                (FileNotFoundError("synthetic-sensitive-detail"), "ruta de Python"),
                                (OSError(8, "synthetic-sensitive-detail"), "código 8")):
            with self.subTest(error=type(error).__name__):
                bridge = SdkBridge()
                with patch("orca_flashforge.bridge.subprocess.Popen", side_effect=error):
                    with self.assertRaisesRegex(FlashForgeError, expected) as result:
                        bridge.request("probe", library="unused")
                self.assertNotIn("synthetic-sensitive-detail", str(result.exception))
                self.assertIsNone(bridge.process)

    def test_camera_server_lifecycle_in_worker(self):
        bridge = SdkBridge(sys.executable)
        try:
            first = bridge.request("camera_server")
            self.assertEqual(first, bridge.request("camera_server"))
            from urllib.request import urlopen
            with urlopen(first["url"], timeout=2) as response:
                self.assertIn(b"hls.js", response.read())
        finally:
            bridge.shutdown()


class CameraServerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg no disponible')
    def test_fallback_decodes_synthetic_h264_without_putting_source_in_arguments(self):
        from urllib.request import urlopen
        class SilentHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'sample.m3u8')
            generated = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=64x64:r=5',
                                        '-t', '1', '-threads', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                                        '-f', 'hls', '-hls_time', '1', path],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if generated.returncode:
                self.skipTest('No hay codificador H.264 para generar el vídeo simulado')
            source = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SilentHandler, directory=directory))
            threading.Thread(target=source.serve_forever, daemon=True).start()
            camera = CameraServer()
            try:
                url = camera.fallback(f'http://127.0.0.1:{source.server_port}/sample.m3u8?synthetic-secret')
                with patch('orca_flashforge.camera_server.subprocess.Popen', wraps=subprocess.Popen) as launched:
                    with urlopen(url, timeout=30) as response:
                        self.assertIn('multipart/x-mixed-replace', response.headers['Content-Type'])
                        boundary = response.headers['Content-Type'].split('boundary=', 1)[1].encode()
                        self.assertTrue(response.read(len(boundary) + 2).startswith(b'--' + boundary))
                    self.assertNotIn('synthetic-secret', repr(launched.call_args.args[0]))
            finally:
                camera.close()
                source.shutdown()
                source.server_close()

    def test_only_random_path_on_loopback_is_served(self):
        from urllib.error import HTTPError
        from urllib.request import urlopen
        from urllib.parse import urlsplit
        server = CameraServer()
        try:
            parsed = urlsplit(server.url)
            self.assertEqual(parsed.hostname, "127.0.0.1")
            with urlopen(server.url, timeout=2) as response:
                page = response.read().decode()
                self.assertNotIn("__HLS_LIBRARY__", page)
                self.assertIn("camera-play", page)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            with self.assertRaises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{parsed.port}/wrong", timeout=2)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            server.close()


class StatusTests(unittest.TestCase):
    def test_machine_info_keeps_missing_values_unknown_and_wan_hours(self):
        result = normalize({'cumulativePrintTime': 12.5, 'cumulativeFilament': 8.2,
                            'remainingDiskSpace': 4.5, 'nozzleModel': '0.4', 'measure': '220x220', 'lidar': 1},
                           mode='cloud', account_model='Creator 5 Pro')
        info = result['machine_info']
        self.assertEqual(info['print_hours'], 12.5)
        self.assertEqual(info['filament_metres'], 8.2)
        self.assertEqual(info['free_storage_gb'], 4.5)
        self.assertTrue(result['flow_calibration_available'])
        self.assertIsNone(normalize({}, mode='cloud')['machine_info']['print_hours'])
        self.assertFalse(normalize({'lidar': True}, mode='cloud')['flow_calibration_available'])
    def test_job_thumbnail_and_identifier_are_real_status_fields(self):
        result = normalize({'jobId': 'job-1', 'printFileThumbUrl': 'https://example.test/preview.png'}, mode='cloud')
        self.assertEqual(result['job_id'], 'job-1')
        self.assertEqual(result['job_thumbnail'], 'https://example.test/preview.png')
        self.assertEqual(normalize({'printFileThumbUrl': 'javascript:alert(1)'}, mode='cloud')['job_thumbnail'], '')
    def test_creator_nozzles_pair_materials_by_received_order_not_slot_number(self):
        for pid in (0x0028, 0x0029):
            with self.subTest(pid=pid):
                result = normalize({"pid": pid, "nozzleTemps": [205, 0], "nozzleTargetTemps": [210, 0],
                    "platTemp": 60, "matlStationInfo": {"slotInfos": [
                        {"slotId": 42, "hasFilament": 1, "materialName": "PLA", "materialColor": "#eeeeee"},
                        {"slotId": 7, "hasFilament": 0}]}}, mode="cloud")
                self.assertEqual([t.get("filament_slot_id") for t in result["temperatures"]], [42, 7, None])
                self.assertEqual(result["temperatures"][1]["current"], 0)
                self.assertFalse(result["slots"][1]["has_filament"])

    def test_unknown_models_and_ambiguous_material_lists_do_not_invent_pairings(self):
        base = {"pid": 0x0029, "nozzleTemps": [205, 25], "matlStationInfo": {"slotInfos": [
            {"slotId": 1, "hasFilament": 1}, {"slotId": 2, "hasFilament": 0}]}}
        for detail in ({**base, "pid": 0x0026}, {**base, "pid": None},
                       {**base, "matlStationInfo": {"slotInfos": [{"slotId": 1}]}},
                       {**base, "matlStationInfo": {"slotInfos": [{"slotId": 1}, {"slotId": 1}]}},
                       {**base, "matlStationInfo": {"slotInfos": [{"slotId": "1"}, {"slotId": 2}]}}):
            with self.subTest(detail=detail):
                result = normalize(detail, mode="cloud")
                self.assertFalse(any("filament_slot_id" in t for t in result["temperatures"]))

    def test_creator_account_model_allows_matching_nozzles_when_pid_is_missing(self):
        detail = {"nozzleTemps": [200, 25], "matlStationInfo": {"slotInfos": [
            {"slotId": 42, "hasFilament": 1}, {"slotId": 7, "hasFilament": 0}]}}
        result = normalize(detail, mode="cloud", account_model="Creator 5 Pro")
        self.assertEqual([t.get("filament_slot_id") for t in result["temperatures"]], [42, 7])
        detail["pid"] = 0x0026
        result = normalize(detail, mode="cloud", account_model="Creator 5 Pro")
        self.assertFalse(any("filament_slot_id" in t for t in result["temperatures"]))

    def test_absence_never_becomes_a_measurement(self):
        status = normalize({}, mode="lan")
        self.assertIsNone(status["progress"])
        self.assertIsNone(status["duration_seconds"])
        self.assertEqual(status["temperatures"], [])
        self.assertEqual(status["slots"], [])

    def test_firmware_variants_and_invalid_values(self):
        status = normalize({"printProgress": "0.5", "platTemp": "NaN", "platTargetTemp": "60",
                            "MatlStationInfo": {"SlotInfos": [{"slotId": 2, "hasFilament": "1"}]}}, mode="lan")
        self.assertEqual(status["progress"], 50)
        self.assertIsNone(status["temperatures"][0]["current"])
        self.assertEqual(status["temperatures"][0]["target"], 60)
        self.assertTrue(status["slots"][0]["has_filament"])
        json.dumps(status, allow_nan=False)

    def test_camera_rejects_executable_and_foreign_lan_urls(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "http://user:pass@printer:8080/stream",
                    "http://other-printer/stream"):
            self.assertEqual(camera_url(url, "printer"), "")
        self.assertEqual(camera_url("http://printer:8080/?action=stream", "printer"),
                         "http://printer:8080/?action=stream")

    def test_demo_is_labeled(self):
        status = demo_status()
        self.assertEqual(status["mode"], "demo")
        self.assertIn("DEMOSTRACIÓN", status["name"])

    def test_host_cannot_smuggle_credentials_or_url_parts(self):
        for host in ("http://printer", "printer:8898", "user@printer", "printer/path", "printer\r\nX:secret"):
            with self.assertRaises(FlashForgeError):
                validate_host(host)
        self.assertEqual(validate_host("192.168.1.100"), "192.168.1.100")
        self.assertEqual(validate_host("::1"), "::1")


class LanTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = {"code": 0, "detail": {"name": "simulated", "status": "ready", "platTemp": 23}}
        self.http_status = 200
        case = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                case.calls.append((self.path, json.loads(body)))
                self.send_response(case.http_status)
                self.end_headers()
                self.wfile.write(json.dumps(case.response).encode())

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = LanClient("127.0.0.1", "SYNTHETIC", "secret", port=self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_contract(self):
        result = self.client.status()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.calls, [("/detail", {"serialNumber": "SYNTHETIC", "checkCode": "secret"})])

    def test_api_rejection_even_with_http_200(self):
        self.response = {"code": 1001, "message": "Do not echo secret"}
        with self.assertRaises(FlashForgeError) as result:
            self.client.status()
        self.assertNotIn("secret", str(result.exception))

    def test_redirect_does_not_forward_credentials(self):
        self.http_status = 302
        with self.assertRaisesRegex(FlashForgeError, "HTTP 302"):
            self.client.status()
        self.assertEqual(len(self.calls), 1)

    def test_success_without_detail_is_not_a_connected_printer(self):
        self.response = {"code": 0}
        with self.assertRaises(FlashForgeError):
            self.client.status()


@unittest.skipUnless(shutil.which("c++"), "C++ compiler needed for native ABI checks")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.library = cls.root / "libFakeFlashNetwork.so"
        subprocess.run(["c++", "-shared", "-fPIC", "-I", str(ROOT / "native"),
                        str(ROOT / "tests/fake_sdk.cpp"), "-o", str(cls.library)], check=True, capture_output=True)
        cls.settings = cls.root / "SYNTHETIC.DAT"
        cls.settings.write_text("test fixture; not a vendor settings file")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_every_used_struct_matches_public_header(self):
        structures = {"LogSettings": "fnet_log_settings_t", "Token": "fnet_token_data_t",
                      "UpdateInfo": "fnet_update_info_t", "WanDevice": "fnet_wan_dev_info_t",
                      "Slot": "fnet_matl_slot_info_t", "Station": "fnet_matl_station_info_t",
                      "IndependentMaterial": "fnet_indep_matl_info_t", "Detail": "fnet_dev_detail_t",
                      "UserProfile": "fnet_user_profile_t"}
        printing = {"MaterialMapping": "fnet_material_mapping_t", "SendGcode": "fnet_send_gcode_data_t",
                    "CloudGcode": "fnet_clound_gcode_data_t", "CloudJob": "fnet_clound_job_data_t",
                    "AddJobResult": "fnet_add_clound_job_result_t", "BindAccountResult": "fnet_bind_account_relp_result_t",
                    "MqttConfig": "fnet_mqtt_config_t", "ConnectionSettings": "fnet_conn_settings_t",
                    "WriteMulti": "fnet_conn_write_multi_data_t", "WriteResult": "fnet_conn_write_multi_result_t",
                    "ReadData": "fnet_conn_read_data_t", "CameraControl": "fnet_camera_stream_ctrl_t",
                    "WriteData": "fnet_conn_write_data_t"}
        code = ['#include "FlashNetwork-3.4.0.h"', '#include <cstdio>', '#include <cstddef>', 'int main() {']
        expected = {}
        for python_name, c_name in {**structures, **printing}.items():
            struct = getattr(sdk_print if python_name in printing else sdk, python_name)
            expected[python_name] = ctypes.sizeof(struct)
            code.append(f'printf("{python_name} %zu\\n", sizeof({c_name}));')
            for field, _ in struct._fields_:
                key = f"{python_name}.{field}"
                expected[key] = getattr(struct, field).offset
                code.append(f'printf("{key} %zu\\n", offsetof({c_name}, {field}));')
        code.append('}')
        source = self.root / "layout.cpp"
        source.write_text("\n".join(code))
        program = self.root / "layout"
        subprocess.run(["c++", "-I", str(ROOT / "native"), str(source), "-o", str(program)],
                       check=True, capture_output=True)
        output = subprocess.check_output([str(program)], text=True)
        actual = {key: int(value) for key, value in (line.split() for line in output.splitlines())}
        self.assertEqual(expected, actual)

    def test_login_refresh_devices_and_detail_release_native_allocations(self):
        session = sdk.CloudSession(self.library, self.settings)
        try:
            session.login("synthetic@example.invalid", "synthetic-password")
            session.deadline = 0
            devices = session.devices()
            self.assertEqual(devices[0]["devId"], "synthetic-device")
            result = session.status("synthetic-device")
            self.assertEqual(result["progress"], 42)
            self.assertEqual(len(result["temperatures"]), 6)
            self.assertEqual(result["slots"][0]["material"], "PLA simulado")
            self.assertEqual(session.lib.test_outstanding(), 0)
        finally:
            session.close()

    def test_failed_authentication_releases_native_message(self):
        session = sdk.CloudSession(self.library, self.settings)
        try:
            with self.assertRaises(FlashForgeError) as result:
                session.login("synthetic@example.invalid", "bad")
            self.assertNotIn("sensitive", str(result.exception))
            self.assertEqual(session.lib.test_outstanding(), 0)
        finally:
            session.close()

    def test_real_worker_keeps_native_output_out_of_rpc(self):
        bridge = SdkBridge(sys.executable, timeout=5)
        try:
            bridge.request("login", library=str(self.library), settings=str(self.settings),
                           username="synthetic@example.invalid", password="synthetic-password")
            self.assertEqual(bridge.request("status", device_id="synthetic-device")["progress"], 42)
            self.assertNotIn("synthetic-password", " ".join(bridge.process.args))
            # Simulate a native process dying between requests. The restarted worker has no session.
            bridge.process.kill()
            bridge.process.wait()
            with self.assertRaisesRegex(FlashForgeError, "Inicia sesión"):
                bridge.request("status", device_id="synthetic-device")
        finally:
            bridge.close()

    def test_declared_permissions_allow_real_worker_under_host_audit_contract(self):
        # Run an audit hook in a separate interpreter: hooks cannot be removed.
        # Orca audits Popen's argv repr. The parent must not open /dev/null.
        code = '''
import os, sys, types
host = types.ModuleType("orca")
host.pages = types.SimpleNamespace(PagesPluginCapabilityBase=object)
host.printer_agent = types.SimpleNamespace(PrinterAgentBase=object)
host.base = object
host.plugin = lambda cls: cls
grants = {}
host.request_permissions = lambda **kwargs: grants.update(kwargs)
host.register_capability = lambda cls: None
sys.modules["orca"] = host
from orca_flashforge.plugin import FlashForgePlugin
from orca_flashforge.bridge import SdkBridge
FlashForgePlugin().register_capabilities()
assert grants.get("threading") is True
def audit(event, args):
    if event == "open" and args[0] == os.devnull:
        raise PermissionError("parent must not open stderr sink")
    if event == "subprocess.Popen":
        if str(args[1]) not in grants.get("process", []):
            raise PermissionError("ungranted worker command")
sys.addaudithook(audit)
bridge = SdkBridge()
try:
    assert bridge.request("probe", library=sys.argv[1]) == {"sdk_version": "3.4.0", "supported": True}
finally:
    bridge.close()
'''
        subprocess.run([sys.executable, "-c", code, str(self.library)], check=True, capture_output=True, timeout=10)

    def test_unvalidated_sdk_is_rejected_before_init(self):
        with patch.dict(os.environ, {"FAKE_SDK_VERSION": "3.4.3"}):
            with self.assertRaisesRegex(FlashForgeError, "no validado"):
                sdk.CloudSession(self.library, self.settings)

    def test_saved_session_uses_refresh_without_password_and_persists_each_rotation(self):
        from unittest.mock import Mock
        session = sdk.CloudSession(self.library, self.settings)
        store = Mock()
        vault = SessionVault(store)
        try:
            previous_logins = session.lib.test_password_logins()
            previous_refreshes = session.lib.test_refreshes()
            vault.attach(session, "synthetic@example.invalid", True)
            result = session.restore("synthetic-saved-refresh", "synthetic@example.invalid")
            self.assertTrue(result["connected"])
            self.assertNotIn("token", json.dumps(result))
            self.assertEqual(session.lib.test_password_logins(), previous_logins)
            self.assertEqual(session.lib.test_refreshes(), previous_refreshes + 1)
            store.save.assert_called_once_with("synthetic@example.invalid", b"synthetic-refresh-token")
            session.deadline = 0
            session.devices()
            self.assertEqual(store.save.call_count, 2)
            self.assertEqual(session.lib.test_refreshes(), previous_refreshes + 2)
        finally:
            session.close()
        self.assertEqual(session.lib.test_outstanding(), 0)

    def test_camera_open_uses_selected_account_device_and_documented_command(self):
        session = sdk.CloudSession(self.library, self.settings)
        try:
            session.login("synthetic@example.invalid", "good")
            transport = sdk_print.PrintTransport(session)
            session._transport = transport
            before = session.lib.test_camera_opens()
            transport.open_camera("synthetic-device")
            self.assertEqual(session.lib.test_camera_opens(), before + 1)
            with self.assertRaises(FlashForgeError):
                transport.open_camera("not-owned")
            self.assertEqual(session.lib.test_camera_opens(), before + 1)
        finally:
            session.close()
        self.assertEqual(session.lib.test_outstanding(), 0)

    def test_print_upload_creation_dispatch_and_callback_memory(self):
        import hashlib
        source = self.root / "print-fixture.gcode"
        source.write_text("T4\nG28\nG1 X10\n")
        store = JobStore()
        session = sdk.CloudSession(self.library, self.settings)
        try:
            session.login("synthetic@example.invalid", "synthetic-password")
            session.lib.test_reset_print()
            prepared = store.stage(str(source), device_id="synthetic-device", profile="Synthetic profile", plate=2)
            source.write_text("G1 X999\n")  # Sending must use the reviewed snapshot.
            options = {"plate": 2, "use_material_station": True, "bed_leveling": True,
                       "material_mappings": [{"toolId": 4, "slotId": 1, "materialName": "PLA simulado",
                                              "toolMaterialColor": "#ff0000", "slotMaterialColor": "#ffffff"}]}
            job, options = store.take(prepared["id"], "synthetic-device", options)
            transport = sdk_print.PrintTransport(session)
            session._transport = transport
            events = []
            result = transport.send(job, options, threading.Event(), events.append)
            self.assertTrue(result["dispatched"])
            self.assertFalse(result["printer_started_verified"])
            self.assertEqual((session.lib.test_uploads(), session.lib.test_jobs(), session.lib.test_dispatches()), (1, 1, 1))
            self.assertEqual(session.lib.test_slot(), 1)
            self.assertEqual(session.lib.test_plate(), 1)
            session.lib.test_md5.restype = ctypes.c_char_p
            self.assertEqual(session.lib.test_md5().decode(), hashlib.md5(b"T4\nG28\nG1 X10\n", usedforsecurity=False).hexdigest())
            self.assertEqual({event["phase"] for event in events}, {"uploading", "creating", "dispatching"})
            session.lib.test_emit_read_events.argtypes = [ctypes.c_void_p]
            session.lib.test_emit_read_events(transport.connection)
            self.assertEqual(session.lib.test_outstanding(), 1)  # Only the live MQTT connection remains.
        finally:
            session.close()
            store.close()
        self.assertEqual(session.lib.test_outstanding(), 0)

    def test_print_failures_release_memory_and_do_not_dispatch_after_rejection(self):
        source = self.root / "failure-fixture.gcode"
        source.write_text("G28\n")
        for phase, calls in (("upload", (1, 0, 0)), ("create", (1, 1, 0)),
                             ("busy", (1, 1, 0)), ("dispatch", (1, 1, 1))):
            with self.subTest(phase=phase), patch.dict(os.environ, {"FAKE_PRINT_FAIL": phase}):
                session = sdk.CloudSession(self.library, self.settings)
                store = JobStore()
                try:
                    session.login("synthetic@example.invalid", "synthetic-password")
                    session.lib.test_reset_print()
                    prepared = store.stage(str(source), device_id="synthetic-device")
                    job, options = store.take(prepared["id"], "synthetic-device", {})
                    session._transport = sdk_print.PrintTransport(session)
                    with self.assertRaises(FlashForgeError) as result:
                        session._transport.send(job, options, threading.Event(), lambda event: None)
                    self.assertEqual((session.lib.test_uploads(), session.lib.test_jobs(), session.lib.test_dispatches()), calls)
                    if phase == "dispatch":
                        self.assertIn("Comprueba la impresora", str(result.exception))
                finally:
                    session.close()
                    store.close()
                self.assertEqual(session.lib.test_outstanding(), 0)

    def test_cancelled_transfer_never_creates_a_job(self):
        source = self.root / "cancel-fixture.gcode"
        source.write_text("G28\n")
        session, store, cancel = sdk.CloudSession(self.library, self.settings), JobStore(), threading.Event()
        try:
            session.login("synthetic@example.invalid", "synthetic-password")
            session.lib.test_reset_print()
            prepared = store.stage(str(source), device_id="synthetic-device")
            job, options = store.take(prepared["id"], "synthetic-device", {})
            session._transport = sdk_print.PrintTransport(session)
            with self.assertRaisesRegex(FlashForgeError, "cancelada"):
                session._transport.send(job, options, cancel, lambda event: cancel.set())
            self.assertEqual(session.lib.test_jobs(), 0)
            self.assertEqual(session.lib.test_dispatches(), 0)
        finally:
            session.close()
            store.close()
        self.assertEqual(session.lib.test_outstanding(), 0)

    def test_worker_requires_review_confirmation_and_consumes_print_attempt(self):
        source = self.root / "worker-print-fixture.gcode"
        source.write_text("G28\n")
        bridge = SdkBridge(sys.executable, timeout=5)
        try:
            bridge.request("login", library=str(self.library), settings=str(self.settings),
                           username="synthetic@example.invalid", password="synthetic-password")
            with self.assertRaisesRegex(FlashForgeError, "no pertenece"):
                bridge.request("prepare_print", path=str(source), device_id="foreign-device")
            prepared = bridge.request("prepare_print", path=str(source), device_id="synthetic-device")
            with self.assertRaisesRegex(FlashForgeError, "Confirma"):
                bridge.request("commit_print", job_id=prepared["id"], device_id="synthetic-device", options={})
            progress = []
            self.assertTrue(bridge.request("commit_print", job_id=prepared["id"], device_id="synthetic-device",
                                           confirmed=True, options={}, on_progress=progress.append)["dispatched"])
            self.assertTrue(progress)
            with self.assertRaisesRegex(FlashForgeError, "caducado"):
                bridge.request("commit_print", job_id=prepared["id"], device_id="synthetic-device", confirmed=True, options={})
            discarded = bridge.request("prepare_print", path=str(source), device_id="synthetic-device")
            self.assertEqual(bridge.request("discard_print"), {"discarded": True})
            with self.assertRaisesRegex(FlashForgeError, "caducado"):
                bridge.request("commit_print", job_id=discarded["id"], device_id="synthetic-device", confirmed=True, options={})
        finally:
            bridge.close()

    def test_worker_can_cancel_upload_while_native_call_is_running(self):
        source = self.root / "worker-cancel-fixture.gcode"
        source.write_text("G28\n")
        with patch.dict(os.environ, {"FAKE_UPLOAD_SLOW": "1"}):
            bridge = SdkBridge(sys.executable, timeout=5)
            try:
                bridge.request("login", library=str(self.library), settings=str(self.settings),
                               username="synthetic@example.invalid", password="synthetic-password")
                prepared = bridge.request("prepare_print", path=str(source), device_id="synthetic-device")
                with self.assertRaisesRegex(FlashForgeError, "cancelada"):
                    bridge.request("commit_print", job_id=prepared["id"], device_id="synthetic-device", confirmed=True,
                                   options={}, on_progress=lambda event: bridge.cancel_upload())
                self.assertEqual(bridge.request("devices")[0]["devId"], "synthetic-device")
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
