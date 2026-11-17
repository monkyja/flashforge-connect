import unittest
from unittest.mock import Mock

from orca_flashforge.cloud_http import HttpsTransport
from orca_flashforge.model import FlashForgeError


class CloudHttpTests(unittest.TestCase):
    def test_requires_https_base_without_credentials_or_query(self):
        for url in ("http://example.test", "https://user@example.test", "https://example.test/?x=1"):
            with self.subTest(url=url), self.assertRaises(FlashForgeError):
                HttpsTransport(url)

    def test_sends_request_to_injected_origin(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true}'
        opener = Mock()
        opener.open.return_value = response

        transport = HttpsTransport("https://cloud.example.test", opener=opener)
        result = transport.request("GET", "/known", headers={"Accept": "application/json"})

        self.assertEqual(result, b'{"ok":true}')
        request, = opener.open.call_args.args
        self.assertEqual(request.full_url, "https://cloud.example.test/known")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_rejects_oversized_response(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"12345"
        opener = Mock()
        opener.open.return_value = response

        with self.assertRaisesRegex(FlashForgeError, "demasiado grande"):
            HttpsTransport("https://cloud.example.test", max_response_bytes=4,
                           opener=opener).request("GET", "/known")

    def test_rejects_non_absolute_or_external_path(self):
        transport = HttpsTransport("https://cloud.example.test", opener=Mock())
        for path in ("known", "//other.test/path", "/bad\npath"):
            with self.subTest(path=path), self.assertRaises(FlashForgeError):
                transport.request("GET", path)


if __name__ == "__main__":
    unittest.main()
