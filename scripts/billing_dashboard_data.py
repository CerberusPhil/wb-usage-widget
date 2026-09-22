# -*- coding: utf-8 -*-
"""
WorkBuddy 积分看板 · 账单真值版数据脚本（Phase B/C）
====================================================
数据源：
  1) ~/.workbuddy/server_usage_cache.json —— server_usage_sync.py 同步的官方账单真值
  2) token_sessions.build_token_data() —— 本机 jsonl 会话聚合 / 对账用 crid 集合（Phase C）

聚合口径：
  - 按天：积分合计 / 请求数 / 免费数 / 按模型 / 本机·其他端积分拆分
  - 详单：每笔（时间 / 模型 / 来源[本机·其他端·网页版·云端] / 摘要 / 积分）
  - 来源拆分：对账（jsonl conversationRequestId ↔ 账单 requestId）
      · local   = 本机可还原
      · web / cloud = 网页版 / 云端任务
      · unmatched = 客户端未匹配（含另一台设备 + 不可对账的辅助调用），
        并以 ±10 分钟时间对齐估算其中「疑似另一台设备」的部分（unmatched_silent）
  - 会话：本机 jsonl 会话聚合（含按模型拆分）→ 消耗详单「按会话」视图
  - token_days：本机 jsonl 按天 Token 聚合 → 卡片 / 日历的 Token 维度
  - session_days：按天 × 会话聚合 → 「会话消耗 · 按天」视图
  - 余额 / 模型 / 统计：同前

用法：
  python billing_dashboard_data.py        # 生成静态版 billing_dashboard.html
模块复用：
  from billing_dashboard_data import build_data   # billing_server.py 用它喂服务
"""
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

HOME_WB = os.path.expanduser('~') + '/.workbuddy'
CACHE = os.environ.get('WB_SERVER_USAGE_CACHE') or os.path.join(HOME_WB, 'server_usage_cache.json')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(SCRIPT_DIR, 'billing_template.html')
OUT_HTML = os.path.join(os.path.dirname(SCRIPT_DIR), 'billing_dashboard.html')

CLIENT_LABEL = {'WorkBuddy': '客户端', 'web_agents': '网页版', 'app_cloud': 'APP'}


def load_cache():
    """读账单缓存；不存在或损坏时抛异常，由调用方决定降级策略。"""
    if not os.path.exists(CACHE):
        raise FileNotFoundError(f'账单缓存不存在：{CACHE}（请先运行 server_usage_sync.py）')
    with open(CACHE, encoding='utf-8') as f:
        return json.load(f)


def _token_data():
    """本机 jsonl 会话聚合 + 对账数据；失败降级为空（不拖垮看板）。"""
    try:
        import token_sessions
        d = token_sessions.build_token_data()
        d['ok'] = True
        return d
    except Exception as e:
        return {'ok': False, 'sessions': [], 'stats': {'error': str(e)},
                'crids': set(), 'act_min': set()}


def _near(act_min, t, minutes=10):
    """账单时间 t('YYYY-mm-dd HH:MM:SS') 与本机活动分钟集合 ±minutes 内是否有交集。"""
    if not act_min:
        return False
    try:
        dt = datetime.strptime(t[:16], '%Y-%m-%d %H:%M')
    except ValueError:
        return False
    for k in range(-minutes, minutes + 1):
        if (dt + timedelta(minutes=k)).strftime('%Y-%m-%d %H:%M') in act_min:
            return True
    return False


def build_data():
    """读缓存 + 本机 jsonl → 聚合成看板 DATA（静态版与挂件服务共用）。"""
    cache = load_cache()
    records = list((cache.get('records') or {}).values())
    tok = _token_data()
    crids = tok.get('crids') or set()
    act_min = tok.get('act_min') or set()
    label_mode = bool(crids)          # crid 集合缺失（扫描失败）时不打本机/其他端标签

    days = defaultdict(lambda: {'credit': 0.0, 'req_n': 0, 'free_n': 0,
                                'by_model': defaultdict(float), 'by_model_n': defaultdict(int),
                                'local_credit': 0.0, 'other_credit': 0.0})
    details = defaultdict(list)
    client_dist = defaultdict(int)
    model_agg = defaultdict(lambda: {'credit': 0.0, 'n': 0, 'free_n': 0})
    split = {'local': {'n': 0, 'credit': 0.0}, 'web': {'n': 0, 'credit': 0.0},
             'cloud': {'n': 0, 'credit': 0.0}, 'unmatched': {'n': 0, 'credit': 0.0},
             'unmatched_silent': {'n': 0, 'credit': 0.0}}

    for r in records:
        t = r.get('requestTime') or ''
        if len(t) < 19:
            continue
        dk = t[:10]
        credit = float(r.get('credit') or 0)
        model = r.get('model') or '未知'
        client = r.get('client') or ''
        matched = label_mode and (r.get('requestId') in crids)
        if matched:
            bucket = 'local'
        elif client == 'web_agents':
            bucket = 'web'
        elif client == 'app_cloud':
            bucket = 'cloud'
        else:
            bucket = 'unmatched'
            if client == 'WorkBuddy' and not _near(act_min, t):
                split['unmatched_silent']['n'] += 1
                split['unmatched_silent']['credit'] += credit
        split[bucket]['n'] += 1
        split[bucket]['credit'] += credit

        if label_mode:
            if client == 'WorkBuddy':
                label = '本机' if matched else '非本机'
            else:
                label = CLIENT_LABEL.get(client, client or '—')
        else:
            label = CLIENT_LABEL.get(client, client or '—')

        d = days[dk]
        d['credit'] += credit
        d['req_n'] += 1
        if credit == 0:
            d['free_n'] += 1
        if label_mode:
            if matched:
                d['local_credit'] += credit
            else:
                d['other_credit'] += credit
        d['by_model'][model] += credit
        d['by_model_n'][model] += 1
        ma = model_agg[model]
        ma['credit'] += credit
        ma['n'] += 1
        if credit == 0:
            ma['free_n'] += 1
        client_dist[label] += 1
        details[dk].append({
            't': t[11:19],
            'model': model,
            'client': label,
            'credit': round(credit, 2),
            'input': ((r.get('inputTrunc') or r.get('input') or '').strip())[:70],
        })

    days_out = {}
    for dk, d in days.items():
        days_out[dk] = {
            'credit': round(d['credit'], 2),
            'req_n': d['req_n'],
            'free_n': d['free_n'],
            'local_credit': round(d['local_credit'], 2),
            'other_credit': round(d['other_credit'], 2),
            'by_model': {k: round(v, 2) for k, v in sorted(d['by_model'].items(), key=lambda x: -x[1])},
            'by_model_n': dict(sorted(d['by_model_n'].items(), key=lambda x: -x[1])),
        }
    for dk in details:
        details[dk].sort(key=lambda x: x['t'])

    # 余额：取容量最大的包为主包
    balance = None
    summary = cache.get('summary') or {}
    pkgs = summary.get('Packages') or []
    if pkgs:
        main_pkg = max(pkgs, key=lambda p: float(p.get('CycleTotalCapacity') or 0))
        balance = {
            'total': round(float(main_pkg.get('CycleTotalCapacity') or 0), 2),
            'used': round(float(main_pkg.get('CycleUsedCapacity') or 0), 2),
            'remain': round(float(main_pkg.get('CycleRemainCapacity') or 0), 2),
            'pkg_n': len(pkgs),
        }

    total_credit = round(sum(float(x.get('credit') or 0) for x in records), 2)
    times = sorted(x.get('requestTime') for x in records if x.get('requestTime'))
    models_out = [
        {'model': m, 'credit': round(v['credit'], 2), 'n': v['n'], 'free_n': v['free_n']}
        for m, v in sorted(model_agg.items(), key=lambda kv: (-kv[1]['credit'], -kv[1]['n']))
    ]

    source_split = None
    if label_mode:
        other_credit = split['web']['credit'] + split['cloud']['credit'] + split['unmatched']['credit']
        source_split = {
            'local': {'n': split['local']['n'], 'credit': round(split['local']['credit'], 2)},
            'web': {'n': split['web']['n'], 'credit': round(split['web']['credit'], 2)},
            'cloud': {'n': split['cloud']['n'], 'credit': round(split['cloud']['credit'], 2)},
            'unmatched': {'n': split['unmatched']['n'], 'credit': round(split['unmatched']['credit'], 2)},
            'unmatched_silent': {'n': split['unmatched_silent']['n'],
                                 'credit': round(split['unmatched_silent']['credit'], 2)},
            'other_credit': round(other_credit, 2),
            'other_n': split['web']['n'] + split['cloud']['n'] + split['unmatched']['n'],
        }

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'days': days_out,
        'details': dict(details),
        'models': models_out,
        'sessions': tok.get('sessions') or [],
        'token_stats': tok.get('stats') or {},
        'token_days': tok.get('token_days') or {},
        'session_days': tok.get('session_days') or {},
        'source_split': source_split,
        'stats': {
            'total_n': len(records),
            'credit_total': total_credit,
            'free_n': sum(1 for x in records if float(x.get('credit') or 0) == 0),
            'client_dist': dict(client_dist),
            'range_start': times[0][:10] if times else '',
            'range_end': times[-1][:10] if times else '',
        },
        'balance': balance,
        'meta': {
            'source': '服务端账单（www.workbuddy.cn · 官方结算真值）+ 本机 jsonl（会话 / Token 聚合）',
            'synced_at': cache.get('updated_at') or '',
        },
    }


def main():
    try:
        data = build_data()
    except FileNotFoundError as e:
        print(f'[data] {e}')
        raise SystemExit(1)
    except Exception as e:
        print(f'[data] 聚合失败：{e}')
        raise SystemExit(1)

    with open(TEMPLATE, encoding='utf-8') as f:
        tpl = f.read()
    html = tpl.replace('/*__DATA__*/', json.dumps(data, ensure_ascii=False))
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    s = data['stats']
    print(f"账单记录: {s['total_n']} 条 (总 {s['credit_total']} 积分, 免费 {s['free_n']} 条)")
    print(f"覆盖天数: {len(data['days'])} | 范围: {s['range_start']} ~ {s['range_end']}")
    print(f"客户端分布: {s['client_dist']}")
    b = data['balance']
    if b:
        print(f"余额: 剩余 {b['remain']} / 总量 {b['total']} (已用 {b['used']}, 主包, 共{b['pkg_n']}包)")
    sp = data.get('source_split')
    if sp:
        print(f"来源拆分: 本机 {sp['local']['credit']} 积分 / 其他端 {sp['other_credit']} 积分"
              f"（网页 {sp['web']['credit']} + 云端 {sp['cloud']['credit']} + 未匹配 {sp['unmatched']['credit']}，"
              f"其中疑似另一台设备 ≈{sp['unmatched_silent']['credit']}）")
    t = data.get('token_stats') or {}
    if t.get('sessions_n'):
        print(f"本机会话: {t['sessions_n']} 个 / 跨模型 {t['multi_n']} / 总 token {t['total_tokens']:,} / 命中 {t['hit_rate']}%")
    print(f"已生成: {OUT_HTML}")


if __name__ == '__main__':
    main()
