# -*- coding: utf-8 -*-
"""cookie_auto_renew.py - 专用 Edge 配置档自动续期/提取积分同步 cookie（方案 B）。

背景（2026-09-28 与 Philip 约定）：积分账单同步依赖浏览器 cookie（滑动续期 7 天），
人工复制环节用本脚本归零——独立 user-data-dir + 固定调试口 9224，登录一次后
每日自动化 renew：访问 plans-usage（服务端滑动续期）→ CDP 读 cookie（可读 HttpOnly）
→ 写 ~/.workbuddy/server_usage.json → 触发挂件同步。

⚠️ 实测关键（2026-09-28）：服务端风控把新登录会话绑定到「签发时的 User-Agent」，
   校验必须用专用 Edge 的真实 UA；cookie 必须取 Network.getCookies(urls=[目标URL])
   的「浏览器发送视角」集合（不是自己按域名拼）。
   headless 模式 UA 会带 HeadlessChrome 标记 → 启动时用 --user-agent 强制回放登录 UA。

用法：
  python cookie_auto_renew.py login              # 弹出登录窗口（仅首次，保持打开让 Philip 登录）
  python cookie_auto_renew.py renew [--headless] # 续期+提取+写入+触发同步（自动化用 --headless）
  python cookie_auto_renew.py check              # 只读：当前已存 cookie 剩余有效期

安全：cookie 内容永不打印；只落本机 ~/.workbuddy/server_usage.json。
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
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, '_tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import save_cookie as sc  # noqa: E402  （复用 validate / session_expiry）

PORT = 9224
PROFILE = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'WBCreditWidget', 'edge_cookie_profile')
START_URL = 'https://www.codebuddy.cn/profile/plans-usage'
ORIGIN = 'https://www.codebuddy.cn/'
EDGES = [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
         r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']
CREDS = os.path.join(os.path.expanduser('~'), '.workbuddy', 'server_usage.json')
WIDGET_SYNC = 'http://127.0.0.1:8790/api/sync'
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
DETACHED = 0x00000008 | 0x00000200


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m))


def port_alive():
    try:
        OP.open('http://127.0.0.1:%d/json/version' % PORT, timeout=2).read()
        return True
    except Exception:
        return False


def find_edge():
    for p in EDGES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError('未找到 msedge.exe')


def load_creds():
    try:
        return json.load(open(CREDS, encoding='utf-8'))
    except Exception:
        return {}


def launch_edge(headless, detach, ua=None):
    args = [find_edge(),
            '--user-data-dir=' + PROFILE,
            '--remote-debugging-port=%d' % PORT,
            '--no-first-run', '--no-default-browser-check',
            '--disable-features=Translate',
            START_URL]
    if headless:
        args.insert(1, '--headless=new')
        if ua:                                   # headless UA 带 HeadlessChrome 标记，强制回放登录 UA
            args.insert(2, '--user-agent=' + ua)
    flags = DETACHED if detach else 0
    subprocess.Popen(args, creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_port(timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_alive():
            return True
        time.sleep(0.5)
    return False


def http_json(path):
    return json.loads(OP.open('http://127.0.0.1:%d%s' % (PORT, path), timeout=5).read().decode())


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
            raise ConnectionError('WS 握手非 101: ' + buf[:120].decode('utf-8', 'replace'))

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
                raise RuntimeError('%s: %s' % (method, m['error'].get('message')))
            return m.get('result', {})
    raise TimeoutError('%s 无响应' % method)


def _ws_path(ws_url):
    # ws://127.0.0.1:9224/devtools/page/XXX -> /devtools/page/XXX
    return '/' + ws_url.split('/', 3)[-1]


def find_page():
    targets = http_json('/json/list')
    pages = [t for t in targets if t.get('type') == 'page']
    for t in pages:
        if 'codebuddy' in (t.get('url') or ''):
            return t
    return pages[0] if pages else None


def browser_close():
    try:
        ver = http_json('/json/version')
        ws = WS('127.0.0.1', PORT, _ws_path(ver['webSocketDebuggerUrl']))
        ws.send(json.dumps({'id': 99, 'method': 'Browser.close'}))
        time.sleep(1)
        ws.close()
    except Exception:
        pass


def trigger_sync():
    try:
        r = OP.open(WIDGET_SYNC, timeout=5).read().decode()
        log('已触发挂件同步: ' + r)
    except Exception as e:
        log('挂件服务未启动（跳过触发，下次打开挂件自动用新 cookie）: %s' % type(e).__name__)


def renew(headless):
    creds = load_creds()
    stored_ua = creds.get('ua')
    launched = False
    if not port_alive():
        log('启动专用 Edge（%s）...' % ('headless' if headless else '有头'))
        launch_edge(headless, detach=False, ua=stored_ua if headless else None)
        launched = True
        if not wait_port():
            log('FAIL: 调试口 %d 未就绪' % PORT)
            return 2
    page_ws = None
    try:
        page = find_page()
        if not page:
            log('FAIL: 无 page target')
            return 2
        page_ws = WS('127.0.0.1', PORT, _ws_path(page['webSocketDebuggerUrl']))
        cdp_call(page_ws, 'Page.enable', None, 1)
        if 'codebuddy' not in (page.get('url') or ''):
            cdp_call(page_ws, 'Page.navigate', {'url': START_URL}, 2)
        time.sleep(6)                       # 等加载 + Set-Cookie（滑动续期）

        ua = cdp_call(page_ws, 'Runtime.evaluate',
                      {'expression': 'navigator.userAgent', 'returnByValue': True}, 3
                      ).get('result', {}).get('value') or stored_ua or sc.UA
        ck = cdp_call(page_ws, 'Network.getCookies', {'urls': [ORIGIN, START_URL]}, 4, timeout=15).get('cookies', [])
        cookie = '; '.join('%s=%s' % (c['name'], c['value']) for c in ck)
        log('UA: %s' % ua[:90])
        log('cookie（浏览器发送视角）: %d 条, len=%d, 含 session: %s' % (len(ck), len(cookie), 'session=' in cookie))
        if 'session=' not in cookie:
            log('FAIL: 未取到 session —— 配置档未登录或会话失效，请运行 login 并重新登录')
            return 3
        sc.UA = ua
        ok, msg = sc.validate(cookie)
        log('接口校验: %s %s' % ('可用 ✔' if ok else '不可用 ✘', msg))
        if not ok:
            return 3
        payload = {'cookie': cookie, 'ua': ua,
                   'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'note': 'WB web session cookie via cookie_auto_renew.py (dedicated Edge profile).'}
        os.makedirs(os.path.dirname(CREDS), exist_ok=True)
        with open(CREDS, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        exp = sc.session_expiry(cookie)
        if exp:
            log('已写入 %s | 预计可用到 %s（约 %.1f 天）'
                % (CREDS, exp.strftime('%Y-%m-%d %H:%M'),
                   (exp - datetime.datetime.now()).total_seconds() / 86400))
        trigger_sync()
        return 0
    finally:
        if page_ws:
            page_ws.close()
        if launched and headless:
            browser_close()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'login':
        if not port_alive():
            launch_edge(headless=False, detach=True)
            log('已弹出专用 Edge 窗口（独立配置档，不影响你日常的 Edge）。')
        else:
            log('专用 Edge 已在运行。')
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
    sys.exit(main())
