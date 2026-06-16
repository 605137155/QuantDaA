"""
首板断板融合竞价策略 - 本地完整回测
数据源: 同花顺涨停板 + 腾讯不复权日线 + 腾讯实时行情
完全不依赖聚宽，输出格式与聚宽回测日志一致
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import time
import requests
import warnings
warnings.filterwarnings('ignore')

from datetime import datetime, timedelta

_tc = requests.Session()
_tc.trust_env = False
_tc.verify = False

_ths_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}

# ========================================================================
# 数据源
# ========================================================================

def ths_limit_up(date_str):
    """同花顺涨停板列表"""
    date_fmt = date_str.replace('-', '')
    stocks = set()
    page = 1
    while True:
        url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page={page}&limit=200&field=code&order=desc&date={date_fmt}'
        try:
            resp = requests.get(url, headers=_ths_headers, timeout=10)
            data = resp.json()
            if data.get('status_code') != 0 or not data.get('data', {}).get('info'):
                break
            stocks |= {s['code'] for s in data['data']['info']}
            if len(stocks) >= data['data']['page']['total']:
                break
            page += 1
        except:
            break
    return stocks


def tencent_daily_bfq(code, count=110):
    """腾讯不复权日线"""
    sym = f'sh{code}' if code.startswith('6') else f'sz{code}'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{count},bfq'
    try:
        resp = _tc.get(url, timeout=10)
        data = resp.json()
        klines = data.get('data', {}).get(sym, {}).get('day', [])
        if not klines:
            klines = data.get('data', {}).get(sym, {}).get('bfqday', [])
        return klines
    except:
        return []


def tencent_realtime(codes):
    """腾讯实时行情"""
    result = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        try:
            resp = _tc.get(f'https://qt.gtimg.cn/q={",".join(syms)}', timeout=10)
            for line in resp.text.strip().split('\n'):
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 50:
                    continue
                code_raw = parts[2]
                code = f'{code_raw}.XSHG' if code_raw.startswith('6') else f'{code_raw}.XSHE'
                def sf(s, d=0.0):
                    try: return float(s) if s else d
                    except: return d
                result[code] = {
                    'name': parts[1], 'open': sf(parts[5]), 'last': sf(parts[3]),
                    'volume': sf(parts[6]), 'turnover': sf(parts[38]),
                }
                for j in range(1, 6):
                    result[code][f'bid{j}_p'] = sf(parts[8+(j-1)*2])
                    result[code][f'bid{j}_v'] = sf(parts[9+(j-1)*2])
                    result[code][f'ask{j}_p'] = sf(parts[18+(j-1)*2])
                    result[code][f'ask{j}_v'] = sf(parts[19+(j-1)*2])
        except:
            pass
    return result


def get_names(codes):
    """腾讯获取股票名称"""
    names = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        try:
            resp = _tc.get(f'https://qt.gtimg.cn/q={",".join(syms)}', timeout=10)
            for line in resp.text.strip().split('\n'):
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) > 2:
                    names[parts[2]] = parts[1]
        except:
            pass
    return names


def to_jq(code):
    return f'{code}.XSHG' if code.startswith('6') else f'{code}.XSHE'


# ========================================================================
# 策略参数
# ========================================================================

CONDITION_RULES_SETUP1 = [
    ('E: 一字板/准一字 竞价>=9.8%', 1.098, 1.11, 0.005, 1.0),
    ('A: 竞价高开7~9%', 1.07, 1.098, 0.025, 0.25),
    ('B: 竞价高开4~7%', 1.04, 1.07, 0.02, 0.25),
    ('C: 竞价平开至小高开0~4%', 1.00, 1.04, 0.015, 0.15),
]

CONDITION_RULES_SETUP2 = [
    ('反包E: 竞价高开8~12%', 1.08, 1.12, 0.005, 0.25),
    ('反包A: 竞价高开4~8%', 1.04, 1.08, 0.005, 0.20),
    ('反包B: 竞价高开2~4%', 1.02, 1.04, 0.005, 0.15),
    ('反包C: 竞价平开至小高开0~2%', 1.00, 1.02, 0.005, 0.12),
    ('反包D: 竞价低开-3~0%', 0.97, 1.00, 0.005, 0.12),
    ('反包F: 深低开-5~-3%', 0.95, 0.97, 0.005, 0.10),
]

MIN_CAP, MAX_CAP = 10, 1200
MIN_AMOUNT, MAX_AMOUNT = 1e8, 100e8
MAX_BUY_COUNT = 2

_name_cache = {}
_bars_cache = {}


# ========================================================================
# 数据获取（带缓存）
# ========================================================================

def get_bars(code, count=110):
    """获取不复权日线（带缓存）"""
    if code not in _bars_cache:
        _bars_cache[code] = tencent_daily_bfq(code, count)
    return _bars_cache[code]


def find_bar_by_date(klines, date_str):
    """在klines中查找指定日期的bar"""
    for k in klines:
        if k[0] == date_str:
            return {'open': float(k[1]), 'close': float(k[2]),
                    'high': float(k[3]), 'low': float(k[4]),
                    'volume': float(k[5]) if len(k) > 5 else 0}
    return None


# ========================================================================
# 盘前选股
# ========================================================================

def premarket_scan(test_date, trade_dates):
    idx = trade_dates.index(test_date)
    y_day = trade_dates[idx - 1]
    d_2 = trade_dates[idx - 2]
    d_3 = trade_dates[idx - 3]

    print(f"\n{'='*80}")
    print(f"【盘前选股】昨日: {y_day}")
    print(f"{'='*80}")

    # 获取涨停板
    print("[选股] 获取涨停板数据...")
    limit_1 = ths_limit_up(y_day)
    limit_2 = ths_limit_up(d_2)
    limit_3 = ths_limit_up(d_3)
    print(f"[选股] {y_day}涨停: {len(limit_1)}只 | {d_2}涨停: {len(limit_2)}只")

    # 主板池
    main_pool = [c for c in (limit_1 | limit_2) if not c.startswith(('3', '4', '8', '9', '68'))]

    # 形态分类
    raw_s1 = [c for c in main_pool if c in limit_1 and c not in limit_2]
    raw_s2 = [c for c in main_pool if c in limit_2 and c not in limit_3 and c not in limit_1]
    print(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_s1)}只 | Setup 2 (断板反包): {len(raw_s2)}只")

    # 过滤
    s1 = filter_stocks(raw_s1, y_day)
    s2 = filter_stocks(raw_s2, y_day)

    # 转聚宽格式
    s1_jq = [to_jq(c) for c in s1]
    s2_jq = [to_jq(c) for c in s2]

    # 获取名称
    all_codes = s1 + s2
    names = get_names(all_codes)
    for c in all_codes:
        _name_cache[c] = names.get(c, c)

    def fmt(c):
        pure = c.split('.')[0]
        return f"{c}({_name_cache.get(pure, c)})"
    print(f"今日选股池 (Setup 1 1进2-{len(s1_jq)}只): {', '.join([fmt(c) for c in s1_jq])}")
    print(f"今日选股池 (Setup 2 断板反包-{len(s2_jq)}只): {', '.join([fmt(c) for c in s2_jq])}")

    return s1_jq, s2_jq, y_day


def filter_stocks(codes, y_day):
    """4道过滤（用腾讯不复权日线）"""
    if not codes:
        return codes

    filtered = []
    for code in codes:
        try:
            klines = get_bars(code, 110)
            if len(klines) < 5:
                filtered.append(code)
                continue

            # 转成结构化数据
            bars = []
            for k in klines:
                bars.append({
                    'date': k[0],
                    'open': float(k[1]), 'close': float(k[2]),
                    'high': float(k[3]), 'low': float(k[4]),
                    'volume': float(k[5]) if len(k) > 5 else 0,
                })

            # 找到y_day的索引
            y_idx = None
            for i, b in enumerate(bars):
                if b['date'] == y_day:
                    y_idx = i
                    break
            if y_idx is None:
                filtered.append(code)
                continue

            # 取y_day及之前的数据
            bars_before = bars[:y_idx + 1]

            # 过滤1: 5日波动>40%
            recent5 = bars_before[-5:]
            if len(recent5) >= 5:
                h5 = max(b['high'] for b in recent5)
                l5 = min(b['low'] for b in recent5)
                if l5 > 0 and (h5 - l5) / l5 > 0.40:
                    continue

            # 过滤2: 5日涨停>=4天
            if len(bars_before) >= 6:
                ratio = 0.20 if code.startswith(('300', '301', '688')) else 0.10
                recent6 = bars_before[-6:]
                limit_count = 0
                for i in range(1, len(recent6)):
                    prev_c = recent6[i-1]['close']
                    if prev_c > 0 and recent6[i]['close'] >= prev_c * (1 + ratio) * 0.998:
                        limit_count += 1
                if limit_count >= 4:
                    continue

            # 过滤3: 百日高位
            if len(bars_before) >= 101:
                max_high = max(b['high'] for b in bars_before[-101:-1])
                yst_close = bars_before[-1]['close']
                if yst_close < max_high * 0.9:
                    continue

            filtered.append(code)
        except:
            filtered.append(code)

    print(f"过滤后: 保留{len(filtered)}/{len(codes)}只")
    return filtered


# ========================================================================
# 竞价匹配
# ========================================================================

def auction_match(s1_jq, s2_jq, y_day, test_date):
    """竞价匹配（用当日开盘价模拟竞价，用成交量模拟量比）"""
    all_targets = s1_jq + s2_jq
    if not all_targets:
        return []

    print(f"\n{'─'*80}")
    print(f"【竞价开始】共有 {len(s1_jq)}只 Setup 1 候选，{len(s2_jq)}只 Setup 2 候选")
    print(f"{'─'*80}")

    # 获取昨日和今日的不复权日线
    prev_map = {}
    today_map = {}
    for code_jq in all_targets:
        pure = code_jq.split('.')[0]
        klines = get_bars(pure, 5)
        prev_bar = find_bar_by_date(klines, y_day)
        today_bar = find_bar_by_date(klines, test_date)
        if prev_bar:
            prev_map[code_jq] = prev_bar
        if today_bar:
            today_map[code_jq] = today_bar

    qualified = []

    for code_jq in all_targets:
        pure = code_jq.split('.')[0]
        name = _name_cache.get(pure, code_jq)
        is_s1 = code_jq in s1_jq

        prev = prev_map.get(code_jq)
        today = today_map.get(code_jq)
        if prev is None or today is None:
            continue

        yst_close = prev['close']
        yst_volume = prev['volume']
        open_price = today['open']
        today_volume = today['volume']

        # 基础过滤
        if open_price <= 3:
            continue

        # 竞价指标（用开盘价和成交量模拟）
        cur_ratio = open_price / yst_close if yst_close > 0 else 0
        auction_ratio = today_volume / yst_volume if yst_volume > 0 else 0

        # OBI替代：用换手率近似（无法获取历史五档盘口）
        # 换手率高 = 资金活跃 = OBI偏高
        obi_proxy = min(today_volume / max(yst_volume, 1) * 0.5, 5.0)
        obi_proxy = max(obi_proxy, 0.5)

        # OBI过滤（放宽，因为是近似值）
        if obi_proxy < 0.4:
            continue

        # 规则匹配
        rules = CONDITION_RULES_SETUP1 if is_s1 else CONDITION_RULES_SETUP2
        matched = None
        for cn, lo, hi, al, ah in rules:
            if lo < cur_ratio <= hi and al <= auction_ratio <= ah:
                matched = cn
                break

        if matched is None:
            continue

        # 打分（OBI用近似值）
        turnover_proxy = today_volume / max(yst_volume, 1)
        wts = turnover_proxy * cur_ratio
        score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts * 1.5 + obi_proxy * 2.0
        if is_s1 and cur_ratio >= 1.098:
            score += 15.0
        if not is_s1 and cur_ratio >= 1.08:
            score += 12.0
        if not is_s1 and cur_ratio < 0.97:
            score += 5.0

        stype = '1进2' if is_s1 else '断板反包'
        qualified.append({
            'code': code_jq, 'name': name, 'score': round(score, 2),
            'type': f'{stype}({matched})',
            'open_gap': round((cur_ratio - 1) * 100, 2),
            'vol_ratio': round(auction_ratio * 100, 2),
        })
        print(f"[OK] {code_jq}({name}) {stype} | {matched} | 开盘:{(cur_ratio-1)*100:+.2f}% | 量比:{auction_ratio*100:.1f}% | 得分:{score:.2f}")

    qualified.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n竞价终筛: {len(qualified)}只")
    final = qualified[:MAX_BUY_COUNT]
    if final:
        print(f"【重排选优】排序前 {len(final)} 只龙头股票：")
        for i, f in enumerate(final):
            print(f"  -{i+1}. {f['code']}({f['name']}) | 得分: {f['score']:.2f} | 类型: {f['type']}")
        for f in final:
            print(f"下单买入: {f['code']}({f['name']}) | 得分: {f['score']:.2f} | 条件: {f['type']}")

    return qualified


# ========================================================================
# 主程序
# ========================================================================

def get_trade_dates(start, end):
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    dates = [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10] for d in df['trade_date']]
    return [d for d in dates if start <= d <= end]


if __name__ == '__main__':
    print("=" * 80)
    print("首板断板融合竞价策略 - 本地完整回测（腾讯不复权数据）")
    print("=" * 80)

    trade_dates = get_trade_dates('2026-05-20', '2026-06-10')
    test_dates = ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05', '2026-06-06']

    for test_date in test_dates:
        try:
            s1, s2, y_day = premarket_scan(test_date, trade_dates)
            qualified = auction_match(s1, s2, y_day, test_date)
            print(f"\n{'='*80}")
            print(f"[{test_date}] 完成 | 通过: {len(qualified)}只")
            print(f"{'='*80}\n")
        except Exception as e:
            print(f"[{test_date}] 错误: {e}")
            import traceback
            traceback.print_exc()
