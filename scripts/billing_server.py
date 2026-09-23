# -*- coding: utf-8 -*-
"""
Workbuddy积分看板 · 本地服务（桌面挂件层 · 127.0.0.1:8790）
========================================================
为桌面挂件（billing_widget.py）与浏览器提供迷你常驻服务：
  GET /            挂件精简视图（billing_widget_template.html + 实时数据注入）
  GET /widget      同 /
  GET /api/data    聚合 JSON（挂件每 60s 轮询；含 stale / age_min / syncing 状态）
  GET /api/sync    手动触发账单同步（后台线程执行，立即返回）
  GET /full        完整版看板（billing_template.html 注入，浏览器实时看）
后台线程：启动后先同步一次，之后每 30 分钟自动同步（进程内调用 server_usage_sync.run_sync，
          幂等合并；打包成 exe 后同样可用，无子进程依赖）。
降级：凭据失效（桌面登录态过期）/ 网络失败 → 继续用缓存渲染，payload.meta.stale=True（页面提示"数据截至"）。

与主看板的关系：数据链完全一致（server_usage_cache.json → build_data），只多了常驻服务层。
端口 8790 独立于日志版服务（8787），互不干扰。

打包说明（PyInstaller onefile / windowed，入口 wb_widget_app.py）：
  - 模板与图标经 --add-data 置于包根目录；冻结时 SCRIPT_DIR 指向 sys._MEIPASS
  - serve(port) 可在线程内运行（wb_widget_app.py 的内嵌服务模式）
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):            # PyInstaller：数据文件在 _MEIPASS 根
    SCRIPT_DIR = getattr(sys, '_MEIPASS', SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import billing_dashboard_data as bdd

HOST, PORT = '127.0.0.1', 8790
SYNC_INTERVAL_MIN = 30
WIDGET_TEMPLATE = os.path.join(SCRIPT_DIR, 'billing_widget_template.html')
FULL_TEMPLATE = os.path.join(SCRIPT_DIR, 'billing_template.html')


class Store:
    """聚合数据 + 同步调度（线程安全）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.data_at = None
        self.syncing = False
        self.last_sync = None          # {'ok':bool,'at':'HH:MM:SS','msg':str}

    def refresh(self):
        """重建聚合数据（读本地缓存，毫秒级）。"""
        try:
            d = bdd.build_data()
            with self.lock:
                self.data = d
                self.data_at = datetime.now()
            return True
        except Exception as e:
            print(f'[store] refresh failed: {e}')
            return False

    def sync_now(self):
        """跑一次账单同步（进程内调用，源码/exe 通用）→ 刷新数据。"""
        with self.lock:
            if self.syncing:
                return False
            self.syncing = True
        try:
            import server_usage_sync as sus
            ok, msg = sus.run_sync(quiet=True)
            msg = str(msg or ('同步完成' if ok else '同步失败'))
            with self.lock:
                self.last_sync = {'ok': ok, 'at': datetime.now().strftime('%H:%M:%S'), 'msg': msg[:140]}
            self.refresh()
            print(f'[sync] ok={ok} {msg[:80]}')
            return ok
        except Exception as e:
            with self.lock:
                self.last_sync = {'ok': False, 'at': datetime.now().strftime('%H:%M:%S'), 'msg': str(e)[:140]}
            print(f'[sync] failed: {e}')
            return False
        finally:
            with self.lock:
                self.syncing = False

    def payload(self):
        """给页面的 JSON：聚合数据 + 新鲜度状态。"""
        with self.lock:
            d = self.data
            at = self.data_at
            syncing = self.syncing
            last_sync = dict(self.last_sync) if self.last_sync else None
        if d is None:
            return {'ok': False, 'reason': 'no data (sync pending or not logged in)',
                    'meta': {'stale': True, 'age_min': None, 'syncing': syncing,
                             'last_sync': last_sync, 'auth': _auth_status_safe()}}
        out = dict(d)
        age_min = int((datetime.now() - at).total_seconds() // 60) if at else None
        stale = (age_min is None) or (age_min > SYNC_INTERVAL_MIN * 2)
        if last_sync and not last_sync.get('ok'):
            stale = True
        meta = dict(out.get('meta') or {})
        meta.update({'stale': stale, 'age_min': age_min, 'syncing': syncing, 'last_sync': last_sync})
        out['meta'] = meta
        return out


STORE = Store()


def sync_loop():
    time.sleep(3)                       # 等服务先就绪
    while True:
        STORE.sync_now()
        time.sleep(SYNC_INTERVAL_MIN * 60)


def _auth_status_safe():
    """凭据自检（异常兜底，供占位页显示）。"""
    try:
        import server_usage_sync as sus
        return sus.auth_status()
    except Exception as e:
        return '自检失败：%s' % e


def _esc(x):
    return (str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


NO_DATA_HTML_TMPL = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>暂无数据</title>
<style>body{font-family:Consolas,'Microsoft YaHei',monospace;background:#0b0b0c;color:#e8dcc0;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{text-align:center;line-height:1.9;padding:0 16px}.t{color:#ffb300}
.diag{margin-top:16px;padding-top:12px;border-top:1px solid #3a3a20;font-size:12px;
color:#9a7c3a;text-align:left;line-height:1.75;word-break:break-all}
a{color:#ffd23d}</style></head><body><div class="box">
<div class="t">Workbuddy积分看板</div><div>暂无账单数据</div>
<div style="font-size:12px;color:#9a7c3a">首次同步约需数秒，本页每 10 秒自动刷新</div>
<div class="diag">同步状态：{{DETAIL}}<br>凭据自检：{{AUTH}}<br><a href="/api/sync">点此立即重试同步</a></div>
</div></body></html>"""


def no_data_html():
    """占位页：把「为什么没数据」直接显示出来，便于远程排障。"""
    with STORE.lock:
        ls = dict(STORE.last_sync) if STORE.last_sync else None
        syncing = STORE.syncing
    if ls:
        head = '上次同步成功（但无记录）' if ls.get('ok') else '上次同步失败'
        detail = '%s · %s · %s' % (head, ls.get('at') or '', ls.get('msg') or '')
    else:
        detail = '正在同步…（启动后约 3~10 秒）' if syncing else '尚未开始同步（等 10 秒会自动重试）'
    return (NO_DATA_HTML_TMPL
            .replace('{{DETAIL}}', _esc(detail))
            .replace('{{AUTH}}', _esc(_auth_status_safe())))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass                            # 静默访问日志

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _render(self, template_path):
        with open(template_path, encoding='utf-8') as f:
            tpl = f.read()
        return tpl.replace('/*__DATA__*/', json.dumps(STORE.payload(), ensure_ascii=False)).encode('utf-8')

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/data':
            body = json.dumps(STORE.payload(), ensure_ascii=False).encode('utf-8')
            self._send(200, 'application/json; charset=utf-8', body)
            return
        if path == '/api/sync':
            threading.Thread(target=STORE.sync_now, daemon=True).start()
            self._send(200, 'application/json; charset=utf-8', b'{"started": true}')
            return
        if path in ('/', '/widget'):
            if STORE.data is None:
                self._send(200, 'text/html; charset=utf-8', no_data_html().encode('utf-8'))
                return
            try:
                self._send(200, 'text/html; charset=utf-8', self._render(WIDGET_TEMPLATE))
            except Exception as e:
                self._send(500, 'text/plain; charset=utf-8', f'widget render error: {e}'.encode('utf-8'))
            return
        if path == '/full':
            if STORE.data is None:
                self._send(200, 'text/html; charset=utf-8', no_data_html().encode('utf-8'))
                return
            try:
                self._send(200, 'text/html; charset=utf-8', self._render(FULL_TEMPLATE))
            except Exception as e:
                self._send(500, 'text/plain; charset=utf-8', f'full render error: {e}'.encode('utf-8'))
            return
        self._send(404, 'text/plain; charset=utf-8', b'not found')


def port_in_use(port=None):
    """端口是否已被占用（含自身服务）。"""
    import socket
    port = port or PORT
    s = socket.socket()
    try:
        s.bind((HOST, port))
        s.close()
        return False
    except OSError:
        return True


def serve(port=None):
    """启动服务（阻塞）。端口被占用时立即返回（视为已有实例，复用即可）。

    注意：Python HTTPServer 默认开 SO_REUSEADDR，Windows 下两个实例可以
    同端口双绑定（新实例起得来、日志正常，但连接仍落旧进程）。
    因此先做一次「无 REUSEADDR 的占位检测」，确保新版本实例不会悄悄被旧进程顶掉。
    """
    port = port or PORT
    if port_in_use(port):
        print(f'[server] port {port} already in use, another instance is running. Exit.')
        return
    try:
        httpd = HTTPServer((HOST, port), Handler)
    except OSError:
        print(f'[server] port {port} already in use, another instance is running. Exit.')
        return
    STORE.refresh()                     # 先加载现有缓存（有缓存则立即可用）
    threading.Thread(target=sync_loop, daemon=True).start()
    print(f'[server] billing widget serving at http://{HOST}:{port}')
    httpd.serve_forever()


def main():
    serve()


if __name__ == '__main__':
    main()
