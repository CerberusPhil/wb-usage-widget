# -*- coding: utf-8 -*-
"""
WorkBuddy 服务端账单同步（Phase B 数据管道 · 真值来源）
=========================================================
接口：https://<domain>/billing/meter/*
  - get-user-request-usage   : 逐笔账单（requestId / credit / model / client / requestTime / input 前段）
  - get-user-resource-summary: 账户周期容量汇总（总量/已用/剩余）
凭据（双模式，自动选择）：
  1) 本机桌面登录态（推荐 · 零配置）—— WorkBuddy 客户端登录后即存在，Bearer 直调；
     路径见 _auth_candidates()（跨平台）；token 只读、不落盘、不外传。
     2026-09-21 实测：四个接口全通（Bearer-only 即可，无需 platform 头；两域名均可）
  2) 浏览器 cookie（兜底）—— ~/.workbuddy/server_usage.json（F12 抓包获得；过期后重抓覆盖）
缓存：~/.workbuddy/server_usage_cache.json（records 按 requestId 去重合并，增量累积）
      —— 可用环境变量 WB_SERVER_USAGE_CACHE 覆盖路径（如放同步目录供双机共享）

接口边界（2026-09-21 实测钉死）：
  - **31 天滚动窗口**：可查范围 = 最近 31 天（2026-09-21 时最早可查恰为 8/21，早于此整体返回空）
    → 每次同步拉近 30 天（留 1 天安全余量），与本地缓存合并 —— **只要同步间隔 < 31 天，数据永久不丢**
  - pageSize 300 可用；首次全量实测 769 条 / 4800.49 积分（8/21-9/21）
  - 含所有客户端：WorkBuddy(本地) / web_agents(网页) / app_cloud(云端任务)
  - 含免费记录（credit=0，实测约占 38%）

用法：
  python server_usage_sync.py            # CLI：拉取最近 30 天并与缓存合并
模块复用：
  from server_usage_sync import run_sync # 进程内调用（billing_server 自动同步线程 / 打包 exe 用，不 sys.exit）
退出码：0 成功 / 1 无凭据或请求失败（旧缓存保留不动）
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

HOME_WB = os.path.expanduser('~') + '/.workbuddy'
CRED_FILE = os.path.join(HOME_WB, 'server_usage.json')
CACHE_FILE = os.environ.get('WB_SERVER_USAGE_CACHE') or os.path.join(HOME_WB, 'server_usage_cache.json')
BASE = 'https://www.workbuddy.cn'
WINDOW_DAYS = 30                    # 每次拉近 30 天（接口为 31 天滚动窗口，留 1 天安全余量）

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36')

COOKIE_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'content-type': 'application/json',
    'origin': BASE,
    'referer': BASE + '/profile/plans-usage',
    'user-agent': UA,
    'x-client-platform': 'web',
}

BEARER_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'user-agent': UA,
}


def _auth_candidates():
    """WorkBuddy 桌面登录态文件候选路径（跨平台，依次探测）。"""
    home = os.path.expanduser('~')
    cands = []
    if sys.platform.startswith('win'):
        for env in ('LOCALAPPDATA', 'APPDATA'):
            base = os.environ.get(env, '')
            if base:
                for app in ('CodeBuddyExtension', 'WorkBuddy', 'WorkBuddyDesktop'):
                    cands.append(os.path.join(base, app, 'Data', 'Public',
                                              'auth', 'workbuddy-desktop.info'))
    elif sys.platform == 'darwin':
        cands.append(os.path.join(home, 'Library', 'Application Support', 'CodeBuddyExtension',
                                  'Data', 'Public', 'auth', 'workbuddy-desktop.info'))
    else:
        cands.append(os.path.join(home, '.config', 'CodeBuddyExtension', 'Data', 'Public',
                                  'auth', 'workbuddy-desktop.info'))
    cands.append(os.path.join(home, '.workbuddy', 'auth', 'workbuddy-desktop.info'))
    # 固定候选全部不存在时，才做一次深度搜索兜底（避免每次白扫盘）
    if not any(os.path.isfile(p) for p in cands):
        try:
            cands.extend(_discover_auth_files())
        except Exception:
            pass
    seen, out = set(), []
    for p in cands:
        if not p:
            continue
        k = os.path.normcase(os.path.abspath(p))
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


_AUTH_SCAN = {'at': 0.0, 'files': None}
_BEARER_NOTE = ''          # 桌面登录态不可用时的原因（供界面/日志显示）


def _discover_auth_files(max_depth=5, limit=10, ttl=600):
    """在常见数据目录下有限深度搜索登录态文件——客户端升级换了存放路径时自动兜底。"""
    now = time.time()
    if _AUTH_SCAN['files'] is not None and (now - _AUTH_SCAN['at']) < ttl:
        return list(_AUTH_SCAN['files'])
    home = os.path.expanduser('~')
    roots = []
    if sys.platform.startswith('win'):
        for env in ('LOCALAPPDATA', 'APPDATA'):
            b = os.environ.get(env, '')
            if b:
                roots.append(b)
    elif sys.platform == 'darwin':
        roots.append(os.path.join(home, 'Library', 'Application Support'))
    else:
        roots.append(os.path.join(home, '.config'))
    roots.append(os.path.join(home, '.workbuddy'))
    skip = {'node_modules', 'Cache', 'cache', 'Code Cache', 'GPUCache', 'Temp', 'temp',
            'logs', 'Logs', 'blobs', 'binaries', 'projects', 'sessions', 'workspace',
            'shell-snapshots', 'connectors', 'connectors-marketplace', 'plugins'}
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        base = root.rstrip('\\/').count(os.sep)
        for dp, dirs, files in os.walk(root):
            if dp.count(os.sep) - base >= max_depth:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in skip]
            parent = os.path.basename(dp).lower()
            for f in files:
                fl = f.lower()
                if not fl.endswith(('.info', '.json', '.dat')):
                    continue
                if not ('workbuddy' in fl or 'auth' in fl or 'token' in fl or 'session' in fl
                        or 'auth' in parent or 'login' in parent or 'token' in parent):
                    continue
                p = os.path.join(dp, f)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    mt = 0
                if 'workbuddy-desktop' in fl:
                    score = 3
                elif 'workbuddy' in fl or 'desktop' in fl:
                    score = 2
                else:
                    score = 1
                found.append((score, mt, p))
                if len(found) >= 60:
                    break
    found.sort(key=lambda x: (-x[0], -x[1]))
    out = [p for _, _, p in found[:limit]]
    _AUTH_SCAN['at'] = now
    _AUTH_SCAN['files'] = out
    return list(out)


def _looks_like_wb_auth(d, path):
    """确认这份登录态确实属于 WorkBuddy——避免误读其它应用的凭据文件。"""
    pl = path.lower()
    if 'workbuddy' in pl or 'codebuddy' in pl:
        return True
    try:
        blob = json.dumps(d, ensure_ascii=False).lower()
    except Exception:
        blob = ''
    return ('workbuddy' in blob) or ('codebuddy' in blob)


def load_bearer():
    """读本机桌面登录态 → {'token','domain','expired','file'}；找不到/不可用返回 None。

    ⚠️ 新版 WorkBuddy 客户端把 auth.accessToken 改为**加密信封**
    （`{"$wbEncrypted":1,"envelope":"..."}`），本地拿不到明文 JWT。
    遇到这种（非字符串）值即视为不可用 → 返回 None，
    由 resolve_auth() 自动改用浏览器 cookie 兜底（此时会在 _BEARER_NOTE 记录原因）。
    """
    global _BEARER_NOTE
    enc_file = None
    for p in _auth_candidates():
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if not _looks_like_wb_auth(d, p):
            continue
        a = d.get('auth') or {}
        token = (a.get('accessToken') or a.get('access_token') or a.get('token')
                 or d.get('accessToken') or d.get('access_token') or d.get('token'))
        if not token:
            continue
        if not isinstance(token, str):
            # 加密信封（dict）：不可用。记下现场，继续尝试其它候选（可能是旧版明文文件）
            enc_file = p
            continue
        domain = a.get('domain') or d.get('domain') or 'www.codebuddy.cn'
        expired = False
        exp = a.get('expiresAt') or a.get('expires_at')
        if exp:
            try:
                e = int(exp)
                if e > 10 ** 11:      # 毫秒 → 秒
                    e = e / 1000.0
                expired = e <= time.time()
            except (TypeError, ValueError):
                pass
        _BEARER_NOTE = ''
        return {'token': token, 'domain': domain, 'expired': expired, 'file': p}
    if enc_file:
        _BEARER_NOTE = ('桌面登录态的 accessToken 已加密（新版客户端 at-rest 加密：'
                        '{"$wbEncrypted":1,...}），本地无法取得明文令牌')
    return None


def load_cookie():
    try:
        with open(CRED_FILE, encoding='utf-8') as f:
            return json.load(f).get('cookie') or ''
    except Exception:
        return ''


def resolve_auth():
    """凭据选择：桌面登录态（Bearer）优先 → 浏览器 cookie 兜底。"""
    b = load_bearer()
    if b:
        return {'mode': 'bearer', **b}
    c = load_cookie()
    if c:
        return {'mode': 'cookie', 'cookie': c}
    return None


def auth_status():
    """不联网自检：返回一句话说明当前凭据情况（供界面显示与排障）。"""
    try:
        b = load_bearer()
    except Exception as e:
        return '凭据检查异常：%s' % e
    if b:
        extra = '（登录态已过期，请重开 WB 客户端）' if b.get('expired') else ''
        return '已找到登录态：%s%s' % (b['file'], extra)
    try:
        if load_cookie():
            if _BEARER_NOTE:
                return '桌面登录态不可用：%s；已改用浏览器 cookie 兜底（%s）' % (_BEARER_NOTE, CRED_FILE)
            return '未找到客户端登录态，改用浏览器 cookie 兜底（%s）' % CRED_FILE
    except Exception:
        pass
    try:
        n = len(_auth_candidates())
    except Exception:
        n = 0
    try:
        scanned = len(_AUTH_SCAN['files'] or [])
    except Exception:
        scanned = 0
    return '未找到登录态文件（固定路径 + 数据目录深度搜索，共 %d 个候选，扫描命中 %d）——请确认 WB 客户端已登录' % (n, scanned)


def _proxy_url():
    """系统代理：环境变量优先 → Windows IE/WinINET 设置；无则空串。"""
    for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        v = os.environ.get(k)
        if v:
            return v
    if sys.platform.startswith('win'):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as k:
                enabled, _ = winreg.QueryValueEx(k, 'ProxyEnable')
                if not enabled:
                    return ''
                server, _ = winreg.QueryValueEx(k, 'ProxyServer')
                if not server:
                    return ''
                if '=' in server:
                    parts = dict(x.split('=', 1) for x in server.split(';') if '=' in x)
                    server = parts.get('https') or parts.get('http') or ''
                if not server:
                    return ''
                return server if server.lower().startswith('http') else 'http://' + server
        except Exception:
            return ''
    return ''


def _opener():
    p = _proxy_url()
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http': p, 'https': p} if p else {}))


def api(path, body, auth):
    if auth['mode'] == 'bearer':
        doms = [auth['domain']]
        alt = 'www.workbuddy.cn' if 'codebuddy' in auth['domain'] else 'www.codebuddy.cn'
        if alt not in doms:
            doms.append(alt)
        last = None
        for dom in doms:
            url = 'https://' + dom + path
            headers = {**BEARER_HEADERS, 'Authorization': 'Bearer %s' % auth['token']}
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers=headers, method='POST')
            try:
                with _opener().open(req, timeout=40) as r:
                    return json.loads(r.read().decode('utf-8'))
            except Exception as e:
                last = e
        raise last
    url = BASE + path
    headers = {**COOKIE_HEADERS, 'cookie': auth['cookie']}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method='POST')
    with _opener().open(req, timeout=40) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_usage(auth, start, end):
    """分页拉全量账单"""
    rows, page, total = [], 1, None
    while True:
        d = api('/billing/meter/get-user-request-usage',
                {'startTime': start, 'endTime': end, 'pageNum': page, 'pageSize': 300}, auth)
        data = d.get('data') or {}
        batch = data.get('data') or []
        rows.extend(batch)
        total = data.get('total')
        if not batch or len(rows) >= (total or 0):
            break
        page += 1
        if page > 20:   # 安全上限
            break
    return rows, total


def run_sync(quiet=False):
    """执行一次同步（进程内调用安全：不 sys.exit）。返回 (ok: bool, msg: str)。"""
    msgs = []

    def say(s):
        msgs.append(s)
        if not quiet:
            print(s)

    auth = resolve_auth()
    if not auth:
        st = auth_status()
        lines = ['[sync] 未找到凭据：请登录 WorkBuddy 桌面客户端（自动读取本机登录态，推荐）；',
                 '[sync] 自检：%s' % st]
        try:
            lines += ['[sync] 已检查：%s' % c for c in _auth_candidates()[:12]]
        except Exception:
            pass
        lines.append('[sync] 或完成 F12 抓包并将 cookie 写入 %s（兜底）' % CRED_FILE)
        for ln in lines:
            msgs.append(ln)
            try:
                print(ln)      # 即使 quiet（exe 场景）也打印：stdout 落 widget.log，便于远程排障
            except Exception:
                pass
        return False, '未找到凭据（%s）' % st
    if auth['mode'] == 'bearer':
        warn = '（提示：登录态可能已过期，打开 WB 客户端重登可自愈）' if auth.get('expired') else ''
        say('[sync] 凭据：桌面登录态 %s %s' % (auth['file'], warn))
    else:
        if _BEARER_NOTE:
            say('[sync] 凭据：%s；改用 cookie 兜底' % _BEARER_NOTE)
        say('[sync] 凭据：浏览器 cookie（%s，兜底模式）' % CRED_FILE)
    _px = _proxy_url()
    if _px:
        say('[sync] 使用系统代理：%s' % _px)

    now = datetime.now()
    # 31 天滚动窗口 → 每次拉近 30 天，合并进缓存（幂等：按 requestId 去重覆盖）
    start = (now - timedelta(days=WINDOW_DAYS)).strftime('%Y-%m-%d 00:00:00')
    end = now.strftime('%Y-%m-%d %H:%M:%S')

    # 旧缓存
    cache = {'records': {}, 'summary': None, 'updated_at': None}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                old = json.load(f)
            cache['records'] = old.get('records') or {}
            cache['summary'] = old.get('summary')
        except Exception as e:
            say(f'[sync] 旧缓存读取失败（将重建）: {e}')

    try:
        rows, total = fetch_usage(auth, start, end)
    except urllib.error.HTTPError as e:
        # Bearer 被服务端拒绝（401/403）时，自动回退到浏览器 cookie 再试一次
        if auth.get('mode') == 'bearer' and e.code in (401, 403):
            ck = load_cookie()
            if ck:
                say('[sync] 桌面登录态被拒（HTTP %s）→ 改用浏览器 cookie 兜底重试' % e.code)
                auth = {'mode': 'cookie', 'cookie': ck}
                try:
                    rows, total = fetch_usage(auth, start, end)
                except urllib.error.HTTPError as e2:
                    say('[sync] cookie 兜底也失败：HTTP %s（cookie 可能已过期，需重新抓取）' % e2.code)
                    return False, 'HTTP %s：登录态与 cookie 均不可用' % e2.code
                except Exception as e2:
                    say('[sync] cookie 兜底异常：%s' % e2)
                    return False, 'cookie 兜底失败：%s' % e2
            else:
                say('[sync] HTTP %s：桌面登录态被服务端拒绝，且无 cookie 兜底' % e.code)
                return False, 'HTTP %s：登录态不可用且无 cookie 兜底' % e.code
        elif auth.get('mode') == 'cookie':
            say('[sync] HTTP %s：浏览器 cookie 可能已过期，需重新抓取并更新 %s' % (e.code, CRED_FILE))
            return False, 'HTTP %s：cookie 已过期（需重新抓取）' % e.code
        else:
            say('[sync] HTTP %s：请求被服务端拒绝' % e.code)
            return False, 'HTTP %s' % e.code
    except Exception as e:
        say(f'[sync] 请求失败：{e}')
        return False, f'请求失败：{e}'

    added = 0
    if not rows:
        say('[sync] 警告：本次返回 0 条（窗口边界或服务端异常），缓存保持不变')
    for r in rows:
        rid = r.get('requestId')
        if not rid:
            continue
        if rid not in cache['records']:
            added += 1
        cache['records'][rid] = r     # 新值覆盖旧值（结算值可能更新）

    # 汇总快照（best-effort）
    try:
        s = api('/billing/meter/get-user-resource-summary', {}, auth)
        cache['summary'] = s.get('data')
    except Exception as e:
        say(f'[sync] summary 拉取失败（保留旧值）: {e}')

    cache['updated_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
    tmp = CACHE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)

    # 统计
    recs = list(cache['records'].values())
    tot = sum(float(x.get('credit') or 0) for x in recs)
    say(f'[sync] ok  区间={start}~{end}  本次返回={len(rows)}（total={total}）新增={added}')
    say(f'[sync] 缓存累计: {len(recs)} 条 / {round(tot, 2)} 积分')
    if cache.get('summary'):
        pk = cache['summary'].get('Packages') or []
        for p in pk:
            say(f"  pkg {p.get('PackageCode','')[:22]}: 已用 {p.get('CycleUsedCapacity')} / 总量 {p.get('CycleTotalCapacity')}（剩余 {p.get('CycleRemainCapacity')}）")
    say(f'[sync] cache -> {CACHE_FILE}')
    return True, f'新增 {added} 条 · 累计 {len(recs)} 条 / {round(tot, 2)} 积分'


def main():
    ok, msg = run_sync(quiet=False)
    if not ok:
        print(f'[sync] 失败：{msg}')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
