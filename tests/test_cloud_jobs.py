import unittest

from orca_flashforge.cloud_jobs import (create_job_payload, created_job_id, start_job_payload,
                                       existing_job_payload, start_existing_job_payload)


class CloudJobPayloadTests(unittest.TestCase):
    def test_existing_job_payloads_match_offline_sdk_serializers(self):
        options = {"bed_leveling": False, "timelapse": False, "use_material_station": True,
                   "material_mappings": [{"toolId": 2, "slotId": 4, "materialName": "PLA",
                                          "toolMaterialColor": "#112233", "slotMaterialColor": "#445566"}]}
        payload = existing_job_payload("device", "test.gcode", options)
        self.assertEqual(payload["deviceID"], "device")
        self.assertEqual(payload["ms"], [{"tNum": 2, "slot": 4, "mt": "PLA", "sRgb": "#112233", "tRgb": "#445566"}])
        start = start_existing_job_payload("test.gcode", "job", "https://example.test/thumb", options)
        self.assertEqual(start["cmd"], "newLocalJob_cmd")
        self.assertEqual(start["args"]["jobId"], "job")
        self.assertEqual(start["args"]["fileName"], "test.gcode")

    def test_job_id_matches_device_in_sdk_result(self):
        self.assertEqual(created_job_id({"code": 0, "data": {"startResult": {"DEVICE": "JOB"}, "startError": {}}}, "DEVICE"), "JOB")
    def test_payloads_match_sdk_serializers(self):
        job = {"device_id": "DEVICE", "name": "FILE.gcode", "type": "gcode", "md5": "MD5", "size": 123,
               "thumbnail_name": "THUMB.png", "thumbnail_md5": "MD5THUMB", "thumbnail_size": 42,
               "union_id": "UNION", "plate": 1}
        storage = {"bucket": "BUCKET", "endpoint": "ENDPOINT", "file_key": "KEY", "file_url": "URL",
                   "thumb_key": "TKEY", "thumb_url": "TURL"}
        options = {"bed_leveling": False, "timelapse": False, "use_material_station": True,
                   "material_mappings": [{"toolId": 0, "slotId": 3, "materialName": "PLA",
                                           "toolMaterialColor": "#112233", "slotMaterialColor": "#445566"}]}
        mapping = {"tNum": 0, "slot": 3, "mt": "PLA", "sRgb": "#112233", "tRgb": "#445566"}
        created = create_job_payload(job, options, storage)
        self.assertEqual(created["deviceID"], ["DEVICE"])
        self.assertEqual(created["fileMeta"], {"name": "FILE.gcode", "contentType": "gcode", "md5": "MD5",
                                               "size": 123, "storageUrl": "URL", "storageKey": "KEY"})
        self.assertEqual(created["ms"], [mapping])
        self.assertEqual(created["plateNo"], 0)
        start = start_job_payload(job, options, storage, "SERIAL", "JOB")
        self.assertEqual(start["cmd"], "newJob_cmd")
        self.assertEqual(start["jobInfo"], [["SERIAL", "JOB"]])
        self.assertEqual(start["args"]["ms"], [mapping])
        self.assertEqual(start["args"]["filepath"], "URL")
        self.assertFalse(start["args"]["flowCalibration"])
        options["flow_calibration"] = True
        self.assertTrue(create_job_payload(job, options, storage)["flowCalibration"])
        self.assertTrue(start_job_payload(job, options, storage, "SERIAL", "JOB")["args"]["flowCalibration"])
