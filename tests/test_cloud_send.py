import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from orca_flashforge.cloud_send import send, send_existing
from orca_flashforge.model import FlashForgeError


class CloudSendTests(unittest.TestCase):
    def test_repeat_stored_file_creates_job_then_dispatches_without_claiming_start(self):
        session = Mock(client_id="client", access="token")
        session._devices = {"device": {"devTopic": "received-topic"}}
        session._json.return_value = {"code": 0, "data": {"jobID": "job", "thumbPath": "https://example.test/thumb"}}
        options = {"bed_leveling": False, "timelapse": False, "use_material_station": True,
                   "material_mappings": [{"toolId": 0, "slotId": 1, "materialName": "PLA",
                                          "toolMaterialColor": "#FFFFFF", "slotMaterialColor": "#FFFFFF"}]}
        phases = []
        with patch("orca_flashforge.cloud_send.publish") as publish:
            result = send_existing(session, {"device_id": "device", "name": "sample.gcode"}, options, phases.append)
        self.assertFalse(result["printer_started_verified"])
        self.assertEqual(session.api.request.call_args.args[:2], ("POST", "/api/v2/external/truck/device/localJob"))
        payload = json.loads(publish.call_args.args[3])
        self.assertEqual(payload["cmd"], "newLocalJob_cmd")
        self.assertEqual(payload["args"]["jobId"], "job")
        self.assertEqual([p["phase"] for p in phases], ["creating", "dispatching"])

    def test_upload_create_and_dispatch_and_uncertain_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path, thumb = Path(directory) / "file.gcode", Path(directory) / "thumb.png"
            path.write_bytes(b"G1 X0\n")
            thumb.write_bytes(b"image")
            job = {"device_id": "device", "path": str(path), "thumbnail": str(thumb), "name": path.name,
                   "size": path.stat().st_size, "plate": 1, "union_id": "union"}
            options = {"bed_leveling": False, "timelapse": False, "use_material_station": False, "material_mappings": []}
            session = Mock(client_id="client", access="token")
            session._devices = {"device": {"serialNumber": "serial", "devTopic": "topic"}}
            session.mqtt_config.return_value = {"server": "mqtt.test", "username": "user", "password": "password"}
            session._json.return_value = {"code": 0, "data": {"startResult": {"device": "job"}, "startError": {}}}
            phases = []

            def config(_session, keys):
                return {"bucket": "bucket", "endpoint": "storage.test", "urls": {key: "https://storage.test/upload?sign=x" for key in keys}}

            with patch("orca_flashforge.cloud_send.upload_config", side_effect=config), \
                 patch("orca_flashforge.cloud_send.put_file") as upload, \
                 patch("orca_flashforge.cloud_send.publish") as publish:
                result = send(session, job, options, threading.Event(), phases.append)
                self.assertTrue(result["dispatched"])
                self.assertEqual(upload.call_count, 2)
                payload = json.loads(publish.call_args.args[3])
                self.assertEqual(payload["jobInfo"], [["serial", "job"]])
                self.assertEqual([p["phase"] for p in phases], ["uploading", "creating", "dispatching"])
                def report_progress(_url, uploaded_path, _cancel, callback):
                    size = Path(uploaded_path).stat().st_size
                    callback(size, size)
                upload.side_effect = report_progress
                phases.clear()
                send(session, job, options, threading.Event(), phases.append)
                uploaded = [p for p in phases if p["phase"] == "uploading"]
                self.assertEqual([p["now"] for p in uploaded], [0, path.stat().st_size, path.stat().st_size + thumb.stat().st_size])
                self.assertTrue(all(p["total"] == path.stat().st_size + thumb.stat().st_size for p in uploaded))
                publish.side_effect = OSError("simulated")
                with self.assertRaisesRegex(FlashForgeError, "se creó"):
                    send(session, job, options, threading.Event(), lambda value: None)

    def test_selected_slots_are_sent_without_material_revalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.gcode"
            path.write_bytes(b"G1 X0\n")
            thumb = Path(directory) / "thumb.png"
            thumb.write_bytes(b"image")
            job = {"device_id": "device", "path": str(path), "thumbnail": str(thumb), "name": path.name,
                   "size": path.stat().st_size, "plate": 1, "union_id": "union"}
            options = {"bed_leveling": False, "timelapse": False, "use_material_station": True,
                       "material_mappings": [{"toolId": 0, "slotId": 1, "materialName": "PLA",
                                              "toolMaterialColor": "#FF0000", "slotMaterialColor": "#FFFFFF"}]}
            session = Mock(client_id="client", access="token")
            session._devices = {"device": {"serialNumber": "serial", "devTopic": "topic"}}
            session._json.return_value = {"code": 0, "data": {"startResult": {"device": "job"}, "startError": {}}}
            def storage(_session, keys):
                return {"bucket": "bucket", "endpoint": "storage.test", "urls": {key: "https://storage.test/upload" for key in keys}}
            with patch("orca_flashforge.cloud_send.upload_config", side_effect=storage), \
                 patch("orca_flashforge.cloud_send.put_file"), patch("orca_flashforge.cloud_send.publish"):
                send(session, job, options, threading.Event(), lambda value: None)
            session.status.assert_not_called()
