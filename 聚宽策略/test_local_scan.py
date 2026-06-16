import sys
sys.stdout.reconfigure(encoding='utf-8')
import warnings
warnings.filterwarnings('ignore')
import time
import pandas as pd
import numpy as np
import requests

from jqdatasdk import auth, get_price, get_fundamentals, get_all_securities, get_trade_days, query, valuation
auth('18813368263', 'Forymq_10')

_tc = requests.Session()
_tc.trust_env = False
_tc.verify = False

_name_cache = {}
def get_name(code):
    if code in _name_cache:
        return _name_cache[code]
    try:
        info = get_all_securities(types=['stock']).loc[code]
        name = info['display_name']
    except:
        name = code
    _name_cache[code] = name
    return name

def to_jq(code):
    """纯数字代码 → 聚宽格式"""
    if code.startswith(('6','9','688')):
        return f'{code}.XSHG'
    return f'{code}.XSHE'

def ths_limit_up(date_str):
    """同花顺涨停板（返回聚宽格式代码集合）"""
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}
    date_fmt = date_str.replace('-', '')
    url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&field=code&order=desc&date={date_fmt}'
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        return {to_jq(s['code']) for s in data.get('data', {}).get('info', [])}
    except:
        return set()

# ====== 测试 2026-03-02 ======
test_date = '2026-03-02'
trade_days_list = [d.strftime('%Y-%m-%d') for d in get_trade_days(end_date=test_date, count=10)]
y_day = trade_days_list[-2]
d_1 = y_day
d_2 = trade_days_list[-3]
d_3 = trade_days_list[-4]

print(f'\n{"="*80}')
print(f'【盘前选股】昨日: {y_day}')
print(f'{"="*80}')

limit_1 = ths_limit_up(d_1)  # 昨日涨停（聚宽格式）
limit_2 = ths_limit_up(d_2)  # 前日涨停
limit_3 = ths_limit_up(d_3)  # 大前日涨停
print(f'[选股] {d_1}涨停: {len(limit_1)}只 | {d_2}涨停: {len(limit_2)}只')

# 主板池
main_pool = [c for c in (limit_1 | limit_2) if not c.startswith(('3','4','8','9','68'))]
raw_s1 = [c for c in main_pool if c in limit_1 and c not in limit_2]
raw_s2 = [c for c in main_pool if c in limit_2 and c not in limit_3 and c not in limit_1]
print(f'[选股] Setup 1: {len(raw_s1)}只 | Setup 2: {len(raw_s2)}只')

# 百日高位过滤
def filter_high(codes):
    qualified = []
    for code in codes:
        try:
            bars = get_price(code, end_date=y_day, frequency='daily',
                           fields=['high','close'], count=101, panel=False,
                           fill_paused=False, skip_paused=True, fq='pre')
            if bars.empty or len(bars) < 101:
                qualified.append(code)
                continue
            max_high = bars['high'].iloc[:-1].max()
            yst_close = bars['close'].iloc[-1]
            if yst_close >= max_high * 0.9:
                qualified.append(code)
        except:
            qualified.append(code)
    return qualified

s1 = filter_high(raw_s1)
s2 = filter_high(raw_s2)
print(f'[过滤后] Setup1: {len(s1)}只 | Setup2: {len(s2)}只')

for c in s1 + s2:
    get_name(c)

print(f'Setup 1: {", ".join([f"{c}({_name_cache.get(c,c)})" for c in s1[:10]])}...')
print(f'Setup 2: {", ".join([f"{c}({_name_cache.get(c,c)})" for c in s2[:10]])}...')

# ====== 竞价匹配 ======
print(f'\n{"─"*80}')
print(f'【竞价开始】{len(s1)}只 Setup1，{len(s2)}只 Setup2')
print(f'{"─"*80}')

all_targets = s1 + s2
prev_df = get_price(all_targets, end_date=y_day, frequency='daily',
                   fields=['close','volume','money'], count=1, panel=False,
                   fill_paused=False, skip_paused=True)
prev_map = {row['code']: row for _, row in prev_df.iterrows()}

val_df = get_fundamentals(
    query(valuation.code, valuation.market_cap, valuation.circulating_market_cap, valuation.turnover_ratio)
    .filter(valuation.code.in_(all_targets)), date=y_day)
val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

# 腾讯行情（需要转成腾讯格式）
def jq_to_tencent(code):
    pure = code.split('.')[0]
    if code.endswith('XSHG'):
        return f'sh{pure}'
    return f'sz{pure}'

symbols = [jq_to_tencent(c) for c in all_targets]
rt = {}
for i in range(0, len(symbols), 80):
    batch = symbols[i:i+80]
    try:
        resp = _tc.get(f'https://qt.gtimg.cn/q={",".join(batch)}', timeout=10)
        for line in resp.text.strip().split('\n'):
            if '~' not in line: continue
            parts = line.split('~')
            if len(parts) < 50: continue
            code_raw = parts[2]
            code = to_jq(code_raw)
            def sf(s, d=0.0):
                try: return float(s) if s else d
                except: return d
            rt[code] = {
                'open': sf(parts[5]), 'volume': sf(parts[6]), 'turnover': sf(parts[38]),
            }
            for j in range(1, 6):
                rt[code][f'bid{j}_p'] = sf(parts[8+(j-1)*2])
                rt[code][f'bid{j}_v'] = sf(parts[9+(j-1)*2])
                rt[code][f'ask{j}_p'] = sf(parts[18+(j-1)*2])
                rt[code][f'ask{j}_v'] = sf(parts[19+(j-1)*2])
    except Exception as e:
        print(f'腾讯行情错误: {e}')

qualified = []
for code in all_targets:
    name = _name_cache.get(code, code)
    is_s1 = code in s1
    prev = prev_map.get(code)
    quote = rt.get(code)
    val = val_map.get(code)
    if prev is None or quote is None: continue

    yst_close = prev['close']
    yst_volume = prev['volume']
    money = prev['money']
    open_price = quote['open']

    if open_price <= 3: continue
    if val is not None and (val['market_cap'] < 10 or val['circulating_market_cap'] > 1200): continue
    if money < 1e8 or money > 100e8: continue

    hl_base = yst_close * 1.1  # 涨停价
    cur_ratio = open_price / yst_close if yst_close > 0 else 0  # 相对昨日收盘
    auction_ratio = quote['volume'] / yst_volume if yst_volume > 0 else 0

    buymoney = sum(quote.get(f'bid{j}_p',0) * quote.get(f'bid{j}_v',0) * 100 for j in range(1,6))
    sellmoney = sum(quote.get(f'ask{j}_p',0) * quote.get(f'ask{j}_v',0) * 100 for j in range(1,6))
    obi = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

    if obi < 0.6:
        print(f'  [排除] {code}({name}) OBI={obi:.2f} < 0.6')
        continue

    rules = [
        ('E: 一字板>=9.8%', 1.098, 1.11, 0.005, 1.0),
        ('A: 高开7~9%', 1.07, 1.098, 0.025, 0.25),
        ('B: 高开4~7%', 1.04, 1.07, 0.02, 0.25),
        ('C: 平开0~4%', 1.00, 1.04, 0.015, 0.15),
    ] if is_s1 else [
        ('反包E: 高开8~12%', 1.08, 1.12, 0.005, 0.25),
        ('反包A: 高开4~8%', 1.04, 1.08, 0.005, 0.20),
        ('反包B: 高开2~4%', 1.02, 1.04, 0.005, 0.15),
        ('反包C: 平开0~2%', 1.00, 1.02, 0.005, 0.12),
        ('反包D: 低开-3~0%', 0.97, 1.00, 0.005, 0.12),
        ('反包F: 深低开-5~-3%', 0.95, 0.97, 0.005, 0.10),
    ]

    matched = None
    for cn, lo, hi, al, ah in rules:
        if lo < cur_ratio <= hi and al <= auction_ratio <= ah:
            matched = cn
            break

    if matched is None:
        print(f'  [排除] {code}({name}) 未匹配: 涨幅={(cur_ratio-1)*100:.2f}% 竞昨比={auction_ratio*100:.2f}%')
        continue

    tr = val['turnover_ratio']/100.0 if val is not None and not pd.isna(val.get('turnover_ratio', float('nan'))) else 0
    wts = tr * cur_ratio
    score = (cur_ratio-1)*100*1.2 + auction_ratio*100*0.8 + wts*1.5 + obi*2.0
    if is_s1 and cur_ratio >= 1.098: score += 15.0
    if not is_s1 and cur_ratio >= 1.08: score += 12.0
    if not is_s1 and cur_ratio < 0.97: score += 5.0

    stype = '1进2' if is_s1 else '断板反包'
    qualified.append({'code':code, 'name':name, 'score':round(score,2), 'type':f'{stype}({matched})'})
    print(f'[OK] {code}({name}) {stype} | {matched} | 得分:{score:.2f} | OBI:{obi:.2f}')

qualified.sort(key=lambda x: x['score'], reverse=True)
print(f'\n竞价终筛: {len(qualified)}只')
final = qualified[:2]
for i, f in enumerate(final):
    print(f'  -{i+1}. {f["code"]}({f["name"]}) | 得分:{f["score"]} | {f["type"]}')
for f in final:
    print(f'下单买入: {f["code"]}({f["name"]}) | 得分:{f["score"]} | {f["type"]}')
