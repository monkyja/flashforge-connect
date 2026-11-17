import io
import tempfile
import unittest
import zipfile
import struct
import zlib
from pathlib import Path

from orca_flashforge.jobs import EMPTY_THUMBNAIL, JobStore, inspect_gcode, validate_options
from orca_flashforge.model import FlashForgeError


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = JobStore()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_only_explicit_tool_commands_are_detected(self):
        tools, _ = inspect_gcode(io.BytesIO(b"; nozzle count = 4\n; T0\nT4\nG1 X5\nT4 ; select\nT7\n"))
        self.assertEqual(tools, [4, 7])

    def test_plate_summary_uses_only_gcode_metadata(self):
        metadata = {}
        tools, thumbnail = inspect_gcode(io.BytesIO(b"; estimated printing time (normal mode) = 2h 13m\n; total filament used [g] = 42.50\nT0\nG1 X1\n"), metadata)
        self.assertEqual(tools, [0])
        self.assertIsNone(thumbnail)
        self.assertEqual(metadata["estimated printing time (normal mode)"], "2h 13m")
        self.assertEqual(metadata["total filament used [g]"], "42.50")

    def test_fallback_thumbnail_is_a_valid_png_with_a_real_scanline(self):
        offset, compressed = 8, bytearray()
        while offset < len(EMPTY_THUMBNAIL):
            size = struct.unpack(">I", EMPTY_THUMBNAIL[offset:offset + 4])[0]
            content = EMPTY_THUMBNAIL[offset + 4:offset + 8 + size]
            crc = struct.unpack(">I", EMPTY_THUMBNAIL[offset + 8 + size:offset + 12 + size])[0]
            self.assertEqual(zlib.crc32(content), crc)
            if content[:4] == b"IDAT":
                compressed.extend(content[4:])
            offset += 12 + size
        self.assertEqual(len(zlib.decompress(compressed)), 5)  # filter byte plus one RGBA pixel

    def test_unsliced_projects_and_missing_plate_are_rejected(self):
        path = self.root / "unsliced.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("3D/3dmodel.model", "not sliced")
        with self.assertRaisesRegex(FlashForgeError, "laminada"):
            self.store.stage(str(path), device_id="device")
        self.assertIsNone(self.store.prepared)

    def test_three_mf_uses_the_explicit_plate(self):
        path = self.root / "plates.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "T0\nG28\n")
            archive.writestr("Metadata/plate_2.gcode", "T5\nG28\n")
        result = self.store.stage(str(path), device_id="device", plate=2)
        self.assertEqual(result["tools"], [5])
        self.assertNotIn("path", result)
        self.assertNotIn("thumbnail", result)

    def test_material_metadata_comes_from_the_sliced_file_and_does_not_create_tools(self):
        path = self.root / "colors.gcode"
        path.write_text('; filament_type = PLA;PETG\n; filament_colour = #FF0000;#0000FF\nT1\nG1 X2\n')
        result = self.store.stage(str(path), device_id="device")
        self.assertEqual(result["tools"], [1])
        self.assertEqual(result["filaments"], [{"toolId": 1, "material": "PETG", "color": "#0000FF"}])

    def test_sliced_filament_brand_and_weight_match_explicit_tool_index(self):
        path = self.root / "usage.gcode"
        path.write_text('; filament_type = PLA;PETG\n; filament_colour = #FF0000;#0000FF\n'
                        '; filament_vendor = Acme;Example\n; filament used [g] = 1.25, 4.50\n'
                        '; filament used [mm] = 530.00, 1870.00\nT1\nG1 X2\n')
        result = self.store.stage(str(path), device_id="device")
        self.assertEqual(result["filaments"], [{"toolId": 1, "material": "PETG", "color": "#0000FF",
                                                "brand": "Example", "weight": "4.50", "length_mm": "1870.00"}])

    def test_review_is_bound_to_device_and_plate_and_consumed_once(self):
        path = self.root / "job.gcode"
        path.write_text("G28\n")
        result = self.store.stage(str(path), device_id="device")
        for device, options in (("other", {}), ("device", {"plate": 2})):
            with self.assertRaises(FlashForgeError):
                self.store.take(result["id"], device, options)
        self.store.take(result["id"], "device", {})
        with self.assertRaisesRegex(FlashForgeError, "caducado"):
            self.store.take(result["id"], "device", {})

    def test_mappings_never_assume_a_tool_equals_a_slot(self):
        with self.assertRaisesRegex(FlashForgeError, "explícitamente"):
            validate_options({"use_material_station": True}, [4])
        options = validate_options({"use_material_station": True, "material_mappings": [{"toolId": 4, "slotId": 1}]}, [4])
        self.assertEqual(options["material_mappings"][0]["slotId"], 1)
        with self.assertRaisesRegex(FlashForgeError, "no es válido"):
            validate_options({"use_material_station": True, "material_mappings": [{"toolId": 0, "slotId": 1}]}, [4])

    def test_boolean_options_reject_strings(self):
        with self.assertRaises(FlashForgeError):
            validate_options({"use_material_station": "false"}, [])

    def test_plain_text_and_non_gcode_files_are_rejected(self):
        path = self.root / "not-gcode.gcode"
        path.write_text("not gcode\n")
        with self.assertRaisesRegex(FlashForgeError, "no contiene G-code"):
            self.store.stage(str(path), device_id="device")
        with self.assertRaises(FlashForgeError):
            self.store.stage(str(self.root), device_id="device")
