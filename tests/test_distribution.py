"""Distribution safeguards for the login form."""

from html.parser import HTMLParser
from pathlib import Path
import unittest


class LoginFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id") in {"username", "password"}:
            self.fields[attrs["id"]] = attrs.get("value", "")


class DistributionTests(unittest.TestCase):
    def test_login_fields_have_no_default_credentials(self):
        parser = LoginFields()
        parser.feed((Path(__file__).parents[1] / "src/orca_flashforge/ui/device.html").read_text())
        self.assertEqual(parser.fields, {"username": "", "password": ""})
