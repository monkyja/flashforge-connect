"""Native-agent contract tests with a host double; no printer is contacted."""

import importlib
import json
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from test_plugin import PageBase
from orca_flashforge.materials import ams_payload, slot_table, suggest_mappings
from orca_flashforge.model import FlashForgeError


SLOTS = [{"id": 7, "material": "PLA", "color": "#FF0000", "has_filament": True},
         {"id": 42, "material": "PETG", "color": "#0000FF", "has_filament": True},
         {"id": 99, "material": "", "color": "", "has_filament": False}]
STATUS = {"status": "ready", "slots": SLOTS, "firmware": "real-received-version"}


class MaterialTests(unittest.TestCase):
    def test_display_positions_have_a_reverse_table_and_preserve_empty_slots(self):
        slots = slot_table({"slots": list(reversed(SLOTS))})
        self.assertEqual([s["id"] for s in slots], [7, 42, 99])
        payload = ams_payload(slots)
        self.assertEqual(payload["tray_exist_bits"], "3")
        self.assertEqual(payload["ams"][0]["tray"][1]["tray_type"], "PETG")
        self.assertEqual(payload["ams"][0]["tray"][1]["id"], "1")
        self.assertEqual(payload["ams"][0]["tray"][2]["tray_slot_placeholder"], "1")

    def test_suggestions_require_unique_exact_material_and_color(self):
        filament = {"toolId": 3, "material": "PLA", "color": "#ff0000"}
        self.assertEqual(suggest_mappings([filament], SLOTS)[0]["slotId"], 7)
        self.assertEqual(suggest_mappings([filament], SLOTS + [dict(SLOTS[0], id=8)]), [])
        self.assertEqual(suggest_mappings([dict(filament, material="PLA-CF")], SLOTS), [])
        self.assertEqual(suggest_mappings([dict(filament, color="")], SLOTS), [])
        self.assertEqual(suggest_mappings([filament], [dict(SLOTS[0], has_filament=False)]), [])

    def test_invalid_slot_ids_are_rejected_instead_of_guessing(self):
        for slots in ([dict(SLOTS[0], id="7")], [SLOTS[0], SLOTS[0]], [dict(SLOTS[0], id=-1)]):
            with self.assertRaises(FlashForgeError):
                slot_table({"slots": slots})


class PrinterAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host = types.ModuleType("orca")
        host.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PageBase)
        host.printer_agent = types.SimpleNamespace(PrinterAgentBase=PageBase,
            AgentInfo=lambda *args: types.SimpleNamespace(id=args[0]),
            FilamentSyncMode=types.SimpleNamespace(Pull="pull"))
        host.base = object
        host.plugin = lambda package: package
        host.request_permissions = Mock()
        host.register_capability = Mock()
        cls.host_patch = patch.dict(sys.modules, {"orca": host})
        cls.host_patch.start()
        cls.plugin = importlib.import_module("orca_flashforge.plugin")
        cls.module = importlib.import_module("orca_flashforge.printer_agent")

    @classmethod
    def tearDownClass(cls):
        cls.host_patch.stop()

    def setUp(self):
        self.page = self.plugin.FlashForgePage()
        self.module.attach_page(self.page)
        self.page.config = json.dumps({"mode": "cloud", "profile_bindings": {"Creator profile": "device"}})
        self.page._agent_profile = {"name": "Creator profile", "model": "Creator 5 Pro", "plate": 2}
        self.page._account = {"nickname": "Synthetic account"}
        self.page._devices = [{"devId": "device"}]
        self.bridge = self.page._bridge = Mock()
        self.agent = self.module.FlashForgePrinterAgent()
        self.agent._slots["device"] = SLOTS
        self.params = types.SimpleNamespace(dev_id="device", filename="synthetic.3mf", plate_index=2,
            ams_mapping="[-1, 1]", ams_mapping_info='[{}, {"sourceColor":"0000FFFF", "filamentType":"PETG"}]',
            task_use_ams=True, task_bed_leveling=True, task_record_timelapse=False)
        self.job = {"id": "review", "plate": 2, "tools": [1]}
        self.update = Mock()

    def tearDown(self):
        self.agent.on_unload()
        self.page._bridge = None

    def responses(self, command, **kwargs):
        return {"prepare_print": self.job, "status": STATUS,
                "discard_print": {"discarded": True},
                "commit_print": {"dispatched": True, "name": "synthetic.3mf"}}[command]

    def test_native_print_probe_reads_status_without_sending_a_job(self):
        self.bridge.request.side_effect = self.responses
        probe = types.SimpleNamespace(dev_id="device", project_name="verify_job")
        self.assertEqual(self.agent.start_send_gcode_to_sdcard(probe, None, None, None), 0)
        self.bridge.request.assert_called_once_with("status", device_id="device")
        self.assertFalse(self.page._busy.locked())
        self.bridge.request.reset_mock()
        actual_send = types.SimpleNamespace(dev_id="device", project_name="model.gcode.3mf")
        self.assertEqual(self.agent.start_send_gcode_to_sdcard(actual_send, self.update, None, None), self.module.UNSUPPORTED)
        self.bridge.request.assert_not_called()

    def test_native_print_probe_rejects_wrong_or_disconnected_destination(self):
        probe = types.SimpleNamespace(dev_id="other", project_name="verify_job")
        self.assertEqual(self.agent.start_send_gcode_to_sdcard(probe, None, None, None), self.module.INVALID)
        self.page._account = None
        probe.dev_id = "device"
        self.assertEqual(self.agent.start_send_gcode_to_sdcard(probe, None, None, None), self.module.INVALID)
        self.bridge.request.assert_not_called()

    def test_registers_native_capability_with_own_id_and_reuses_page_session(self):
        self.bridge.request.side_effect = self.responses
        self.assertEqual(self.agent.get_agent_info().id, "org.orca-flashforge.cloud")
        self.assertEqual(self.agent.get_user_selected_machine(), "device")
        self.assertEqual(self.agent.start_local_print(self.params, self.update, lambda: False), 0)
        commit = next(call for call in self.bridge.request.call_args_list if call.args == ("commit_print",))
        options = commit.kwargs["options"]
        self.assertEqual(options["plate"], 2)  # PrintJob supplies 1-based plate indices.
        self.assertEqual(options["material_mappings"][0]["slotId"], 42)  # Native tray 1 is SDK slot 42.
        self.assertEqual(options["material_mappings"][0]["toolId"], 1)
        self.assertTrue(options["bed_leveling"])
        self.assertEqual(self.update.call_args.args[0], 6)
        self.assertFalse(self.page._busy.locked())

    def test_stale_or_changed_lfs_mapping_never_reaches_commit(self):
        self.bridge.request.side_effect = self.responses
        self.agent._slots["device"] = [dict(SLOTS[0], color="#FFFFFF"), *SLOTS[1:]]
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.assertNotIn("commit_print", [c.args[0] for c in self.bridge.request.call_args_list])

    def test_empty_trays_wrong_materials_and_missing_tool_mappings_are_rejected(self):
        self.bridge.request.side_effect = self.responses
        for mapping, info in (("[-1,2]", "[]"), ("[-1]", "[]"),
                              ("[-1,0]", '[{}, {"filamentType":"PETG"}]'), ("{}", "[]")):
            with self.subTest(mapping=mapping):
                self.params.ams_mapping, self.params.ams_mapping_info = mapping, info
                self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.assertNotIn("commit_print", [c.args[0] for c in self.bridge.request.call_args_list])

    def test_profile_changes_busy_and_disconnected_accounts_reject_before_upload(self):
        self.bridge.request.side_effect = self.responses
        self.params.dev_id = "foreign"
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.params.dev_id = "device"
        self.page._busy.acquire()
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.page._busy.release()
        self.page._account = None
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.bridge.request.assert_not_called()

    def test_lifecycle_change_during_preparation_rejects_commit(self):
        def respond(command, **kwargs):
            if command == "prepare_print":
                self.page._generation += 1
            return self.responses(command, **kwargs)
        self.bridge.request.side_effect = respond
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.assertNotIn("commit_print", [c.args[0] for c in self.bridge.request.call_args_list])

    def test_cancel_before_upload_does_not_prepare_and_cancel_during_upload_uses_pipe(self):
        self.assertEqual(self.agent.start_print(self.params, self.update, lambda: True, None), -18)
        self.bridge.request.assert_not_called()
        cancel = threading.Event()
        def respond(command, **kwargs):
            if command == "commit_print":
                kwargs["on_progress"]({"phase": "uploading", "now": 0, "total": 10})
                cancel.set()
                deadline = time.monotonic() + 2
                while not self.bridge.cancel_upload.called and time.monotonic() < deadline:
                    time.sleep(.01)
                raise FlashForgeError("Subida cancelada.")
            return self.responses(command, **kwargs)
        self.bridge.request.side_effect = respond
        self.assertEqual(self.agent.start_print(self.params, self.update, cancel.is_set, None), -18)
        self.bridge.cancel_upload.assert_called_once()

    def test_uncertain_dispatch_is_not_success_or_retried(self):
        def respond(command, **kwargs):
            if command == "commit_print":
                kwargs["on_progress"]({"phase": "dispatching"})
                raise FlashForgeError("No se confirmó MQTT.")
            return self.responses(command, **kwargs)
        self.bridge.request.side_effect = respond
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)
        self.assertEqual([c.args[0] for c in self.bridge.request.call_args_list].count("commit_print"), 1)
        self.assertIn("Comprueba la impresora", self.update.call_args.args[2])
        self.assertNotIn(6, [c.args[0] for c in self.update.call_args_list])

    def test_read_only_filament_sync_publishes_real_received_data_and_no_commands(self):
        self.bridge.request.return_value = STATUS
        callback = Mock()
        self.agent.set_on_local_message_fn(callback)
        self.assertTrue(self.agent.fetch_filament_info("device"))
        self.bridge.request.assert_called_once_with("status", device_id="device")
        message = json.loads(callback.call_args.args[1])
        # The host's storage gate represents cloud upload support, not an SD probe.
        self.assertTrue(message["print"]["sdcard"])
        self.assertEqual(message["info"]["module"][0]["sw_ver"], STATUS["firmware"])
        self.assertEqual(message["print"]["ams"]["tray_exist_bits"], "3")
        self.assertEqual(self.agent.send_message("device", '{"print":{"command":"gcode_line","param":"G28"}}', 0, 0), -7010)
        self.assertEqual(self.agent.start_sdcard_print(None, None, None), -7010)

    def test_host_access_code_probe_is_ignored_without_sending_bambu_command(self):
        self.agent._selected = "device"
        self.agent._thread = Mock()
        self.agent._thread.is_alive.return_value = True
        self.assertEqual(self.agent.send_message("device", '{"system":{"command":"get_access_code"}}', 0, 0), 0)
        self.bridge.request.assert_not_called()
        self.assertEqual(self.agent.send_message("device", '{"system":{"command":"reboot"}}', 0, 0), self.module.UNSUPPORTED)

    def test_creator_model_id_requires_matching_account_and_preset(self):
        self.bridge.request.return_value = STATUS
        callback = Mock()
        self.agent.set_on_local_message_fn(callback)
        self.page._devices = [{"devId": "device", "model": "Creator 5 Pro"}]
        self.page._agent_profile["model"] = "Flashforge Creator 5 Pro"
        self.assertTrue(self.agent.fetch_filament_info("device"))
        self.assertEqual(json.loads(callback.call_args.args[1])["print"]["printer_type"], "Flashforge-Creator-5-Pro")
        self.page._devices[0]["model"] = "Another model"
        self.assertTrue(self.agent.fetch_filament_info("device"))
        self.assertNotIn("printer_type", json.loads(callback.call_args.args[1])["print"])

    def test_polling_cannot_silently_accept_a_changed_spool(self):
        changed = {**STATUS, "slots": [dict(SLOTS[0], color="#FFFFFF"), *SLOTS[1:]]}
        self.agent._publish("device", changed)
        self.assertEqual(self.agent._slots["device"], SLOTS)
        with self.assertRaisesRegex(FlashForgeError, "cambió"):
            self.agent._options(self.params, self.job, slot_table(changed))
        self.agent._publish("device", changed, sync=True)
        self.assertEqual(self.agent._slots["device"], changed["slots"])

    def test_native_selection_can_wait_for_session_restore_without_claiming_connection(self):
        self.page._account = None
        self.assertEqual(self.agent.get_user_selected_machine(), "device")
        self.assertEqual(self.agent.set_user_selected_machine("device"), 0)
        self.assertEqual(self.agent.set_user_selected_machine("foreign"), -19)
        self.bridge.request.assert_not_called()

    def test_result_without_dispatch_confirmation_is_an_error(self):
        self.bridge.request.side_effect = [self.job, STATUS, {"dispatched": False}]
        self.assertEqual(self.agent.start_print(self.params, self.update, None, None), -19)


if __name__ == "__main__":
    unittest.main()
