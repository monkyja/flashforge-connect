"""Contract tests with a minimal host double; not a substitute for native OrcaSlicer loading."""

import importlib
import json
import os
import sys
import threading
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from orca_flashforge.model import FlashForgeError


class PageBase:
    def __init__(self):
        self.config = "{}"
        self.messages = []

    def get_config(self):
        return self.config

    def save_config(self, value):
        self.config = value
        return True

    def post_message(self, value):
        self.messages.append(value)


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = []
        host = types.ModuleType("orca")
        host.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PageBase)
        host.printer_agent = types.SimpleNamespace(PrinterAgentBase=PageBase,
            AgentInfo=lambda *args: types.SimpleNamespace(id=args[0]),
            FilamentSyncMode=types.SimpleNamespace(Pull="pull"))
        host.base = type("PluginPackage", (), {})
        host.plugin = lambda package: package
        host.request_permissions = lambda **kwargs: None
        host.register_capability = cls.registered.append
        cls.host_patch = patch.dict(sys.modules, {"orca": host})
        cls.host_patch.start()
        cls.package = importlib.reload(importlib.import_module("orca_flashforge"))
        cls.plugin = importlib.import_module("orca_flashforge.plugin")

    @classmethod
    def tearDownClass(cls):
        cls.host_patch.stop()

    def test_registers_real_pages_capability_and_packages_html(self):
        self.plugin.FlashForgePlugin().register_capabilities()
        self.assertIn(self.plugin.FlashForgePage, self.registered)
        self.assertEqual(self.registered, [self.plugin.FlashForgePage])
        self.assertEqual(self.plugin.DEFAULTS["mode"], "cloud")
        page = self.plugin.FlashForgePage()
        html = page.get_ui()
        self.assertIn('id="username"', html)
        self.assertIn('id="temperatures"', html)
        self.assertIn('window.orca.postMessage', html)
        self.assertIn('Plugin no oficial · v0.1.0', html)
        self.assertNotIn('__PLUGIN_VERSION__', html)
        self.assertLess(html.index('class="card status-card"'), html.index('id="transfer"'))
        self.assertLess(html.index('id="transfer"'), html.index('class="camera-card"'))

    def test_read_grant_targets_only_orcaslicer_model_tree(self):
        with patch.object(self.plugin.orca, "request_permissions") as request, \
             patch.object(self.plugin.orca, "register_capability"), \
             patch.dict(os.environ, {"TMPDIR": "/tmp"}):
            self.plugin.FlashForgePlugin().register_capabilities()
        self.assertEqual(request.call_args.kwargs["fs_read"],
                         [f"/tmp/orcaslicer_{os.getuid()}/orcaslicer_model/*"])
        self.assertNotIn("fs_readwrite", request.call_args.kwargs)

    def test_existing_configuration_restores_with_experimental_backend(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"cloud_backend": "official", "remember_account": True,
                                  "account_name": "synthetic"})
        page.on_message = Mock()
        page.on_load()
        self.assertEqual(json.loads(page.config)["cloud_backend"], "experimental")
        self.assertEqual(json.loads(page.config)["account_name"], "synthetic")
        page.on_message.assert_called_once_with({"command": "restore_login"})

    def test_wheel_entry_package_exports_plugin_class(self):
        self.assertIs(self.package.FlashForgePlugin, self.plugin.FlashForgePlugin)

    def test_config_never_sends_lan_access_code_back_to_html(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"access_code": "synthetic-secret"})
        page.on_message({"command": "config"})
        self.assertNotIn("synthetic-secret", json.dumps(page.messages))
        self.assertTrue(page.messages[0]["config"]["has_access_code"])
        self.assertEqual(page.messages[0]["status_protocol"], 1)

    def test_status_with_background_flag_uses_the_dedicated_refresh_path(self):
        page = self.plugin.FlashForgePage()
        page._start_status_refresh = Mock()
        message = {"command": "status", "background": True, "request_id": "page:1", "device_id": "synthetic"}
        page.on_message(message)
        page._start_status_refresh.assert_called_once_with(message)
        self.assertFalse(page._busy.locked())

    def test_background_refresh_returns_immediately_and_has_its_own_completion(self):
        page = self.plugin.FlashForgePage()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def read_status(config, device_id):
            started.set()
            if not release.wait(3):
                raise RuntimeError("Test did not release the simulated read")
            return {"status": "ready"}
        page._read_status = read_status
        original_post = page._post
        def post(message):
            if message["command"] == "status_refresh":
                self.assertFalse(page._busy.locked())
            original_post(message)
            if message["command"] == "status_refresh":
                finished.set()
        page._post = post
        try:
            page.on_message({"command": "refresh_status", "request_id": 7, "device_id": "synthetic"})
            self.assertTrue(started.wait(1))
            self.assertTrue(page._busy.locked())
            page.on_message({"command": "config"})
            self.assertEqual(page.messages[-1]["command"], "config")
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
        result = next(m for m in page.messages if m["command"] == "status_refresh")
        self.assertEqual(result["request_id"], 7)
        self.assertEqual(result["device_id"], "synthetic")
        self.assertEqual(result["status"], {"status": "ready"})
        self.assertNotIn("done", [m["command"] for m in page.messages])

    def test_refresh_skips_an_active_operation_without_releasing_it(self):
        page = self.plugin.FlashForgePage()
        page._busy.acquire()
        with patch.object(self.plugin.threading, "Thread") as thread:
            page.on_message({"command": "refresh_status", "request_id": 8, "device_id": "synthetic"})
        thread.assert_not_called()
        self.assertTrue(page._busy.locked())
        self.assertEqual(page.messages, [{"command": "status_refresh", "request_id": 8,
                                         "device_id": "synthetic", "skipped": True}])
        page._busy.release()

    def test_refresh_errors_and_thread_denial_do_not_change_foreground_state(self):
        page = self.plugin.FlashForgePage()
        page._read_status = Mock(side_effect=FlashForgeError("Lectura simulada fallida"))
        page._busy.acquire()
        page._upload_phase = "uploading"
        page._run_status_refresh(page._settings(), {"command": "status_refresh", "request_id": 9, "device_id": "synthetic"})
        self.assertFalse(page._busy.locked())
        self.assertEqual(page.messages[-1]["error"], "Lectura simulada fallida")
        self.assertEqual(page._upload_phase, "uploading")
        with patch.object(self.plugin.threading, "Thread", side_effect=PermissionError("denied")):
            page.on_message({"command": "refresh_status", "request_id": 10})
        self.assertFalse(page._busy.locked())
        self.assertEqual(page.messages[-1]["command"], "status_refresh")
        self.assertEqual(page.messages[-1]["request_id"], 10)

    def test_saving_rejects_password_and_forces_experimental_account(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"access_code": "synthetic-lan-code"})
        page._busy.acquire()
        page._run({"command": "save", "config": {"mode": "lan", "password": "must-not-persist", "access_code": ""}})
        self.assertNotIn("must-not-persist", page.config)
        self.assertEqual(json.loads(page.config)["access_code"], "synthetic-lan-code")
        self.assertEqual(json.loads(page.config)["mode"], "cloud")
        self.assertEqual(json.loads(page.config)["cloud_backend"], "experimental")

    def test_unloaded_page_cannot_start_an_sdk_process(self):
        page = self.plugin.FlashForgePage()
        page.on_unload()
        with self.assertRaisesRegex(Exception, "descargado"):
            page._cloud({"python": sys.executable})

    def test_profile_binding_stores_only_the_pair_and_keeps_defaults_isolated(self):
        page = self.plugin.FlashForgePage()
        page._devices = [{"devId": "device", "name": "Synthetic"}]
        page._busy.acquire()
        page._run({"command": "bind_profile", "device_id": "device", "_profile": {"name": "Creator profile"}})
        self.assertEqual(json.loads(page.config)["profile_bindings"], {"Creator profile": "device"})
        self.assertEqual(self.plugin.FlashForgePage().get_default_config()["profile_bindings"], {})
        self.assertNotIn("password", page.config)

    def test_unknown_device_cannot_be_bound_to_a_profile(self):
        page = self.plugin.FlashForgePage()
        page._busy.acquire()
        page._run({"command": "bind_profile", "device_id": "foreign", "_profile": {"name": "Creator profile"}})
        self.assertEqual(page.config, "{}")
        self.assertTrue(any(m.get("command") == "error" for m in page.messages))

    def test_page_preparation_refreshes_lfs_without_a_prior_status_click(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._devices = [{"devId": "device", "model": "Creator 5 Pro"}]
        page._bridge.request.side_effect = [
            {"id": "review", "tools": [3], "device": {"devId": "device"},
             "filaments": [{"toolId": 3, "material": "PLA", "color": "#FF0000"}]},
            {"slots": [{"id": 17, "material": "PLA", "color": "#FF0000", "has_filament": True}]}]
        page._busy.acquire()
        page._run({"command": "prepare_print", "device_id": "device", "path": "synthetic.gcode", "_profile": {"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1}})
        prepared = next(m["job"] for m in page.messages if m["command"] == "print_prepared")
        self.assertEqual(prepared["suggested_mappings"][0]["slotId"], 17)
        self.assertEqual(prepared["suggested_mappings"][0]["toolId"], 3)

    def test_stored_file_review_loads_current_slots_without_sending_print(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._devices = [{"devId": "device", "model": "Creator 5 Pro"}]
        page._bridge.request.side_effect = [
            {"id": "stored-review", "tools": [2], "stored_file": True, "device": {"devId": "device"}},
            {"slots": [{"id": 7, "material": "PLA", "color": "#FFFFFF", "has_filament": True}]}]
        page._busy.acquire()
        page._run({"command": "prepare_stored", "name": "saved.gcode", "device_id": "device",
                   "_profile": {"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1}})
        prepared = next(message['job'] for message in page.messages if message['command'] == 'print_prepared')
        self.assertEqual(prepared['slots'][0]['id'], 7)
        self.assertEqual([call.args[0] for call in page._bridge.request.call_args_list], ['prepare_stored', 'status'])
        self.assertEqual(page._prepared_id, 'stored-review')

    def test_preparation_rejects_wrong_model_before_contacting_cloud(self):
        for model, backend in (("Other printer", "experimental"), ("Other printer", "official")):
            page = self.plugin.FlashForgePage()
            page.config = json.dumps({"mode": "cloud", "cloud_backend": backend})
            page._devices = [{"devId": "device", "model": model}]
            page._bridge = Mock()
            page._busy.acquire()
            page._run({"command": "prepare_print", "device_id": "device", "path": "synthetic.gcode",
                       "_profile": {"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1}})
            page._bridge.request.assert_not_called()
            self.assertTrue(any(m.get("command") == "error" for m in page.messages))

    def test_selected_device_is_not_overridden_by_old_profile_binding(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"device_id": "chosen", "profile_bindings": {"Creator": "old"}})
        page._profile_context = Mock(return_value={"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1})
        page.on_message({"command": "config"})
        self.assertEqual(page.messages[-1]["config"]["device_id"], "chosen")

    def test_move_does_not_dispatch_with_experimental_library(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._busy.acquire()
        page._run({"command": "move", "axis": "x", "delta": 1})
        page._bridge.request.assert_not_called()
        self.assertTrue(any("no está disponible" in m.get("error", "") for m in page.messages))

    def test_flow_calibration_requires_confirmed_lidar_capability(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._prepared_id = "review"
        page._prepared_profile = {"name": "Creator", "plate": 1}
        page._prepared_generation = page._generation
        page._prepared_ready = True
        page._busy.acquire()
        page._run({"command": "commit_print", "job_id": "review", "device_id": "device", "confirmed": True,
                   "_profile": page._prepared_profile,
                   "options": {"use_material_station": True, "flow_calibration": True}})
        page._bridge.request.assert_not_called()
        self.assertTrue(any("calibración de flujo" in item.get("error", "") for item in page.messages))

    def test_unconfirmed_dispatch_is_never_reported_as_sent(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._bridge.request.return_value = {"dispatched": False}
        page._prepared_id = "review"
        page._prepared_profile = {"name": "Creator", "plate": 1}
        page._prepared_generation = page._generation
        page._prepared_ready = True
        page._busy.acquire()
        page._run({"command": "commit_print", "job_id": "review", "device_id": "device", "confirmed": True,
                   "options": {"use_material_station": True}, "_profile": {"name": "Creator", "plate": 1}})
        self.assertFalse(any(m.get("command") == "print_dispatched" for m in page.messages))
        self.assertTrue(any("No se confirmó" in m.get("error", "") for m in page.messages))

    def test_printing_requires_reviewed_tools_and_lfs_mapping(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._prepared_id = "review"
        page._prepared_profile = {"name": "Creator", "plate": 1}
        page._prepared_generation = page._generation
        page._prepared_ready = False
        page._busy.acquire()
        page._run({"command": "commit_print", "job_id": "review", "device_id": "device", "confirmed": True,
                   "options": {"use_material_station": True}, "_profile": {"name": "Creator", "plate": 1}})
        page._bridge.request.assert_not_called()
        self.assertTrue(any("ranuras LFS" in m.get("error", "") for m in page.messages))

    def test_login_fields_do_not_contain_prefilled_credentials(self):
        import re
        html = self.plugin.FlashForgePage().get_ui()
        for field in ("username", "password"):
            tag = re.search(r'<input\s+id="' + field + r'"[^>]*>', html).group()
            self.assertNotRegex(tag, r'\svalue="[^"]+"')

    def test_printing_is_rejected_if_project_generation_changed(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._prepared_id = "review"
        page._prepared_profile = {"name": "Creator", "plate": 1}
        page._prepared_generation = page._generation
        page._generation += 1
        page._busy.acquire()
        page._run({"command": "commit_print", "job_id": "review", "device_id": "device", "confirmed": True,
                   "_profile": {"name": "Creator", "plate": 1}})
        page._bridge.request.assert_not_called()
        self.assertTrue(any(m.get("command") == "error" for m in page.messages))

    def test_printing_is_rejected_when_the_native_profile_changed(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._prepared_id = "review"
        page._prepared_profile = {"name": "Original", "model": "Creator", "plate": 1}
        page._busy.acquire()
        page._run({"command": "commit_print", "job_id": "review", "confirmed": True,
                   "_profile": {"name": "Different", "model": "Creator", "plate": 1}})
        page._bridge.request.assert_not_called()
        self.assertTrue(any("perfil" in m.get("error", "") for m in page.messages))

    def test_preparation_does_not_survive_a_project_change(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._devices = [{"devId": "device", "model": "Creator 5 Pro"}]
        def prepare(*args, **kwargs):
            page._generation += 1
            return {"id": "review", "device": {"devId": "device"}}
        page._bridge.request.side_effect = prepare
        page._busy.acquire()
        page._run({"command": "prepare_print", "device_id": "device", "path": "synthetic.gcode", "_profile": {"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1}})
        self.assertIsNone(page._prepared_id)
        self.assertTrue(any("proyecto cambió" in m.get("error", "") for m in page.messages))

    def test_slice_events_capture_plate_and_selection_preserves_other_plates(self):
        page = self.plugin.FlashForgePage()
        page._profile_context = Mock(return_value={"model": "Flashforge Creator 5 Pro", "plate": 1})
        def event(name, **values):
            page.on_lifecycle_event(types.SimpleNamespace(name=name), types.SimpleNamespace(**values))
        ok = types.SimpleNamespace(name="Ok")
        event("SliceStarted", id="model")
        event("GCodeExportFinished", id="model", code=ok, msg="/tmp/plate1.gcode")
        event("SlicingJobComplete", id="model", code=ok)
        self.assertEqual(page._sliced_plates, {1: "/tmp/plate1.gcode"})
        event("PlateSelected", index=1)
        self.assertEqual(page._sliced_plates, {1: "/tmp/plate1.gcode"})
        event("PresetSelected")
        self.assertEqual(page._sliced_plates, {})

    def test_ambiguous_or_failed_slice_never_exposes_plate(self):
        ok = types.SimpleNamespace(name="Ok")
        failed = types.SimpleNamespace(name="Error")
        for export_id, finish_id, finish_code, change_selection in [
            ("other", "model", ok, False), ("model", "other", ok, False),
            ("model", "model", failed, False), ("model", "model", ok, True),
            ("", "model", ok, False),
        ]:
            with self.subTest(export_id=export_id, finish_id=finish_id, selection=change_selection):
                page = self.plugin.FlashForgePage()
                page._profile_context = Mock(return_value={"model": "Flashforge Creator 5 Pro", "plate": 1})
                def event(name, **values):
                    page.on_lifecycle_event(types.SimpleNamespace(name=name), types.SimpleNamespace(**values))
                event("SliceStarted", id="model")
                event("GCodeExportFinished", id=export_id, code=ok, msg="/tmp/plate.gcode")
                if change_selection:
                    event("PlateSelected", index=1)
                event("SlicingJobComplete", id=finish_id, code=finish_code)
                self.assertEqual(page._sliced_plates, {})

    def test_missing_automatic_plate_does_not_contact_cloud(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._devices = [{"devId": "device", "model": "Creator 5 Pro"}]
        page._busy.acquire()
        page._run({"command": "prepare_print", "device_id": "device", "plate": 2,
                   "_profile": {"name": "Creator", "model": "Flashforge Creator 5 Pro", "plate": 1}})
        page._bridge.request.assert_not_called()
        self.assertTrue(any("Lamina la placa" in m.get("error", "") for m in page.messages))

    def test_plate_details_use_slice_data_and_discard_stale_results(self):
        page = self.plugin.FlashForgePage()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plate.gcode"
            path.write_text("; estimated printing time (normal mode) = 1h 5m\n"
                            "; total filament used [g] = 12.4\nT0\nG1 X1\n")
            token = object()
            page._sliced_plates[1] = str(path)
            page._plate_tokens[1] = token
            session = {"filament_type": ["PLA"], "filament_colour": ["#123456"]}
            page._read_plate_details(1, str(path), token, session)
            details = page._plate_details[1]
            self.assertEqual(details["time"], "1h 5m")
            self.assertEqual(details["weight"], "12.4")
            self.assertEqual(details["filaments"], [{"tool": 0, "material": "PLA", "color": "#123456"}])
            page._plate_details.clear()
            page._plate_tokens[1] = object()
            page._read_plate_details(1, str(path), token, session)
            self.assertEqual(page._plate_details, {})

    def test_project_previews_match_plate_and_ignore_changed_project(self):
        from orca_flashforge.jobs import EMPTY_THUMBNAIL
        page = self.plugin.FlashForgePage()
        for stale in (False, True):
            temporary = tempfile.TemporaryDirectory()
            path = Path(temporary.name) / "project.3mf"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Metadata/plate_2.png", EMPTY_THUMBNAIL)
            page._sliced_plates = {1: "plate1", 2: "plate2"}
            generation = page._generation
            if stale:
                page._generation += 1
            page._plate_details.clear()
            page._read_project_previews(temporary, str(path), generation, [1, 2])
            self.assertNotIn(1, page._plate_details)
            if stale:
                self.assertNotIn(2, page._plate_details)
            else:
                self.assertTrue(page._plate_details[2]["thumbnail"].startswith("data:image/png;base64,"))

    def test_generate_previews_uses_plugin_storage_and_cleans_up(self):
        from orca_flashforge.jobs import EMPTY_THUMBNAIL
        page = self.plugin.FlashForgePage()
        page._sliced_plates = {1: "plate1"}
        with tempfile.TemporaryDirectory() as storage:
            paths = []
            def export(path):
                paths.append(Path(path))
                self.assertEqual(paths[-1].parent.parent, Path(storage))
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("Metadata/plate_1.png", EMPTY_THUMBNAIL)
            host = types.SimpleNamespace(plugin=types.SimpleNamespace(storage=lambda: storage),
                                         plater=lambda: types.SimpleNamespace(export_3mf_copy=export))
            def immediate_thread(*, target, args, **kwargs):
                return types.SimpleNamespace(start=lambda: target(*args))
            with patch.object(self.plugin.orca, "host", host, create=True), \
                 patch.object(self.plugin.threading, "Thread", side_effect=immediate_thread):
                page.on_message({"command": "generate_previews"})
            self.assertFalse(paths[0].parent.exists())
            self.assertTrue(page._plate_details[1]["thumbnail"].startswith("data:image/png;base64,"))

    def test_preview_cleanup_does_not_scan_with_file_descriptors(self):
        with tempfile.TemporaryDirectory() as storage:
            preview = self.plugin.PreviewTemporary(storage)
            (Path(preview.name) / "project.3mf").write_bytes(b"fixture")
            with patch("os.scandir", side_effect=AssertionError("unexpected directory scan")):
                preview.cleanup()
            self.assertFalse(Path(preview.name).exists())

    def test_load_attempts_restore_only_when_remembering_is_enabled_and_sdk_configured(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"sdk_library": "fake-library", "sdk_settings": "fake-settings", "remember_account": True})
        page.on_message = Mock()
        page.on_load()
        page.on_message.assert_called_once_with({"command": "restore_login"})
        page.config = json.dumps({"sdk_library": "fake-library", "sdk_settings": "fake-settings", "remember_account": False})
        page.on_message.reset_mock()
        page.on_load()
        page.on_message.assert_not_called()

    def test_save_restores_remembered_experimental_account(self):
        page = self.plugin.FlashForgePage()
        page.config = json.dumps({"mode": "cloud", "cloud_backend": "experimental", "remember_account": True,
                                  "account_name": "synthetic@example.invalid"})
        page._busy.acquire()
        with patch.object(page, "_restore_account") as restore:
            page._run({"command": "save", "config": {}})
        restore.assert_called_once()
        self.assertTrue(next(message for message in page.messages if message.get("command") == "saved")["restoring"])

    def test_remember_login_saves_only_preference_and_account_name_in_config(self):
        page = self.plugin.FlashForgePage()
        page._bridge = Mock()
        page._bridge.request.side_effect = [{"profile": {"nickname": "Synthetic"}, "remembered": True}, []]
        page._busy.acquire()
        page._run({"command": "login", "username": "synthetic@example.invalid",
                   "password": "must-not-persist", "remember": True})
        stored = json.loads(page.config)
        self.assertTrue(stored["remember_account"])
        self.assertEqual(stored["account_name"], "synthetic@example.invalid")
        self.assertNotIn("must-not-persist", page.config)
        self.assertNotIn("token", page.config)

    def test_sign_out_disables_restore_and_deletes_saved_session(self):
        page = self.plugin.FlashForgePage()
        bridge = page._bridge = Mock()
        page.config = json.dumps({"remember_account": True, "account_name": "synthetic@example.invalid"})
        page._busy.acquire()
        page._run({"command": "disconnect"})
        bridge.request.assert_called_once_with("forget_account", wait_timeout=30)
        self.assertFalse(json.loads(page.config)["remember_account"])
        self.assertEqual(json.loads(page.config)["account_name"], "")
        self.assertIsNone(page._bridge)


if __name__ == "__main__":
    unittest.main()
