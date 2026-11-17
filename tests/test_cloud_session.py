import json
import unittest
from urllib.parse import parse_qs, urlsplit

from orca_flashforge.cloud_session import IndependentSession


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return json.dumps(next(self.responses)).encode()


class IndependentSessionTests(unittest.TestCase):
    def test_light_state_uses_the_received_cloud_value(self):
        api = Transport([{"code": 0, "data": {"detail": {"light": "close"}}},
                         {"code": 0, "data": {"detail": {}}}])
        session = IndependentSession(api=api, auth=Transport([]))
        session.access, session.deadline = "synthetic", float("inf")
        session._devices = {"machine": {}}
        self.assertEqual(session.status("machine")["light_status"], "close")
        self.assertEqual(session.status("machine")["light_status"], "")

    def test_stored_files_are_device_files_with_explicit_tool_metadata(self):
        api = Transport([{"code": 0, "data": [{"file_name": "sample.gcode", "file_obj": {"url": "https://example.test/preview.png"},
            "file_detail": {"printingTime": 600, "totalFilamentWeight": 12.5, "gcodeToolCnt": 1,
                            "gcodeToolDatas": [{"toolID": 0, "slotID": 7, "materialName": "PLA", "materialColor": "#FFFFFF", "filamentWeight": 12.5}]}}]}])
        session = IndependentSession(api=api, auth=Transport([]))
        session.access, session.deadline = "synthetic", float("inf")
        session._devices = {"machine": {}}
        item = session.stored_files("machine")[0]
        self.assertEqual(item["name"], "sample.gcode")
        self.assertEqual(item["seconds"], 600)
        self.assertEqual(item["filaments"][0]["toolId"], 0)
        self.assertNotIn("slotId", item["filaments"][0])
        self.assertTrue(item["can_repeat"])
        self.assertEqual(urlsplit(api.calls[0][1]).path, "/api/v2/external/truck/device/local_files")

    def test_mqtt_config_uses_required_client_type(self):
        api = Transport([{"code": 0, "data": {"server": "mqtt.example.test", "username": "synthetic", "password": "synthetic"}}])
        session = IndependentSession(api=api, auth=Transport([]))
        session.access, session.deadline = "synthetic", float("inf")
        self.assertEqual(session.mqtt_config()["server"], "mqtt.example.test")
        self.assertEqual(api.calls[0][2]["headers"]["clientType"], "pc")

    def test_recordings_only_exposes_safe_video_urls(self):
        api = Transport([{"code": 0, "data": {"videos": [
            {"filename": "clip.mp4", "videoUrl": "https://example.test/clip", "thumbnail": "javascript:alert(1)"}]}}])
        session = IndependentSession(api=api, auth=Transport([]))
        session.access, session.deadline = "synthetic", float("inf")
        session._devices = {"machine": {}}
        videos = session.recordings("machine")
        self.assertEqual(videos[0]["name"], "clip.mp4")
        self.assertEqual(videos[0]["url"], "https://example.test/clip")
        self.assertEqual(videos[0]["thumbnail"], "")

    def test_login_devices_and_temperatures(self):
        auth = Transport([{"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}])
        api = Transport([
            {"code": 0, "data": {"next": False, "detail": [{"deviceID": "machine", "name": "Printer", "sn": "serial", "gTopic": "topic"}]}},
            {"code": 0, "data": {"detail": {"printerName": "Printer", "nozzleTemps": [205],
                                   "nozzleTargetTemps": [210], "platformCurTemperature": 60, "light": "open"}}},
        ])
        session = IndependentSession(auth=auth, api=api)
        rotated = []
        session._token_sink = rotated.append
        session.login("synthetic@example.test", "synthetic-password")
        self.assertEqual(rotated, [b"refresh"])
        params = parse_qs(urlsplit(auth.calls[0][1]).query)
        self.assertEqual(params["username"], ["synthetic@example.test"])
        device = session.devices()[0]
        self.assertEqual(device["devId"], "machine")
        self.assertNotIn("sn", device)
        self.assertEqual(session._devices["machine"], {"serialNumber": "serial", "devTopic": "topic", "model": ""})
        status = session.status("machine")
        self.assertEqual(status["temperatures"][0]["current"], 205)
        self.assertEqual(status["temperatures"][1]["current"], 60)
        self.assertEqual(status["light_status"], "open")
        self.assertEqual(api.calls[0][2]["headers"]["token"], "access")


if __name__ == "__main__":
    unittest.main()
