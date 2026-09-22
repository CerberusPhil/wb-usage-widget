# -*- coding: utf-8 -*-
"""
WorkBuddy 积分挂件 · 桌面壳（pywebview，缺则 Edge --app 降级）
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
TITLE = 'WB-积分挂件'
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
    return u


def _hwnd(timeout=6.0):
    """按窗口标题找 hwnd（等 GUI 起来，最多 timeout 秒）。"""
    try:
        u = _user32()
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = u.FindWindowW(None, TITLE)
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
    def drag_begin(self):
        """记录窗口基点与 DPI 缩放（后续按增量移动）。"""
        try:
            u = _user32()
            hwnd = _hwnd(timeout=1.5)
            if not hwnd:
                return False
            r = _RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            try:
                scale = u.GetDpiForWindow(hwnd) / 96.0
            except Exception:
                scale = 1.0
            self._drag = {'hwnd': hwnd, 'x0': r.left, 'y0': r.top, 'scale': scale}
            return True
        except Exception:
            return False

    def drag_to(self, dx, dy):
        """按屏幕增量移动窗口（保持原抓取点跟随光标）。"""
        d = getattr(self, '_drag', None)
        if not d:
            return False
        try:
            u = _user32()
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            x = int(d['x0'] + float(dx) * d['scale'])
            y = int(d['y0'] + float(dy) * d['scale'])
            u.SetWindowPos(d['hwnd'], None, x, y, 0, 0,
                           SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            return True
        except Exception:
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
