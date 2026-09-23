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
    """把已有挂件窗口带到前台（尽力而为，可能受系统焦点策略限制）。"""
    import ctypes
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = u.FindWindowW(None, _window_title())
        if hwnd:
            u.ShowWindow(ctypes.c_void_p(hwnd), 9)          # SW_RESTORE
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            return True
        time.sleep(0.15)
    return False


def wait_server(port, timeout=25.0):
    """轮询等待服务可用（自己新起的 / 复用的旧实例均可）。"""
    import urllib.request
    url = 'http://127.0.0.1:%d/api/data' % port
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.5)
            return True
        except Exception:
            time.sleep(0.25)
    return False


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

    # 内嵌服务：端口空闲则起；已占用则视为可复用（旧实例 / 开发模式服务）
    threading.Thread(target=billing_server.serve, args=(port,),
                     daemon=True, name='wb-server').start()
    if wait_server(port):
        log('server ready: http://127.0.0.1:%d' % port)
    else:
        log('WARN: server not reachable at port %d after timeout' % port)

    url = 'http://127.0.0.1:%d/' % port
    log('opening widget window -> %s' % url)
    code = bw.run_pywebview(url, 420, 450, None, None, False)
    log('widget window closed (exit=%s)' % code)
    return code


if __name__ == '__main__':
    _setup_frozen_stdio()
    log('==== WBCreditWidget start (frozen=%s, pid=%s) ====' %
        (getattr(sys, 'frozen', False), os.getpid()))
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log('FATAL:\n' + traceback.format_exc())
        sys.exit(1)
