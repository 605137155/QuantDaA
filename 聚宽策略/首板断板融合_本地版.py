# ========================================================================
# 首板与断板反包竞价融合策略 - 本地版（不依赖聚宽平台）
# ========================================================================
# 数据源：
#   - jqdatasdk：历史日线（不复权，试用账号可用）
#   - 同花顺API：涨停板股票列表
#   - 腾讯行情：实时竞价数据（开盘价、五档盘口、换手率）
# 输出格式：与聚宽回测日志完全一致
# ========================================================================

import sys
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
import warnings
warnings.filterwarnings('ignore')

from jqdatasdk import auth, get_price, get_fundamentals, get_all_securities, get_trade_days, query, valuation

# 聚宽认证
auth('18813368263', 'Forymq_10')

# 腾讯请求session（绕过代理）
_tc_session = requests.Session()
_tc_session.trust_env = False
_tc_session.verify = False

# ========================================================================
# ██ 数据源接口 ██
# ========================================================================

def ths_limit_up(date_str):
    """从同花顺获取涨停板股票"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html',
    }
    date_fmt = date_str.replace('-', '')
    all_stocks = []
    page = 1
    while True:
        url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page={page}&limit=100&field=code&order=desc&date={date_fmt}'
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            if data.get('status_code') != 0 or not data.get('data', {}).get('info'):
                break
            all_stocks.extend(data['data']['info'])
            if len(all_stocks) >= data['data']['page']['total']:
                break
            page += 1
        except:
            break
    return all_stocks


def ths_to_jq(code):
    """同花顺代码转聚宽代码"""
    if code.startswith(('6', '9', '688')):
        return f'{code}.XSHG'
    return f'{code}.XSHE'


def tencent_realtime(codes):
    """腾讯实时行情（含五档盘口）"""
    result = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        symbols = []
        for code in batch:
            pure = code.split('.')[0]
            if code.endswith('XSHG'):
                symbols.append(f'sh{pure}')
            else:
                symbols.append(f'sz{pure}')
        url = f"https://qt.gtimg.cn/q={','.join(symbols)}"
        try:
            resp = _tc_session.get(url, timeout=10)
            lines = resp.text.strip().split('\n')
            for line in lines:
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 50:
                    continue
                code_raw = parts[2]
                if code_raw.startswith('6'):
                    code = f'{code_raw}.XSHG'
                else:
                    code = f'{code_raw}.XSHE'
                try:
                    def safe_float(s, default=0.0):
                        try:
                            return float(s) if s else default
                        except:
                            return default

                    result[code] = {
                        'name': parts[1],
                        'last': safe_float(parts[3]),
                        'open': safe_float(parts[5]),
                        'high': safe_float(parts[33]),
                        'low': safe_float(parts[34]),
                        'volume': safe_float(parts[6]),
                        'amount': safe_float(parts[37]),
                        'turnover': safe_float(parts[38]),
                    }
                    # 五档盘口
                    for j in range(1, 6):
                        result[code][f'bid{j}_p'] = safe_float(parts[8 + (j-1)*2])
                        result[code][f'bid{j}_v'] = safe_float(parts[9 + (j-1)*2])
                        result[code][f'ask{j}_p'] = safe_float(parts[18 + (j-1)*2])
                        result[code][f'ask{j}_v'] = safe_float(parts[19 + (j-1)*2])
                except:
                    pass
        except:
            pass
    return result


def to_jq_code(code):
    """同花顺代码转聚宽代码"""
    if code.startswith(('6', '9', '688')):
        return f'{code}.XSHG'
    return f'{code}.XSHE'


# ========================================================================
# ██ 策略参数 ██
# ========================================================================

LIMIT_UP_RATIO = 0.998
MIN_YESTERDAY_CLOSE_RATIO = 0.95

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

MIN_CAP = 10
MAX_CAP = 1200
MIN_AMOUNT = 1e8
MAX_AMOUNT = 100e8
MAX_BUY_COUNT = 2


# ========================================================================
# ██ 名称缓存 ██
# ========================================================================

_name_cache = {}

def get_name(code):
    if code in _name_cache:
        return _name_cache[code]
    try:
        info = get_all_securities(types=['stock']).loc[code]
        name = info['display_name'] if 'display_name' in info.index else code
    except:
        name = code
    _name_cache[code] = name
    return name


# ========================================================================
# ██ 盘前选股 ██
# ========================================================================

def scan_premarket(test_date):
    """盘前选股：从同花顺获取涨停板 → 形态分类 → 过滤"""
    trade_days = get_trade_days(end_date=test_date, count=10)
    trade_days = [d.strftime('%Y-%m-%d') for d in trade_days]

    y_day = trade_days[-2]  # 昨日（test_date的前一个交易日）
    d_1 = y_day
    d_2 = trade_days[-3]
    d_3 = trade_days[-4]
    d_4 = trade_days[-5]
    recent_6 = trade_days[-6:]

    print(f"\n{'='*80}")
    print(f"【盘前选股】昨日: {y_day}")
    print(f"{'='*80}")

    # 从同花顺获取各日涨停板
    print("[选股] 正在从同花顺获取涨停板数据...")
    limit_data = {}
    for d in [d_1, d_2, d_3, d_4]:
        stocks = ths_limit_up(d)
        limit_data[d] = {s['code'] for s in stocks}
        print(f"  {d}: {len(limit_data[d])}只涨停")
        time.sleep(0.5)

    # 近6日有涨停的主板股
    all_limit = set()
    for d in recent_6:
        if d in limit_data:
            all_limit |= limit_data[d]
        else:
            stocks = ths_limit_up(d)
            limit_data[d] = {s['code'] for s in stocks}
            all_limit |= limit_data[d]
            time.sleep(0.3)

    main_pool = [c for c in all_limit if not c.startswith(('3', '4', '8', '9', '68'))]
    print(f"[选股] 候选池: {len(main_pool)}只（近6日有涨停的主板股）")

    # 形态分类
    raw_setup1, raw_setup2, raw_setup3 = [], [], []

    for code in main_pool:
        in_d1 = code in limit_data.get(d_1, set())
        in_d2 = code in limit_data.get(d_2, set())
        in_d3 = code in limit_data.get(d_3, set())
        in_d4 = code in limit_data.get(d_4, set())

        # Setup 1: 昨日涨停 + 前日未涨停
        if in_d1 and not in_d2:
            raw_setup1.append(code)
        # Setup 2: 前日涨停 + 大前日未涨停 + 昨日未涨停 + 跌幅<5%
        elif in_d2 and not in_d3 and not in_d1:
            bars = get_price(to_jq_code(code), end_date=d_1, frequency='daily',
                           fields=['close'], count=2, panel=False)
            if not bars.empty and len(bars) >= 2:
                yst_close = bars.iloc[-1]['close']
                prev_close = bars.iloc[-2]['close']
                if prev_close > 0 and yst_close >= prev_close * MIN_YESTERDAY_CLOSE_RATIO:
                    raw_setup2.append(code)
        # Setup 3: 三日前涨停
        elif in_d4 and not in_d3 and not in_d2 and not in_d1:
            raw_setup3.append(code)

    print(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_setup1)}只 | Setup 2 (断板反包): {len(raw_setup2)}只 | Setup 3 (三日断板): {len(raw_setup3)}只")

    # 过滤：百日高位
    def filter_high(codes, label):
        if not codes:
            return codes
        qualified = []
        for code in codes:
            bars = get_price(to_jq_code(code), end_date=y_day, frequency='daily',
                           fields=['high', 'close'], count=101, panel=False,
                           fill_paused=False, skip_paused=True, fq='pre')
            if bars.empty or len(bars) < 101:
                qualified.append(code)
                continue
            max_high = bars['high'].iloc[:-1].max()
            yst_close = bars['close'].iloc[-1]
            if yst_close >= max_high * 0.9:
                qualified.append(code)
        print(f"前100日最高价过滤: 保留{len(qualified)}/{len(codes)}只")
        return qualified

    s1 = filter_high(raw_setup1, 'Setup1')
    s2 = filter_high(raw_setup2, 'Setup2')
    s3 = filter_high(raw_setup3, 'Setup3')

    # 输出选股池
    all_targets = s1 + s2 + s3
    for c in all_targets:
        _ = get_name(c)

    print(f"今日选股池 (Setup 1 1进2-{len(s1)}只): {', '.join([f'{c}({_name_cache.get(c, c)})' for c in s1])}")
    print(f"今日选股池 (Setup 2 断板反包-{len(s2)}只): {', '.join([f'{c}({_name_cache.get(c, c)})' for c in s2])}")
    print(f"今日选股池 (Setup 3 三日断板-{len(s3)}只): {', '.join([f'{c}({_name_cache.get(c, c)})' for c in s3])}")

    return s1, s2, s3, y_day


# ========================================================================
# ██ 竞价匹配 ██
# ========================================================================

def match_auction(s1, s2, s3, y_day):
    """竞价匹配：腾讯实时行情 + 规则匹配 + 打分"""
    all_targets = s1 + s2 + s3
    if not all_targets:
        print("[竞价] 今日无任何候选股票")
        return []

    print(f"\n{'─'*80}")
    print(f"【竞价开始】共有 {len(s1)}只 Setup 1 候选，{len(s2)}只 Setup 2 候选，{len(s3)}只 Setup 3 候选")
    print(f"{'─'*80}")

    # 获取昨日数据
    prev_df = get_price(all_targets, end_date=y_day, frequency='daily',
                       fields=['close', 'volume', 'money'], count=1, panel=False,
                       fill_paused=False, skip_paused=True)
    prev_map = {row['code']: row for _, row in prev_df.iterrows()} if not prev_df.empty else {}

    # 获取市值
    val_df = get_fundamentals(
        query(valuation.code, valuation.market_cap, valuation.circulating_market_cap, valuation.turnover_ratio)
        .filter(valuation.code.in_(all_targets)),
        date=y_day
    )
    val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

    # 获取实时行情
    rt = tencent_realtime(all_targets)

    qualified = []

    for code in all_targets:
        name = _name_cache.get(code, code)
        is_s1 = code in s1
        is_s2 = code in s2

        prev = prev_map.get(code)
        quote = rt.get(code)
        val = val_map.get(code)

        if prev is None or quote is None:
            continue

        yst_close = prev['close']
        yst_volume = prev['volume']
        money = prev['money']
        open_price = quote['open']
        turnover = quote['turnover']

        # 基础过滤
        if open_price <= 3:
            continue
        if val is not None:
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                continue
        if money < MIN_AMOUNT or money > MAX_AMOUNT:
            continue

        # 竞价指标
        hl_base = yst_close * 1.1 if not code.startswith(('300', '301', '688')) else yst_close * 1.2
        cur_ratio = open_price / hl_base if hl_base > 0 else 0
        auction_ratio = quote['volume'] / yst_volume if yst_volume > 0 else 0

        # OBI（五档盘口）
        buymoney = sum(quote.get(f'bid{j}_p', 0) * quote.get(f'bid{j}_v', 0) * 100 for j in range(1, 6))
        sellmoney = sum(quote.get(f'ask{j}_p', 0) * quote.get(f'ask{j}_v', 0) * 100 for j in range(1, 6))
        obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

        if obi_ratio < 0.6:
            print(f"  [排除] {code}({name}) 竞价买卖比不符: OBI={obi_ratio:.2f} < 0.6")
            continue

        # 规则匹配
        rules = CONDITION_RULES_SETUP1 if is_s1 else CONDITION_RULES_SETUP2
        matched = None
        for cond_name, open_lo, open_hi, auc_lo, auc_hi in rules:
            if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                matched = cond_name
                break

        if matched is None:
            print(f"  [排除] {code}({name}) 竞价未匹配: 涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}%")
            continue

        # 打分
        turnover_ratio = val['turnover_ratio'] / 100.0 if val is not None and not pd.isna(val.get('turnover_ratio', np.nan)) else 0
        wts = turnover_ratio * cur_ratio
        score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts * 1.5 + obi_ratio * 2.0

        if is_s1 and cur_ratio >= 1.098:
            score += 15.0
        if is_s2 and cur_ratio >= 1.08:
            score += 12.0
        if is_s2 and cur_ratio < 0.97:
            score += 5.0

        setup_type = '1进2' if is_s1 else ('断板反包' if is_s2 else '三日断板')
        qualified.append({
            'code': code, 'name': name, 'score': round(score, 2),
            'type': f"{setup_type}({matched})",
            'open_gap': round((cur_ratio - 1) * 100, 2),
            'vol_ratio': round(auction_ratio * 100, 2),
            'turnover': round(turnover, 2),
            'obi': round(obi_ratio, 2),
        })
        print(f"[OK] {code}({name}) 符合 {setup_type}，命中: {matched} | 得分: {score:.2f} | 换手: {turnover:.2f}% | OBI: {obi_ratio:.2f}")

    # 排序
    qualified.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n竞价终筛结果：符合竞价过滤条件的个股共 {len(qualified)} 只")

    final = qualified[:MAX_BUY_COUNT]
    if final:
        print(f"【重排选优】排序前 {len(final)} 只龙头股票：")
        for i, item in enumerate(final):
            print(f"  -{i+1}. {item['code']}({item['name']}) | 得分: {item['score']:.2f} | 类型: {item['type']}")
        for item in final:
            print(f"下单买入: {item['code']}({item['name']}) | 得分: {item['score']:.2f} | 条件: {item['type']}")

    return qualified


# ========================================================================
# ██ 主程序 ██
# ========================================================================

if __name__ == '__main__':
    print("=" * 80)
    print("首板断板融合竞价策略 - 本地版（同花顺+jqdatasdk+腾讯）")
    print("=" * 80)

    # 测试日期
    test_dates = ['2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05', '2026-03-06']

    for test_date in test_dates:
        try:
            s1, s2, s3, y_day = scan_premarket(test_date)
            qualified = match_auction(s1, s2, s3, y_day)
            print(f"\n{'='*80}")
            print(f"[{test_date}] 完成 | 通过: {len(qualified)}只")
            print(f"{'='*80}\n")
        except Exception as e:
            print(f"[{test_date}] 错误: {e}")
            import traceback
            traceback.print_exc()
