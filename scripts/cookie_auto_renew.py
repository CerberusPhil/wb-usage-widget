# -*- coding: utf-8 -*-
"""cookie_auto_renew.py - 专用浏览器配置档自动续期/提取积分同步 cookie（方案 B）。

背景（2026-09-28 与 Philip 约定）：积分账单同步依赖浏览器 cookie（滑动续期 7 天），
人工复制环节用本脚本归零——独立 user-data-dir（所有平台随机回环调试端口），登录一次后
每日自动化 renew：访问 plans-usage（服务端滑动续期）→ CDP 读 cookie（可读 HttpOnly）
→ 写 macOS Keychain / Windows DPAPI → 触发挂件同步。

⚠️ 实测关键（2026-09-28）：服务端风控把新登录会话绑定到「签发时的 User-Agent」，
   校验必须用专用浏览器的真实 UA；cookie 必须取 Network.getCookies(urls=[目标URL])
   的「浏览器发送视角」集合（不是自己按域名拼）。
   headless 模式 UA 会带 HeadlessChrome 标记 → 启动时用 --user-agent 强制回放登录 UA。

用法：
  python cookie_auto_renew.py login              # 弹出登录窗口（仅首次，保持打开让 Philip 登录）
  python cookie_auto_renew.py renew [--headless] # 续期+提取+写入+触发同步（自动化用 --headless）
  python cookie_auto_renew.py check              # 只读：当前已存 cookie 剩余有效期

安全：cookie 内容永不打印；仅保护存储，不读取日常浏览器配置档。
依赖：仅标准库（自带最小 WebSocket 客户端，自动化托管 Python 无第三方包也能跑）。
"""
import base64
import datetime
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, '_tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import save_cookie as sc  # noqa: E402  （复用 validate / session_expiry）
from browser_runtime import app_data_dir, find_browser
from credential_store import load_record, save_record, private_tree, read_private, StorageCancelled, StorageError
from server_usage_sync import NoRedirect

PORT = 0
# Windows 沿用已有的挂件私有目录，不迁移或读取日常浏览器 profile。
PROFILE = os.path.join(app_data_dir(), 'browser_cookie_profile' if sys.platform == 'darwin'
                       else 'edge_cookie_profile')
START_URL = 'https://www.codebuddy.cn/profile/plans-usage'
ORIGIN = 'https://www.codebuddy.cn/'
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
_BROWSER_LOCK = threading.Lock()
_LAUNCHED_HEADLESS = False
_PROFILE_SECURED = False
_BROWSER_PROCESS = None


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m))


def _secure_profile():
    global _PROFILE_SECURED
    if not _PROFILE_SECURED:
        private_tree(PROFILE)
        _PROFILE_SECURED = True


def port_alive():
    global PORT
    _secure_profile()
    try:
        lines = read_private(os.path.join(PROFILE, 'DevToolsActivePort')).decode('utf-8').splitlines()
        port, expected_path = int(lines[0]), lines[1]
        if not 0 < port < 65536 or not expected_path.startswith('/devtools/browser/'):
            return False
        with OP.open('http://127.0.0.1:%d/json/version' % port, timeout=2) as response:
            version = json.load(response)
        address = urlsplit(version.get('webSocketDebuggerUrl', ''))
        if (not version.get('Browser') or address.scheme != 'ws'
                or address.hostname not in ('127.0.0.1', 'localhost')
                or address.port != port or address.path != expected_path
                or address.username or address.password or address.query or address.fragment):
            return False
        PORT = port
        return True
    except (OSError, ValueError, IndexError):
        return False


def load_creds(cancel_event=None):
    return load_record('cookie', cancel_event=cancel_event)


def launch_browser(headless, detach, ua=None):
    global _LAUNCHED_HEADLESS, _BROWSER_PROCESS
    executable = find_browser()
    _secure_profile()
    args = [executable,
            '--user-data-dir=' + PROFILE,
            '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port=0',
            '--no-first-run', '--no-default-browser-check',
            '--disable-features=Translate',
            START_URL]
    if headless:
        args.insert(1, '--headless=new')
        if ua:                                   # headless UA 带 HeadlessChrome 标记，强制回放登录 UA
            args.insert(2, '--user-agent=' + ua)
    options = {'close_fds': True, 'stdout': subprocess.DEVNULL,
               'stderr': subprocess.DEVNULL}
    if sys.platform == 'win32':
        options['creationflags'] = (subprocess.DETACHED_PROCESS |
                                    subprocess.CREATE_NEW_PROCESS_GROUP) if detach else 0
    elif detach:
        options['start_new_session'] = True
    process = subprocess.Popen(args, **options)
    _BROWSER_PROCESS = process
    _LAUNCHED_HEADLESS = headless
    return process


def wait_port(timeout=25, cancel_event=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return False
        try:
            if port_alive() and find_page():
                return True
        except (OSError, ValueError, KeyError):
            pass
        if cancel_event is None:
            time.sleep(0.5)
        elif cancel_event.wait(0.5):
            return False
    return False


def http_json(path):
    if not port_alive():
        raise ConnectionError('Dedicated browser identity unavailable')
    with OP.open('http://127.0.0.1:%d%s' % (PORT, path), timeout=5) as response:
        return json.load(response)


# ---------- 最小 WebSocket 客户端（仅够 CDP 使用） ----------
class WS:
    def __init__(self, host, port, path):
        self.s = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (path, host, port, key))
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError('WS 握手失败')
            buf += chunk
        if b' 101 ' not in buf.split(b'\r\n', 1)[0]:
            self.s.close()
            raise ConnectionError('WS handshake rejected')

    def send(self, text):
        data = text.encode()
        mask = secrets.token_bytes(4)
        head = b'\x81'
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _readn(self, n):
        buf = b''
        while len(buf) < n:
            c = self.s.recv(n - len(buf))
            if not c:
                raise ConnectionError('WS 断开')
            buf += c
        return buf

    def recv_text(self, timeout=10):
        self.s.settimeout(timeout)
        payload = b''
        while True:
            b1, b2 = self._readn(2)
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack('>H', self._readn(2))[0]
            elif ln == 127:
                ln = struct.unpack('>Q', self._readn(8))[0]
            payload += self._readn(ln)
            if op in (1, 2):            # 文本/二进制完整帧（CDP 常规不分片）
                return payload.decode('utf-8', 'replace')
            if op == 8:
                raise ConnectionError('WS 对端关闭')
            # 0x0 分片继续收，9/10 ping/pong 忽略

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def cdp_call(ws, method, params, msg_id, timeout=15):
    ws.send(json.dumps({'id': msg_id, 'method': method, 'params': params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            m = json.loads(ws.recv_text(timeout=max(1, deadline - time.time())))
        except socket.timeout:
            break
        if m.get('id') == msg_id:
            if 'error' in m:
                raise RuntimeError('Browser protocol request failed')
            return m.get('result', {})
    raise TimeoutError('%s 无响应' % method)


def _ws_path(ws_url):
    address = urlsplit(ws_url)
    if (address.scheme != 'ws' or address.hostname not in ('127.0.0.1', 'localhost')
            or address.port != PORT or address.username or address.password
            or address.query or address.fragment
            or not address.path.startswith(('/devtools/page/', '/devtools/browser/'))
            or '\\' in address.path or any(c.isspace() for c in address.path)):
        raise ValueError('Unexpected browser debugging endpoint')
    return address.path


def _official_page(url):
    address = urlsplit(url)
    return (address.scheme == 'https' and address.hostname == 'www.codebuddy.cn'
            and address.port in (None, 443) and not address.username and not address.password)


def find_page():
    targets = http_json('/json/list')
    return next((t for t in targets if t.get('type') == 'page'
                 and _official_page(t.get('url') or '')), None)


def _close_endpoint():
    """Close only the endpoint whose identity matches the private profile."""
    ws = None
    try:
        if not port_alive():
            return True
        ver = http_json('/json/version')
        ws = WS('127.0.0.1', PORT, _ws_path(ver['webSocketDebuggerUrl']))
        ws.send(json.dumps({'id': 99, 'method': 'Browser.close'}))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not port_alive():
                return True
            time.sleep(0.2)
        return False
    except Exception:
        return False
    finally:
        if ws:
            ws.close()


def browser_close():
    closed = _close_endpoint()
    process = _BROWSER_PROCESS
    if process is None or process.poll() is not None:
        return closed
    # Only this application's own Popen handle may be terminated. Never discover
    # or kill a browser by name/PID/profile: it could belong to the user.
    try:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        return not port_alive()
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_interactive(cancel_event=None):
    """Make the dedicated login window visible; never reuse a hidden login browser."""
    cancel_event = cancel_event or threading.Event()
    while not _BROWSER_LOCK.acquire(timeout=0.2):
        if cancel_event.is_set():
            return
    try:
        if cancel_event.is_set():
            return
        if port_alive():
            version = http_json('/json/version')
            if _LAUNCHED_HEADLESS or 'Headless' in version.get('User-Agent', ''):
                browser_close()
                deadline = time.monotonic() + 5
                while port_alive():
                    if cancel_event.wait(0.2):
                        return
                    if time.monotonic() >= deadline:
                        raise RuntimeError('后台浏览器未能退出，请关闭挂件专用浏览器后重新授权。')
            else:
                page = find_page()
                if page:
                    ws = WS('127.0.0.1', PORT, _ws_path(page['webSocketDebuggerUrl']))
                    try:
                        cdp_call(ws, 'Page.bringToFront', {}, 1)
                    finally:
                        ws.close()
                    return
        if not cancel_event.is_set():
            launch_browser(headless=False, detach=True)
    finally:
        _BROWSER_LOCK.release()


def trigger_sync(sync_callback=None):
    try:
        if sync_callback is not None:
            sync_callback()
        else:
            from server_usage_sync import run_sync
            run_sync(quiet=True)
    except Exception as exc:
        log('凭据已保存；同步未完成（%s），请稍后重试。' % type(exc).__name__)


def renew(headless, cancel_event=None, sync_callback=None):
    """Serialize browser access; cancellation never commits newly collected credentials."""
    cancel_event = cancel_event or threading.Event()
    while not _BROWSER_LOCK.acquire(timeout=0.2):
        if cancel_event.is_set():
            return 4
    try:
        if cancel_event.is_set():
            return 4
        try:
            return _renew(headless, cancel_event, sync_callback)
        except StorageCancelled:
            return 4
    finally:
        _BROWSER_LOCK.release()


def _renew(headless, cancel_event, sync_callback=None):
    try:
        creds = load_creds(cancel_event)
    except StorageCancelled:
        raise
    except StorageError:
        if headless:
            raise
        # Explicit login can still reach the single deliberate Keychain prompt
        # at save time; passive preflight must never trigger that prompt.
        creds = {}
    stored_ua = creds.get('ua')
    launched = False
    if not port_alive():
        log('启动专用浏览器（%s）...' % ('headless' if headless else '有头'))
        process = launch_browser(headless, detach=False, ua=stored_ua if headless else None)
        launched = True
        if not wait_port(cancel_event=cancel_event):
            if headless:
                browser_close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            log('FAIL: 专用浏览器未就绪或授权已取消')
            return 4 if cancel_event.is_set() else 2
    page_ws = None
    try:
        page = find_page()
        if not page:
            log('FAIL: 无 page target')
            return 2
        page_ws = WS('127.0.0.1', PORT, _ws_path(page['webSocketDebuggerUrl']))
        cdp_call(page_ws, 'Page.enable', None, 1)
        if cancel_event.wait(6):             # 等加载 + Set-Cookie；取消无需等待完整间隔
            return 4

        ua = cdp_call(page_ws, 'Runtime.evaluate',
                      {'expression': 'navigator.userAgent', 'returnByValue': True}, 3
                      ).get('result', {}).get('value') or stored_ua or sc.UA
        ck = cdp_call(page_ws, 'Network.getCookies', {'urls': [ORIGIN, START_URL]}, 4, timeout=15).get('cookies', [])
        ck = [c for c in ck if c.get('domain', '').lstrip('.') in ('codebuddy.cn', 'www.codebuddy.cn')]
        cookie = '; '.join('%s=%s' % (c['name'], c['value']) for c in ck)
        if not any(c.get('name') == 'session' for c in ck):
            log('FAIL: 未取到 session —— 配置档未登录或会话失效，请运行 login 并重新登录')
            return 3
        if cancel_event.is_set():
            return 4
        sc.UA = ua
        ok, msg = sc.validate(cookie)
        log('接口校验: %s %s' % ('可用 ✔' if ok else '不可用 ✘', msg))
        if not ok:
            return 5
        if cancel_event.is_set():
            return 4
        payload = {'cookie': cookie, 'ua': ua,
                   'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'note': 'WB web session cookie via cookie_auto_renew.py (dedicated browser profile).'}
        save_record('cookie', payload, cancel_event=cancel_event, interactive=not headless)
        log('已写入操作系统保护存储')
        if not browser_close():
            log('凭据已保存；请手动关闭挂件专用登录窗口以停止浏览器调试端口。')
        if not cancel_event.is_set():
            trigger_sync(sync_callback)
        return 0
    finally:
        if page_ws:
            page_ws.close()
        if launched and headless:
            browser_close()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'login':
        start_interactive()
        if not wait_port():
            log('FAIL: 登录窗口未就绪，请关闭专用浏览器后重试。')
            return 2
        log('已打开专用浏览器窗口（独立配置档，不影响日常浏览器）。')
        log('请在该窗口内完成 codebuddy.cn 登录（一次即可），之后日常全自动。')
        return 0
    if cmd == 'renew':
        return renew('--headless' in sys.argv)
    if cmd == 'check':
        d = load_creds()
        if not d.get('cookie'):
            print('尚未保存 cookie')
            return 1
        if d.get('ua'):
            sc.UA = d['ua']
        ok, msg = sc.validate(d.get('cookie', ''))
        exp = sc.session_expiry(d.get('cookie', ''))
        print('保存时间:', d.get('saved_at'))
        if exp:
            print('预计可用到: %s（剩 %.1f 天）' % (exp.strftime('%Y-%m-%d %H:%M'),
                  (exp - datetime.datetime.now()).total_seconds() / 86400))
        print('接口校验:', ('可用 ✔ ' if ok else '不可用 ✘ ') + msg)
        return 0 if ok else 1
    print(__doc__)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        log('操作失败（%s）；请检查专用浏览器及系统凭据保护权限后重试。' % type(exc).__name__)
        sys.exit(1)
