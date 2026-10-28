"""Loopback authentication, private native handoff, and HTML-safe JSON.

Native callers use client_url() for navigation and local_request() for HTTP.
Capabilities never enter a query string; browser tickets expire after 60 seconds
and can be redeemed once. The owner-private runtime file supports source-mode
server and widget processes without exposing a public ticket-minting route.
"""
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from urllib.request import Request, build_opener, ProxyHandler


def script_json(value):
    """Serialize data in a script element without allowing HTML parser breakout."""
    return (json.dumps(value, ensure_ascii=False).replace('&', '\\u0026')
            .replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def _capability_path(port):
    from browser_runtime import app_data_dir
    return os.path.join(app_data_dir(), 'runtime', 'http-%d.cap' % int(port))


def publish_capability(port, capability):
    from credential_store import atomic_write
    atomic_write(_capability_path(port), capability.encode('ascii'))


def local_request(port=8790, path='/api/data', method='GET', payload=None):
    """Authenticated native request; no proxy, redirects, or secret-bearing URL."""
    from credential_store import read_private
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if path not in ('/api/data', '/api/sync', '/api/login', '/api/login/cancel', '/api/bootstrap-ticket'):
        raise ValueError('Unsupported local operation')
    capability = read_private(_capability_path(port)).decode('ascii')
    request = Request('http://127.0.0.1:%d%s' % (int(port), path),
                      data=json.dumps(payload or {}).encode('utf-8') if method == 'POST' else None,
                      method=method, headers={'Authorization': 'Bearer ' + capability,
                                              'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
        return json.load(response)


def client_url(port=8790, path='/widget'):
    """Return a one-use navigation URL. Callers MUST NOT log this URL."""
    if path not in ('/', '/widget', '/full'):
        raise ValueError('Unsupported local view')
    value = local_request(port, '/api/bootstrap-ticket', method='POST', payload={'path': path})
    return 'http://127.0.0.1:%d/bootstrap#%s' % (int(port), value['ticket'])


class LocalAccess:
    def __init__(self):
        self.capability = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.tickets = {}
        self.sessions = {}

    @staticmethod
    def cookie_name(port):
        return 'wb_session_%d' % port

    def native(self, headers):
        supplied = headers.get('Authorization', '')
        return secrets.compare_digest(supplied.encode('utf-8'),
                                      ('Bearer ' + self.capability).encode('ascii'))

    def authenticated(self, headers, port):
        if self.native(headers):
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('Cookie', ''))
        except CookieError:
            return False
        item = cookie.get(self.cookie_name(port))
        if item is None:
            return False
        with self.lock:
            expiry = self.sessions.get(item.value, 0)
            if expiry <= time.monotonic():
                self.sessions.pop(item.value, None)
                return False
            self.sessions[item.value] = time.monotonic() + 86400
        return True

    def ticket(self, path):
        if path not in ('/', '/widget', '/full'):
            raise ValueError('Unsupported local view')
        now = time.monotonic()
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v[0] > now}
            if len(self.tickets) >= 256:
                raise ValueError('Too many pending views')
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = (now + 60, path)
        return ticket

    def redeem(self, ticket):
        if not isinstance(ticket, str):
            return None
        with self.lock:
            entry = self.tickets.pop(ticket, None)
            now = time.monotonic()
            if entry is None or entry[0] <= now:
                return None
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 256:
                return None
            session = secrets.token_urlsafe(32)
            self.sessions[session] = now + 86400
            return session, entry[1]


BOOTSTRAP_HTML = b'''<!doctype html><html><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer"><title>WorkBuddy</title></head>
<body><p id="status">Opening WorkBuddy...</p><script>
(async function(){
  const ticket=location.hash.slice(1);
  history.replaceState(null,'','/bootstrap');
  try {
    if(!ticket) throw new Error();
    const response=await fetch('/api/session',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});
    if(!response.ok) throw new Error();
    const value=await response.json();
    location.replace(value.path);
  } catch(e) { document.getElementById('status').textContent=
      'Please reopen this view from the WorkBuddy tray.'; }
})();
</script></body></html>'''
