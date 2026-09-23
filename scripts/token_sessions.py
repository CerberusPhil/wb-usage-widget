# -*- coding: utf-8 -*-
"""
WorkBuddy 本机 Token 会话聚合（消耗详单「按会话」视图数据源 · Phase C）
========================================================================
数据源：~/.workbuddy/projects/<项目>/<会话id>.jsonl 的 providerData.rawUsage
  每条带用量记录含：prompt/completion/total tokens、思考 tokens、缓存命中/未命中、
  credit（行级积分）、conversationRequestId（= 账单 requestId，供跨设备对账）

输出（供 billing_dashboard_data.build_data() 合并展示）：
  - sessions : 按 sessionId 聚合（跨天合并），含按模型拆分；按总 token 降序
      · title / title_src：标题链 = 手工覆盖(~/.workbuddy/token_sessions_titles.json)
        > workbuddy.db sessions 表 > 首条用户消息摘要（回填）
      · sub / agent：子代理任务会话说（providerData.isSubAgent 全量命中；
        子代理会话不在 sessions 表中、天然无标题——如 Explore/Plan 子任务）
  - stats    : 会话数 / 跨模型会话数 / 总 token / 缓存命中率 / 按模型 Token 聚合（by_model）
  - crids    : 全部 conversationRequestId 集合（账单对账：本机 vs 其他端）
  - token_days: 按天 Token 聚合（本机 jsonl 视角；供卡片 / 日历的 Token 维度）
  - session_days: 按天 × 会话聚合（含按模型明细，供「会话消耗 · 按天」视图）
  - Token 三类归属（按请求级判定，贯穿会话/模型/按天）：
      · tc = 积分 Token（rawUsage.credit > 0）
      · tf = 免费 Token（credit 存在且 = 0，如 Hy3 / Hy4-preview）
      · te = 外部 API Token（无 credit 字段，BYOK：智谱 GLM 资源包 / MiMo 等）
  - act_min  : 本机活动分钟集合（供「其他端」疑似度估算，不随 payload 下发）

口径：纯本机视角（服务端账单不含 token 字段，无法跨设备）；模型名全局归一
      （小写 + 去空格/连字符/下划线 + 去「（…）」注记；显示名取全局最多拼写）。
用法：python token_sessions.py     # 打印统计（自检）
"""
import collections
import glob
import json
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta

HOME_WB = os.path.expanduser('~') + '/.workbuddy'
PROJECTS = os.path.join(HOME_WB, 'projects')
DB = os.path.join(HOME_WB, 'workbuddy.db')
OVERRIDES = os.path.join(HOME_WB, 'token_sessions_titles.json')
CST = timezone(timedelta(hours=8))

_SNIP_LIMIT = 40
_STRIP_BLOCKS = (
    re.compile(r'<system-reminder[\s\S]*?</system-reminder>', re.I),
    re.compile(r'<user_info[\s\S]*?</user_info>', re.I),
    re.compile(r'</?[a-z_-]{2,24}[^>]{0,120}data-role="user-context"[^>]*>', re.I),
)


def _ts2s(t):
    """jsonl 时间戳（毫秒整数或 ISO 串）→ 'YYYY-mm-dd HH:MM'（北京时间）"""
    if isinstance(t, (int, float)):
        t = t / 1000.0 if t > 10 ** 11 else t
        try:
            return datetime.fromtimestamp(t, CST).strftime('%Y-%m-%d %H:%M')
        except (OSError, ValueError, OverflowError):
            return ''
    return str(t)[:16].replace('T', ' ')


def _clean_snippet(t, limit=_SNIP_LIMIT):
    """用户消息 → 单行摘要（剥 system-reminder/user_info 块、thoroughness 前缀、
    行首标记与「背景」前缀；压缩空白；限长）"""
    t = str(t or '')
    for rx in _STRIP_BLOCKS:
        t = rx.sub(' ', t)
    t = re.sub(r'(?i)^\s*thoroughness:\s*[^#]{0,60}', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'^[\s*#>\-•·]+', '', t)
    t = t.replace('**', '').replace('`', '')
    for pat in (r'^背景[：:]\s*', r'^背景\s+', r'^背景[（(][^）)]{0,40}[）)]\s*'):
        t = re.sub(pat, '', t)
    t = t.strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + '…'
    return t


def _text_of(content):
    """消息 content（str 或 parts 列表）→ 纯文本"""
    if isinstance(content, str):
        return content
    txt = ''
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get('type') in ('input_text', 'text'):
                txt += str(part.get('text') or '')
    return txt


def _titles():
    """会话标题（只读；失败返回空 dict）"""
    out = {}
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
        try:
            for sid, t, ct in con.execute('SELECT id, title, custom_title FROM sessions'):
                tt = (ct or t or '').strip()
                if tt:
                    out[sid] = tt
        finally:
            con.close()
    except Exception:
        pass
    return out


def _load_overrides():
    """手工标题覆盖（可选文件：{'sid': 'title'} 或 {'titles': {...}}）"""
    try:
        with open(OVERRIDES, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            d = d.get('titles') or d
            return {k: str(v).strip() for k, v in d.items()
                    if isinstance(v, str) and v.strip()}
    except Exception:
        pass
    return {}


def _custom_model_ids():
    """本机自定义模型库（~/.workbuddy/models.json，vendor=Custom）→ 小写 id 集合。
    这是判定「外部 API（BYOK）」的唯一可靠依据：凡走自建 Key 的模型都不进 WB 账单。"""
    out = set()
    try:
        with open(os.path.join(HOME_WB, 'models.json'), encoding='utf-8') as f:
            for m in json.load(f):
                mid = str(m.get('id') or '').strip().lower()
                if mid:
                    out.add(mid)
    except Exception:
        pass
    return out


_CUSTOM_IDS = None


def _bill_class(ru, mid=''):
    """请求级计费归属：'credit'（有积分）/ 'free'（免费或未计费）/ 'ext'（外部 API·BYOK）

    判定顺序：
      1) rawUsage 带 credit 且 > 0            → 积分
      2) rawUsage 带 credit 且 = 0            → 免费（平台免费模型，如 Hy3 / Hy4-preview）
      3) 无 credit 字段 + 模型属自定义(BYOK)   → 外部 API（智谱 GLM 资源包系列 / MiMo 等）
      4) 无 credit 字段 + 平台内置模型         → 免费/未计费
         （实测 GLM-5.3-Flash 342 条 / 29.3M token 属此类，经账单 requestId 交叉验证确认为未计费）
    """
    global _CUSTOM_IDS
    if 'credit' in ru:
        return 'credit' if float(ru.get('credit') or 0) > 0 else 'free'
    if _CUSTOM_IDS is None:
        _CUSTOM_IDS = _custom_model_ids()
    return 'ext' if (mid or '').strip().lower() in _CUSTOM_IDS else 'free'


_TOK_KEY = {'credit': 'tc', 'free': 'tf', 'ext': 'te'}


def build_token_data():
    """扫描 jsonl → {'sessions', 'stats', 'crids', 'act_min'}"""
    sessions = {}
    crids = set()
    act_min = set()
    tok_days = {}
    sess_days = {}
    files = glob.glob(os.path.join(PROJECTS, '**', '*.jsonl'), recursive=True)
    for f in files:
        is_agent_file = os.path.basename(f).startswith('agent-')
        try:
            fh = open(f, encoding='utf-8', errors='ignore')
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"rawUsage"' not in line and '"role"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                pd_ = o.get('providerData') or {}
                ru = pd_.get('rawUsage')
                sid = o.get('sessionId') or os.path.basename(f)[:-6]
                s = sessions.get(sid)
                if s is None:
                    s = sessions[sid] = {'sid': sid, 'n': 0, 'n_sub': 0, 'agent': '',
                                         'inp': 0, 'out': 0, 'think': 0, 'hit': 0, 'miss': 0,
                                         'credit': 0.0, 'tmin': '', 'tmax': '',
                                         'tc': 0, 'tf': 0, 'te': 0,
                                         'first_user': '', 'models': {}}
                # 首条用户消息（用于无标题会话的回填）
                if not s['first_user'] and o.get('type') == 'message' and o.get('role') == 'user':
                    snip = _clean_snippet(_text_of(o.get('content')), 200)
                    if len(snip) >= 4:
                        s['first_user'] = snip
                if not ru:
                    continue
                c = pd_.get('conversationRequestId')
                if c:
                    crids.add(c)
                if pd_.get('isSubAgent') or is_agent_file:
                    s['n_sub'] += 1
                    if not s['agent'] and pd_.get('agent'):
                        s['agent'] = str(pd_['agent'])[:20]
                name = (pd_.get('requestModelName') or pd_.get('model') or '').strip() or '未知'
                mkey = name.lower().replace(' ', '').replace('-', '').replace('_', '')
                if '（' in mkey and '）' in mkey:    # 去自定义模型名括号注记（仅归并用）
                    mkey = mkey.split('（')[0]
                ts = _ts2s(o.get('timestamp'))
                if ts:
                    act_min.add(ts)
                mm = s['models'].get(mkey)
                if mm is None:
                    mm = s['models'][mkey] = {'names': {}, 'n': 0, 'inp': 0, 'out': 0,
                                              'think': 0, 'hit': 0, 'miss': 0, 'credit': 0.0,
                                              'tc': 0, 'tf': 0, 'te': 0}
                mm['names'][name] = mm['names'].get(name, 0) + 1
                pt = int(ru.get('prompt_tokens') or 0)
                ct = int(ru.get('completion_tokens') or 0)
                th = int(ru.get('completion_thinking_tokens') or 0)
                h = int(ru.get('prompt_cache_hit_tokens') or 0)
                ms = int(ru.get('prompt_cache_miss_tokens') or 0)
                cr = float(ru.get('credit') or 0)
                tt = pt + ct
                tkey = _TOK_KEY[_bill_class(ru, pd_.get('model') or '')]   # tc / tf / te
                for obj in (s, mm):
                    obj['n'] += 1
                    obj['inp'] += pt
                    obj['out'] += ct
                    obj['think'] += th
                    obj['hit'] += h
                    obj['miss'] += ms
                    obj['credit'] += cr
                    obj[tkey] += tt
                if ts:
                    if not s['tmin'] or ts < s['tmin']:
                        s['tmin'] = ts
                    if not s['tmax'] or ts > s['tmax']:
                        s['tmax'] = ts
                    td = tok_days.get(ts[:10])
                    if td is None:
                        td = tok_days[ts[:10]] = {'n': 0, 'inp': 0, 'out': 0, 'think': 0,
                                                  'credit': 0.0, 'tc': 0, 'tf': 0, 'te': 0}
                    td['n'] += 1
                    td['inp'] += pt
                    td['out'] += ct
                    td['think'] += th
                    td['credit'] += cr
                    td[tkey] += tt
                    dmap = sess_days.get(ts[:10])
                    if dmap is None:
                        dmap = sess_days[ts[:10]] = {}
                    ss = dmap.get(sid)
                    if ss is None:
                        ss = dmap[sid] = {'n': 0, 'inp': 0, 'out': 0, 'think': 0,
                                          'hit': 0, 'miss': 0, 'credit': 0.0,
                                          'tc': 0, 'tf': 0, 'te': 0, 'models': {}}
                    ss['n'] += 1
                    ss['inp'] += pt
                    ss['out'] += ct
                    ss['think'] += th
                    ss['hit'] += h
                    ss['miss'] += ms
                    ss['credit'] += cr
                    ss[tkey] += tt
                    sm = ss['models'].get(mkey)
                    if sm is None:
                        sm = ss['models'][mkey] = {'n': 0, 'inp': 0, 'out': 0, 'think': 0,
                                                  'hit': 0, 'miss': 0, 'credit': 0.0,
                                                  'tc': 0, 'tf': 0, 'te': 0}
                    sm['n'] += 1
                    sm['inp'] += pt
                    sm['out'] += ct
                    sm['think'] += th
                    sm['hit'] += h
                    sm['miss'] += ms
                    sm['credit'] += cr
                    sm[tkey] += tt

    titles = _titles()
    overrides = _load_overrides()

    # 全局显示名归一：同一模型（按归一 key）取全局出现最多的拼写
    votes = {}
    for s_ in sessions.values():
        for key_, mm_ in s_['models'].items():
            c = votes.setdefault(key_, {})
            for nm, cnt in (mm_.get('names') or {}).items():
                c[nm] = c.get(nm, 0) + cnt
    canon = {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in votes.items()}

    out = []
    for sid, s in sessions.items():
        if not s['n']:
            continue
        models = []
        for key, mm in sorted(s['models'].items(), key=lambda kv: -kv[1]['inp']):
            hr = mm['hit'] / max(1, mm['hit'] + mm['miss']) * 100
            models.append({'name': canon.get(key) or '未知', 'n': mm['n'], 'inp': mm['inp'],
                           'out': mm['out'], 'think': mm['think'], 'hit': mm['hit'],
                           'hit_rate': round(hr, 1), 'credit': round(mm['credit'], 2),
                           'tokens': mm['inp'] + mm['out'],
                           'tc': mm['tc'], 'tf': mm['tf'], 'te': mm['te']})
        hr = s['hit'] / max(1, s['hit'] + s['miss']) * 100
        db_title = titles.get(sid, '')
        ov = overrides.get(sid, '')
        if ov:
            title, tsrc = ov, 'override'
        elif db_title:
            title, tsrc = db_title, 'db'
        elif s['first_user']:
            title = s['first_user'][:_SNIP_LIMIT] + ('…' if len(s['first_user']) > _SNIP_LIMIT else '')
            tsrc = 'derived'
        else:
            title, tsrc = '', 'none'
        out.append({'sid': sid, 'title': title, 'title_src': tsrc,
                    'sub': s['n_sub'] == s['n'], 'agent': s['agent'],
                    'n': s['n'], 'tmin': s['tmin'], 'tmax': s['tmax'],
                    'inp': s['inp'], 'out': s['out'], 'think': s['think'], 'hit': s['hit'],
                    'tokens': s['inp'] + s['out'], 'hit_rate': round(hr, 1),
                    'credit': round(s['credit'], 2),
                    'tc': s['tc'], 'tf': s['tf'], 'te': s['te'],
                    'models': models})
    out.sort(key=lambda x: -x['tokens'])

    tot = {'inp': 0, 'out': 0, 'think': 0, 'hit': 0, 'miss': 0}
    for s in sessions.values():
        for k in tot:
            tot[k] += s[k]
    bm = {}
    for x in out:
        for m in x['models']:
            b = bm.setdefault(m['name'], {'n': 0, 'inp': 0, 'out': 0, 'think': 0,
                                          'credit': 0.0, 'tc': 0, 'tf': 0, 'te': 0})
            b['n'] += m['n']
            b['inp'] += m['inp']
            b['out'] += m['out']
            b['think'] += m['think']
            b['credit'] += m.get('credit') or 0
            b['tc'] += m.get('tc') or 0
            b['tf'] += m.get('tf') or 0
            b['te'] += m.get('te') or 0
    by_model = [{'name': k, 'n': v['n'], 'inp': v['inp'], 'out': v['out'],
                 'think': v['think'], 'tokens': v['inp'] + v['out'],
                 'credit': round(v['credit'], 2), 'tc': v['tc'], 'tf': v['tf'], 'te': v['te']}
                for k, v in sorted(bm.items(), key=lambda kv: -(kv[1]['inp'] + kv[1]['out']))]
    tok_split = {'tc': sum(s['tc'] for s in sessions.values()),
                 'tf': sum(s['tf'] for s in sessions.values()),
                 'te': sum(s['te'] for s in sessions.values()),
                 'tc_credit': round(sum(s['credit'] for s in sessions.values()), 2)}
    stats = {
        'tok_split': tok_split,
        'sessions_n': len(out),
        'multi_n': sum(1 for x in out if len(x['models']) > 1),
        'sub_n': sum(1 for x in out if x['sub']),
        'total_tokens': tot['inp'] + tot['out'],
        'total_inp': tot['inp'],
        'total_out': tot['out'],
        'total_think': tot['think'],
        'hit_rate': round(tot['hit'] / max(1, tot['hit'] + tot['miss']) * 100, 1),
        'files': len(files),
        'by_model': by_model,
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    tok_days_out = {k: {'n': v['n'], 'inp': v['inp'], 'out': v['out'], 'think': v['think'],
                        'credit': round(v['credit'], 2), 'tokens': v['inp'] + v['out'],
                        'tc': v['tc'], 'tf': v['tf'], 'te': v['te']}
                    for k, v in tok_days.items()}
    def _day_models(v):
        """按天×会话的按模型明细（显示名走全局归一）"""
        res = []
        for key_, mm_ in sorted((v.get('models') or {}).items(),
                                key=lambda kv: -(kv[1]['inp'] + kv[1]['out'])):
            hr_ = mm_['hit'] / max(1, mm_['hit'] + mm_['miss']) * 100
            res.append({'name': canon.get(key_) or '未知', 'n': mm_['n'],
                        'inp': mm_['inp'], 'out': mm_['out'], 'think': mm_['think'],
                        'hit_rate': round(hr_, 1), 'tokens': mm_['inp'] + mm_['out'],
                        'credit': round(mm_['credit'], 2),
                        'tc': mm_['tc'], 'tf': mm_['tf'], 'te': mm_['te']})
        return res

    sess_days_out = {dk: {sid: {'n': v['n'], 'inp': v['inp'], 'out': v['out'], 'think': v['think'],
                                'hit': v['hit'], 'miss': v['miss'], 'credit': round(v['credit'], 2),
                                'tokens': v['inp'] + v['out'],
                                'tc': v['tc'], 'tf': v['tf'], 'te': v['te'],
                                'models': _day_models(v)}
                           for sid, v in dmap.items()}
                      for dk, dmap in sess_days.items()}
    return {'sessions': out, 'stats': stats, 'crids': crids, 'act_min': act_min,
            'token_days': tok_days_out, 'session_days': sess_days_out}


if __name__ == '__main__':
    import time
    t0 = time.time()
    d = build_token_data()
    dt = time.time() - t0
    s = d['stats']
    n_derived = sum(1 for x in d['sessions'] if x['title_src'] == 'derived')
    print(f"[token_sessions] 扫描 {s['files']} 个 jsonl · 耗时 {dt:.2f}s")
    print(f"会话 {s['sessions_n']}（子代理 {s['sub_n']}）· 跨模型 {s['multi_n']} · "
          f"总 token {s['total_tokens']:,} · 命中率 {s['hit_rate']}% · 回填标题 {n_derived} 个")
    print(f"crid {len(d['crids'])} · 活动分钟 {len(d['act_min'])} · Token 天 {len(d['token_days'])}"
          f" · 会话天 {len(d['session_days'])}")
    sp = s.get('tok_split') or {}
    t3 = (sp.get('tc') or 0) + (sp.get('tf') or 0) + (sp.get('te') or 0)
    print(f"Token 三类：积分 {sp.get('tc', 0):,} ({(sp.get('tc', 0) / max(1, t3) * 100):.1f}%) · "
          f"免费 {sp.get('tf', 0):,} ({(sp.get('tf', 0) / max(1, t3) * 100):.1f}%) · "
          f"外部API {sp.get('te', 0):,} ({(sp.get('te', 0) / max(1, t3) * 100):.1f}%)")
    print("\n-- TOP6 会话 --")
    for x in d['sessions'][:6]:
        tag = ' [子代理]' if x['sub'] else ''
        print(f"  {x['sid'][:8]} | {x['title'][:40]}{tag} | {x['n']}req | "
              f"{x['tokens']:,} | {x['hit_rate']}% | cr {x['credit']}")
    print("\n-- 按模型 Token（by_model）--")
    for m in s['by_model']:
        print(f"  {m['name']:<36} {m['tokens']:>13,} · {m['n']} 次")
