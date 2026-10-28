"""Synthetic-only tests: never touch real Keychain entries or user auth files."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import credential_store as store
import cookie_auto_renew as browser
import save_cookie
import server_usage_sync as sync


class CredentialStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.protected = {}
        self.legacy = Path(self.temp.name, 'server_usage.json')
        self.record = {'cookie': 'session=synthetic-not-a-real-secret', 'ua': 'SyntheticBrowser'}
        patches = [
            patch.object(store, '_legacy_path', side_effect=lambda kind: str(Path(self.temp.name, store._LEGACY[kind]))),
            patch.object(store, '_protected_read', side_effect=lambda kind: self.protected.get(kind)),
            patch.object(store, '_protected_write', side_effect=self.protected_write),
            patch.object(store, '_protected_delete', side_effect=lambda kind: self.protected.pop(kind, None)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def protected_write(self, kind, data, cancel_event):
        store._cancel(cancel_event)
        self.protected[kind] = data

    def write_legacy(self):
        self.legacy.write_text(json.dumps(self.record), encoding='utf-8')

    def test_legacy_cookie_migrates_and_remains_usable(self):
        self.write_legacy()
        self.assertEqual(store.load_record('cookie'), self.record)
        self.assertFalse(self.legacy.exists())
        self.assertEqual(json.loads(self.protected['cookie']), self.record)
        self.assertEqual(store.load_record('cookie'), self.record)

    def test_denied_store_preserves_legacy_and_never_falls_back(self):
        self.write_legacy()
        with patch.object(store, '_protected_write', side_effect=store.StorageError('denied')):
            with self.assertRaises(store.StorageError):
                store.load_record('cookie')
        self.assertEqual(json.loads(self.legacy.read_text()), self.record)
        self.assertNotIn('cookie', self.protected)

    def test_readback_failure_preserves_legacy(self):
        self.write_legacy()
        with patch.object(store, '_protected_read', return_value=None):
            with self.assertRaises(store.StorageError):
                store.load_record('cookie')
        self.assertTrue(self.legacy.exists())

    def test_cancelled_migration_does_not_write_or_delete(self):
        self.write_legacy()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(store.StorageCancelled):
            store.load_record('cookie', cancel_event=cancel)
        self.assertTrue(self.legacy.exists())
        self.assertFalse(self.protected)

    def test_cancellation_during_os_prompt_restores_previous_record(self):
        previous = b'{"cookie": "synthetic-old"}'
        self.protected['cookie'] = previous
        cancel = threading.Event()
        def commit(kind, data, event):
            self.protected[kind] = data
            cancel.set()
        with patch.object(store, '_protected_write', side_effect=commit):
            with self.assertRaises(store.StorageCancelled):
                store.save_record('cookie', self.record, cancel_event=cancel)
        self.assertEqual(self.protected['cookie'], previous)

    def test_cancelled_first_migration_does_not_leave_empty_record(self):
        self.write_legacy()
        cancel = threading.Event()
        def commit(kind, data, event):
            self.protected[kind] = data
            cancel.set()
        with patch.object(store, '_protected_write', side_effect=commit):
            with self.assertRaises(store.StorageCancelled):
                store.load_record('cookie', cancel_event=cancel)
        self.assertTrue(self.legacy.exists())
        self.assertNotIn('cookie', self.protected)
        self.assertEqual(store.load_record('cookie'), self.record)

    def test_atomic_private_write_preserves_previous_on_cancel(self):
        target = Path(self.temp.name, 'private', 'fixture')
        store.atomic_write(target, b'synthetic-old')
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(store.StorageCancelled):
            store.atomic_write(target, b'synthetic-new', cancel)
        self.assertEqual(store.read_private(target), b'synthetic-old')
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipIf(os.name == 'nt', 'symlink privilege varies on Windows')
    def test_legacy_symlink_is_never_read(self):
        other = Path(self.temp.name, 'other-app-fixture')
        other.write_text(json.dumps(self.record))
        self.legacy.symlink_to(other)
        with self.assertRaises(store.StorageError):
            store.load_record('cookie')
        self.assertTrue(other.exists())
        self.assertFalse(self.protected)

    def test_different_legacy_account_is_not_deleted_by_passive_read(self):
        self.write_legacy()
        current = {'cookie': 'session=synthetic-different-account'}
        self.protected['cookie'] = json.dumps(current).encode()
        self.assertEqual(store.load_record('cookie'), current)
        self.assertEqual(json.loads(self.legacy.read_text()), self.record)

    def test_identical_migration_leftover_is_removed(self):
        self.write_legacy()
        self.protected['cookie'] = json.dumps(self.record).encode()
        self.assertEqual(store.load_record('cookie'), self.record)
        self.assertFalse(self.legacy.exists())

    def test_keychain_denial_is_retryable_without_plaintext_fallback(self):
        self.write_legacy()
        with patch.object(store, '_protected_read', side_effect=store.StorageError('denied')):
            with self.assertRaises(store.StorageError):
                store.load_record('cookie')
        self.assertTrue(self.legacy.exists())
        self.assertEqual(store.load_record('cookie'), self.record)
        self.assertFalse(self.legacy.exists())

    def test_passive_status_does_not_wait_for_interactive_save(self):
        acquired, release = threading.Event(), threading.Event()
        def hold_authorization():
            with store._LOCK:
                acquired.set()
                release.wait(3)
        worker = threading.Thread(target=hold_authorization)
        worker.start()
        self.assertTrue(acquired.wait(1))
        try:
            with patch.object(store, '_protected_read') as read, self.assertRaises(store.StorageError):
                store.load_record('cookie')
            read.assert_not_called()
        finally:
            release.set()
            worker.join(3)

    def test_only_explicit_save_allows_one_interactive_query(self):
        reads = []
        def read(kind, interactive=False):
            reads.append(interactive)
            return self.protected.get(kind)
        with patch.object(store, '_protected_read', side_effect=read):
            store.save_record('cookie', self.record, interactive=True)
        self.assertEqual(reads, [True, False])


class CredentialTransportTests(unittest.TestCase):
    def test_redirects_never_forward_cookie_or_tokens(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        destinations = []
        class RedirectServer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == '/target':
                    destinations.append(dict(self.headers))
                    self.send_response(200)
                else:
                    self.send_response(int(self.path.strip('/')))
                    self.send_header('Location', '/target')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), RedirectServer)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(sync, '_proxy_url', return_value=None):
                opener = sync._opener()
            for status in (301, 302, 303, 307, 308):
                with self.subTest(status=status), self.assertRaises(HTTPError):
                    opener.open(Request('http://127.0.0.1:%d/%d' % (server.server_port, status),
                                        headers={'Cookie': 'session=synthetic',
                                                 'Authorization': 'Bearer synthetic',
                                                 'X-Refresh-Token': 'synthetic'}), timeout=2)
            self.assertEqual(destinations, [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_bearer_domain_cannot_control_destination(self):
        with patch.object(sync, '_opener', side_effect=AssertionError('network reached')):
            with self.assertRaises(ValueError):
                sync.api('/billing/meter/get-user-request-usage', {},
                         {'mode': 'bearer', 'domain': 'evil.invalid', 'token': 'synthetic'})

    def test_rt_status_never_exposes_token_fragments(self):
        secret = 'synthetic-prefix-middle-suffix'
        with patch.object(sync, 'load_record', return_value={'rt': secret}):
            result = json.dumps(sync.rt_info())
        self.assertNotIn(secret, result)
        self.assertNotIn('synthetic', result)
        self.assertNotIn('suffix', result)

    def test_validation_errors_do_not_echo_exception_contents(self):
        class FailingOpener:
            def open(self, *args, **kwargs):
                raise ValueError('synthetic-secret-header-content')
        with patch.object(sync, '_opener', return_value=FailingOpener()):
            ok, message = save_cookie.validate('session=synthetic')
            refreshed = sync.rt_refresh('synthetic')
        self.assertFalse(ok)
        self.assertEqual(message, 'ValueError')
        self.assertEqual(refreshed['msg'], 'ValueError')

    def test_cookie_cli_does_not_echo_unknown_secret_argument(self):
        stderr = io.StringIO()
        with patch.object(sys, 'argv', ['save_cookie.py', 'synthetic-secret']), \
                contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            save_cookie.main()
        self.assertNotIn('synthetic-secret', stderr.getvalue())

    def test_renewal_filters_cookie_domains_and_uses_callback(self):
        cancel = threading.Event()
        captured = {}
        calls = []
        class Socket:
            def close(self):
                pass
        def cdp(ws, method, params, message_id, **kwargs):
            calls.append((method, params))
            if method == 'Network.getCookies':
                return {'cookies': [
                    {'name': 'session', 'value': 'synthetic', 'domain': '.codebuddy.cn'},
                    {'name': 'other', 'value': 'never-capture', 'domain': 'unrelated.invalid'},
                ]}
            if method == 'Runtime.evaluate':
                return {'result': {'value': 'SyntheticBrowser'}}
            return {}
        def save(kind, record, **kwargs):
            captured.update(record)
        with patch.object(browser, 'load_creds', return_value={}), \
                patch.object(browser, 'port_alive', return_value=True), \
                patch.object(browser, 'find_page', return_value={'webSocketDebuggerUrl': 'synthetic'}), \
                patch.object(browser, '_ws_path', return_value='/devtools/page/synthetic'), \
                patch.object(browser, 'WS', return_value=Socket()), \
                patch.object(browser, 'cdp_call', side_effect=cdp), \
                patch.object(cancel, 'wait', return_value=False), \
                patch.object(browser.sc, 'validate', return_value=(True, 'OK')), \
                patch.object(browser.sc, 'UA', 'SyntheticBrowser'), \
                patch.object(browser, 'save_record', side_effect=save), \
                patch.object(browser, 'log'), \
                patch.object(browser, 'browser_close') as close, \
                patch.object(browser, 'trigger_sync') as trigger:
            callback = lambda: None
            self.assertEqual(browser.renew(False, cancel, callback), 0)
        self.assertEqual(captured['cookie'], 'session=synthetic')
        self.assertIn(('Network.getCookies', {'urls': [browser.ORIGIN, browser.START_URL]}), calls)
        trigger.assert_called_once_with(callback)
        close.assert_called_once_with()


class KeychainInteractionTests(unittest.TestCase):
    def test_passive_query_disables_ui_and_restores_policy_on_denial(self):
        changes = []
        class Function:
            def __init__(self, call):
                self.call = call
            def __call__(self, *args):
                return self.call(*args)
        class Security:
            def __init__(self):
                def get(flag):
                    flag._obj.value = 1
                    return 0
                self.SecKeychainGetUserInteractionAllowed = Function(get)
                self.SecKeychainSetUserInteractionAllowed = Function(lambda flag: changes.append(flag) or 0)
        with patch.object(store.ctypes, 'CDLL', return_value=Security()), \
                patch.object(store, '_keychain_operation', side_effect=store.StorageError('denied')):
            with self.assertRaises(store.StorageError):
                store._keychain('cookie')
        self.assertEqual(changes, [False, 1])

    def test_access_cache_is_bound_to_authorized_refresh_token(self):
        with patch.object(sync, 'load_record', return_value={
                'at': 'synthetic-previous-account', 'ts': 9999999999,
                'rt_hash': 'different-token'}):
            self.assertIsNone(sync._at_cache_read('synthetic-new-token'))


@unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI native round trip')
class WindowsProtectionTests(unittest.TestCase):
    def test_dpapi_ciphertext_roundtrip(self):
        value = b'synthetic-not-a-real-credential'
        encrypted = store._dpapi(value)
        self.assertNotIn(value, encrypted)
        self.assertEqual(store._dpapi(encrypted, decrypt=True), value)


if __name__ == '__main__':
    unittest.main()
