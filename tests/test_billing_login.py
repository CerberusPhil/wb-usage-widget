"""Regression coverage for the public login HTTP/state contract."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler


class LoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'billing_login_test_server', Path(__file__).resolve().parents[1] / 'scripts' / 'billing_server.py')
        self.server_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server_module)
        self.car = types.SimpleNamespace(
            start_interactive=lambda **kwargs: None,
            port_alive=lambda: True,
            find_page=lambda: {'type': 'page'},
            renew=lambda **kwargs: 4,
        )
        self.browser_patch = patch.dict(sys.modules, {'cookie_auto_renew': self.car})
        self.browser_patch.start()
        self.addCleanup(self.browser_patch.stop)
        self.workers = []
        self.releases = []
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        self.server_module.cancel_login()
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(2)

    def wait_state(self, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.server_module.login_status()
            if status['state'] == state:
                return status
            time.sleep(0.01)
        self.fail('Login did not reach expected state: ' + state)

    def test_http_cancel_remains_responsive_during_browser_startup(self):
        entered, release = threading.Event(), threading.Event()
        def start_browser(**kwargs):
            self.workers.append(threading.current_thread())
            entered.set()
            release.wait(5)
        self.car.start_interactive = start_browser
        self.releases.append(release)
        server = self.server_module.ThreadingHTTPServer(
            ('127.0.0.1', 0), self.server_module.Handler)
        server.access = self.server_module.LocalAccess()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:%d' % server.server_port
        urlopen = build_opener(ProxyHandler({})).open
        headers = {'Authorization': 'Bearer ' + server.access.capability}
        with urlopen(Request(base + '/api/login', method='POST', headers=headers), timeout=1) as response:
            self.assertEqual(response.status, 202)
            self.assertTrue(json.load(response)['running'])
        self.assertTrue(entered.wait(1))
        with urlopen(Request(base + '/api/login/cancel', method='POST', headers=headers), timeout=1) as response:
            status = json.load(response)
        self.assertFalse(status['running'])
        self.assertEqual(status['state'], 'cancelled')
        release.set()

    def test_old_worker_cannot_replace_new_login_after_cancel(self):
        first_entered, first_release, first_done = (threading.Event() for _ in range(3))
        second_entered, second_release = threading.Event(), threading.Event()
        count = 0
        def start_browser(**kwargs):
            nonlocal count
            count += 1
            self.workers.append(threading.current_thread())
            if count == 1:
                first_entered.set()
                first_release.wait(5)
                first_done.set()
                raise RuntimeError('old startup failed')
            second_entered.set()
            second_release.wait(5)
        self.car.start_interactive = start_browser
        self.releases.extend((first_release, second_release))
        self.server_module.start_login()
        self.assertTrue(first_entered.wait(1))
        cancelled = self.server_module.cancel_login()
        self.assertFalse(cancelled['running'])
        code, new_status = self.server_module.start_login()
        self.assertEqual(code, 202)
        self.assertTrue(second_entered.wait(1))
        first_release.set()
        self.assertTrue(first_done.wait(1))
        # Join the obsolete worker so the assertion observes its terminal update.
        self.workers[0].join(1)
        self.assertFalse(self.workers[0].is_alive())
        current = self.server_module.login_status()
        self.assertTrue(current['running'])
        self.assertEqual(current['generation'], new_status['generation'])
        second_release.set()

    def test_debug_endpoint_without_page_keeps_waiting(self):
        queried, ready, renewed = (threading.Event() for _ in range(3))
        def find_page():
            queried.set()
            return {'type': 'page'} if ready.is_set() else None
        def renew(**kwargs):
            self.workers.append(threading.current_thread())
            renewed.set()
            return 4
        self.car.find_page = find_page
        self.car.renew = renew
        self.server_module.start_login()
        self.assertTrue(queried.wait(1))
        self.assertEqual(self.server_module.login_status()['state'], 'starting')
        self.assertFalse(renewed.is_set())
        ready.set()
        self.assertTrue(renewed.wait(3))

    def test_failed_login_can_be_retried(self):
        def fail_start(**kwargs):
            self.workers.append(threading.current_thread())
            raise RuntimeError('browser unavailable')
        self.car.start_interactive = fail_start
        self.assertEqual(self.server_module.start_login()[0], 202)
        self.assertFalse(self.wait_state('failed')['running'])
        entered, release = threading.Event(), threading.Event()
        def retry_start(**kwargs):
            self.workers.append(threading.current_thread())
            entered.set()
            release.wait(5)
        self.car.start_interactive = retry_start
        self.releases.append(release)
        code, status = self.server_module.start_login()
        self.assertEqual(code, 202)
        self.assertTrue(status['running'])
        self.assertTrue(entered.wait(1))
        release.set()


if __name__ == '__main__':
    unittest.main()
