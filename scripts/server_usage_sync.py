# -*- coding: utf-8 -*-
"""
WorkBuddy 服务端账单同步（Phase B 数据管道 · 真值来源）
=========================================================
接口：https://<domain>/billing/meter/*
  - get-user-request-usage   : 逐笔账单（requestId / credit / model / client / requestTime / input 前段）
  - get-user-resource-summary: 账户周期容量汇总（总量/已用/剩余）
凭据：仅使用本挂件显式授权的浏览器 cookie 或手动录入的长效令牌。
macOS Keychain / Windows DPAPI 保护存储；仅迁移本挂件旧版凭据文件，
不扫描或读取 WorkBuddy、签到技能或日常浏览器的登录态。
缓存：~/.workbuddy/server_usage_cache.json（records 按 requestId 去重合并，增量累积）
      —— 可用环境变量 WB_SERVER_USAGE_CACHE 覆盖路径（如放同步目录供双机共享）

长效令牌管理（CLI，均不打印令牌明文）：
  python server_usage_sync.py --rt-info            # 查状态（脱敏）
  python server_usage_sync.py --setup-rt           # 隐藏输入，不进入进程参数
  python server_usage_sync.py --setup-rt-file <文件>
  python server_usage_sync.py --forget-rt          # 清除本看板自己的令牌

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
import hashlib
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from credential_store import load_record, save_record, atomic_write, StorageError

HOME_WB = os.path.expanduser('~') + '/.workbuddy'
CACHE_FILE = os.environ.get('WB_SERVER_USAGE_CACHE') or os.path.join(HOME_WB, 'server_usage_cache.json')
BASE = 'https://www.codebuddy.cn'
OFFICIAL_DOMAINS = ('www.codebuddy.cn', 'www.workbuddy.cn')
WINDOW_DAYS = 30                    # 全量窗口：每次拉近 30 天（接口为 31 天滚动窗口，留 1 天安全余量）
INCR_DAYS = 2                       # 增量窗口：日常同步只拉近 2 天（结算值晚到/更新也覆盖）
FULL_SYNC_INTERVAL = 20             # 全量兜底周期（小时）：20h 一次全量，其余走增量（2026-10-14 优化）

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


_RT_NOTE = ''


def load_cookie():
    return load_record('cookie').get('cookie') or ''


def load_ua():
    """Replay the user agent associated with the authorized browser session."""
    return load_record('cookie').get('ua') or UA


# Explicitly supplied refresh tokens use the official refresh endpoint only.
RT_REFRESH_API = 'https://copilot.tencent.com/v2/plugin/auth/token/refresh'
RT_HEADERS = {
    'Content-Type': 'application/json',
    'X-Auth-Refresh-Source': 'plugin',
    'X-Domain': 'copilot.tencent.com',
}
# Access-token cache is protected by the same OS store as the refresh token.
AT_MAX_AGE_DAYS = 7


def read_rt():
    """Only the widget's own protected refresh-token record is eligible."""
    obj = load_record('rt')
    return (obj.get('rt') or obj.get('refresh_token') or '').strip()


def _rt_write(tok, source='refresh', interactive=False):
    save_record('rt', {'rt': tok, 'source': source, 'saved_at': int(time.time())}, interactive=interactive)


def rt_refresh(tok):
    """用长效令牌换新接口令牌。返回 dict（ok/token/rt/expires_in/domain/msg）。"""
    req = urllib.request.Request(RT_REFRESH_API, data=b'{}', method='POST')
    for k, v in RT_HEADERS.items():
        req.add_header(k, v)
    req.add_header('X-Refresh-Token', tok)
    try:
        with _opener().open(req, timeout=30) as r:
            res = json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return {'ok': False, 'msg': 'HTTP %s' % e.code}
    except Exception as e:
        return {'ok': False, 'msg': type(e).__name__}
    if res.get('code') != 0 or not isinstance(res.get('data'), dict):
        return {'ok': False, 'msg': '令牌刷新被拒绝'}
    d = res['data']
    at = d.get('accessToken') or ''
    if not at:
        return {'ok': False, 'msg': '应答缺少 accessToken'}
    return {'ok': True, 'token': at, 'rt': d.get('refreshToken') or '',
            'expires_in': d.get('expiresIn'),
            'domain': d.get('domain') or 'www.codebuddy.cn'}


def _at_cache_read(rt):
    obj = load_record('at')
    at = obj.get('at')
    if obj.get('rt_hash') != hashlib.sha256(rt.encode('utf-8')).hexdigest():
        return None
    try:
        ts = float(obj.get('ts') or 0)
    except (ValueError, TypeError):
        return None
    if isinstance(at, str) and at and 0 <= time.time() - ts < AT_MAX_AGE_DAYS * 86400:
        return {'token': at, 'age_days': (time.time() - ts) / 86400.0}
    return None


def _at_cache_write(at, rt):
    save_record('at', {'at': at, 'ts': int(time.time()),
                      'rt_hash': hashlib.sha256(rt.encode('utf-8')).hexdigest()})


def load_bearer_via_rt(force=False):
    """长效令牌通道：优先复用 7 天内缓存的 accessToken；否则刷新换新并回写轮换值。

    force=True 时跳过缓存强制刷新（401/403 兜底用）。
    返回 {'mode':'bearer','token':..,'domain':..,'expired':False,'via':'rt',...}
    或 None（未配置 / 刷新失败；原因写入 _RT_NOTE 供界面与日志显示）。
    """
    global _RT_NOTE
    tok = read_rt()
    if not tok:
        _RT_NOTE = '未配置长效令牌'
        return None
    if not force:
        cached = _at_cache_read(tok)
        if cached:
            _RT_NOTE = ''
            return {'mode': 'bearer', 'token': cached['token'], 'expired': False,
                    'domain': 'www.codebuddy.cn', 'via': 'rt',
                    'at_cached': True, 'at_age_days': round(cached['age_days'], 1)}
    r = rt_refresh(tok)
    if not r.get('ok'):
        _RT_NOTE = '长效令牌刷新失败：%s' % r.get('msg')
        return None
    new_rt = r.get('rt') or ''
    if new_rt and new_rt != tok:
        _rt_write(new_rt, source='refresh')
    _at_cache_write(r['token'], new_rt or tok)
    _RT_NOTE = ''
    return {'mode': 'bearer', 'token': r['token'], 'expired': False,
            'domain': 'www.codebuddy.cn',      # 账单接口的规范域名（api() 会自动加备用域名）
            'via': 'rt', 'expires_in': r.get('expires_in')}


def rt_info():
    """Status only: never disclose even a token prefix or suffix."""
    obj = load_record('rt')
    if not (obj.get('rt') or obj.get('refresh_token')):
        return {'configured': False}
    out = {'configured': True, 'storage': 'OS protected'}
    saved = obj.get('saved_at')
    if isinstance(saved, (int, float)):
        out['saved_days_ago'] = round((time.time() - saved) / 86400.0, 1)
    return out


def setup_rt(value):
    """Store an explicitly supplied refresh token in OS protected storage."""
    value = (value or '').strip()
    if len(value) < 200 or value.count('.') != 2:
        return False, ('内容不像有效的长效令牌'
                       '（应为较长的 base64 JWT 三段式，含两个点号）')
    _rt_write(value, source='manual', interactive=True)
    save_record('at', {})
    return True, '长效令牌已保存到操作系统保护存储'


def forget_rt():
    save_record('rt', {}, interactive=True)
    save_record('at', {})
    return True, '已清除本挂件的长效令牌和访问令牌缓存'


def resolve_auth():
    r = load_bearer_via_rt()
    if r:
        return r
    record = load_record('cookie')
    if record.get('cookie'):
        return {'mode': 'cookie', 'cookie': record['cookie'], 'ua': record.get('ua') or UA}
    return None


def auth_status():
    """Local protected-store status; no external application credential reads."""
    try:
        if rt_info().get('configured'):
            return '已配置长效令牌（操作系统保护存储）'
        if load_cookie():
            return '已保存网页登录凭据（操作系统保护存储）'
    except StorageError:
        return '凭据保护存储不可用；请解锁/允许 macOS 钥匙串或使用原 Windows 账户后重试'
    return '请点击「登录 / 重新授权」；本挂件不读取其他应用的登录凭据'


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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward authorization, refresh tokens or cookies via redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    p = _proxy_url()
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http': p, 'https': p} if p else {}), NoRedirect())


def api(path, body, auth):
    if path not in ('/billing/meter/get-user-request-usage',
                    '/billing/meter/get-user-resource-summary'):
        raise ValueError('Unsupported billing API path')
    if auth['mode'] == 'bearer':
        domain = auth.get('domain', 'www.codebuddy.cn')
        if domain not in OFFICIAL_DOMAINS:
            raise ValueError('Untrusted billing API domain')
        doms = [domain] + [d for d in OFFICIAL_DOMAINS if d != domain]
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
    headers = {**COOKIE_HEADERS, 'cookie': auth['cookie'], 'user-agent': auth.get('ua') or UA}
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

    try:
        auth = resolve_auth()
    except StorageError:
        return False, '凭据保护存储不可用；请解锁/允许钥匙串或使用原 Windows 账户后重试'
    if not auth:
        return False, auth_status()
    if auth['mode'] == 'bearer':
        if auth.get('via') == 'rt':
            if auth.get('at_cached'):
                say('[sync] 凭据：长效令牌（accessToken 本地缓存 %.1f 天龄，7 天内免刷新）'
                    % auth.get('at_age_days', 0))
            else:
                days = round((auth.get('expires_in') or 0) / 86400.0, 1)
                say('[sync] 凭据：长效令牌自续期（有效期约 %s 天）' % days)
    else:
        say('[sync] 凭据：网页登录（操作系统保护存储）')
    _px = _proxy_url()
    if _px:
        say('[sync] 使用系统代理（地址不记录）')

    now = datetime.now()
    end = now.strftime('%Y-%m-%d %H:%M:%S')

    # 旧缓存
    cache = {'records': {}, 'summary': None, 'updated_at': None}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                old = json.load(f)
            cache['records'] = old.get('records') or {}
            cache['summary'] = old.get('summary')
            cache['last_full_sync'] = old.get('last_full_sync')
        except Exception as e:
            say('[sync] 旧缓存读取失败（将重建）: %s' % type(e).__name__)

    # 拉取窗口（2026-10-14 优化）：
    #   全量 30 天：距上次全量 ≥20h 时做一次（兜住结算值晚到/更新、31 天滚动窗口不漏段）
    #   增量近 2 天：其余每次（手动点同步/30 分钟自动同步都走这条，网络耗时降 ~70%）
    last_full = cache.get('last_full_sync') or ''
    full_due = True
    if last_full:
        try:
            full_due = (now - datetime.strptime(last_full, '%Y-%m-%d %H:%M:%S')) >= timedelta(hours=FULL_SYNC_INTERVAL)
        except ValueError:
            pass
    if full_due:
        start = (now - timedelta(days=WINDOW_DAYS)).strftime('%Y-%m-%d 00:00:00')
    else:
        start = (now - timedelta(days=INCR_DAYS)).strftime('%Y-%m-%d 00:00:00')

    try:
        rows, total = fetch_usage(auth, start, end)
    except urllib.error.HTTPError as e:
        # Bearer 被服务端拒绝（401/403）时，自动回退到浏览器 cookie 再试一次
        rows = total = None
        if auth.get('mode') == 'bearer' and e.code in (401, 403):
            # 长效令牌通道：先强制重刷一次（应对轮换竞态 / 令牌刚过期），成功即恢复
            if auth.get('via') == 'rt':
                fresh = load_bearer_via_rt(force=True)
                if fresh:
                    say('[sync] 长效令牌被拒（HTTP %s）→ 重刷后重试' % e.code)
                    try:
                        rows, total = fetch_usage(fresh, start, end)
                        auth = fresh
                    except Exception as e2:
                        say('[sync] 重刷后仍失败：%s' % type(e2).__name__)
                        rows = total = None
            if rows is None:
                ck = load_cookie()
                if ck:
                    say('[sync] 令牌被拒（HTTP %s）→ 改用浏览器 cookie 兜底重试' % e.code)
                    auth = {'mode': 'cookie', 'cookie': ck, 'ua': load_ua()}
                    try:
                        rows, total = fetch_usage(auth, start, end)
                    except urllib.error.HTTPError as e2:
                        say('[sync] cookie 兜底也失败：HTTP %s（cookie 可能已过期，需重新抓取）' % e2.code)
                        return False, 'HTTP %s：登录态与 cookie 均不可用' % e2.code
                    except Exception as e2:
                        say('[sync] cookie 兜底异常：%s' % type(e2).__name__)
                        return False, 'cookie 兜底失败：%s' % type(e2).__name__
                else:
                    say('[sync] HTTP %s：令牌被服务端拒绝，且无 cookie 兜底' % e.code)
                    return False, 'HTTP %s：登录态不可用且无 cookie 兜底' % e.code
        elif auth.get('mode') == 'cookie':
            say('[sync] HTTP %s：网页登录凭据不可用，请点击「登录 / 重新授权」' % e.code)
            return False, 'HTTP %s：网页登录凭据不可用，请重新授权' % e.code
        else:
            say('[sync] HTTP %s：请求被服务端拒绝' % e.code)
            return False, 'HTTP %s' % e.code
    except Exception as e:
        say('[sync] 请求失败：%s' % type(e).__name__)
        return False, '请求失败：%s' % type(e).__name__

    added = 0
    if not rows:
        if full_due:
            say('[sync] 警告：全量窗口返回 0 条（窗口边界或服务端异常），缓存保持不变')
        else:
            say('[sync] 增量窗口返回 0 条（近 %d 天无新账单，正常）' % INCR_DAYS)
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
        say('[sync] summary 拉取失败（保留旧值）: %s' % type(e).__name__)

    cache['updated_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
    if full_due:
        cache['last_full_sync'] = end
    atomic_write(CACHE_FILE, json.dumps(cache, ensure_ascii=False).encode('utf-8'))

    # 统计
    recs = list(cache['records'].values())
    tot = sum(float(x.get('credit') or 0) for x in recs)
    say(f'[sync] ok  区间={start}~{end}（{"全量" if full_due else "增量"}）  本次返回={len(rows)}（total={total}）新增={added}')
    say(f'[sync] 缓存累计: {len(recs)} 条 / {round(tot, 2)} 积分')
    if cache.get('summary'):
        pk = cache['summary'].get('Packages') or []
        for p in pk:
            say(f"  pkg {p.get('PackageCode','')[:22]}: 已用 {p.get('CycleUsedCapacity')} / 总量 {p.get('CycleTotalCapacity')}（剩余 {p.get('CycleRemainCapacity')}）")
    say(f'[sync] cache -> {CACHE_FILE}')
    return True, f'新增 {added} 条 · 累计 {len(recs)} 条 / {round(tot, 2)} 积分'


def _rt_cli(argv):
    """Explicit imports only; never accept a secret in process arguments."""
    if not any(a in ('--setup-rt', '--setup-rt-file', '--rt-info', '--rt-status', '--forget-rt')
               for a in argv):
        return None
    try:
        if '--rt-info' in argv or '--rt-status' in argv:
            print(json.dumps(rt_info(), ensure_ascii=False, indent=2))
            return 0
        if '--forget-rt' in argv:
            ok, msg = forget_rt()
        elif '--setup-rt-file' in argv:
            path = argv[argv.index('--setup-rt-file') + 1]
            with open(path, encoding='utf-8') as stream:
                value = stream.read().strip()
            ok, msg = setup_rt(value)
        else:
            if argv != ['--setup-rt']:
                print('令牌不得放入命令参数；使用 --setup-rt 的隐藏输入或 --setup-rt-file。')
                return 1
            import getpass
            ok, msg = setup_rt(getpass.getpass('Refresh token: '))
        print(msg)
        return 0 if ok else 1
    except Exception as exc:
        print('令牌操作失败（%s）；请检查输入及操作系统保护存储权限。' % type(exc).__name__)
        return 1


def main():
    argv = sys.argv[1:]
    rc = _rt_cli(argv)
    if rc is not None:
        sys.exit(rc)
    ok, msg = run_sync(quiet=False)
    if not ok:
        print(f'[sync] 失败：{msg}')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
