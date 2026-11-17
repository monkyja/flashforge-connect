import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from orca_flashforge.cloud_storage import put_file, upload_config
from orca_flashforge.model import FlashForgeError


class StorageTests(unittest.TestCase):
    def test_config_uses_two_signed_urls(self):
        session = Mock(client_id="client", access="token")
        session._json.return_value = {"code": 0, "data": {"method": "PUT", "bucket": "b", "endpoint": "e",
            "keyMap": {"file": "https://storage.test/file?sign=synthetic", "thumb": "https://storage.test/thumb?sign=synthetic"}}}
        result = upload_config(session, ["file", "thumb"])
        self.assertEqual(set(result["urls"]), {"file", "thumb"})
        session._authenticate.assert_called_once()
        session._json.return_value["data"]["keyMap"]["thumb"] = "http://storage.test/thumb?sign=x"
        with self.assertRaises(FlashForgeError):
            upload_config(session, ["file", "thumb"])

    def test_put_streams_and_closes_without_exposing_url(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic"
            path.write_bytes(b"example")
            progress = Mock()
            put_file("https://storage.test/file?sign=synthetic", path, threading.Event(), progress,
                     connection_factory=Mock(return_value=connection))
        connection.send.assert_called_once_with(b"example")
        connection.close.assert_called_once()
        progress.assert_called_once_with(7, 7)
