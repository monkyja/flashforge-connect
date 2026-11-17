import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from orca_flashforge.credentials import CredentialError, SecretStore, SessionVault
from orca_flashforge.model import FlashForgeError
from orca_flashforge.sdk import AuthenticationError


class MemoryStore:
    def __init__(self, record=None):
        self.record = record
        self.saves = []
        self.clears = 0

    def load(self):
        return self.record

    def save(self, username, token):
        self.saves.append((username, token))
        self.record = {"username": username, "refresh_token": token.decode()}

    def clear(self):
        self.clears += 1
        self.record = None


class RestorableSession:
    def __init__(self, *args):
        self._token_sink = None
        self.closed = False

    def restore(self, token, username):
        if token == "expired":
            raise AuthenticationError("synthetic-sensitive-detail")
        if token == "offline":
            raise FlashForgeError("Servidor no disponible")
        self._token_sink(b"synthetic-rotated-refresh")
        return {"connected": True, "profile": {"nickname": username}}

    def close(self):
        self.closed = True


class SecretStoreTests(unittest.TestCase):
    def test_only_refresh_token_is_stored_and_only_on_stdin(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
        store = SecretStore(runner=runner)
        store.save("synthetic@example.invalid", b"synthetic-refresh-secret")
        args, kwargs = runner.call_args
        self.assertNotIn("synthetic-refresh-secret", " ".join(args[0]))
        payload = json.loads(kwargs["input"])
        self.assertEqual(set(payload), {"version", "username", "refresh_token"})
        self.assertEqual(payload["refresh_token"], "synthetic-refresh-secret")
        self.assertNotIn("password", payload)
        self.assertNotIn("access_token", payload)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    def test_missing_entry_is_not_an_error_but_denied_service_is(self):
        runner = Mock(return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b""))
        store = SecretStore(runner=runner)
        self.assertIsNone(store.load())
        store.clear()  # secret-tool also exits 1 when there is nothing left to clear.
        runner.return_value.stderr = b"synthetic-secret-error"
        with self.assertRaises(CredentialError) as result:
            store.load()
        self.assertNotIn("synthetic-secret-error", str(result.exception))

    def test_bad_records_never_leak_their_contents(self):
        for payload in (b"synthetic-sensitive-detail", b'{"version":1,"refresh_token":"synthetic-secret"}',
                        json.dumps({"version": 2, "username": "user", "refresh_token": "synthetic-secret"}).encode()):
            with self.subTest(payload_type=type(payload).__name__):
                store = SecretStore(runner=Mock(return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=b"")))
                with self.assertRaises(CredentialError) as result:
                    store.load()
                self.assertNotIn("synthetic", str(result.exception))

    def test_unavailable_and_locked_service_have_no_plain_file_fallback(self):
        for error in (FileNotFoundError("synthetic-secret"), subprocess.TimeoutExpired("secret-tool", 15)):
            store = SecretStore(runner=Mock(side_effect=error))
            with self.assertRaises(CredentialError) as result:
                store.save("user", b"synthetic-refresh-secret")
            self.assertNotIn("synthetic", str(result.exception))

    def test_lookup_filters_extra_fields_and_clear_is_namespace_scoped(self):
        record = {"version": 1, "username": "user", "refresh_token": "refresh", "access_token": "must-not-return"}
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(record).encode(), stderr=b""))
        store = SecretStore(runner=runner)
        self.assertEqual(store.load(), {"username": "user", "refresh_token": "refresh"})
        store.clear()
        self.assertEqual(runner.call_args.args[0], ["/usr/bin/secret-tool", "clear", "application", "org.orca-flashforge",
                                                  "purpose", "refresh-session"])


class SessionVaultTests(unittest.TestCase):
    def test_new_session_restores_and_rotates_without_a_password(self):
        store = MemoryStore({"username": "user", "refresh_token": "synthetic-initial"})
        vault = SessionVault(store)
        session, result = vault.restore(RestorableSession, "library", "settings", "user")
        self.assertTrue(result["connected"])
        self.assertTrue(vault.remembered)
        self.assertEqual(store.record["refresh_token"], "synthetic-rotated-refresh")
        self.assertNotIn("refresh", json.dumps(result))
        session.close()
        _, restored = SessionVault(store).restore(RestorableSession, "library", "settings", "user")
        self.assertTrue(restored["connected"])

    def test_expired_token_is_forgotten_and_session_closed(self):
        store = MemoryStore({"username": "user", "refresh_token": "expired"})
        session = RestorableSession()
        with self.assertRaisesRegex(AuthenticationError, "caducó"):
            SessionVault(store).restore(lambda *args: session, "library", "settings")
        self.assertTrue(session.closed)
        self.assertIsNone(store.record)

    def test_offline_error_keeps_the_saved_account_for_retry(self):
        store = MemoryStore({"username": "user", "refresh_token": "offline"})
        session = RestorableSession()
        with self.assertRaisesRegex(FlashForgeError, "Servidor"):
            SessionVault(store).restore(lambda *args: session, "library", "settings")
        self.assertTrue(session.closed)
        self.assertEqual(store.record["refresh_token"], "offline")
        self.assertEqual(store.clears, 0)

    def test_failed_store_keeps_live_session_and_reports_memory_only(self):
        store = MemoryStore()
        store.save = Mock(side_effect=CredentialError("unavailable"))
        vault, session = SessionVault(store), RestorableSession()
        vault.attach(session, "user", True)
        result = session.restore("token", "user")
        self.assertTrue(result["connected"])
        self.assertFalse(vault.remembered)
        self.assertIn("no se pudo guardar", vault.warning)
        self.assertIsNone(store.record)

    def test_different_saved_account_is_never_restored_silently(self):
        store = MemoryStore({"username": "other-user", "refresh_token": "old"})
        factory = Mock()
        with self.assertRaisesRegex(CredentialError, "no coincide"):
            SessionVault(store).restore(factory, "library", "settings", "expected-user")
        factory.assert_not_called()

    def test_memory_only_does_not_access_the_keyring(self):
        store = Mock()
        session = RestorableSession()
        SessionVault(store).attach(session, "user", False)
        self.assertIsNone(session._token_sink)
        store.save.assert_not_called()
        store.load.assert_not_called()
        store.clear.assert_not_called()

    def test_empty_keyring_does_not_initialize_the_sdk(self):
        factory = Mock()
        session, result = SessionVault(MemoryStore()).restore(factory, "library", "settings")
        self.assertIsNone(session)
        self.assertFalse(result["connected"])
        factory.assert_not_called()
