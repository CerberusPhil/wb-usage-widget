"""A stale browser port must never attach to an unrelated local service."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cookie_auto_renew as browser


class BrowserEndpointTests(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.version = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(owner.version).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.profile_patch = patch.object(browser, 'PROFILE', self.profile.name)
        self.profile_patch.start()
        self.secured_patch = patch.object(browser, '_PROFILE_SECURED', False)
        self.secured_patch.start()
        self.original_port = browser.PORT
        self.process_patch = patch.object(browser, '_BROWSER_PROCESS', None)
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)
        Path(self.profile.name, 'DevToolsActivePort').write_text(
            '%d\n/devtools/browser/owned-session\n' % self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.profile_patch.stop()
        self.secured_patch.stop()
        browser.PORT = self.original_port
        self.profile.cleanup()

    def test_port_reused_by_relay_is_not_a_login_session(self):
        self.version = {'ompRelayVersion': 'test', 'error': 'extension is not connected'}
        self.assertFalse(browser.port_alive())

    def test_stale_profile_does_not_attach_to_different_browser(self):
        self.version = {
            'Browser': 'Chrome/153',
            'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/browser/other-session' % self.server.server_port,
        }
        self.assertFalse(browser.port_alive())

    def test_profile_identity_selects_its_dynamic_endpoint(self):
        self.version = {
            'Browser': 'Chrome/153',
            'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/browser/owned-session' % self.server.server_port,
        }
        self.assertTrue(browser.port_alive())
        self.assertEqual(browser.PORT, self.server.server_port)

    def test_matching_identity_on_non_loopback_host_is_rejected(self):
        self.version = {
            'Browser': 'Chrome/153',
            'webSocketDebuggerUrl': 'ws://untrusted.invalid:%d/devtools/browser/owned-session' % self.server.server_port,
        }
        self.assertFalse(browser.port_alive())

    def test_page_selection_requires_exact_official_https_host(self):
        with patch.object(browser, 'http_json', return_value=[
            {'type': 'page', 'url': 'https://codebuddy.cn.evil.invalid/', 'id': 'evil'},
            {'type': 'page', 'url': 'http://www.codebuddy.cn/', 'id': 'http'},
            {'type': 'page', 'url': 'https://www.codebuddy.cn/profile/plans-usage', 'id': 'official'},
        ]):
            self.assertEqual(browser.find_page()['id'], 'official')

    def test_no_fallback_to_unrelated_login_page(self):
        with patch.object(browser, 'http_json', return_value=[
            {'type': 'page', 'url': 'https://unrelated.invalid/'},
        ]):
            self.assertIsNone(browser.find_page())

    def test_all_platforms_launch_dynamic_loopback_debugging(self):
        with patch.object(browser, 'find_browser', return_value='synthetic-browser'), \
                patch.object(browser, '_secure_profile'), \
                patch.object(browser.subprocess, 'Popen') as launch:
            browser.launch_browser(headless=False, detach=False)
        args = launch.call_args.args[0]
        self.assertIn('--remote-debugging-port=0', args)
        self.assertIn('--remote-debugging-address=127.0.0.1', args)
        self.assertIn('--user-data-dir=' + self.profile.name, args)

    def test_browser_and_page_websocket_paths_remain_usable(self):
        with patch.object(browser, 'PORT', 12345):
            for path in ('/devtools/browser/owned-session', '/devtools/page/owned-session'):
                self.assertEqual(browser._ws_path('ws://127.0.0.1:12345' + path), path)
            for path in ('/devtools/browser/bad path', '/devtools/browser/bad\\path'):
                with self.assertRaises(ValueError):
                    browser._ws_path('ws://127.0.0.1:12345' + path)

    def test_endpoint_ready_before_official_page_does_not_finish_startup(self):
        cancel = threading.Event()
        pages = iter([None, {'type': 'page'}])
        with patch.object(browser, 'port_alive', return_value=True), \
                patch.object(browser, 'find_page', side_effect=lambda: next(pages)):
            self.assertTrue(browser.wait_port(timeout=2, cancel_event=cancel))
        self.assertEqual(list(pages), [])

    def test_close_falls_back_only_to_owned_process(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with patch.object(browser, '_BROWSER_PROCESS', process), \
                    patch.object(browser, '_close_endpoint', return_value=False), \
                    patch.object(browser, 'port_alive', return_value=False):
                self.assertTrue(browser.browser_close())
            self.assertIsNotNone(process.poll())
            self.assertIsNone(unrelated.poll())
        finally:
            for child in (process, unrelated):
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)

    def test_cancelled_renewal_does_not_launch_or_write_credentials(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(browser, 'launch_browser', side_effect=AssertionError('browser launched')):
            self.assertEqual(browser.renew(False, cancel_event=cancel), 4)


if __name__ == '__main__':
    unittest.main()
