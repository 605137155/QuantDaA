"""
本地复现聚宽回测 - 完全不依赖聚宽
数据源：同花顺涨停板 + akshare日线(绕代理) + 腾讯实时行情
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import warnings
warnings.filterwarnings('ignore')
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime

# 绕过代理的requests session
_session = requests.Session()
_session.trust_env = False
_session.verify = False

# ========================================================================
# 数据源
# ========================================================================

def ths_limit_up(date_str):
    """同花顺涨停板 → 返回聚宽格式代码集合"""
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}
    date_fmt = date_str.replace('-', '')
    url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&field=code&order=desc&date={date_fmt}'
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        return {to_jq(s['code']) for s in data.get('data', {}).get('info', [])}
    except:
        return set()

def to_jq(code):
    if code.startswith(('6','9','688')):
        return f'{code}.XSHG'
    return f'{code}.XSHE'

def get_daily_bars_ak(code, end_date, count):
    """akshare获取日线（绕代理）"""
    import akshare as ak
    try:
        symbol = code.split('.')[0]
        # 用较早的start_date确保拿到足够数据
        start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=count*2)
        df = ak.stock_zh_a_hist(
            symbol=symbol, period='daily', adjust='',
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date.replace('-', ''),
            timeout=10
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={'日期':'time','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume','成交额':'money'})
        df['time'] = pd.to_datetime(df['time'])
        return df.tail(count).reset_index(drop=True)
    except:
        return None

def get_stock_info_ak(codes):
    """akshare获取股票名称和市值（用索引避免编码问题）"""
    import akshare as ak
    result = {}
    for code in codes:
        try:
            symbol = code.split('.')[0]
            df = ak.stock_individual_info_em(symbol=symbol, timeout=10)
            # 字段顺序: [0]PE [1]代码 [2]简称 [3]总股本 [4]流通股 [5]总市值 [6]流通市值 [7]行业 [8]上市时间
            name = str(df.iloc[2]['value']) if len(df) > 2 else code
            market_cap = float(df.iloc[5]['value']) / 1e8 if len(df) > 5 else 0
            circ_cap = float(df.iloc[6]['value']) / 1e8 if len(df) > 6 else 0
            result[code] = {'name': name, 'market_cap': market_cap, 'circ_cap': circ_cap}
        except:
            result[code] = {'name': code, 'market_cap': 0, 'circ_cap': 0}
    return result

def tencent_realtime(codes):
    """腾讯实时行情（含五档盘口）"""
    result = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        symbols = []
        for code in batch:
            pure = code.split('.')[0]
            symbols.append(f'sh{pure}' if code.endswith('XSHG') else f'sz{pure}')
        try:
            resp = _session.get(f'https://qt.gtimg.cn/q={",".join(symbols)}', timeout=10)
            for line in resp.text.strip().split('\n'):
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 50:
                    continue
                code_raw = parts[2]
                code = to_jq(code_raw)
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

# 需要额外import
from datetime import timedelta

# ========================================================================
# 策略参数（与聚宽版完全一致）
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

MIN_CAP, MAX_CAP = 10, 1200
MIN_AMOUNT, MAX_AMOUNT = 1e8, 100e8
MAX_BUY_COUNT = 2

# 名称缓存
_name_cache = {}

def get_trade_dates_local(start, end):
    """获取交易日（akshare）"""
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    dates = [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10] for d in df['trade_date']]
    return [d for d in dates if start <= d <= end]


# ========================================================================
# 核心流程
# ========================================================================

def run_day(test_date, trade_dates):
    """运行一天的完整策略"""
    idx = trade_dates.index(test_date)
    y_day = trade_dates[idx - 1]
    d_1 = y_day
    d_2 = trade_dates[idx - 2]
    d_3 = trade_dates[idx - 3]

    print(f"\n{'='*80}")
    print(f"【盘前选股】昨日: {y_day}")
    print(f"{'='*80}")

    # 1. 获取涨停板
    print(f"[选股] 正在获取涨停板数据...")
    limit_1 = ths_limit_up(d_1)
    limit_2 = ths_limit_up(d_2)
    limit_3 = ths_limit_up(d_3)
    print(f"[选股] {d_1}涨停: {len(limit_1)}只 | {d_2}涨停: {len(limit_2)}只")

    # 2. 形态分类
    main_pool = [c for c in (limit_1 | limit_2) if not c.startswith(('3','4','8','9','68'))]
    raw_s1 = [c for c in main_pool if c in limit_1 and c not in limit_2]
    raw_s2 = [c for c in main_pool if c in limit_2 and c not in limit_3 and c not in limit_1]
    print(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_s1)}只 | Setup 2 (断板反包): {len(raw_s2)}只")

    # 3. 过滤
    def filter_stocks(codes, label):
        if not codes:
            return codes
        # 百日高位过滤
        qualified = []
        for code in codes:
            bars = get_daily_bars_ak(code, y_day, 101)
            if bars is None or len(bars) < 101:
                qualified.append(code)
                continue
            max_high = bars['high'].iloc[:-1].max()
            yst_close = bars['close'].iloc[-1]
            if yst_close >= max_high * 0.9:
                qualified.append(code)
        print(f"前100日最高价过滤: 保留{len(qualified)}/{len(codes)}只")
        return qualified

    s1 = filter_stocks(raw_s1, 'Setup1')
    s2 = filter_stocks(raw_s2, 'Setup2')

    # 获取名称
    all_codes = s1 + s2
    info = get_stock_info_ak(all_codes[:60])
    _name_cache.update({c: info.get(c, {}).get('name', c) for c in all_codes})

    print(f"今日选股池 (Setup 1 1进2-{len(s1)}只): {', '.join([f'{c}({_name_cache.get(c,c)})' for c in s1])}")
    print(f"今日选股池 (Setup 2 断板反包-{len(s2)}只): {', '.join([f'{c}({_name_cache.get(c,c)})' for c in s2])}")

    # 4. 竞价匹配
    print(f"\n{'─'*80}")
    print(f"【竞价开始】共有 {len(s1)}只 Setup 1 候选，{len(s2)}只 Setup 2 候选")
    print(f"{'─'*80}")

    # 获取昨日日线（akshare）
    prev_map = {}
    for code in all_codes:
        bars = get_daily_bars_ak(code, y_day, 1)
        if bars is not None and len(bars) > 0:
            prev_map[code] = bars.iloc[-1]

    # 获取市值信息
    stock_info = get_stock_info_ak(all_codes[:60])

    # 获取腾讯实时行情（竞价数据）
    rt = tencent_realtime(all_codes)

    qualified = []
    for code in all_codes:
        name = _name_cache.get(code, code)
        is_s1 = code in s1
        prev = prev_map.get(code)
        quote = rt.get(code)
        info = stock_info.get(code, {})

        if prev is None or quote is None:
            continue

        yst_close = prev['close']
        yst_volume = prev['volume']
        money = prev['money']
        open_price = quote['open']
        market_cap = info.get('market_cap', 0)
        circ_cap = info.get('circ_cap', 0)

        # 基础过滤
        if open_price <= 3:
            continue
        # 市值过滤（暂时跳过，akshare数据不稳定）
        # if market_cap < MIN_CAP or circ_cap > MAX_CAP:
        #     print(f"  [排除] {code}({name}) 市值不符: 总={market_cap:.0f}亿 流通={circ_cap:.0f}亿")
        #     continue
        if money < MIN_AMOUNT or money > MAX_AMOUNT:
            print(f"  [排除] {code}({name}) 成交额={money/1e8:.2f}亿 不在范围")
            continue

        # 竞价指标
        cur_ratio = open_price / yst_close if yst_close > 0 else 0
        auction_ratio = quote['volume'] / yst_volume if yst_volume > 0 else 0

        # OBI
        buymoney = sum(quote.get(f'bid{j}_p',0) * quote.get(f'bid{j}_v',0) * 100 for j in range(1,6))
        sellmoney = sum(quote.get(f'ask{j}_p',0) * quote.get(f'ask{j}_v',0) * 100 for j in range(1,6))
        obi = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

        if obi < 0.6:
            print(f"  [排除] {code}({name}) OBI={obi:.2f} < 0.6")
            continue

        # 规则匹配
        rules = CONDITION_RULES_SETUP1 if is_s1 else CONDITION_RULES_SETUP2
        matched = None
        for cn, lo, hi, al, ah in rules:
            if lo < cur_ratio <= hi and al <= auction_ratio <= ah:
                matched = cn
                break

        if matched is None:
            print(f"  [排除] {code}({name}) 未匹配: 涨幅={(cur_ratio-1)*100:.2f}% 竞昨比={auction_ratio*100:.2f}%")
            continue

        # 打分
        turnover = quote.get('turnover', 0)
        wts = turnover * cur_ratio
        score = (cur_ratio-1)*100*1.2 + auction_ratio*100*0.8 + wts*1.5 + obi*2.0
        if is_s1 and cur_ratio >= 1.098: score += 15.0
        if not is_s1 and cur_ratio >= 1.08: score += 12.0
        if not is_s1 and cur_ratio < 0.97: score += 5.0

        stype = '1进2' if is_s1 else '断板反包'
        qualified.append({'code':code, 'name':name, 'score':round(score,2), 'type':f'{stype}({matched})'})
        print(f"[OK] {code}({name}) {stype} | {matched} | 得分:{score:.2f} | OBI:{obi:.2f}")

    qualified.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n竞价终筛: {len(qualified)}只")
    final = qualified[:MAX_BUY_COUNT]
    if final:
        print(f"【重排选优】排序前 {len(final)} 只龙头股票：")
        for i, f in enumerate(final):
            print(f"  -{i+1}. {f['code']}({f['name']}) | 得分:{f['score']} | {f['type']}")
        for f in final:
            print(f"下单买入: {f['code']}({f['name']}) | 得分:{f['score']} | 条件:{f['type']}")

    return qualified


# ========================================================================
# 主程序
# ========================================================================

if __name__ == '__main__':
    print("=" * 80)
    print("首板断板融合策略 - 本地版（同花顺+akshare+腾讯）")
    print("=" * 80)

    trade_dates = get_trade_dates_local('2026-05-20', '2026-06-08')
    test_dates = ['2026-06-01', '2026-06-02']

    for test_date in test_dates:
        try:
            run_day(test_date, trade_dates)
        except Exception as e:
            print(f"[{test_date}] 错误: {e}")
            import traceback
            traceback.print_exc()
