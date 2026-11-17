"""One-shot MQTT 3.1.1 QoS 1 over the SDK's WebSocket transport."""

import base64
import hashlib
import os
import socket
import struct

from .model import FlashForgeError


def _field(value):
    data = value.encode("utf-8")
    if not data or len(data) > 65535 or b"\0" in data:
        raise FlashForgeError("Datos MQTT no válidos.")
    return struct.pack("!H", len(data)) + data


def _packet(header, body):
    size = len(body)
    if size > 1024 * 1024:
        raise FlashForgeError("Mensaje MQTT demasiado grande.")
    length = bytearray()
    while True:
        value = size % 128
        size //= 128
        length.append(value | (128 if size else 0))
        if not size:
            return bytes([header]) + bytes(length) + body


def _recv_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise FlashForgeError("La conexión MQTT se cerró antes de la confirmación del servidor.")
        data.extend(part)
    return bytes(data)


def _send_frame(sock, payload):
    length = len(payload)
    head = bytearray([0x82])
    if length < 126:
        head.append(0x80 | length)
    elif length <= 65535:
        head.append(0xfe)
        head.extend(struct.pack("!H", length))
    else:
        head.append(0xff)
        head.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    sock.sendall(bytes(head) + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))


def _read_frame(sock):
    first, second = _recv_exact(sock, 2)
    if first != 0x82 or second & 0x80:
        raise FlashForgeError("Respuesta WebSocket MQTT no admitida.")
    size = second & 0x7f
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if size > 1024 * 1024:
        raise FlashForgeError("Respuesta MQTT demasiado grande.")
    return _recv_exact(sock, size)


def publish(config, client_id, topic, payload, *, connector=socket.create_connection):
    host = config.get("server")
    if not isinstance(host, str) or not host or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in host):
        raise FlashForgeError("Servidor MQTT no válido.")
    if not isinstance(topic, str) or not topic or "+" in topic or "#" in topic:
        raise FlashForgeError("Destino MQTT no válido.")
    sock = None
    try:
        sock = connector((host, 8083), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET /mqtt HTTP/1.1\r\nHost: {host}:8083\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n")
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            response.extend(_recv_exact(sock, 1))
            if len(response) > 16384:
                raise FlashForgeError("Respuesta WebSocket demasiado grande.")
        lines = bytes(response).split(b"\r\n")
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.lower()] = v.strip()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not lines[0].startswith(b"HTTP/1.1 101 ") or headers.get(b"sec-websocket-accept") != expected:
            raise FlashForgeError("El servidor rechazó la conexión WebSocket MQTT.")
        connect = _field("MQTT") + bytes([4, 0xc2]) + struct.pack("!H", 60)
        connect += _field(client_id) + _field(config["username"]) + _field(config["password"])
        _send_frame(sock, _packet(0x10, connect))
        if _read_frame(sock) != b"\x20\x02\x00\x00":
            raise FlashForgeError("El servidor rechazó la conexión MQTT.")
        _send_frame(sock, _packet(0x32, _field(topic) + b"\x00\x01" + payload))
        if _read_frame(sock) != b"\x40\x02\x00\x01":
            raise FlashForgeError("El servidor no confirmó la recepción MQTT.")
        _send_frame(sock, b"\xe0\x00")
    except (OSError, KeyError, UnicodeError):
        raise FlashForgeError("No se pudo completar la conexión MQTT.") from None
    finally:
        if sock:
            sock.close()
