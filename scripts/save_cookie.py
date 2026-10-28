# -*- coding: utf-8 -*-
"""更新浏览器 cookie（积分同步的兜底凭据）。

背景：新版 WorkBuddy 客户端把桌面登录态 accessToken 改成加密存储，本地拿不到明文令牌；
      因此积分/账单同步改走「浏览器 cookie」通道。cookie 会过期，过期后用本脚本换新。

用法（推荐：先复制、再运行）
  1) 浏览器登录 https://www.codebuddy.cn/profile/plans-usage
  2) F12 → Network → 选择 www.codebuddy.cn 的账单接口请求 → 复制该请求的 Cookie 值
     （仅输入此官方站点 Cookie；不要复制其他网站的 Cookie 或完整请求头）
  3) 运行：  python save_cookie.py            # 从剪贴板读取
     python save_cookie.py --file ck.txt      # 从文件读取
     python save_cookie.py --check            # 只校验当前已保存的 cookie 是否还有效

保存到 macOS 钥匙串 / Windows DPAPI；旧版本挂件凭据自动安全迁移。
"""
import argparse
import ctypes
import datetime
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from credential_store import load_record, save_record

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36')


def read_clipboard():
    """macOS 使用 pbpaste；Windows 使用 Win32 API（不依赖 PowerShell）。

    ⚠️ 64 位下必须配 argtypes/restype：GetClipboardData 返回 64 位 HANDLE、
    GlobalLock 返回 64 位指针，默认按 32 位 int 截断 → GlobalLock 拿到野指针 →
    c_wchar_p 解引用直接段错误（2026-09-28 实锤，exit 139）。
    """
    if sys.platform == 'darwin':
        return subprocess.run(['/usr/bin/pbpaste'], check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout
    if sys.platform != 'win32':
        raise OSError('剪贴板读取目前支持 macOS 和 Windows；其他系统请使用 --file。')
    CF_UNICODETEXT = 13
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.OpenClipboard.restype = ctypes.c_bool
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    u.CloseClipboard.restype = ctypes.c_bool
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.restype = ctypes.c_bool
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return ''
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ''
        p = k.GlobalLock(h)
        if not p:
            return ''
        try:
            return ctypes.c_wchar_p(p).value or ''
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def clean_cookie(raw):
    """从粘贴内容里抽出 cookie 串（兼容整行 Cookie: / curl -H / 纯 cookie）。"""
    s = (raw or '').strip()
    if not s:
        return ''
    m = re.search(r"(?:-H\s+)?['\"]?[Cc]ookie:\s*(.+?)['\"]?\s*$", s, re.S | re.M)
    if m:
        s = m.group(1)
    s = s.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    s = re.sub(r'\s*;\s*', '; ', s).strip().strip('"\'')
    return s


def validate(cookie):
    """调一次账单接口验证 cookie；返回 (ok, 说明)。"""
    from server_usage_sync import _opener
    now = datetime.datetime.now()
    body = json.dumps({
        'startTime': (now - datetime.timedelta(days=3)).strftime('%Y-%m-%d 00:00:00'),
        'endTime': now.strftime('%Y-%m-%d %H:%M:%S'),
        'pageNum': 1, 'pageSize': 1,
    }).encode()
    for dom in ('www.codebuddy.cn',):
        req = urllib.request.Request(
            'https://%s/billing/meter/get-user-request-usage' % dom, data=body, method='POST',
            headers={'accept': 'application/json, text/plain, */*',
                     'content-type': 'application/json', 'user-agent': UA,
                     'origin': 'https://' + dom,
                     'referer': 'https://%s/profile/plans-usage' % dom,
                     'x-client-platform': 'web', 'cookie': cookie})
        try:
            with _opener().open(req, timeout=30) as r:
                j = json.loads(r.read().decode())
                if not isinstance(j, dict) or j.get('code') != 0 or not isinstance(j.get('data'), dict):
                    return False, '账单接口未确认授权'
                return True, 'HTTP %s（官方账单接口）' % r.status
        except urllib.error.HTTPError as e:
            last = 'HTTP %s（%s）' % (e.code, dom)
        except Exception as e:
            last = type(e).__name__
    return False, last


def session_expiry(cookie):
    """从 session cookie 里读疑似过期时间戳。"""
    m = re.search(r'\|(\d{9,11})\|', cookie or '')
    if not m:
        return None
    return datetime.datetime.fromtimestamp(int(m.group(1)))

class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes unknown arguments, potentially cookies.
        self.exit(2, '参数无效；Cookie 请通过剪贴板或 --file 输入，不要放入命令参数。\n')



def main():
    ap = SafeParser()
    ap.add_argument('--file', help='从文件读取 cookie')
    ap.add_argument('--check', action='store_true', help='只校验当前保存的 cookie')
    a = ap.parse_args()

    if a.check:
        d = load_record('cookie')
        if not d.get('cookie'):
            print('尚未保存网页登录凭据')
            return 1
        ok, msg = validate(d.get('cookie', ''))
        exp = session_expiry(d.get('cookie', ''))
        print('保存时间:', d.get('saved_at'))
        if exp:
            left = (exp - datetime.datetime.now()).days
            print('疑似过期:', exp.strftime('%Y-%m-%d %H:%M'), '（剩 %d 天）' % left)
        print('接口校验:', ('可用 ✔ ' if ok else '不可用 ✘ ') + msg)
        return 0 if ok else 1

    if a.file:
        with open(a.file, encoding='utf-8') as stream:
            raw = stream.read()
    else:
        raw = read_clipboard()
    if not raw or not raw.strip():
        print('没读到内容。请先复制 Cookie 再运行，或用 --file 指定文件。')
        return 2
    cookie = clean_cookie(raw)
    if len(cookie) < 30:
        print('内容看起来不像完整 Cookie；未显示或保存输入内容。')
        return 2

    ok, msg = validate(cookie)
    print('接口校验:', ('可用 ✔ ' if ok else '不可用 ✘ ') + msg)
    if not ok:
        print('→ 校验未通过，未写入。请确认已在浏览器登录 codebuddy.cn 且复制的是完整 Cookie。')
        return 1

    payload = {'cookie': cookie, 'ua': UA,
               'saved_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               'note': 'WB web session cookie for /billing/meter APIs. Refresh via save_cookie.py when expired.'}
    save_record('cookie', payload, interactive=True)
    exp = session_expiry(cookie)
    print('已写入操作系统保护存储')
    if exp:
        print('预计可用到:', exp.strftime('%Y-%m-%d %H:%M'), '（约 %d 天）' % (exp - datetime.datetime.now()).days)
    print('验证同步:  cd scripts && python server_usage_sync.py')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('凭据操作失败（%s）；请解锁/允许钥匙串或检查 Windows 账户权限后重试。' % type(exc).__name__)
        sys.exit(1)
