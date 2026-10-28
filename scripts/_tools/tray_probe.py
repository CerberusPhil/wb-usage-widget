# -*- coding: utf-8 -*-
"""tray_icon.py 自动化探针：生命周期 / 回调路由 / remove 幂等 / 重启。

用法：python tray_probe.py        （退出码 0 = 全过）
注意：运行期间桌面通知区域会短暂出现一个托盘图标（约 5 秒），属预期。
右键菜单弹窗会阻塞托盘线程等真人点击 —— 探针不自动派发 WM_RBUTTONUP，
菜单交互（P0 项）留给人工点检。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))            # scripts/
import tray_icon as ti                                # noqa: E402

ICON = os.path.join(os.path.dirname(HERE), 'widget_icon.ico')
FAILS = []


def check(name, cond):
    print(('  [PASS] ' if cond else '  [FAIL] ') + name)
    if not cond:
        FAILS.append(name)


calls = []


def cb(name):
    def fn():
        calls.append(name)
    return fn


def start():
    ok = ti.start_tray(ICON, 'WB tray probe', cb('toggle'), cb('sync'), cb('full'),
                      cb('exit'), cb('show'), cb('login'))
    time.sleep(0.3)
    return ok


def main():
    print('== 1) 启动与注册 ==')
    ok = start()
    check('start_tray 返回 True', ok)
    check('is_active() = True', ti.is_active())
    check('宿主窗句柄存在', bool(ti._state.get('hwnd')))
    check('图标已加载（NIF_ICON）', bool(ti._state.get('hicon')))

    print('== 2) 回调路由（直接喂消息，绕过真鼠标）==')
    hwnd = ti._state.get('hwnd')
    before = len(calls)
    ti._dispatch(hwnd, ti.WM_APP_TRAY, 1, ti.WM_LBUTTONUP)      # 左键 → on_toggle
    check('WM_LBUTTONUP → on_toggle 触发', len(calls) == before + 1 and calls[-1] == 'toggle')

    print('== 3) TaskbarCreated → 图标重注册 ==')
    ti._nid_delete()                      # 模拟 Explorer 重启：旧图标随之消失（真实场景前提）
    check('图标删除后 is_active = False', not ti.is_active())
    ti._dispatch(hwnd, ti._state.get('wm_taskbarcreated'), 0, 0)
    check('TaskbarCreated 广播后图标恢复', ti.is_active())

    print('== 4) remove_tray 幂等 + 线程退出 ==')
    ti.remove_tray()
    deadline = time.time() + 5
    while time.time() < deadline and ti._state['thread'].is_alive():
        time.sleep(0.1)
    check('托盘线程 5s 内退出', not ti._state['thread'].is_alive())
    check('is_active() = False', not ti.is_active())
    t0 = time.time()
    ti.remove_tray()
    ti.remove_tray()
    check('未运行时连调 remove 不抛异常且即时返回 (%.2fs)' % (time.time() - t0),
          time.time() - t0 < 1.0)

    print('== 5) 同进程重启（唯一类名防撞）==')
    calls.clear()
    ok = start()
    check('第二次 start_tray 成功', ok)
    check('is_active() = True', ti.is_active())
    ti.remove_tray()
    deadline = time.time() + 5
    while time.time() < deadline and ti._state['thread'].is_alive():
        time.sleep(0.1)
    check('第二次退出干净', not ti._state['thread'].is_alive())

    print()
    if FAILS:
        print('结果: %d 项失败 → %s' % (len(FAILS), FAILS))
        return 1
    print('结果: 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
