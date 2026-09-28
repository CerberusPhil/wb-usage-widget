# -*- coding: utf-8 -*-
"""
Workbuddy积分看板 · 桌面壳（pywebview，缺则 Edge --app 降级）
=============================================================
依赖：本机 8790 上的 billing_server.py 必须先跑起来（start_billing_widget.bat 会先起它）。

用法：
  python billing_widget.py                  # 默认 420x450，桌面右下角，置顶，可拖拽
  python billing_widget.py --x 20 --y 40    # 指定位置
  python billing_widget.py --w 520 --h 700  # 指定尺寸
  python billing_widget.py --transparent    # 实验：透明背景（Win11 有已知鼠标事件问题，默认关）
  python billing_widget.py --fallback       # 强制 Edge/Chrome --app 模式（无置顶）

交互（页内按钮，2026-09-21；拖拽/点击手势当日二次重构）：
  - 任意卡片/空白处拖动：移动窗口（自实现指针拖拽：位移越过 4px 阈值才启用鼠标捕获并开拖，
    纯点击不被打断；drag_begin/drag_to/drag_end → SetWindowPos）
  - 页头 −  最小化到任务栏（pywebview minimize，失败降级 Win32 ShowWindow）
  - 页头 ✕  关闭窗口（仅关窗；后台同步服务继续跑，要连服务一起停用 stop_billing_widget.bat）
  - 右缘拖动：等比缩放（高按当前宽高比跟随，内容整体缩放）
  - 下缘拖动：纵向拉长 → 列表显示更多行；按住 Shift = 锁比例缩放
  - 右下角拖动：自由调整宽高（斜向一次到位）；按住 Shift = 锁比例缩放
  - 余额卡点击：用系统默认浏览器打开成长计划页（open_url 桥；浏览器预览走原生链接）
  - 窗口/任务栏图标：scripts\\widget_icon.ico（换图：替换该文件即可，改路径见 ICON_FILE）
已知边界（2025-2026 调研）：
  - pywebview 透明窗口在 Win11 24H2 有鼠标事件历史问题 → 默认不透明卡片式（更稳、也像小组件卡片）
  - Edge --app 降级无置顶：可配 PowerToys 的 Always On Top（Win+Ctrl+T）；降级模式下页内按钮不可用
"""
import ctypes
import os
import sys
import argparse
import subprocess
import time

URL_BASE = 'http://127.0.0.1:8790'
TITLE = 'Workbuddy积分看板'                  # 窗口标题 = 任务栏显示名（唯一真相：别处请引用本常量）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):            # PyInstaller onefile：数据文件解包在 _MEIPASS 根
    SCRIPT_DIR = getattr(sys, '_MEIPASS', SCRIPT_DIR)
ICON_FILE = os.path.join(SCRIPT_DIR, 'widget_icon.ico')
BROWSERS = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
]


def log(msg):
    """挂件日志（2026-09-24）：与 wb_widget_app.log 同格式，落 %LOCALAPPDATA%\\WBCreditWidget\\widget.log。

    ⚠️ 本模块的 drag_begin/drag_to/dbg 直接调用 log()——过去没定义、运行时 NameError，
    把拖拽诊断日志全炸掉（拖不动排障的断点之一）。billing_widget 被单独 import 时也必须能落日志。
    """
    line = '[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'WBCreditWidget')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'widget.log'), 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
    try:
        print(line.rstrip())
    except Exception:
        pass


def server_up(timeout=1.5):
    try:
        import urllib.request
        urllib.request.urlopen(URL_BASE + '/api/data', timeout=timeout)
        return True
    except Exception:
        return False


def find_browser():
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    return None


def default_pos(w, h):
    """默认右下角（留出任务栏）。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        return max(0, sw - w - 24), max(0, sh - h - 72)
    except Exception:
        return None, None


# ---------- Win32 小工具（图标 / 最小化 / 缩放 / 拖拽 的底座） ----------
class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


def _user32():
    """配置好 argtypes 的 user32（64 位下必须，避免指针截断）。"""
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.LoadImageW.restype = ctypes.c_void_p
    u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SendMessageW.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = ctypes.c_bool
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    u.GetWindowRect.restype = ctypes.c_bool
    u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    u.GetDpiForWindow.restype = ctypes.c_uint
    # 句柄安全（64 位下 int→c_void_p 显式声明，防截断；2026-09-24 拖拽排障加固）
    u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    u.GetWindowThreadProcessId.restype = ctypes.c_uint
    u.InternalGetWindowText.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.InternalGetWindowText.restype = ctypes.c_int
    return u


TABPROXY_CLS = 'Windows.Internal.Shell.TabProxyWindow'   # Win11 标签代理窗（explorer 所有，标题照抄标签页）


def _win_title_of(u, hwnd):
    """读窗口标题：InternalGetWindowText 直读缓存（跨进程不发消息，挂起的窗也能读到）。"""
    buf = ctypes.create_unicode_buffer(256)
    try:
        n = u.InternalGetWindowText(hwnd, buf, 256)
        if n:
            return buf.value
    except Exception:
        pass
    try:
        u.GetWindowTextW(hwnd, buf, 256)
    except Exception:
        pass
    return buf.value


def _exe_of_pid(pid):
    """进程映像名（小写），失败返回 ''。"""
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, 0, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(32768)
            n = ctypes.c_uint(32768)
            if k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n)):
                return os.path.basename(buf.value).lower()
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        pass
    return ''


def find_hwnd(title, exe_name=None, pid=None):
    """精确找窗：标题 + 归属进程（映像名或 pid），排除 Win11 标签代理窗。

    不能用 FindWindowW(None, title) —— 浏览器开着同名标签页时（如完整看板），
    Win11 会造 explorer 的 TabProxyWindow 幽灵窗、标题与真窗完全相同，
    FindWindowW 会命中幽灵窗 → 拖拽/图标全打偏（2026-09-24 踩坑实录）。
    """
    u = _user32()
    u.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    want_exe = (exe_name or '').lower()
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        cls = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(hwnd, cls, 128)
        if cls.value == TABPROXY_CLS:
            return True
        if _win_title_of(u, hwnd) != title:
            return True
        wpid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if pid is not None and wpid.value != pid:
            return True
        if want_exe and _exe_of_pid(wpid.value) != want_exe:
            return True
        found.append(hwnd)
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def _hwnd(timeout=6.0):
    """找本挂件主窗口 hwnd：pywebview 自带句柄优先，退化为「标题 + 自有进程」过滤查找。"""
    try:
        import webview
        wins = getattr(webview, 'windows', None)
        w = wins[0] if wins else None
        native = getattr(w, 'native', None) if w is not None else None
        h = getattr(native, 'Handle', None) if native is not None else None
        if h:
            try:
                return int(h)
            except Exception:
                try:
                    return int(h.ToInt64())
                except Exception:
                    pass
    except Exception:
        pass
    try:
        me = os.path.basename(sys.executable).lower()
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = find_hwnd(TITLE, pid=os.getpid()) or find_hwnd(TITLE, exe_name=me)
            if h:
                return h
            time.sleep(0.1)
    except Exception:
        pass
    return None


def apply_icon():
    """把 widget_icon.ico 设为窗口/任务栏图标（WM_SETICON；随 webview.start(func) 在 GUI 启动后执行）。"""
    if not os.path.isfile(ICON_FILE):
        print('[widget] icon file not found: %s' % ICON_FILE)
        return
    try:
        u = _user32()
        hwnd = _hwnd()
        if not hwnd:
            print('[widget] icon skipped: window not found')
            return
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        for size, which in ((16, ICON_SMALL), (32, ICON_BIG)):
            h = u.LoadImageW(None, ICON_FILE, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if h:
                u.SendMessageW(hwnd, WM_SETICON, which, h)
        print('[widget] icon applied')
    except Exception as e:
        print('[widget] icon apply skipped: %s' % e)


class WidgetApi:
    """JS → Python 桥：窗口控制（关闭 / 最小化 / 缩放）。"""

    def close_widget(self):
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
        return True

    def minimize_widget(self):
        """最小化到任务栏：pywebview 原生优先，失败走 Win32。"""
        try:
            import webview
            win = webview.windows[0] if webview.windows else None
            if win is not None and hasattr(win, 'minimize'):
                win.minimize()
                return True
        except Exception:
            pass
        try:
            hwnd = _hwnd(timeout=1.5)
            if hwnd:
                _user32().ShowWindow(hwnd, 6)      # SW_MINIMIZE
                return True
        except Exception:
            pass
        return False

    def open_url(self, url):
        """用系统默认浏览器打开链接（余额卡 → 成长计划、「完整版」按钮等）。"""
        try:
            url = str(url)
            if not url.lower().startswith(('http://', 'https://')):
                return False
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def resize_window(self, w, h):
        """程序化缩放窗口（拖拽热区用）：pywebview 原生优先，失败走 Win32（保持左上角）。"""
        try:
            w, h = int(w), int(h)
        except Exception:
            return False
        try:
            import webview
            win = webview.windows[0] if webview.windows else None
            if win is not None and hasattr(win, 'resize'):
                win.resize(w, h)
                return True
        except Exception:
            pass
        try:
            hwnd = _hwnd(timeout=1.5)
            if hwnd:
                SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0002, 0x0004, 0x0010
                _user32().SetWindowPos(hwnd, None, 0, 0, w, h,
                                       SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
                return True
        except Exception:
            pass
        return False

    # ---------- 整窗拖拽（自实现，替代 pywebview easy_drag） ----------
    def dbg(self, m):
        """JS 侧诊断探针（拖拽排障用，轻量、留作常备）。"""
        log('DBG js: %s' % m)
        return True

    def drag_begin(self):
        """记录窗口基点与 DPI 缩放（后续按增量移动）。"""
        try:
            u = _user32()
            hwnd = _hwnd(timeout=1.5)
            if not hwnd:
                log('DBG drag_begin: hwnd NOT FOUND')
                return False
            r = _RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                log('DBG drag_begin: hwnd=%s GetWindowRect FAILED' % hwnd)
                self._drag = None
                return False
            cls = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, cls, 128)
            try:
                scale = u.GetDpiForWindow(hwnd) / 96.0
            except Exception:
                scale = 1.0
            if not scale or scale < 0.5 or scale > 5:      # DPI 异常兜底：scale=0 会让所有位移归零
                log('DBG drag_begin: hwnd=%s bad scale=%s -> 1.0' % (hwnd, scale))
                scale = 1.0
            self._drag = {'hwnd': hwnd, 'x0': r.left, 'y0': r.top, 'scale': scale, 'n': 0}
            log('DBG drag_begin: hwnd=%s cls=%s rect=(%s,%s,%s,%s) scale=%s'
                % (hwnd, cls.value, r.left, r.top, r.right, r.bottom, round(scale, 3)))
            return True
        except Exception as e:
            try:
                log('DBG drag_begin: EXC %r' % e)
            except Exception:
                pass
            return False

    def drag_to(self, dx, dy):
        """按屏幕增量移动窗口（保持原抓取点跟随光标）。"""
        d = getattr(self, '_drag', None)
        if not d:
            log('DBG drag_to: no _drag state (drag_begin 未先行成功)')
            return False
        try:
            u = _user32()
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            x = int(d['x0'] + float(dx) * d['scale'])
            y = int(d['y0'] + float(dy) * d['scale'])
            ok = bool(u.SetWindowPos(d['hwnd'], None, x, y, 0, 0,
                                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
            if d.get('n', 0) < 3:
                d['n'] = d.get('n', 0) + 1
                log('DBG drag_to #%d: dx=%s dy=%s -> (%d,%d) ok=%s' % (d['n'], dx, dy, x, y, ok))
            return ok
        except Exception as e:
            log('DBG drag_to: EXC %s' % e)
            return False

    def drag_end(self):
        self._drag = None
        return True


def run_fallback(url, w, h, x, y):
    b = find_browser()
    if not b:
        print('[widget] no Edge/Chrome found, cannot fallback.')
        return 1
    cmd = [b, '--app=' + url, '--window-size=%d,%d' % (w, h),
           '--disable-features=Translate,msTranslate']
    if x is not None and y is not None:
        cmd.append('--window-position=%d,%d' % (x, y))
    subprocess.Popen(cmd)
    print('[widget] fallback (browser app mode): ' + b)
    print('[widget] note: no always-on-top in fallback; use PowerToys AlwaysOnTop (Win+Ctrl+T)')
    return 0


def run_pywebview(url, w, h, x, y, transparent):
    import webview
    if x is None or y is None:
        x, y = default_pos(w, h)
    kw = dict(title=TITLE, url=url, width=w, height=h,
              frameless=True, easy_drag=False, draggable=False, on_top=True,
              js_api=WidgetApi(), min_size=(350, 340))
    if transparent:
        kw['transparent'] = True
    if x is not None:
        kw['x'] = x
    if y is not None:
        kw['y'] = y
    webview.create_window(**kw)
    # 图标双通道：① start(icon=) 原生设 Form.Icon（winforms 后端实际支持）；
    #            ② apply_icon 线程用 WM_SETICON 兜底（防原生通道失效）
    webview.start(apply_icon, icon=ICON_FILE)
    return 0


def main():
    ap = argparse.ArgumentParser(description='WB billing desktop widget shell')
    ap.add_argument('--w', type=int, default=420)
    ap.add_argument('--h', type=int, default=450)
    ap.add_argument('--x', type=int, default=None)
    ap.add_argument('--y', type=int, default=None)
    ap.add_argument('--transparent', action='store_true', help='experimental: transparent background')
    ap.add_argument('--fallback', action='store_true', help='force browser --app mode')
    a = ap.parse_args()

    url = URL_BASE + '/'
    if not server_up():
        print('[widget] billing server is NOT running at %s' % URL_BASE)
        print('[widget] start it first:  scripts\\start_billing_widget.bat')
        return 2

    if not a.fallback:
        try:
            import webview  # noqa: F401
        except Exception as e:
            print('[widget] pywebview unavailable (%s) -> fallback' % e)
            return run_fallback(url, a.w, a.h, a.x, a.y)
        return run_pywebview(url, a.w, a.h, a.x, a.y, a.transparent)
    return run_fallback(url, a.w, a.h, a.x, a.y)


if __name__ == '__main__':
    sys.exit(main())
