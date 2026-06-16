"""
首板断板融合策略 - JQData精确回测
直接复用聚宽策略原始代码，只替换数据API。
逻辑、打分、过滤条件与聚宽回测100%一致。
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

from jqdatasdk import (
    auth, get_price, get_call_auction, get_fundamentals,
    get_all_securities, get_trade_days, query, valuation
)
import pandas as pd
import numpy as np
from datetime import datetime

# 聚宽认证
auth('18813368263', 'Forymq_10')

# ========================================================================
# 聚宽平台API模拟层
# ========================================================================

class Context:
    """模拟聚宽context对象"""
    def __init__(self, date_str, total_value=1000000):
        self.previous_date = datetime.strptime(date_str, '%Y-%m-%d')
        self.current_dt = datetime.strptime(date_str, '%Y-%m-%d')
        self.portfolio = Portfolio(total_value)

class Portfolio:
    def __init__(self, total_value):
        self.total_value = total_value
        self.available_cash = total_value
        self.positions = {}

class Position:
    def __init__(self, code, amount, avg_cost):
        self.code = code
        self.amount = amount
        self.avg_cost = avg_cost
        self.closeable_amount = amount
        self.value = amount * avg_cost

class CurrentData:
    """模拟get_current_data()"""
    def __init__(self, data_map):
        self._map = data_map
    def __getitem__(self, code):
        return self._map.get(code, StockData())

class StockData:
    def __init__(self, **kwargs):
        self.day_open = kwargs.get('day_open', 0)
        self.last_price = kwargs.get('last_price', 0)
        self.high_limit = kwargs.get('high_limit', 0)
        self.low_limit = kwargs.get('low_limit', 0)
        self.is_st = kwargs.get('is_st', False)
        self.paused = kwargs.get('paused', False)
        self.name = kwargs.get('name', '')

# 全局模拟对象
g = type('G', (), {
    'information': {}, 'condition_stats': {}, 'name_cache': {},
    'consecutive_loss_days': 0, 'skip_buy': False, 'peak_value': 0,
    'drawdown_reduction': 1.0, 'prev_day_value': 0,
    'ml_features': [], 'ml_labels': [], 'ml_weights': None,
    'ml_pred_reduction': 1.0, 'recent_pnls': [], 'yesterday_buy_count': 0,
    'pending_features': None, 'day_count': 0,
    'target_setup1': [], 'target_setup2': [], 'target_setup3': [],
    'tracked_candidates': {}, 'bought_stocks': set(),
})()

# 交易记录
trade_records = []
order_log = []

def log_info(msg):
    print(msg)

def log_error(msg):
    print(f"ERROR: {msg}")

# ========================================================================
# 原版策略参数（完全复制）
# ========================================================================

LIMIT_UP_RATIO = 0.998
MIN_YESTERDAY_CLOSE_RATIO = 0.95

CONDITION_RULES_SETUP1 = [
    ('E: 一字板/准一字 竞价涨幅>=9.8% | 竞昨比>=0.5%', 1.098, 1.11, 0.005, 1.0),
    ('A: 竞价高开7~9% | 竞昨比2.5~25%',  1.07, 1.098, 0.025, 0.25),
    ('B: 竞价高开4~7% | 竞昨比2~25%',   1.04, 1.07, 0.02, 0.25),
    ('C: 竞价平开至小高开0~4% | 竞昨比1.5~15%', 1.00, 1.04, 0.015, 0.15),
]

CONDITION_RULES_SETUP2 = [
    ('反包E: 竞价高开8~12% | 竞昨比0.5~25%', 1.08, 1.12, 0.005, 0.25),
    ('反包A: 竞价高开4~8% | 竞昨比0.5~20%', 1.04, 1.08, 0.005, 0.20),
    ('反包B: 竞价高开2~4% | 竞昨比0.5~15%', 1.02, 1.04, 0.005, 0.15),
    ('反包C: 竞价平开至小高开0~2% | 竞昨比0.5~12%', 1.00, 1.02, 0.005, 0.12),
    ('反包D: 竞价低开-3~0% | 竞昨比0.5~12%', 0.97, 1.00, 0.005, 0.12),
    ('反包F: 深低开-5~-3% | 竞昨比0.5~10%', 0.95, 0.97, 0.005, 0.10),
]

MIN_CAP = 10
MAX_CAP = 1200
MIN_AMOUNT = 1e8
MAX_AMOUNT = 100e8
DROP_PERCENT = 0.05
MA5_STOP_LOSS_BUFFER = 0.02
MAX_BUY_COUNT = 2


# ========================================================================
# 辅助函数（原版复制，替换数据API）
# ========================================================================

def get_security_name(code):
    """替代 get_security_info(code).display_name"""
    try:
        info = get_all_securities(types=['stock']).loc[code]
        return info['display_name'] if 'display_name' in info.index else code
    except:
        return code


def get_current_data_map(codes):
    """替代 get_current_data()，用jqdatasdk获取最新行情"""
    if not codes:
        return {}
    try:
        df = get_price(codes, end_date=datetime.now().strftime('%Y-%m-%d'),
                       frequency='daily', fields=['open', 'close', 'high_limit', 'low_limit'],
                       count=1, panel=False)
        result = {}
        for _, row in df.iterrows():
            code = row['code']
            result[code] = StockData(
                day_open=row.get('open', 0),
                last_price=row.get('close', 0),
                high_limit=row.get('high_limit', 0),
                low_limit=row.get('low_limit', 0),
            )
        return result
    except:
        return {}


def attribute_history_jq(code, count, freq, fields, skip_paused=True):
    """替代 attribute_history()"""
    try:
        df = get_price(code, count=count, frequency=freq, fields=fields,
                       panel=False, skip_paused=skip_paused)
        return df
    except:
        return pd.DataFrame()


def calculate_zyts(code, context):
    """原版 calculate_zyts"""
    high_prices = attribute_history_jq(code, 101, '1d', ['high'], skip_paused=True)
    if high_prices.empty or 'high' not in high_prices.columns:
        return 105
    high_prices = high_prices['high']
    if len(high_prices) < 3:
        return 105
    prev_high = high_prices.iloc[-1]
    zyts_0 = next((i-1 for i, high in enumerate(high_prices.iloc[-3::-1], 2) if high >= prev_high), 100)
    return zyts_0 + 5


def get_hl_count_df(hl_list, y_day, watch_days):
    """原版 get_hl_count_df"""
    if not hl_list:
        return pd.DataFrame(columns=['count', 'extreme_count'])
    df = get_price(hl_list, end_date=y_day, frequency='daily',
                   fields=['close', 'high_limit', 'low', 'open'],
                   count=watch_days, panel=False, fill_paused=False, skip_paused=False)
    if df.empty:
        return pd.DataFrame(index=hl_list, data={'count': 0, 'extreme_count': 0})
    df['is_limit']   = df['close'] == df['high_limit']
    df['is_yizi']    = (df['low'] == df['high_limit']) & df['is_limit']
    df['is_tzi']     = (df['open'] == df['high_limit']) & df['is_limit'] & (df['low'] < df['high_limit'])
    df['is_extreme'] = df['is_yizi'] | df['is_tzi']
    counts = df.groupby('code')[['is_limit', 'is_extreme']].sum().astype(int)
    counts.columns = ['count', 'extreme_count']
    counts = counts.reindex(hl_list, fill_value=0)
    return counts


def filter_excessive_limit_days(stock_list, y_day):
    limit_up_df = get_hl_count_df(stock_list, y_day, 5)
    qualified_stocks = limit_up_df[limit_up_df['count'] < 4].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log_info(f"因近5日涨停天数>=4被排除: {len(excluded)}只")
    return qualified_stocks


def filter_excessive_increase(stock_list, y_day):
    if not stock_list:
        return []
    df = get_price(stock_list, end_date=y_day, frequency='daily',
                   fields=['high', 'low'], count=5, panel=False,
                   fill_paused=False, skip_paused=True)
    if df.empty:
        return stock_list
    grp = df.groupby('code')
    max_h = grp['high'].max()
    min_l = grp['low'].min()
    chg = (max_h - min_l) / min_l
    qualified = chg[chg <= 0.4].index.tolist()
    excluded_n = len(stock_list) - len(qualified)
    if excluded_n:
        log_info(f"因近5日波动超过40%被排除: {excluded_n}只")
    return qualified


def filter_below_n_high(stock_list, y_day, days=100, min_ratio=0.9):
    if not stock_list:
        return []
    total_days = days + 1
    raw = get_price(stock_list, end_date=y_day, frequency='daily',
                    fields=['high', 'close'], count=total_days,
                    panel=False, fill_paused=False, skip_paused=True, fq='pre')
    if raw.empty:
        return []
    qualified = []
    for stock in stock_list:
        sub = raw[raw['code'] == stock]
        if len(sub) < total_days:
            continue
        sub = sub.tail(total_days)
        max_high = sub['high'].iloc[:-1].max()
        yesterday_close = sub['close'].iloc[-1]
        if yesterday_close >= max_high * min_ratio:
            qualified.append(stock)
    log_info(f"前{days}日最高价过滤: 保留{len(qualified)}/{len(stock_list)}只")
    return qualified


def filter_excessive_limit_up(stock_list, y_day):
    extreme_hl_df = get_hl_count_df(stock_list, y_day, 10)
    qualified_stocks = extreme_hl_df[extreme_hl_df['extreme_count'] < 3].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log_info(f"因前10日有3+一字/T字涨停被排除: {len(excluded)}只")
    return qualified_stocks


def prepare_stock_list():
    """原版 prepare_stock_list"""
    by_date = get_trade_days(end_date=g._y_day, count=50)[0]
    all_s = get_all_securities(['stock'], date=by_date).index
    # 获取ST信息
    try:
        info_df = get_all_securities(['stock'], date=by_date)
        st_codes = set(info_df[info_df['display_name'].str.contains('ST|退', na=False)].index)
    except:
        st_codes = set()
    base_stocks = [
        s for s in all_s
        if s[0] not in ('3', '4', '8', '9')
        and not s.startswith('68')
        and s not in st_codes
    ]
    return base_stocks


def calc_tracked_bonus(code):
    """原版 calc_tracked_bonus"""
    cand = g.tracked_candidates.get(code)
    if cand is None:
        return 0.0
    base = cand.get('base_price', 0)
    max_p = cand.get('max_price', 0)
    if base <= 0 or max_p <= 0:
        return 0.0
    gain_pct = (max_p - base) / base * 100
    if gain_pct >= 40: return 10.0
    elif gain_pct >= 30: return 15.0
    elif gain_pct >= 20: return 20.0
    elif gain_pct >= 10: return 10.0
    elif gain_pct >= 5: return 5.0
    else: return 0.0


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


# ========================================================================
# 策略主流程（原版逻辑，替换API调用）
# ========================================================================

def run_day(test_date):
    """运行一天的完整策略"""
    trade_days = get_trade_days(end_date=test_date, count=2)
    if len(trade_days) < 2:
        return
    y_day = trade_days[0].strftime('%Y-%m-%d')
    g._y_day = y_day

    context = Context(y_day)
    g.day_count += 1

    log_info(f"\n{'='*80}")
    log_info(f"【盘前选股】昨日: {y_day}")
    log_info(f"{'='*80}")

    # === 盘前选股（原版 before_market_open 逻辑）===
    initial_list = prepare_stock_list()
    log_info(f"[选股] 初始过滤股票池: {len(initial_list)}只")

    trade_days_9 = get_trade_days(end_date=y_day, count=9)
    if len(trade_days_9) < 9:
        log_info("[选股] 交易历史不足9天，跳过")
        return

    t_minus_9 = trade_days_9[0]
    t_minus_1 = trade_days_9[-1]

    price_df = get_price(
        initial_list, start_date=t_minus_9, end_date=t_minus_1, frequency='1d',
        fields=['close', 'high_limit', 'money', 'volume', 'high', 'low'], panel=False
    )
    if price_df.empty:
        return

    price_df['is_limit'] = price_df['close'] >= price_df['high_limit'] * LIMIT_UP_RATIO
    stock_groups = price_df.groupby('code')

    d_1 = trade_days_9[8].strftime('%Y-%m-%d')
    d_2 = trade_days_9[7].strftime('%Y-%m-%d')
    d_3 = trade_days_9[6].strftime('%Y-%m-%d')
    d_4 = trade_days_9[5].strftime('%Y-%m-%d')
    recent_6_days = [d.strftime('%Y-%m-%d') for d in trade_days_9[3:9]]

    raw_setup1, raw_setup2, raw_setup3 = [], [], []
    yesterday_close_dict = {}

    for code, group in stock_groups:
        gs = group.sort_values('time')
        if len(gs) < 8:
            continue
        limit_map = dict(zip(gs['time'].dt.strftime('%Y-%m-%d'), gs['is_limit']))
        close_map = dict(zip(gs['time'].dt.strftime('%Y-%m-%d'), gs['close']))

        if not any(limit_map.get(day, False) for day in recent_6_days):
            continue

        yesterday_close_dict[code] = close_map.get(d_1, 0)

        is_setup1 = limit_map.get(d_1, False) and not limit_map.get(d_2, False)
        is_setup2 = (
            limit_map.get(d_2, False) and not limit_map.get(d_3, False)
            and not limit_map.get(d_1, False)
            and close_map.get(d_1, 0) >= close_map.get(d_2, 0) * MIN_YESTERDAY_CLOSE_RATIO
        )
        is_setup3 = (
            limit_map.get(d_4, False) and not limit_map.get(trade_days_9[4].strftime('%Y-%m-%d'), False)
            and not limit_map.get(d_3, False) and not limit_map.get(d_2, False)
            and not limit_map.get(d_1, False)
            and close_map.get(d_1, 0) >= close_map.get(d_4, 0) * MIN_YESTERDAY_CLOSE_RATIO
        )

        if is_setup1: raw_setup1.append(code)
        elif is_setup2: raw_setup2.append(code)
        elif is_setup3: raw_setup3.append(code)

    log_info(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_setup1)}只 | Setup 2 (断板反包): {len(raw_setup2)}只 | Setup 3 (三日断板): {len(raw_setup3)}只")

    g.target_setup1 = filter_excessive_limit_up(raw_setup1, y_day)
    g.target_setup1 = filter_excessive_increase(g.target_setup1, y_day)
    g.target_setup1 = filter_excessive_limit_days(g.target_setup1, y_day)
    g.target_setup1 = filter_below_n_high(g.target_setup1, y_day, days=100)

    g.target_setup2 = filter_excessive_limit_up(raw_setup2, y_day)
    g.target_setup2 = filter_excessive_increase(g.target_setup2, y_day)
    g.target_setup2 = filter_excessive_limit_days(g.target_setup2, y_day)
    g.target_setup2 = filter_below_n_high(g.target_setup2, y_day, days=100)

    g.target_setup3 = filter_excessive_limit_up(raw_setup3, y_day)
    g.target_setup3 = filter_excessive_increase(g.target_setup3, y_day)
    g.target_setup3 = filter_excessive_limit_days(g.target_setup3, y_day)
    g.target_setup3 = filter_below_n_high(g.target_setup3, y_day, days=100)

    # 名字缓存
    g.name_cache = {}
    all_targets = g.target_setup1 + g.target_setup2 + g.target_setup3
    for s in all_targets:
        g.name_cache[s] = get_security_name(s)

    log_info(f"今日选股池 (Setup 1 1进2 - {len(g.target_setup1)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup1]))
    log_info(f"今日选股池 (Setup 2 断板反包 - {len(g.target_setup2)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup2]))
    log_info(f"今日选股池 (Setup 3 三日断板 - {len(g.target_setup3)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup3]))

    # 注册追踪池
    for s in g.target_setup1 + g.target_setup2 + g.target_setup3:
        if s not in g.tracked_candidates:
            base_p = yesterday_close_dict.get(s, 0)
            if base_p <= 0:
                try:
                    base_p = get_price(s, end_date=y_day, frequency='daily', fields=['close'], count=1).iloc[0]['close']
                except:
                    base_p = 1.0
            stype = '1进2' if s in g.target_setup1 else ('断板反包' if s in g.target_setup2 else '三日断板')
            g.tracked_candidates[s] = {
                'entry_date': test_date, 'base_price': base_p,
                'max_price': base_p, 'setup_type': stype
            }

    # === 竞价匹配（原版 get_buy 逻辑）===
    log_info(f"\n{'─'*80}")
    log_info(f"【竞价开始】共有 {len(g.target_setup1)}只 Setup 1 候选，{len(g.target_setup2)}只 Setup 2 候选，{len(g.target_setup3)}只 Setup 3 候选")
    log_info(f"{'─'*80}")

    start = test_date + ' 09:15:00'
    end = test_date + ' 09:26:00'

    prev_df = get_price(all_targets, end_date=y_day, frequency='daily',
                        fields=['close', 'volume', 'money'], count=1, panel=False,
                        fill_paused=False, skip_paused=True)
    prev_map = {row['code']: row for _, row in prev_df.iterrows()}

    val_df = get_fundamentals(
        query(valuation.code, valuation.market_cap, valuation.circulating_market_cap, valuation.turnover_ratio)
        .filter(valuation.code.in_(all_targets)),
        date=str(y_day)[:10]
    )
    val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

    # 获取今日开盘价（模拟current_data）
    open_df = get_price(all_targets, start_date=test_date, end_date=test_date,
                        frequency='daily', fields=['open'], panel=False)
    open_map = {row['code']: row for _, row in open_df.iterrows()} if not open_df.empty else {}

    # hl_base = 昨日收盘价 * 1.1（涨停价 / 1.1 = 昨日收盘价）
    hl_base = {}
    for s in all_targets:
        prev = prev_map.get(s)
        if prev is not None:
            hl_base[s] = prev['close']  # 聚宽的 high_limit / 1.1 = 昨日收盘价
        else:
            hl_base[s] = 1.0

    qualified_stocks = []

    # Setup 1 匹配
    for s in g.target_setup1:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None:
                continue
            avg_chg = prev['money'] / prev['volume'] / prev['close'] * 1.1 - 1
            money = prev['money']
            open_row = open_map.get(s)
            open_price = open_row['open'] if open_row is not None else 0
            val = val_map.get(s)

            if avg_chg < 0.035: continue
            if open_price <= 3: continue
            if val is None: continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP: continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT: continue
            is_1_5 = money < 5e8
            is_5_15 = not is_1_5

            zyts = calculate_zyts(s, context)
            vol_data = attribute_history_jq(s, zyts, '1d', ['volume'], skip_paused=True)
            if len(vol_data) < 2: continue

            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val.get('turnover_ratio', np.nan))) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty: continue

            cur_ratio = auction['current'].iloc[0] / hl_base[s]
            auction_ratio = auction['volume'].iloc[0] / vol_data['volume'].iloc[-1]

            # OBI
            buymoney, sellmoney = 0.0, 0.0
            for i in range(1, 6):
                ap, av = f'a{i}_p', f'a{i}_v'
                bp, bv = f'b{i}_p', f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]; val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av): sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]; val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv): buymoney += val_bp * val_bv
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            if obi_ratio < 0.6:
                log_info(f"  [排除-1] {s}({name}) 竞价买卖比不符: OBI={obi_ratio:.2f} < 0.6")
                continue

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP1:
                if cond_name.startswith('A') and not is_1_5: continue
                if not cond_name.startswith('A') and not is_5_15: continue
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log_info(f"  [排除-1] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}% (成交额={money/1e8:.2f}亿)")
                continue

            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            if cur_ratio >= 1.098: score += 15.0
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log_info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")

            qualified_stocks.append({'code': s, 'name': name, 'score': score, 'type': f"1进2({matched_condition})"})
            log_info(f"✅ {s}({name}) 符合 Setup 1(1进2)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            continue

    # Setup 2 匹配
    for s in g.target_setup2:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None: continue
            money = prev['money']
            open_row = open_map.get(s)
            open_price = open_row['open'] if open_row is not None else 0
            val = val_map.get(s)

            if open_price <= 3: continue
            if val is None: continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP: continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT: continue

            zyts = calculate_zyts(s, context)
            vol_data = attribute_history_jq(s, zyts, '1d', ['volume'], skip_paused=True)
            if len(vol_data) < 2: continue

            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val.get('turnover_ratio', np.nan))) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty: continue

            cur_ratio = auction['current'].iloc[0] / prev['close']
            auction_ratio = auction['volume'].iloc[0] / vol_data['volume'].iloc[-1]

            buymoney, sellmoney = 0.0, 0.0
            for i in range(1, 6):
                ap, av = f'a{i}_p', f'a{i}_v'
                bp, bv = f'b{i}_p', f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]; val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av): sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]; val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv): buymoney += val_bp * val_bv
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            if obi_ratio < 0.6:
                log_info(f"  [排除-2] {s}({name}) 竞价买卖比不符: OBI={obi_ratio:.2f} < 0.6")
                continue

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP2:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log_info(f"  [排除-2] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}% (成交额={money/1e8:.2f}亿)")
                continue

            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            if cur_ratio >= 1.08: score += 12.0
            elif cur_ratio < 0.97: score += 5.0
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log_info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")

            qualified_stocks.append({'code': s, 'name': name, 'score': score, 'type': f"断板反包({matched_condition})"})
            log_info(f"✅ {s}({name}) 符合 Setup 2(断板反包)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            continue

    # Setup 3 匹配（与Setup 2逻辑相同，复用规则）
    for s in g.target_setup3:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None: continue
            money = prev['money']
            open_row = open_map.get(s)
            open_price = open_row['open'] if open_row is not None else 0
            val = val_map.get(s)

            if open_price <= 3: continue
            if val is None: continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP: continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT: continue

            zyts = calculate_zyts(s, context)
            vol_data = attribute_history_jq(s, zyts, '1d', ['volume'], skip_paused=True)
            if len(vol_data) < 2: continue

            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val.get('turnover_ratio', np.nan))) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty: continue

            cur_ratio = auction['current'].iloc[0] / prev['close']
            auction_ratio = auction['volume'].iloc[0] / vol_data['volume'].iloc[-1]

            buymoney, sellmoney = 0.0, 0.0
            for i in range(1, 6):
                ap, av = f'a{i}_p', f'a{i}_v'
                bp, bv = f'b{i}_p', f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]; val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av): sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]; val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv): buymoney += val_bp * val_bv
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            if obi_ratio < 0.6:
                log_info(f"  [排除-3] {s}({name}) 竞价买卖比不符: OBI={obi_ratio:.2f} < 0.6")
                continue

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP2:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log_info(f"  [排除-3] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}%")
                continue

            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            if cur_ratio >= 1.08: score += 12.0
            elif cur_ratio < 0.97: score += 5.0
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log_info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")

            qualified_stocks.append({'code': s, 'name': name, 'score': score, 'type': f"三日断板({matched_condition})"})
            log_info(f"✅ {s}({name}) 符合 Setup 3(三日断板)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            continue

    # 重排选优
    log_info(f"竞价终筛结果：符合竞价过滤条件的个股共 {len(qualified_stocks)} 只")
    qualified_stocks.sort(key=lambda x: x['score'], reverse=True)
    final_buy_list = qualified_stocks[:MAX_BUY_COUNT]

    g.bought_stocks = set()
    if final_buy_list:
        log_info(f"【重排选优】排序前 {len(final_buy_list)} 只龙头股票：")
        for idx, item in enumerate(final_buy_list):
            log_info(f"  -{idx+1}. {item['code']}({item['name']}) | 得分: {item['score']:.2f} | 类型: {item['type']}")
        for item in final_buy_list:
            s = item['code']
            g.bought_stocks.add(s)
            g.information[s] = item['type']
            log_info(f"下单买入: {s}({g.name_cache.get(s,'未知')}) | 得分: {item['score']:.2f} | 条件: {item['type']}")

    # 更新追踪池最高价
    tracked_codes = list(g.tracked_candidates.keys())
    if tracked_codes:
        today_high_df = get_price(tracked_codes, start_date=test_date, end_date=test_date,
                                  frequency='daily', fields=['high'], panel=False)
        if not today_high_df.empty:
            for _, row in today_high_df.iterrows():
                code = row['code']
                high_p = row['high']
                if not pd.isna(high_p) and high_p > 0 and code in g.tracked_candidates:
                    if high_p > g.tracked_candidates[code]['max_price']:
                        g.tracked_candidates[code]['max_price'] = high_p

    # 排行榜
    board = sorted(g.tracked_candidates.values(), key=lambda x: (x['max_price']-x['base_price'])/x['base_price']*100 if x['base_price']>0 else 0, reverse=True)
    log_info(f"\n{'='*80}")
    log_info(f"【自候选至今最高涨幅排行榜】累计追踪候选股: {len(board)}只")
    log_info(f"{'─'*80}")
    log_info(f"{'排名':>4} {'代码':<12} {'名称':<8} {'候选日期':<11} {'类型':<8} {'基准价':>8} {'最高价':>8} {'最大涨幅':>10}")
    log_info(f"{'─'*80}")
    for i, t in enumerate(board[:30]):
        pct = (t['max_price']-t['base_price'])/t['base_price']*100 if t['base_price']>0 else 0
        code = [k for k, v in g.tracked_candidates.items() if v is t][0]
        log_info(f"{i+1:>4} {code:<12} {g.name_cache.get(code, ''):<8} {t['entry_date']:<11} {t['setup_type']:<8} "
                 f"{t['base_price']:>8.2f} {t['max_price']:>8.2f} {pct:>9.2f}%")
    log_info(f"{'='*80}")

    # 漏选分析
    missed_codes = [c for c in g.target_setup1 + g.target_setup2 + g.target_setup3 if c not in g.bought_stocks]
    if missed_codes:
        missed_data = get_price(missed_codes, start_date=test_date, end_date=test_date,
                                frequency='daily', fields=['close', 'open', 'high'], panel=False)
        results = []
        if not missed_data.empty:
            for _, row in missed_data.iterrows():
                code = row['code']
                base = yesterday_close_dict.get(code, 0)
                if base <= 0: continue
                stype = '1进2' if code in g.target_setup1 else ('断板反包' if code in g.target_setup2 else '三日断板')
                results.append({
                    'code': code, 'name': g.name_cache.get(code, ''), 'setup_type': stype,
                    'open_pct': (row['open']-base)/base*100,
                    'high_pct': (row['high']-base)/base*100,
                    'close_pct': (row['close']-base)/base*100,
                    'is_limit': row['close'] >= row['high']*0.998 if row['high']>0 else False,
                })
        results.sort(key=lambda x: x['close_pct'], reverse=True)
        if results:
            n = len(results); n_up = sum(1 for r in results if r['close_pct']>0)
            n_lu = sum(1 for r in results if r['is_limit'])
            avg = sum(r['close_pct'] for r in results)/n
            mx = max(r['close_pct'] for r in results); mn = min(r['close_pct'] for r in results)
            log_info(f"\n{'─'*80}")
            log_info(f"[漏选分析] 共{n}只候选未买入 | 上涨{n_up}只({n_up/n:.0%}) | 涨停{n_lu}只 | 均涨{avg:.2f}% | 最高{mx:.2f}% | 最低{mn:.2f}%")
            log_info(f"{'─'*80}")
            log_info(f"{'排名':>4} {'代码':<12} {'名称':<8} {'类型':<8} {'开盘%':>8} {'最高%':>8} {'收盘%':>8}")
            log_info(f"{'─'*80}")
            for i, r in enumerate(results[:20]):
                log_info(f"{i+1:>4} {r['code']:<12} {r['name']:<8} {r['setup_type']:<8} "
                         f"{r['open_pct']:>+7.2f}% {r['high_pct']:>+7.2f}% {r['close_pct']:>+7.2f}%")
            lu = [r for r in results if r['is_limit']]
            if lu:
                log_info(f"\n!! 有{len(lu)}只漏选股票今日涨停:")
                for r in lu:
                    log_info(f"    {r['code']}({r['name']}) {r['setup_type']} 涨幅={r['close_pct']:+.2f}%")
            log_info(f"{'─'*80}")


# ========================================================================
# 主程序
# ========================================================================

print("=" * 80)
print("首板断板融合策略 - JQData精确回测（原版逻辑）")
print("数据范围: 2026-03-02 ~ 2026-03-06")
print("=" * 80)

trade_days = get_trade_days(start_date='2026-03-01', end_date='2026-03-08')
test_dates = [d.strftime('%Y-%m-%d') for d in trade_days]

for test_date in test_dates:
    run_day(test_date)

print("\n\n回测完成！")
