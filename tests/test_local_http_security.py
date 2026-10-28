"""Exercise the loopback HTTP/session seam using synthetic data only."""
import importlib.util
from html.parser import HTMLParser
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
import re
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from local_http_security import LocalAccess, script_json


class ScriptCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts += 1


class LocalHttpSecurityTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('isolated_billing_server', SCRIPTS / 'billing_server.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.server = self.module.ThreadingHTTPServer(('127.0.0.1', 0), self.module.Handler)
        self.server.access = LocalAccess()
        self.port = self.server.server_port
        self.origin = 'http://127.0.0.1:%d' % self.port
        self.native = {'Authorization': 'Bearer ' + self.server.access.capability}
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        auth = patch.object(self.module, '_auth_status_safe', return_value='not authorized')
        auth.start()
        self.addCleanup(auth.stop)

    def request(self, path, method='GET', headers=None, body=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            request_headers = dict(headers or {})
            if body is not None:
                body = json.dumps(body)
                request_headers['Content-Type'] = 'application/json'
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def session(self):
        code, _, body = self.request('/api/bootstrap-ticket', 'POST', self.native, {'path': '/full'})
        self.assertEqual(code, 200)
        ticket = json.loads(body)['ticket']
        code, headers, body = self.request('/api/session', 'POST', {'Origin': self.origin}, {'ticket': ticket})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['path'], '/full')
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return {'Cookie': headers['Set-Cookie'].split(';')[0], 'Origin': self.origin}, ticket

    def test_no_capability_cannot_read_or_mutate(self):
        with patch.object(self.module, 'start_login') as login, patch.object(self.module.STORE, 'sync_now') as sync:
            for path in ('/', '/widget', '/full', '/api/data'):
                self.assertEqual(self.request(path)[0], 401)
            for path in ('/api/login', '/api/login/cancel', '/api/sync', '/api/bootstrap-ticket'):
                self.assertEqual(self.request(path, 'POST', {'Origin': self.origin})[0], 401)
            login.assert_not_called()
            sync.assert_not_called()

    def test_untrusted_host_origin_and_fetch_site_denied_even_with_capability(self):
        for extra in ({'Host': 'attacker.example:%d' % self.port},
                      {'Origin': 'https://attacker.example'}, {'Origin': 'null'},
                      {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}):
            headers = dict(self.native, **extra)
            self.assertEqual(self.request('/api/data', headers=headers)[0], 403)
            self.assertEqual(self.request('/api/login', 'POST', headers)[0], 403)

    def test_mutations_reject_get_and_debug_route_is_removed(self):
        for path in ('/api/login', '/api/sync', '/api/login/cancel'):
            self.assertEqual(self.request(path, headers=self.native)[0], 405)
        self.assertEqual(self.request('/__dbg?m=private', headers=self.native)[0], 404)

    def test_session_works_with_no_data_and_cannot_be_replayed(self):
        headers, ticket = self.session()
        code, response_headers, body = self.request('/full', headers=headers)
        self.assertEqual(code, 200)
        self.assertIn('loginBtn', body.decode())
        self.assertNotIn('Access-Control-Allow-Origin', response_headers)
        self.assertEqual(response_headers['Referrer-Policy'], 'no-referrer')
        self.assertNotIn(self.server.access.capability.encode(), body)
        self.assertEqual(self.request('/api/data', headers=headers)[0], 200)
        self.assertEqual(self.request('/api/session', 'POST', {'Origin': self.origin}, {'ticket': ticket})[0], 401)
        with patch.object(self.module, 'start_login', return_value=(202, {'running': True})) as login:
            self.assertEqual(self.request('/api/login', 'POST', headers)[0], 202)
            login.assert_called_once()
        headers.pop('Origin')
        self.assertEqual(self.request('/api/login', 'POST', headers)[0], 403)

    def test_bootstrap_is_nonprivileged_and_ticket_is_short_lived(self):
        code, _, body = self.request('/bootstrap')
        self.assertEqual(code, 200)
        self.assertNotIn(self.server.access.capability.encode(), body)
        self.assertIn(b'history.replaceState', body)
        with patch('local_http_security.time.monotonic', return_value=100):
            ticket = self.server.access.ticket('/widget')
        with patch('local_http_security.time.monotonic', return_value=161):
            self.assertEqual(self.request('/api/session', 'POST', {'Origin': self.origin}, {'ticket': ticket})[0], 401)

    def test_native_helpers_support_separate_process_storage_contract(self):
        import local_http_security as security
        from credential_store import atomic_write
        with tempfile.TemporaryDirectory() as directory:
            cap_path = Path(directory) / 'http.cap'
            atomic_write(str(cap_path), self.server.access.capability.encode('ascii'))
            with patch.object(security, '_capability_path', return_value=str(cap_path)):
                url = security.client_url(self.port, '/full')
                self.assertTrue(url.startswith(self.origin + '/bootstrap#'))
                self.assertNotIn('?', url)
                self.assertNotIn(self.server.access.capability, url)
                self.assertFalse(security.local_request(self.port)['ok'])

    def test_served_and_static_script_data_cannot_close_script_element(self):
        attack = '</script><script>globalThis.pwned=true</script><img src=x onerror=alert(1)>'
        data = {'title': attack, 'line': '\u2028\u2029', 'amp': '&'}
        encoded = script_json(data)
        self.assertNotIn('<', encoded)
        self.assertEqual(json.loads(encoded), data)
        for filename in ('billing_template.html', 'billing_widget_template.html'):
            template = (SCRIPTS / filename).read_text(encoding='utf-8')
            baseline = ScriptCounter()
            baseline.feed(template)
            rendered = ScriptCounter()
            rendered.feed(template.replace('/*__DATA__*/', encoded))
            self.assertEqual(rendered.scripts, baseline.scripts)
        with patch.object(self.module.STORE, 'payload', return_value=data):
            self.module.STORE.data = data
            for path in ('/widget', '/full'):
                code, _, body = self.request(path, headers=self.native)
                self.assertEqual(code, 200)
                self.assertNotIn(attack.encode(), body)
                self.assertIn(b'\\u003c/script\\u003e', body)
        import billing_dashboard_data as bdd
        fixture = {'stats': {'total_n': 0, 'credit_total': 0, 'free_n': 0,
                             'range_start': '', 'range_end': '', 'client_dist': {}},
                   'days': {}, 'balance': None, 'title': attack}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'dashboard.html'
            with patch.object(bdd, 'build_data', return_value=fixture), patch.object(bdd, 'OUT_HTML', str(output)), patch('builtins.print'):
                bdd.main()
            self.assertNotIn(attack, output.read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute the dashboard renderer')
    def test_dynamic_session_model_and_client_markup_is_escaped(self):
        attack = '<img src=x onerror="globalThis.pwned=true">'
        model = {'name': attack, 'n': 1, 'inp': 2, 'out': 3, 'think': 0,
                 'tokens': 5, 'credit': 1, 'hit_rate': 0, 'tc': 5, 'tf': 0, 'te': 0}
        session = {'sid': '<img>', 'title': attack, 'agent': attack, 'sub': True,
                   'tmin': attack, 'tmax': attack, 'n': 1, 'tokens': 5,
                   'inp': 2, 'out': 3, 'think': 0, 'hit_rate': 0,
                   'credit': 1, 'tc': 5, 'tf': 0, 'te': 0, 'models': [model]}
        data = {'generated_at': '2026-10-28 12:00:00',
                'days': {'2026-10-28': {'credit': 1, 'req_n': 1, 'free_n': 0,
                                        'by_model': {attack: 1}, 'by_model_n': {attack: 1}}},
                'details': {'2026-10-28': [{'t': attack, 'credit': 1, 'model': attack,
                                           'client': attack, 'input': attack}]},
                'sessions': [session], 'token_stats': {'by_model': [model]},
                'stats': {'range_start': attack, 'range_end': attack, 'total_n': 1,
                          'credit_total': 1, 'free_n': 0, 'client_dist': {attack: 1}},
                'meta': {'synced_at': attack}}
        template = (SCRIPTS / 'billing_template.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script>(.*?)</script>', template, re.S)
        script = '\n'.join(scripts).replace('/*__DATA__*/', script_json(data))
        # A small DOM adapter records markup sinks while executing the actual
        # shipped renderer, including startup and all-session rendering.
        harness = r"""
const vm=require('node:vm'), fs=require('node:fs');
const nodes=new Map();
function node(key){
  if(!nodes.has(key)) nodes.set(key,{
    innerHTML:'',textContent:'',dataset:{},style:{setProperty(){}},
    classList:{toggle(){},add(){},remove(){}},addEventListener(){},
    querySelectorAll(){return [];},getBoundingClientRect(){return {width:1,height:1};}
  });
  return nodes.get(key);
}
const context={document:{body:node('body'),querySelector:node,
    getElementById:id=>node('#'+id),querySelectorAll:()=>[]},
  localStorage:{getItem:k=>k==='wb_winrange'?'all':null,setItem(){}},
  performance:{now:()=>0},requestAnimationFrame(){},setTimeout(){},
  innerWidth:1000,innerHeight:1000};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0,'utf8'),context);
process.stdout.write(JSON.stringify(Object.fromEntries(
  [...nodes].map(([key,value])=>[key,value.innerHTML]))));
"""
        result = subprocess.run([shutil.which('node'), '-e', harness], input=script,
                                capture_output=True, text=True, check=True, timeout=10)
        sinks = json.loads(result.stdout)
        for key in ('#sessionPanel', '#detailPanel', '#foot', '#modelPanel'):
            self.assertIn('&lt;img', sinks[key], key)
            self.assertNotIn('<img', sinks[key], key)
            self.assertNotIn('<script', sinks[key], key)

    def test_error_details_do_not_reach_payload(self):
        fake = type('Sync', (), {'run_sync': staticmethod(lambda **kwargs: (False, 'SECRET_VALUE'))})
        with patch.dict(sys.modules, {'server_usage_sync': fake}), patch.object(self.module.STORE, 'refresh'):
            self.module.STORE.sync_now()
        _, _, body = self.request('/api/data', headers=self.native)
        self.assertNotIn(b'SECRET_VALUE', body)


if __name__ == '__main__':
    unittest.main()
