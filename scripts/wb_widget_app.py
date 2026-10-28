# -*- coding: utf-8 -*-
"""
Workbuddy积分看板 · 单文件入口（源码一键启动；PyInstaller 打包后的 exe 入口）
================================================================================
启动流程：
  1) 单实例互斥锁——已有实例运行则聚焦其窗口并退出（不会开出第二个挂件）
  2) 内嵌启动 8790 迷你服务（端口已被旧进程/其它实例占用则跳过，直接复用）
  3) 等服务就绪 → pywebview 无边框挂件窗口（右下角、置顶、可拖拽/缩放）

用法：
  python wb_widget_app.py                # 默认 8790，等价于原 start_billing_widget 全流程
  python wb_widget_app.py --port 8800    # 自定义端口（测试用）
日志（exe/冻结模式写文件；源码模式直接看控制台）：
  %LOCALAPPDATA%\\WBCreditWidget\\widget.log
"""
import os
import sys
import threading
import time
import traceback

APP_MUTEX = 'WBCreditWidget_Singleton_v1'
WINDOW_TITLE = 'Workbuddy积分看板'             # 兜底值；运行期真值取自 billing_widget.TITLE
LOG_DIR_NAME = 'WBCreditWidget'
_KEEP = []                                     # 保持互斥锁句柄引用（进程存活期不释放）
_STDIO_REDIRECTED = False


def _log_file():
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Logs')
    else:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, LOG_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, 'widget.log')


def log(msg):
    line = '[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        with open(_log_file(), 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
    if not _STDIO_REDIRECTED:
        try:
            print(line.rstrip())
        except Exception:
            pass


def _setup_frozen_stdio():
    """冻结 + windowed：stdout/stderr 全部落日志，便于事后排障。"""
    global _STDIO_REDIRECTED
    if getattr(sys, 'frozen', False):
        try:
            f = open(_log_file(), 'a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = f
            _STDIO_REDIRECTED = True
        except Exception:
            pass


def acquire_single_instance():
    """命名互斥锁；返回 False = 已有实例在运行。"""
    if sys.platform == 'darwin':
        import fcntl
        lock = open(os.path.join(os.path.dirname(_log_file()), 'widget.lock'), 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        _KEEP.append(lock)
        return True
    import ctypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = k32.CreateMutexW(None, 0, APP_MUTEX)
    _KEEP.append(handle)                       # 句柄随进程存活，进程退出时自动释放
    return ctypes.get_last_error() != 183      # 183 = ERROR_ALREADY_EXISTS


def _window_title():
    """窗口标题的唯一真相在 billing_widget.TITLE（避免两处硬编码产生漂移）。"""
    try:
        import billing_widget as _bw
        return getattr(_bw, 'TITLE', WINDOW_TITLE)
    except Exception:
        return WINDOW_TITLE


def focus_existing_window(timeout=3.0):
    """Ask the owning process to show its popover through its normal lifecycle."""
    if sys.platform == 'darwin':
        import signal
        try:
            with open(os.path.join(os.path.dirname(_log_file()), 'widget.lock')) as f:
                os.kill(int(f.read()), signal.SIGUSR1)
            return True
        except (OSError, ValueError):
            return False
    import ctypes
    u = ctypes.windll.user32
    u.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    u.RegisterWindowMessageW.restype = ctypes.c_uint
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t]
    u.PostMessageW.restype = ctypes.c_bool
    message = u.RegisterWindowMessageW('WBCreditWidget_ShowPopup')
    return bool(message and u.PostMessageW(ctypes.c_void_p(0xffff), message, 0, 0))


def wait_server(port, timeout=25.0):
    """轮询等待服务可用（自己新起的 / 复用的旧实例均可）。"""
    from billing_server import local_request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            local_request(port, '/api/data')
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _cookie_renew_worker():
    """打开挂件时的凭据保活（2026-09-28 引入，2026-10-13 改为条件触发）。

    - 长效令牌（RT）是主通道：**每次开 exe 顺手刷新一次** —— 既验证可用性，
      又把 refresh_token 的 30 天滑动窗口续上（间隔 < 30 天开一次即永久有效）。
    - 仅当 RT 未配置或刷新失败时，才拉起专用 Edge 续期 cookie（最后兜底通道）。
    - 失败不影响挂件本体（daemon 线程 + 全捕获）；脚本随 exe 分发（spec datas）
    """
    time.sleep(6)                       # 先让窗口起来，保活在后台慢慢跑
    try:
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        if base not in sys.path:
            sys.path.insert(0, base)
        import server_usage_sync as sus
        if sus.rt_info().get('configured'):
            r = sus.load_bearer_via_rt()          # AT 缓存命中则复用；否则刷新 + 回写轮换值
            if r:
                if r.get('at_cached'):
                    log('at 缓存命中（%s 天龄，免刷新）；refresh_token 由 ≤7 天一次的实刷滑动续期，'
                        '跳过 Edge' % r.get('at_age_days'))
                else:
                    days = round((r.get('expires_in') or 0) / 86400.0, 1)
                    log('rt self-renew ok（accessToken 约 %s 天，refresh_token 30 天滑动）'
                        '；cookie 仅作最后兜底，跳过 Edge 续期' % days)
                return
            log('rt self-renew failed: %s → 回退去续期 cookie' % sus._RT_NOTE)
        import cookie_auto_renew as car
        creds = car.load_creds()
        if not creds.get('cookie'):
            log('no saved cookie; waiting for first interactive login')
            return
        import datetime
        import billing_server
        expiry = car.sc.session_expiry(creds['cookie'])
        if (billing_server.STORE.last_sync or {}).get('ok') and expiry is not None:
            if (expiry - datetime.datetime.now()).total_seconds() > 86400:
                log('saved web session is valid; no browser renewal needed')
                return
        rc = car.renew(headless=True)
        log('cookie auto renew: rc=%s' % rc)
    except Exception:
        log('cookie auto renew unavailable; use explicit login if needed')


def main():
    argv = sys.argv[1:]
    port = 8790
    if '--port' in argv:
        try:
            port = int(argv[argv.index('--port') + 1])
        except (ValueError, IndexError):
            pass

    if not acquire_single_instance():
        log('another instance is running; focus existing window and exit')
        focus_existing_window()
        return 0

    import billing_server
    import billing_widget as bw
    if sys.platform == 'darwin':
        import signal
        import macos_widget
        signal.signal(signal.SIGUSR1, lambda *_:
                      threading.Thread(target=macos_widget.show_widget, daemon=True).start())
    bw.URL_BASE = 'http://127.0.0.1:%d' % port

    # A pre-existing listener is reusable only when its private capability validates.
    threading.Thread(target=billing_server.serve, args=(port,),
                     daemon=True, name='wb-server').start()
    if wait_server(port):
        log('server ready: http://127.0.0.1:%d' % port)
    else:
        log('server authentication or startup failed on port %d' % port)
        return 1

    url = billing_server.client_url(port, '/widget')
    log('opening authenticated widget window')
    threading.Thread(target=_cookie_renew_worker, daemon=True, name='wb-cookie-renew').start()
    if '--tray-smoke' in argv:
        threading.Thread(target=_tray_smoke, daemon=True, name='wb-tray-smoke').start()
    code = bw.run_pywebview(url, 420, 450, None, None, False)
    if '--tray-smoke' in argv and _SMOKE_FAILED:
        code = 1
    log('widget window closed (exit=%s)' % code)
    return code


_SMOKE_FAILED = False


def _tray_smoke():
    """Exercise real window geometry, interaction hold, Pin and idle dismissal."""
    global _SMOKE_FAILED
    import billing_widget as bw
    import webview
    time.sleep(8)
    try:
        win = webview.windows[0]
        api = bw.WidgetApi()
        assert bw._tray.is_active(), 'status icon is missing'
        assert not api.popup_state()['visible'], 'idle popover did not dismiss'
        bw.show_widget()
        time.sleep(0.5)
        assert win.evaluate_js('Boolean(document.getElementById(\"btnPin\"))'), 'Pin control is missing'
        api.popup_activity('pointer', True)
        time.sleep(5.3)
        assert api.popup_state()['visible'], 'interaction did not pause dismissal'
        api.set_pinned(True)
        api.popup_activity('pointer', False)
        if sys.platform == 'darwin':
            x0, y0 = bw._tray.window_position()
            assert api.drag_begin(), 'pinned panel cannot drag'
            api.drag_to(-40, 40)
            time.sleep(0.3)
            api.drag_end()
            assert bw._tray.window_position() == (x0 - 40, y0 + 40), 'native drag failed'
            api.resize_window(460, 500)
            time.sleep(0.3)
            assert (win.width, win.height) == (460, 500), 'native resize failed'
        time.sleep(5.3)
        assert api.popup_state()['visible'], 'pinned panel unexpectedly dismissed'
        api.set_pinned(False)
        time.sleep(0.3)
        if sys.platform == 'darwin':
            assert bw._tray.window_position()[1] == y0, 'unpin did not reanchor'
        time.sleep(5.3)
        assert not api.popup_state()['visible'], 'unpin did not restore dismissal'
        if sys.platform == 'darwin':
            assert not win.native.isVisible(), 'native window still visible'
        log('smoke: anchored popup, Pin, hover hold, drag, resize and auto-hide passed')
    except Exception as exc:
        _SMOKE_FAILED = True
        detail = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        log('smoke failed: %s' % detail)
    finally:
        bw.exit_via_tray()


if __name__ == '__main__':
    _setup_frozen_stdio()
    log('==== WBCreditWidget start (frozen=%s, pid=%s) ====' %
        (getattr(sys, 'frozen', False), os.getpid()))
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log('FATAL: %s (details omitted to protect credentials)' % type(exc).__name__)
        sys.exit(1)
