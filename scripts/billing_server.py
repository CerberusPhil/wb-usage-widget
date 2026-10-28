# -*- coding: utf-8 -*-
"""
Workbuddy积分看板 · 本地服务（桌面挂件层 · 127.0.0.1:8790）
========================================================
为桌面挂件（billing_widget.py）与浏览器提供迷你常驻服务：
  GET /            挂件精简视图（billing_widget_template.html + 实时数据注入）
  GET /widget      同 /
  GET /api/data    聚合 JSON（挂件每 60s 轮询；含 stale / age_min / syncing 状态）
  POST /api/sync   手动触发账单同步（后台线程执行，立即返回）
  POST /api/login  后台启动独立浏览器登录（202 已启动 / 200 进行中）
  POST /api/login/cancel 立即取消等待，不关闭浏览器；允许重新授权
                   登录结果通过 /api/data 的 meta.login 和三个页面反馈
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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):            # PyInstaller：数据文件在 _MEIPASS 根
    SCRIPT_DIR = getattr(sys, '_MEIPASS', SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import billing_dashboard_data as bdd
from local_http_security import (LocalAccess, BOOTSTRAP_HTML, script_json,
                                 client_url, local_request, publish_capability)

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
        except Exception:
            print('[store] refresh failed')
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
            msg = '同步完成' if ok else '同步失败，请检查授权或网络后重试'
            with self.lock:
                self.last_sync = {'ok': ok, 'at': datetime.now().strftime('%H:%M:%S'), 'msg': msg[:140]}
            self.refresh()
            print(f'[sync] ok={ok} {msg[:80]}')
            return ok
        except Exception:
            with self.lock:
                self.last_sync = {'ok': False, 'at': datetime.now().strftime('%H:%M:%S'),
                                  'msg': '同步失败，请检查授权或网络后重试'}
            print('[sync] failed')
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
                             'last_sync': last_sync, 'auth': _auth_status_safe(),
                             'login': login_status()}}
        out = dict(d)
        age_min = int((datetime.now() - at).total_seconds() // 60) if at else None
        stale = (age_min is None) or (age_min > SYNC_INTERVAL_MIN * 2)
        if last_sync and not last_sync.get('ok'):
            stale = True
        meta = dict(out.get('meta') or {})
        meta.update({'stale': stale, 'age_min': age_min, 'syncing': syncing,
                     'last_sync': last_sync, 'login': login_status()})
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
    except Exception:
        return '自检失败，请重新授权'


def _esc(x):
    return (str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


NO_DATA_HTML_TMPL = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>暂无数据</title>
<style>body{font-family:Consolas,'Microsoft YaHei',monospace;background:#0b0b0c;color:#e8dcc0;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{text-align:center;line-height:1.9;padding:0 16px}.t{color:#ffb300}
.diag{margin-top:16px;padding-top:12px;border-top:1px solid #3a3a20;font-size:12px;
color:#9a7c3a;text-align:left;line-height:1.75;word-break:break-all}
.windowbar{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;
justify-content:space-between;padding:10px 14px;color:#ffb300;user-select:none;touch-action:none}
.windowbar.desktop{cursor:grab}.windowbar button{font:inherit;font-size:20px;
background:transparent;color:#ffd23d;border:0;padding:0 10px;cursor:pointer}
a{color:#ffd23d}</style></head><body>
<header id="windowBar" class="windowbar"><span>Workbuddy积分看板</span>
<span id="windowControls" hidden><button id="windowMin" title="收起窗口" aria-label="收起窗口">−</button>
<button id="windowClose" title="退出挂件" aria-label="退出挂件">×</button></span></header>
<div class="box"><div>暂无账单数据</div>
<div style="font-size:12px;color:#9a7c3a">首次使用请先登录账号；完成授权后自动同步账单</div>
<!--__LOGIN_PANEL__-->
<div class="diag">同步状态：{{DETAIL}}<br>凭据自检：{{AUTH}}<br><button id="retrySync">立即重试同步</button></div>
</div>
<script>
(function(){
  document.getElementById('retrySync').onclick=function(){
    fetch('/api/sync',{method:'POST'}).catch(function(){});
  };
  var bar=document.getElementById('windowBar');
  var controls=document.getElementById('windowControls');
  var drag=null, queue=Promise.resolve();
  function api(){return window.pywebview && window.pywebview.api;}
  function enqueue(method,args){
    queue=queue.then(function(){return api()[method].apply(api(),args || []);})
               .catch(function(){});
  }
  function attach(){
    var bridge=api();
    if(!bridge || !bridge.minimize_widget || !bridge.close_widget){return;}
    controls.hidden=false;
    bar.classList.add('desktop');
    document.getElementById('windowMin').onclick=function(){enqueue('minimize_widget');};
    document.getElementById('windowClose').onclick=function(){enqueue('close_widget');};
  }
  bar.addEventListener('pointerdown',function(e){
    if(e.button!==0 || drag || document.body.dataset.pinned!=='true' || e.target.closest('button') || !api() || !api().drag_begin){return;}
    e.preventDefault();
    drag={id:e.pointerId,x:e.screenX,y:e.screenY};
    bar.setPointerCapture(e.pointerId);
    enqueue('drag_begin');
  });
  bar.addEventListener('pointermove',function(e){
    if(!drag || drag.id!==e.pointerId){return;}
    enqueue('drag_to',[e.screenX-drag.x,e.screenY-drag.y]);
  });
  function end(e){
    if(!drag || drag.id!==e.pointerId){return;}
    drag=null;
    enqueue('drag_end');
    if(bar.hasPointerCapture(e.pointerId)){bar.releasePointerCapture(e.pointerId);}
  }
  bar.addEventListener('pointerup',end);
  bar.addEventListener('pointercancel',end);
  bar.addEventListener('lostpointercapture',end);
  window.addEventListener('pywebviewready',attach);
  attach();
})();
</script></body></html>"""


def no_data_html():
    """占位页：把「为什么没数据」直接显示出来，便于远程排障。"""
    with STORE.lock:
        ls = dict(STORE.last_sync) if STORE.last_sync else None
        syncing = STORE.syncing
    if ls:
        head = '上次同步成功（但无记录）' if ls.get('ok') else '上次同步失败'
        detail = '%s · %s · %s' % (head, ls.get('at') or '', ls.get('msg') or '')
    else:
        detail = '正在同步…' if syncing else '尚无同步结果，请先登录或手动重试同步'
    return inject_login_ui(NO_DATA_HTML_TMPL
                           .replace('{{DETAIL}}', _esc(detail))
                           .replace('{{AUTH}}', _esc(_auth_status_safe())))


LOGIN_PANEL_HTML = """<section aria-label="WorkBuddy 授权" style="flex:none;padding:8px 0;font-size:12px;line-height:1.5">
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<button id="loginBtn" type="button" style="font:inherit;cursor:pointer">登录 / 重新授权</button>
<button id="loginCancel" type="button" style="font:inherit;cursor:pointer" disabled>取消等待</button>
</div>
<div id="loginTip" role="status" aria-live="polite" style="margin-top:4px;overflow-wrap:anywhere"></div>
</section>"""

LOGIN_SCRIPT = """<script>
(function(){
  var start=document.getElementById('loginBtn'), cancel=document.getElementById('loginCancel');
  var tip=document.getElementById('loginTip'), status=__LOGIN_STATUS__, epoch=0, pending=false;
  var commands=Promise.resolve(), polling=false;
  function render(value){
    if(!value || value.generation < status.generation){return;}
    status=value;
    start.disabled=pending || !!value.running;
    cancel.disabled=!value.running;
    tip.textContent=value.message || '可登录或重新授权 WorkBuddy 账号。';
  }
  async function request(path, method){
    var controller=new AbortController(), timeout=setTimeout(function(){controller.abort();},8000);
    try{
      var response=await fetch(path,{method:method || 'GET',signal:controller.signal});
      if(!response.ok){throw new Error('request failed');}
      return await response.json();
    }finally{clearTimeout(timeout);}
  }
  function command(isCancel){
    var mine=++epoch;
    pending=true;
    render(Object.assign({},status,{running:!isCancel,message:isCancel ?
      '已停止本页等待，正在取消授权任务；浏览器保留，可重新授权。' : '正在打开专用浏览器…'}));
    if(isCancel){start.disabled=false;}
    commands=commands.catch(function(){}).then(function(){
      return request(isCancel ? '/api/login/cancel' : '/api/login','POST');
    }).then(function(value){
      if(mine!==epoch){return;}
      pending=false;render(value);
    }).catch(function(){
      if(mine!==epoch){return;}
      pending=false;
      render(Object.assign({},status,{running:false,message:'无法连接授权服务，请重试；若任务仍在等待，可点击取消。'}));
      cancel.disabled=false;
    });
  }
  start.onclick=function(){command(false);};
  cancel.onclick=function(){command(true);};
  render(status);
  setInterval(async function(){
    if(polling || pending){return;}
    polling=true;
    var mine=epoch;
    try{
      var data=await request('/api/data');
      if(mine!==epoch){return;}
      var next=data.meta && data.meta.login, previous= status.state;
      render(next);
      if((next && next.state==='succeeded' && previous!=='succeeded') ||
         (data.ok!==false && document.title==='暂无数据')){
        location.reload();
      }
    }catch(e){
      if(mine===epoch){tip.textContent='暂时无法连接授权服务，请重试或取消等待。';start.disabled=false;cancel.disabled=false;}
    }finally{polling=false;}
  },2000);
})();
</script>"""


def inject_login_ui(html):
    """所有在线视图共享同一授权入口、状态轮询与取消行为。"""
    script = LOGIN_SCRIPT.replace('__LOGIN_STATUS__', script_json(login_status()))
    if 'id="appTitle"' in html or 'id="windowControls"' in html:
        from popup_ui import POPUP_UI
        script += POPUP_UI
    return html.replace('<!--__LOGIN_PANEL__-->', LOGIN_PANEL_HTML).replace('</body>', script + '</body>')

# 登录结果独立于同步状态，避免自动同步覆盖登录失败的提示。
_LOGIN_LOCK = threading.Lock()
_LOGIN_GENERATION = 0
_LOGIN_CANCEL = None
_LOGIN_JOB = {'running': False, 'state': 'idle', 'message': '可登录或重新授权 WorkBuddy 账号。',
              'generation': 0}


def login_status():
    with _LOGIN_LOCK:
        return dict(_LOGIN_JOB)


def _set_login(generation, event, state, message, running=False):
    with _LOGIN_LOCK:
        if generation != _LOGIN_GENERATION or event.is_set():
            return False
        _LOGIN_JOB.update(running=running, state=state, message=message)
        return True


def start_login():
    """只安排后台工作；HTTP 请求不进行浏览器启动或网络等待。"""
    global _LOGIN_GENERATION, _LOGIN_CANCEL
    with _LOGIN_LOCK:
        if _LOGIN_JOB['running']:
            return 200, dict(_LOGIN_JOB, started=False)
        _LOGIN_GENERATION += 1
        generation = _LOGIN_GENERATION
        event = _LOGIN_CANCEL = threading.Event()
        _LOGIN_JOB.update(running=True, state='starting', generation=generation,
                          message='正在打开专用浏览器；可随时取消等待。')
        try:
            threading.Thread(target=_login_job, args=(generation, event), daemon=True,
                             name='wb-login-%s' % generation).start()
        except RuntimeError:
            _LOGIN_JOB.update(running=False, state='failed', message='无法启动授权任务，请重试。')
            return 503, dict(_LOGIN_JOB, started=False)
        return 202, dict(_LOGIN_JOB, started=True)


def cancel_login():
    """立即释放入口；失效旧任务，不退出用户的浏览器。"""
    global _LOGIN_GENERATION
    with _LOGIN_LOCK:
        if _LOGIN_CANCEL is not None:
            _LOGIN_CANCEL.set()
        _LOGIN_GENERATION += 1
        _LOGIN_JOB.update(running=False, state='cancelled', generation=_LOGIN_GENERATION,
                          message='已取消授权等待，浏览器保持打开；可随时重新授权。')
        return dict(_LOGIN_JOB)


def _expire_login(generation, event, message):
    """网络调用仍在退出时，也按截止时间释放 UI 并禁止保存凭据。"""
    with _LOGIN_LOCK:
        if generation != _LOGIN_GENERATION or event.is_set() or not _LOGIN_JOB['running']:
            return
        event.set()
        _LOGIN_JOB.update(running=False, state='failed', message=message)


def _login_job(generation, event):
    """限时、可取消的后台授权；旧任务不得覆盖新的页面状态。"""
    timer = None
    try:
        import cookie_auto_renew as car
        ready_deadline = time.monotonic() + 25
        timer = threading.Timer(25, _expire_login, args=(
            generation, event, '专用浏览器未在 25 秒内就绪，请检查浏览器后重新授权。'))
        timer.daemon = True
        timer.start()
        if event.is_set():
            return
        car.start_interactive(cancel_event=event)
        while not event.is_set():
            if time.monotonic() >= ready_deadline:
                _set_login(generation, event, 'failed', '专用浏览器未在 25 秒内就绪，请检查浏览器后重新授权。')
                return
            if car.port_alive() and car.find_page():
                break
            event.wait(min(2, max(0, ready_deadline - time.monotonic())))
        if not _set_login(generation, event, 'waiting',
                          '请在专用浏览器完成 WorkBuddy 登录；最长等待 7 分钟，可取消。', running=True):
            return
        timer.cancel()
        timer = threading.Timer(420, _expire_login, args=(
            generation, event, '等待授权超时（7 分钟），尚未获取登录凭据；请重新授权。'))
        timer.daemon = True
        timer.start()
        deadline = time.monotonic() + 420
        while not event.is_set() and time.monotonic() < deadline:
            if not car.port_alive():
                _set_login(generation, event, 'failed', '登录窗口已关闭或连接中断，请重新授权。')
                return
            rc = car.renew(headless=False, cancel_event=event, sync_callback=STORE.sync_now)
            if event.is_set():
                return
            if rc == 4:
                _set_login(generation, event, 'cancelled', '授权等待已停止，可重新授权。')
                return
            if rc == 0:
                # renew 已保存凭据并请求同步，刷新缓存后再通知页面重载。
                STORE.refresh()
                _set_login(generation, event, 'succeeded', '授权成功，凭据已保存；账单同步结果见数据状态。')
                return
            if rc == 5:
                _set_login(generation, event, 'failed',
                           '已读取网页登录态，但账单接口未通过校验；请在专用浏览器重新登录后再授权。')
                return
            if rc != 3:
                _set_login(generation, event, 'failed', '无法连接专用浏览器或读取登录页面，请重新授权。')
                return
            if event.wait(min(2, max(0, deadline - time.monotonic()))):
                return
        _set_login(generation, event, 'failed', '等待授权超时（7 分钟），尚未获取登录凭据；请重新授权。')
    except FileNotFoundError:
        _set_login(generation, event, 'failed',
                   '未找到登录浏览器，请安装 Google Chrome 或 Microsoft Edge 后重新授权。')
    except Exception:
        # 不向页面或日志暴露可能包含 cookie / token 的底层异常。
        _set_login(generation, event, 'failed', '授权失败，浏览器未能启动或连接中断；请重新授权。')
    finally:
        if timer is not None:
            timer.cancel()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass                            # Never log request paths, cookies, or headers.

    def _send(self, code, ctype, body, cookie=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        script_sources = "'self' 'unsafe-inline'"
        if self.path in ('/', '/widget') and ctype.startswith('text/html'):
            # pywebview uses eval for bridge replies. Permit it only on the native
            # panel, while hashes (not unsafe-inline) block injected event handlers.
            import base64
            import hashlib
            import re
            hashes = ["'sha256-%s'" % base64.b64encode(hashlib.sha256(block).digest()).decode('ascii')
                      for block in re.findall(rb'<script>(.*?)</script>', body, re.S)]
            script_sources = "'unsafe-eval' " + ' '.join(hashes)
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; script-src " + script_sources + "; "
                         "style-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
                         "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, value, cookie=None):
        self._send(code, 'application/json; charset=utf-8',
                   json.dumps(value, ensure_ascii=False).encode('utf-8'), cookie)

    def _guard(self, mutation=False, public=False):
        port = self.server.server_port
        host = '127.0.0.1:%d' % port
        origin = 'http://' + host
        # Literal Host is intentional: localhost aliases permit DNS rebinding.
        if self.headers.get_all('Host') != [host]:
            self._json(403, {'error': 'Forbidden host'})
            return False
        origins = self.headers.get_all('Origin') or []
        if origins and origins != [origin]:
            self._json(403, {'error': 'Forbidden origin'})
            return False
        if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            self._json(403, {'error': 'Forbidden site'})
            return False
        native = self.server.access.native(self.headers)
        if mutation and not native and origins != [origin]:
            self._json(403, {'error': 'Same-origin request required'})
            return False
        if not public and not self.server.access.authenticated(self.headers, port):
            self._json(401, {'error': 'Open this view from the WorkBuddy tray'})
            return False
        return True

    def _body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('Expected JSON')
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Unsupported body encoding')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 2048:
            raise ValueError('Invalid body size')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError('Expected object')
        return value

    def _render(self, template_path):
        with open(template_path, encoding='utf-8') as f:
            tpl = f.read()
        html = tpl.replace('/*__DATA__*/', script_json(STORE.payload()))
        return inject_login_ui(html).encode('utf-8')

    def do_GET(self):
        path = self.path
        if not self._guard(public=path == '/bootstrap'):
            return
        if path == '/bootstrap':
            self._send(200, 'text/html; charset=utf-8', BOOTSTRAP_HTML)
        elif path == '/api/data':
            self._json(200, STORE.payload())
        elif path in ('/api/sync', '/api/login', '/api/login/cancel', '/api/session', '/api/bootstrap-ticket'):
            self._json(405, {'error': 'POST required'})
        elif path in ('/', '/widget', '/full'):
            try:
                body = (no_data_html().encode('utf-8') if STORE.data is None else
                        self._render(FULL_TEMPLATE if path == '/full' else WIDGET_TEMPLATE))
                self._send(200, 'text/html; charset=utf-8', body)
            except Exception:
                self._json(500, {'error': 'Unable to render view'})
        else:
            self._json(404, {'error': 'Not found'})

    def do_POST(self):
        path = self.path
        if not self._guard(mutation=True, public=path == '/api/session'):
            return
        if path == '/api/session':
            try:
                result = self.server.access.redeem(self._body().get('ticket'))
            except (ValueError, UnicodeError):
                result = None
            if result is None:
                self._json(401, {'error': 'Invalid or expired ticket'})
                return
            session, target = result
            cookie = '%s=%s; HttpOnly; SameSite=Strict; Path=/' % (
                self.server.access.cookie_name(self.server.server_port), session)
            self._json(200, {'path': target}, cookie)
        elif path == '/api/bootstrap-ticket':
            try:
                ticket = self.server.access.ticket(self._body().get('path', '/widget'))
            except (ValueError, UnicodeError):
                self._json(400, {'error': 'Invalid view request'})
                return
            self._json(200, {'ticket': ticket})
        elif path == '/api/login':
            code, result = start_login()
            self._json(code, result)
        elif path == '/api/login/cancel':
            self._json(200, cancel_login())
        elif path == '/api/sync':
            threading.Thread(target=STORE.sync_now, daemon=True).start()
            self._json(202, {'started': True})
        else:
            self._json(404, {'error': 'Not found'})


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
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        print(f'[server] port {port} already in use, another instance is running. Exit.')
        return
    httpd.access = LocalAccess()
    try:
        publish_capability(port, httpd.access.capability)
    except Exception:
        httpd.server_close()
        print('[server] unable to establish private local authorization')
        return
    STORE.refresh()                     # 先加载现有缓存（有缓存则立即可用）
    threading.Thread(target=sync_loop, daemon=True).start()
    print(f'[server] billing widget serving at http://{HOST}:{port}')
    httpd.serve_forever()


def main():
    serve()


if __name__ == '__main__':
    main()
