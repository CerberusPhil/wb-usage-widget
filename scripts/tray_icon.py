# -*- coding: utf-8 -*-
"""
Workbuddy积分看板 · 系统托盘图标（纯 ctypes · 零第三方依赖）
=============================================================
2026-10-23 v1.10.0 新增：挂件「—」按钮收进托盘（隐藏窗口 + 通知区域图标）。

设计要点（实现前必读，全部是有名的坑）：
  - Shell_NotifyIconW + 隐藏宿主窗 + 独立消息循环线程（daemon）
  - 宿主窗必须是普通不可见顶层窗（style=0、父窗 None）：message-only
    （HWND_MESSAGE）收不到 TaskbarCreated 广播，Explorer 重启后图标无法自愈
  - WNDPROC 回调必须保存模块级强引用，否则被 GC → 随机崩溃（ctypes 经典坑）
  - 显隐判断不在此模块存状态：消费方用 IsWindowVisible 实时查询（单一真相，免锁）
  - 右键菜单 TPM_RETURNCMD + 弹出前 SetForegroundWindow + 弹出后 PostMessage(WM_NULL)
    —— 不做这两步，菜单点外部不消失/留残影（托盘菜单两大经典 bug）
  - 每次启动用唯一窗口类名（自增序号），规避同进程内重启托盘时
    RegisterClassExW 撞 ERROR_CLASS_ALREADY_EXISTS 后旧回调引用悬空的问题

对外 API：
  start_tray(icon_path, tooltip, on_toggle, on_sync, on_full, on_exit, on_show, on_login) -> bool
      启动托盘线程并注册图标（最多等 3s）；返回 False = 创建失败（调用方自行降级）。
      回调在托盘线程内被调用，内部自行保证线程安全。
  remove_tray()   摘除图标并结束托盘线程（幂等；跨线程安全，仅投递消息）
  is_active()     托盘是否在运行（线程活着且图标注册成功）

风险与对策：
  - ico 加载失败 → 16x16 重试 → 仍失败则去 NIF_ICON 注册纯行为图标（功能不挂）
  - 强杀进程会留幽灵图标（Windows 悬停自清，不处理）
  - 任何异常都不外抛：托盘是锦上添花，挂件本体不能被它拖死
"""
import ctypes
import os
import sys
import time
import threading
from ctypes import wintypes

# ---------------------------------------------------------------- 常量
WM_APP_TRAY = 0x8001            # WM_APP+1：托盘鼠标回调（legacy 语义 lparam=鼠标消息）
WM_APP_EXIT = 0x8002            # 自定义：通知托盘线程摘图标退出
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
LR_DEFAULTSIZE = 0x40
MF_STRING = 0x0
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x1, 0x2, 0x40

MENU_IDS = (1001, 1005, 1002, 1003, 1004)
MENU_TEXT = {1001: '显示 / 隐藏面板', 1002: '立即同步', 1003: '打开完整版',
             1004: '退出', 1005: '登录 / 重新授权'}
MENU_ACTION = {1001: 'on_toggle', 1002: 'on_sync', 1003: 'on_full',
               1004: 'on_exit', 1005: 'on_login'}

# ---------------------------------------------------------------- 类型
# wintypes 没有 LRESULT / GUID —— 自行补齐
LRESULT = wintypes.LPARAM


class _GUID(ctypes.Structure):
    _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD),
                ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class NOTIFYICONDATAW(ctypes.Structure):
    class _UTimeoutOrVersion(ctypes.Union):
        _fields_ = [('uTimeout', wintypes.UINT), ('uVersion', wintypes.UINT)]

    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('hWnd', wintypes.HWND),
        ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT),
        ('uCallbackMessage', wintypes.UINT),
        ('hIcon', wintypes.HICON),
        ('szTip', wintypes.WCHAR * 128),
        ('dwState', wintypes.DWORD),
        ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256),
        ('uTimeoutOrVersion', _UTimeoutOrVersion),
        ('szInfoTitle', wintypes.WCHAR * 64),
        ('dwInfoFlags', wintypes.DWORD),
        ('guidItem', _GUID),
        ('hBalloonIcon', wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT),
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
        ('hIconSm', wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]



class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                ('uID', wintypes.UINT), ('guidItem', _GUID)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

# ---------------------------------------------------------------- 日志
def _log(msg):
    """与 billing_widget.log / wb_widget_app.log 同文件同格式（不 import 对方，保持依赖单向）。"""
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'WBCreditWidget')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'widget.log'), 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


# ---------------------------------------------------------------- DLL 与原型
_u = _s = _k = None


def _libs():
    """user32/shell32/kernel32 惰性单例；全部显式 restype/argtypes（64 位防指针截断）。"""
    global _u, _s, _k
    if _u is not None:
        return _u, _s, _k
    u = ctypes.WinDLL('user32', use_last_error=True)
    s = ctypes.WinDLL('shell32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)

    u.RegisterClassExW.restype = wintypes.ATOM
    u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u.DefWindowProcW.restype = LRESULT
    u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.GetMessageW.restype = wintypes.BOOL
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                              wintypes.UINT, wintypes.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.restype = LRESULT
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.PostQuitMessage.argtypes = [ctypes.c_int]
    u.PostMessageW.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.DestroyWindow.restype = wintypes.BOOL
    u.DestroyWindow.argtypes = [wintypes.HWND]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.restype = wintypes.BOOL
    u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
    u.TrackPopupMenuEx.restype = wintypes.BOOL
    u.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.LPVOID]
    u.GetCursorPos.restype = wintypes.BOOL
    u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u.WindowFromPoint.restype = wintypes.HWND
    u.WindowFromPoint.argtypes = [POINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.RegisterWindowMessageW.restype = wintypes.UINT
    u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    u.LoadImageW.restype = wintypes.HICON
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.DestroyMenu.restype = wintypes.BOOL
    u.DestroyMenu.argtypes = [wintypes.HMENU]

    s.Shell_NotifyIconW.restype = wintypes.BOOL
    s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    s.Shell_NotifyIconGetRect.restype = ctypes.c_long
    s.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                        ctypes.POINTER(wintypes.RECT)]
    u.MonitorFromRect.restype = wintypes.HANDLE
    u.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    u.GetMonitorInfoW.restype = wintypes.BOOL
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.SetWindowPos.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    k.GetModuleHandleW.restype = wintypes.HINSTANCE
    k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    _u, _s, _k = u, s, k
    return u, s, k


# ---------------------------------------------------------------- 状态
# hwnd/nid/hicon 只允许托盘线程写；remove_tray 只读 hwnd 投递消息 → 免锁
_state = {
    'hwnd': None, 'nid': None, 'hicon': None, 'thread': None,
    'wndproc_ref': None, 'wm_taskbarcreated': None, 'wm_showpopup': None,
    'cb': {}, 'icon_path': '', 'tooltip': '',
    'added': False, 'error': '',
}
_SEQ = [0]          # 托盘启动序号 → 唯一窗口类名


def is_active():
    t = _state.get('thread')
    return bool(t and t.is_alive() and _state.get('added'))


def window_hovered(hwnd):
    u, _, _ = _libs()
    point = POINT()
    return bool(u.GetCursorPos(ctypes.byref(point)) and
                u.GetAncestor(u.WindowFromPoint(point), 2) == hwnd)


def place_window(hwnd):
    """Use physical pixels throughout, including mixed-DPI and negative-origin monitors."""
    from popup_controller import anchored_position
    u, s, _ = _libs()
    nid = _state.get('nid')
    if not nid:
        return False
    identity = NOTIFYICONIDENTIFIER()
    identity.cbSize = ctypes.sizeof(identity)
    identity.hWnd, identity.uID = nid.hWnd, nid.uID
    anchor, window = wintypes.RECT(), wintypes.RECT()
    found = s.Shell_NotifyIconGetRect(ctypes.byref(identity), ctypes.byref(anchor)) == 0
    if not found:
        # Overflow/hidden icons may have no rectangle. Use the clicked monitor,
        # not the primary display; still keep a reachable panel inside its work area.
        cursor = POINT()
        if not u.GetCursorPos(ctypes.byref(cursor)):
            return False
        anchor = wintypes.RECT(cursor.x, cursor.y, cursor.x + 1, cursor.y + 1)
    monitor = u.MonitorFromRect(ctypes.byref(anchor), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not u.GetMonitorInfoW(monitor, ctypes.byref(info)) or not u.GetWindowRect(hwnd, ctypes.byref(window)):
        return False
    rect = lambda r: (r.left, r.top, r.right, r.bottom)
    width = min(window.right - window.left, info.rcWork.right - info.rcWork.left)
    height = min(window.bottom - window.top, info.rcWork.bottom - info.rcWork.top)
    x, y = anchored_position(rect(anchor), rect(info.rcWork), (width, height))
    return bool(u.SetWindowPos(hwnd, None, x, y, width, height, 0x4 | 0x10))


# ---------------------------------------------------------------- 图标注册
def _nid_add():
    nid = _state.get('nid')
    if not nid:
        return False
    ok = bool(_s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
    _state['added'] = ok
    return ok


def _nid_delete():
    nid = _state.get('nid')
    if nid:
        try:
            _s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
    _state['added'] = False


# ---------------------------------------------------------------- 分发（可单测）
def _fire(name):
    fn = (_state.get('cb') or {}).get(name)
    if not fn:
        return
    def invoke():
        try:
            fn()
        except Exception:
            _log('tray callback %s failed' % name)
    if name in ('on_sync', 'on_full', 'on_login'):
        threading.Thread(target=invoke, daemon=True).start()
    else:
        invoke()


def _dispatch(hwnd, msg, wparam, lparam):
    """WndProc 路由核心（独立成函数：tray_probe 可绕过真鼠标直接单测）。"""
    if msg == WM_APP_TRAY:
        if lparam == WM_LBUTTONUP:
            _fire('on_toggle')
        elif lparam == WM_RBUTTONUP:
            _show_menu(hwnd)
        return 0
    if msg == _state.get('wm_showpopup'):
        _fire('on_show')
        return 0
    if msg == _state.get('wm_taskbarcreated'):
        # Explorer 重启广播 → 重新注册图标（NIM_ADD 幂等）
        _nid_add()
        _log('tray re-added after TaskbarCreated' if _state['added'] else 'tray re-add failed')
        return 0
    if msg == WM_APP_EXIT:
        _nid_delete()
        _u.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        _u.PostQuitMessage(0)
        return 0
    return _u.DefWindowProcW(hwnd, msg, wparam, lparam)


def _show_menu(hwnd):
    """右键菜单：TPM_RETURNCMD 同步拿选中 ID（免 WM_COMMAND 二次路由）。"""
    menu = _u.CreatePopupMenu()
    if not menu:
        return
    try:
        for mid in MENU_IDS:
            _u.AppendMenuW(menu, MF_STRING, mid, MENU_TEXT[mid])
        pt = POINT()
        _u.GetCursorPos(ctypes.byref(pt))
        # 经典 bug 修复①：不先 SetForegroundWindow，点菜单外区域菜单不消失
        _u.SetForegroundWindow(hwnd)
        chosen = _u.TrackPopupMenuEx(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                                     pt.x, pt.y, hwnd, None)
        # 经典 bug 修复②：吃掉前置化残留的 WM_NULL，否则下次单击托盘会再弹一次菜单
        _u.PostMessageW(hwnd, WM_NULL, 0, 0)
    finally:
        _u.DestroyMenu(menu)
    if chosen in MENU_ACTION:
        _fire(MENU_ACTION[chosen])


# ---------------------------------------------------------------- 线程主体
def _thread_main(ev):
    try:
        _thread_init_and_loop()
    except Exception as e:
        _state['error'] = repr(e)
        _log('tray init failed: %r' % e)
    finally:
        try:
            ev.set()
        except Exception:
            pass


def _thread_init_and_loop():
    u, s, k = _libs()
    hinst = k.GetModuleHandleW(None)

    # WNDPROC 强引用挂模块级 —— 被 GC 就是随机崩溃（本文件头注释的坑）
    _state['wndproc_ref'] = WNDPROC(_dispatch)

    _SEQ[0] += 1
    cls_name = 'WBCreditWidget_TrayHost_%d' % _SEQ[0]
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _state['wndproc_ref']
    wc.hInstance = hinst
    wc.lpszClassName = cls_name
    if not u.RegisterClassExW(ctypes.byref(wc)):
        raise OSError('RegisterClassExW failed err=%d' % ctypes.get_last_error())

    # 普通不可见顶层窗（style=0、父窗 None）—— message-only 收不到 TaskbarCreated
    hwnd = u.CreateWindowExW(0, cls_name, 'WBCreditWidgetTrayHost', 0,
                             0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise OSError('CreateWindowExW failed err=%d' % ctypes.get_last_error())
    _state['hwnd'] = hwnd
    _state['wm_taskbarcreated'] = u.RegisterWindowMessageW('TaskbarCreated')
    _state['wm_showpopup'] = u.RegisterWindowMessageW('WBCreditWidget_ShowPopup')

    # 图标：默认尺寸 → 16x16 重试 → 仍失败则纯行为图标（无图但可点，功能不挂）
    hicon = u.LoadImageW(None, _state['icon_path'], IMAGE_ICON, 0, 0,
                         LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if not hicon:
        hicon = u.LoadImageW(None, _state['icon_path'], IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if hicon:
            _log('tray icon fallback to 16x16')
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uCallbackMessage = WM_APP_TRAY
    nid.szTip = (_state['tooltip'] or '')[:127]
    nid.uFlags = NIF_MESSAGE | NIF_TIP
    if hicon:
        nid.uFlags |= NIF_ICON
        nid.hIcon = hicon
        _state['hicon'] = hicon
    _state['nid'] = nid

    if not _nid_add():
        raise OSError('Shell_NotifyIconW(NIM_ADD) failed err=%d' % ctypes.get_last_error())
    _log('tray added (icon=%s)' % bool(hicon))

    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))
    _log('tray loop exit')
    _state['added'] = False
    _state['hwnd'] = None


# ---------------------------------------------------------------- 对外 API
def start_tray(icon_path, tooltip, on_toggle, on_sync, on_full, on_exit, on_show, on_login):
    """启动托盘线程并注册图标（最多等 3s）。返回 False = 失败（调用方降级）。"""
    if not sys.platform.startswith('win'):
        return False
    if _state.get('thread') and _state['thread'].is_alive():
        return is_active()
    _state.update({'hwnd': None, 'nid': None, 'hicon': None, 'wndproc_ref': None,
                   'wm_taskbarcreated': None, 'added': False, 'error': '',
                   'icon_path': icon_path or '', 'tooltip': tooltip or '',
                   'cb': {'on_toggle': on_toggle, 'on_sync': on_sync,
                          'on_full': on_full, 'on_exit': on_exit,
                          'on_show': on_show, 'on_login': on_login}})
    ev = threading.Event()
    t = threading.Thread(target=_thread_main, args=(ev,), daemon=True, name='wb-tray')
    _state['thread'] = t
    t.start()
    ev.wait(3.0)
    if not _state.get('added'):
        return False
    return True


def remove_tray():
    """摘图标 + 结束托盘线程。幂等；跨线程安全（只投递消息，不碰窗口句柄）。"""
    hwnd = _state.get('hwnd')
    if not hwnd:
        return
    try:
        _libs()
        _u.PostMessageW(hwnd, WM_APP_EXIT, 0, 0)
    except Exception as e:
        _log('remove_tray failed: %r' % e)
