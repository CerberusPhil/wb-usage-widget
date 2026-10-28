# -*- coding: utf-8 -*-
"""Cocoa status-item popover. All AppKit mutations run on the main thread."""
import threading

from popup_controller import PopupController, anchored_position

_window = None
_status_item = None
_target = None
_menu = None
_controller = None


def is_active():
    return _status_item is not None


def show_widget():
    if _controller is not None:
        _controller.show()


def hide_widget():
    return _controller.hide() if _controller is not None else False


def toggle_widget():
    if _controller is not None:
        _controller.toggle()


def popup_state():
    return _controller.state() if _controller else {'visible': False, 'pinned': False}


def set_pinned(value):
    return _controller.set_pinned(value) if _controller else popup_state()


def popup_activity(kind, active):
    return _controller.activity(kind, active) if _controller else False


def window_position():
    from AppKit import NSScreen
    frame = _window.native.frame()
    return (frame.origin.x, NSScreen.screens()[0].frame().size.height
            - frame.origin.y - frame.size.height)


def move_window(x, y):
    from AppKit import NSScreen
    from PyObjCTools import AppHelper
    def move():
        if _window is not None:
            _window.native.setFrameTopLeftPoint_((x, NSScreen.screens()[0].frame().size.height - y))
    AppHelper.callAfter(move)


def _place():
    from AppKit import NSScreen
    if _window is None or _status_item is None:
        return
    button = _status_item.button()
    status_window = button.window()
    if status_window is None:
        return
    anchor = status_window.convertRectToScreen_(button.convertRect_toView_(button.bounds(), None))
    screen = status_window.screen() or NSScreen.mainScreen()
    visible = screen.visibleFrame()
    primary_top = NSScreen.screens()[0].frame().size.height
    rect = lambda r: (r.origin.x, primary_top - r.origin.y - r.size.height,
                      r.origin.x + r.size.width, primary_top - r.origin.y)
    size = _window.native.frame().size
    width, height = min(size.width, visible.size.width), min(size.height, visible.size.height)
    if (width, height) != (size.width, size.height):
        _window.native.setContentSize_((width, height))
    x, y = anchored_position(rect(anchor), rect(visible), (width, height))
    _window.native.setFrameTopLeftPoint_((x, primary_top - y))


def _hovered():
    from AppKit import NSEvent, NSWindow
    point = NSEvent.mouseLocation()
    return (_window is not None and
            NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(point, 0)
            == _window.native.windowNumber())


def remove_tray():
    if _controller is not None:
        _controller.close()
    if _status_item is None:
        return
    from PyObjCTools import AppHelper
    from AppKit import NSStatusBar, NSNotificationCenter

    def remove():
        global _status_item, _target, _menu
        if _status_item is not None:
            NSNotificationCenter.defaultCenter().removeObserver_(_target)
            NSStatusBar.systemStatusBar().removeStatusItem_(_status_item)
            _status_item = _target = _menu = None
    AppHelper.callAfter(remove)


def run(url, width, height, x, y, transparent, api):
    import webview
    import billing_widget as bw
    from AppKit import (NSObject, NSMenu, NSMenuItem, NSStatusBar, NSApp,
                        NSVariableStatusItemLength, NSFloatingWindowLevel,
                        NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp, NSEventTypeRightMouseUp,
                        NSNotificationCenter, NSWindowDidResignKeyNotification,
                        NSWindowCollectionBehaviorMoveToActiveSpace,
                        NSWindowCollectionBehaviorFullScreenAuxiliary)
    from PyObjCTools import AppHelper

    class MenuTarget(NSObject):
        def statusClick_(self, sender):
            event = NSApp.currentEvent()
            if event is not None and event.type() == NSEventTypeRightMouseUp:
                _status_item.popUpStatusItemMenu_(_menu)
            else:
                toggle_widget()

        def toggle_(self, sender):
            toggle_widget()

        def sync_(self, sender):
            threading.Thread(target=bw.sync_now_via_tray, daemon=True).start()

        def full_(self, sender):
            threading.Thread(target=bw.open_full_via_tray, daemon=True).start()

        def login_(self, sender):
            threading.Thread(target=bw.login_via_tray, daemon=True).start()

        def quit_(self, sender):
            api.close_widget()

        def windowResigned_(self, notification):
            if _controller is not None and not _controller.state()['pinned']:
                hide_widget()

    def install_menu():
        global _status_item, _target, _menu, _controller
        _target = MenuTarget.alloc().init()
        _menu = NSMenu.alloc().init()
        for title, action in (('显示 / 隐藏面板', 'toggle:'),
                              ('登录 / 重新授权', 'login:'),
                              ('立即同步', 'sync:'),
                              ('打开完整版', 'full:'),
                              ('退出', 'quit:')):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, '')
            item.setTarget_(_target)
            _menu.addItem_(item)
        _status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = _status_item.button()
        button.setTitle_('WB')
        button.setToolTip_('WorkBuddy 用量 · 点击查看，右键菜单')
        button.setTarget_(_target)
        button.setAction_('statusClick:')
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        _window.native.setLevel_(NSFloatingWindowLevel)
        _window.native.setHasShadow_(True)
        _window.native.setCollectionBehavior_(NSWindowCollectionBehaviorMoveToActiveSpace |
                                               NSWindowCollectionBehaviorFullScreenAuxiliary)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            _target, 'windowResigned:', NSWindowDidResignKeyNotification, _window.native)
        _controller = PopupController(_window.show, _window.hide, _place, AppHelper.callAfter,
                                      hovered=_hovered)
        show_widget()
        bw.log('macOS status popover ready')

    global _window, _controller
    _window = webview.create_window(
        bw.TITLE, url, width=width, height=height, x=x, y=y, hidden=True,
        frameless=True, easy_drag=False, draggable=False, on_top=True,
        js_api=api, min_size=(350, 340), transparent=transparent)
    def ready():
        _window.events.loaded.wait()
        AppHelper.callAfter(install_menu)
    try:
        webview.start(ready, gui='cocoa', private_mode=True)
    finally:
        remove_tray()
        _controller = None
        _window = None
    return 0
