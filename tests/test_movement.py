import ctypes
import unittest
from unittest.mock import Mock

from orca_flashforge.model import FlashForgeError
from orca_flashforge.sdk_print import MoveControl, PrintTransport, WriteData


class MovementTests(unittest.TestCase):
    def test_move_uses_account_topic_and_checks_capability(self):
        session = Mock()
        session._devices = {"device": {"devTopic": "synthetic-topic"}}
        session.status.return_value = {"move_enabled": True}
        transport = PrintTransport.__new__(PrintTransport)
        transport.session = session
        transport.lib = Mock()
        transport.connection = ctypes.c_void_p(1)
        transport._connect = Mock()
        sent = []

        def send(connection, pointer):
            write = ctypes.cast(pointer, ctypes.POINTER(WriteData)).contents
            move = ctypes.cast(write.data, ctypes.POINTER(MoveControl)).contents
            sent.append((write.type, write.topic, write.qos, move.axis, move.delta))
            return 0

        transport.lib.fnet_connectionSend = Mock(side_effect=send)
        transport.move("device", "x", -1)
        self.assertEqual(sent, [(8, b"synthetic-topic", 1, b"x", -1)])
        session.status.return_value = {"move_enabled": False}
        with self.assertRaises(FlashForgeError):
            transport.move("device", "x", 1)
        self.assertEqual(len(sent), 1)
