import base64
import hashlib
import unittest

from orca_flashforge.cloud_mqtt import publish
from orca_flashforge.model import FlashForgeError


class FakeSocket:
    def __init__(self, ack=b"\x40\x02\x00\x01"):
        self.data = b""
        self.sent = []
        self.ack = ack
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)
        if data.startswith(b"GET "):
            key = next(line.split(b": ", 1)[1] for line in data.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Key:"))
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            self.data += b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            self.data += b"\x82\x04\x20\x02\x00\x00" + bytes([0x82, len(self.ack)]) + self.ack

    def recv(self, count):
        result, self.data = self.data[:count], self.data[count:]
        return result

    def close(self):
        self.closed = True


class MqttTests(unittest.TestCase):
    def test_publish_requires_qos_one_ack(self):
        for ack, success in ((b"\x40\x02\x00\x01", True), (b"\x40\x02\x00\x02", False)):
            sock = FakeSocket(ack)
            if success:
                publish({"server": "example.test", "username": "user", "password": "secret"}, "client", "device/topic", b"{}", connector=lambda *a, **k: sock)
            else:
                with self.assertRaises(FlashForgeError):
                    publish({"server": "example.test", "username": "user", "password": "secret"}, "client", "device/topic", b"{}", connector=lambda *a, **k: sock)
            self.assertTrue(sock.closed)
