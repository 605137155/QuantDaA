# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略核心：竞价结束后重新排分，开盘半仓买入4个仓位，尾盘清理前一日可卖仓位
# 交易逻辑：
# 1. 盘前基于上一个交易日完整日线数据生成基础候选池
# 2. 开盘时结合集合竞价/开盘信息重新排序，使用半仓买入前4只
# 3. 尾盘14:50卖出前一日或更早买入的可卖仓位，保留当天新买入仓位

from jqdata import *
import numpy as np
import datetime
from datetime import datetime as dt


# ==================== 1. 初始化设置 ====================
def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    log.set_level('order', 'error')

    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0.001,
        open_commission=0.0003,
        close_commission=0.0003,
        close_today_commission=0,
        min_commission=5
    ), type='stock')

    g.max_stocks = 4
    g.half_position_ratio = 0.5
    g.base_pool_size = 100
    g.open_rank_pool_size = 30
    g.pre_rank_candidates = []
    g.open_rank_list = []
    g.target_list = []
    g.last_buy_date = None

    run_daily(open_rank_and_buy, time='09:40')
    run_daily(sell_previous_positions, time='14:50')


# ==================== 2. 盘前基础候选池 ====================
def before_trading_start(context):
    previous_date = context.previous_date
    if isinstance(previous_date, str):
        yesterday = previous_date
        date_obj = dt.strptime(previous_date, '%Y-%m-%d').date()
    else:
        date_obj = previous_date
        yesterday = date_obj.strftime('%Y-%m-%d')

    log.info(f"--- 正在执行 {yesterday} 的盘前基础复盘选股 ---")

    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    if not filtered_stocks:
        clear_daily_candidates("基础股票池为空")
        return

    hot_candidate_pool = get_hot_candidate_pool(filtered_stocks, g.base_pool_size)
    if not hot_candidate_pool:
        clear_daily_candidates("无法获取成交额热度池")
        return

    q = query(
        valuation.code,
        valuation.circulating_market_cap
    ).filter(
        valuation.code.in_(hot_candidate_pool)
    )
    df_cap = get_fundamentals(q, date=yesterday)
    if df_cap is None or df_cap.empty:
        clear_daily_candidates("无法获取流通市值数据")
        return

    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))
    candidate_pool = [code for code in hot_candidate_pool if code in cap_dict]
    if not candidate_pool:
        clear_daily_candidates("候选股票池为空")
        return

    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))
    amount_ranks = {code: idx + 1 for idx, code in enumerate(hot_candidate_pool)}

    h_open = history(65, '1d', 'open', candidate_pool, df=False)
    h_close = history(65, '1d', 'close', candidate_pool, df=False)
    h_high = history(65, '1d', 'high', candidate_pool, df=False)
    h_low = history(65, '1d', 'low', candidate_pool, df=False)
    h_volume = history(65, '1d', 'volume', candidate_pool, df=False)

    scored_candidates = []
    for code in candidate_pool:
        series_map = {
            'open': get_history_series(h_open, code),
            'close': get_history_series(h_close, code),
            'high': get_history_series(h_high, code),
            'low': get_history_series(h_low, code),
            'volume': get_history_series(h_volume, code),
        }
        valid_len = min(len(values) for values in series_map.values())
        if valid_len < 21:
            continue

        open_seq = series_map['open'][-valid_len:]
        close_seq = series_map['close'][-valid_len:]
        high_seq = series_map['high'][-valid_len:]
        low_seq = series_map['low'][-valid_len:]
        vol_seq = series_map['volume'][-valid_len:]

        metrics = calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq)
        if not metrics:
            continue

        rank_no = amount_ranks.get(code, 999)
        heat_score = max(0, int(25 * (100 - min(rank_no, 100)) / 99.0))
        market_cap_score = calc_market_cap_score(cap_dict.get(code, 0.0))
        volume_price_score = calc_volume_price_score(metrics)
        position_score = calc_position_score(metrics)
        risk_penalty = calc_risk_penalty(metrics)

        base_score = heat_score + market_cap_score + volume_price_score + position_score + risk_penalty
        base_score = max(0, min(100, base_score))

        scored_candidates.append({
            'code': code,
            'name': name_dict.get(code, code),
            'base_score': base_score,
            'previous_close': metrics['previous_close'],
            'day_pct': metrics['day_pct'],
            'day_amplitude': metrics['day_amplitude'],
            'heat_score': heat_score,
            'market_cap_score': market_cap_score,
            'volume_price_score': volume_price_score,
            'position_score': position_score,
            'risk_penalty': risk_penalty,
        })

    scored_candidates = sorted(scored_candidates, key=lambda x: x['base_score'], reverse=True)
    g.pre_rank_candidates = scored_candidates[:g.open_rank_pool_size]
    g.open_rank_list = []
    g.target_list = []

    log.info("盘前基础分排名前10：")
    for i, item in enumerate(g.pre_rank_candidates[:10]):
        log.info(
            f"Base Top {i + 1}: {item['code']} ({item['name']}) - "
            f"基础分: {item['base_score']} | 昨日涨幅: {item['day_pct']:.2f}% | "
            f"昨日振幅: {item['day_amplitude']:.2f}%"
        )


# ==================== 3. 竞价后重排并半仓买入 ====================
def open_rank_and_buy(context):
    """集合竞价结束后重新排分，开盘后只买当前价高于开盘价的红柱票。"""
    current_date = context.current_dt.date()
    if g.last_buy_date == current_date:
        log.info("【开盘跳过】今日已执行过开盘买入")
        return
    if not g.pre_rank_candidates:
        log.info("【开盘跳过】无盘前候选池")
        return

    ranked = build_open_rank(context, g.pre_rank_candidates)
    g.open_rank_list = ranked
    g.target_list = [item['code'] for item in ranked[:g.max_stocks]]

    log.info("竞价后重新排分榜前10：")
    for i, item in enumerate(ranked[:10]):
        log.info(
            f"Open Top {i + 1}: {item['code']} ({item['name']}) - "
            f"总分: {item['open_score']:.2f} | 基础分: {item['base_score']:.2f} | "
            f"竞价分: {item['auction_score']:.2f} | 竞价涨幅: {item['auction_pct']:.2f}% | "
            f"竞价额: {item['auction_money'] / 10000.0:.0f}万"
        )

    buy_targets = []
    current_data = get_current_data()
    for item in ranked:
        if len(buy_targets) >= g.max_stocks:
            break

        stock = item['code']
        if not is_buyable_stock(stock, current_data, '开盘'):
            continue
        if not is_red_body_stock(stock, current_data):
            continue

        buy_targets.append(stock)

    if not buy_targets:
        log.info("【开盘跳过】重排榜中无可买入的红柱票")
        g.last_buy_date = current_date
        return

    target_cash = min(
        context.portfolio.total_value * g.half_position_ratio,
        context.portfolio.available_cash * 0.99
    )
    cash_per_stock = target_cash / len(buy_targets)
    if cash_per_stock <= 0:
        log.info("【开盘跳过】可用资金不足")
        g.last_buy_date = current_date
        return

    for stock in buy_targets:
        order_value(stock, cash_per_stock)
        price_info = current_data[stock]
        log.info(
            f"【开盘买入】{stock} 红柱确认，开盘价 {get_open_price(price_info):.2f}，"
            f"当前价 {get_current_price(price_info):.2f}，半仓分配买入资金 {cash_per_stock:.2f}"
        )

    g.last_buy_date = current_date


def build_open_rank(context, base_candidates):
    """用集合竞价/开盘信息对盘前候选池重新排序。"""
    today = context.current_dt.date().strftime('%Y-%m-%d')
    codes = [item['code'] for item in base_candidates]
    auction_map = get_today_auction_map(codes, today)
    current_data = get_current_data()

    ranked = []
    for item in base_candidates:
        code = item['code']
        price_info = current_data[code]
        auction = auction_map.get(code, {})
        auction_score, auction_pct, auction_money = calc_auction_score(item, auction, price_info)
        open_score = item['base_score'] + auction_score

        ranked_item = dict(item)
        ranked_item.update({
            'auction_score': auction_score,
            'auction_pct': auction_pct,
            'auction_money': auction_money,
            'open_score': max(0.0, min(130.0, open_score)),
        })
        ranked.append(ranked_item)

    return sorted(ranked, key=lambda x: x['open_score'], reverse=True)


def get_today_auction_map(codes, today):
    """安全获取当日集合竞价数据；不可用时返回空映射，后续用开盘价兜底。"""
    result = {}
    if not codes:
        return result

    try:
        df = get_call_auction(
            codes,
            start_date=today,
            end_date=today,
            fields=['time', 'current', 'volume', 'money', 'b1_v', 'a1_v']
        )
    except Exception as exc:
        log.info(f"【竞价数据】get_call_auction 不可用，使用开盘价兜底：{exc}")
        return result

    if df is None or len(df) == 0 or not hasattr(df, 'iterrows'):
        return result

    code_set = set(codes)
    for idx, row in df.iterrows():
        code = get_row_code(idx, row, code_set)
        if not code:
            continue
        result[code] = {
            'current': get_row_float(row, 'current'),
            'volume': get_row_float(row, 'volume'),
            'money': get_row_float(row, 'money'),
            'b1_v': get_row_float(row, 'b1_v'),
            'a1_v': get_row_float(row, 'a1_v'),
        }
    return result


def calc_auction_score(base_item, auction, price_info):
    """竞价分：偏好小幅高开、竞价成交活跃、买一量强于卖一量。"""
    previous_close = base_item['previous_close']
    auction_price = auction.get('current', 0.0)
    if auction_price <= 0:
        auction_price = get_current_price(price_info)

    auction_pct = (auction_price - previous_close) / previous_close * 100.0 if previous_close > 0 else 0.0
    auction_money = auction.get('money', 0.0)
    buy_volume = auction.get('b1_v', 0.0)
    sell_volume = auction.get('a1_v', 0.0)
    buy_sell_ratio = buy_volume / sell_volume if sell_volume > 0 else (3.0 if buy_volume > 0 else 0.0)

    score = 0.0

    # 开盘涨幅：小幅高开最友好，过高容易追高，低开则谨慎。
    if 0.5 <= auction_pct <= 4.0:
        score += 12
    elif 4.0 < auction_pct <= 7.0:
        score += 7
    elif 0.0 <= auction_pct < 0.5:
        score += 4
    elif auction_pct < -2.5:
        score -= 8
    elif auction_pct > 8.5:
        score -= 6

    # 竞价成交额：优先选择真实有资金参与的票。
    if auction_money >= 50000000:
        score += 8
    elif auction_money >= 20000000:
        score += 5
    elif auction_money >= 8000000:
        score += 2

    # 买卖盘强弱：买一量明显强于卖一量时加分。
    if buy_sell_ratio >= 2.0:
        score += 6
    elif buy_sell_ratio >= 1.2:
        score += 3

    if getattr(price_info, 'paused', False):
        score -= 20

    return score, auction_pct, auction_money


# ==================== 4. 尾盘清理前一日仓位 ====================
def sell_previous_positions(context):
    """尾盘卖出可卖仓位；当天新买入的股票因T+1不可卖，会自然保留。"""
    current_holdings = list(context.portfolio.positions.keys())
    if not current_holdings:
        return

    current_data = get_current_data()
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        if position.closeable_amount <= 0:
            continue
        if not is_sellable_stock(stock, current_data):
            continue

        order(stock, -position.closeable_amount)
        log.info(f"【尾盘卖出】{stock} 清理前一日可卖仓位 {position.closeable_amount}")


# ==================== 5. 通用辅助函数 ====================
def clear_daily_candidates(reason):
    g.pre_rank_candidates = []
    g.open_rank_list = []
    g.target_list = []
    log.info(f"{reason}，今日不生成竞价重排榜")


def get_hot_candidate_pool(filtered_stocks, limit):
    h_amount_all = history(1, '1d', 'money', filtered_stocks, df=False)
    amount_all_dict = {}
    for code in filtered_stocks:
        amount_series = get_history_series(h_amount_all, code)
        if len(amount_series) > 0:
            amount_all_dict[code] = float(amount_series[-1])

    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    return [item[0] for item in sorted_by_amount_all[:limit]]


def get_history_series(history_map, code):
    if history_map is None or code not in history_map:
        return []
    values = history_map[code]
    if values is None:
        return []
    return list(values)


def get_row_code(idx, row, code_set):
    for field in ['code', 'security']:
        if field in row and row[field] in code_set:
            return row[field]

    if isinstance(idx, tuple):
        for part in idx:
            if part in code_set:
                return part
    if idx in code_set:
        return idx
    return None


def get_row_float(row, field):
    if field not in row:
        return 0.0
    value = row[field]
    if value is None:
        return 0.0
    try:
        if np.isnan(value):
            return 0.0
    except TypeError:
        pass
    return float(value)


def get_current_price(price_info):
    for field in ['last_price', 'day_open']:
        value = getattr(price_info, field, None)
        if value is not None and value > 0:
            return value
    return 0.0


def get_open_price(price_info):
    value = getattr(price_info, 'day_open', None)
    if value is not None and value > 0:
        return value
    return 0.0


def is_red_body_stock(stock, current_data):
    price_info = current_data[stock]
    open_price = get_open_price(price_info)
    current_price = get_current_price(price_info)

    if open_price <= 0 or current_price <= 0:
        log.info(f"【开盘跳过】{stock} 无法获取开盘价或当前价，不能确认红柱")
        return False
    if current_price <= open_price:
        log.info(
            f"【开盘跳过】{stock} 当前不是红柱，开盘价 {open_price:.2f}，"
            f"当前价 {current_price:.2f}"
        )
        return False
    return True


def is_sellable_stock(stock, current_data):
    price_info = current_data[stock]
    if price_info.paused:
        log.info(f"【尾盘卖出跳过】{stock} 停牌，无法卖出")
        return False

    last_price = get_current_price(price_info)
    low_limit = getattr(price_info, 'low_limit', None)
    if last_price <= 0 or low_limit is None:
        log.info(f"【尾盘卖出跳过】{stock} 价格数据无效，无法卖出")
        return False
    if last_price <= low_limit + 0.01:
        log.info(f"【尾盘卖出跳过】{stock} 已接近跌停，卖出成交风险较高")
        return False
    return True


def is_buyable_stock(stock, current_data, scene):
    price_info = current_data[stock]
    if price_info.paused:
        log.info(f"【{scene}跳过】{stock} 停牌，无法买入")
        return False

    last_price = get_current_price(price_info)
    high_limit = getattr(price_info, 'high_limit', None)
    low_limit = getattr(price_info, 'low_limit', None)
    if last_price <= 0 or high_limit is None or low_limit is None:
        log.info(f"【{scene}跳过】{stock} 价格数据无效，无法买入")
        return False
    if last_price >= high_limit - 0.01:
        log.info(f"【{scene}跳过】{stock} 已接近涨停，放弃买入")
        return False
    if last_price <= low_limit + 0.01:
        log.info(f"【{scene}跳过】{stock} 已接近跌停，放弃买入")
        return False
    return True


def filter_basic_stocks(all_stocks_df, date_obj):
    cutoff_date = date_obj - datetime.timedelta(days=90)
    display_names = all_stocks_df['display_name'].fillna('')

    df_filtered = all_stocks_df[
        (~display_names.str.startswith('ST')) &
        (~display_names.str.startswith('*ST')) &
        (~display_names.str.startswith('退')) &
        (all_stocks_df['start_date'] < cutoff_date)
    ]
    return df_filtered.index.tolist()


def calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq):
    if len(close_seq) < 21:
        return None

    c_t = close_seq[-1]
    o_t = open_seq[-1]
    h_t = high_seq[-1]
    l_t = low_seq[-1]
    v_t = vol_seq[-1]
    c_y = close_seq[-2]

    v_prev5 = vol_seq[-6:-1]
    avg5_vol = np.mean(v_prev5) if len(v_prev5) > 0 else 1.0
    vol_ratio_5 = v_t / avg5_vol if avg5_vol > 0 else 0.0

    recent5_close = close_seq[-5:]
    recent5_open = open_seq[-5:]
    recent5_vol = vol_seq[-5:]
    up_vol = sum(v for c, o, v in zip(recent5_close, recent5_open, recent5_vol) if c >= o)
    down_vol = sum(v for c, o, v in zip(recent5_close, recent5_open, recent5_vol) if c < o)
    red_green_ratio_5 = up_vol / down_vol if down_vol > 0 else 999.0

    close_strength = (c_t - l_t) / max(h_t - l_t, 0.0001)
    day_pct = (c_t - c_y) / c_y * 100.0 if c_y > 0 else 0.0
    day_amplitude = (h_t - l_t) / c_y * 100.0 if c_y > 0 else 0.0

    prev20_high = max(high_seq[-21:-1]) if len(high_seq) >= 21 else h_t
    breakout_20 = c_t > prev20_high

    ma5 = np.mean(close_seq[-5:])
    bias_ma5 = (c_t - ma5) / ma5 * 100.0 if ma5 > 0 else 0.0

    recent60_high = max(high_seq[-60:]) if len(high_seq) >= 60 else max(high_seq)
    recent60_low = min(low_seq[-60:]) if len(low_seq) >= 60 else min(low_seq)
    pos60 = (c_t - recent60_low) / max(recent60_high - recent60_low, 0.0001)

    upper_shadow_ratio = (h_t - max(o_t, c_t)) / c_t * 100.0 if c_t > 0 else 0.0

    c_prev3 = close_seq[-4] if len(close_seq) >= 4 else close_seq[0]
    pct3 = (c_t - c_prev3) / c_prev3 * 100.0 if c_prev3 > 0 else 0.0

    return {
        'previous_close': c_t,
        'vol_ratio_5': vol_ratio_5,
        'red_green_ratio_5': red_green_ratio_5,
        'close_strength': close_strength,
        'day_pct': day_pct,
        'day_amplitude': day_amplitude,
        'breakout_20': breakout_20,
        'bias_ma5': bias_ma5,
        'pos60': pos60,
        'upper_shadow_ratio': upper_shadow_ratio,
        'pct3': pct3
    }


# ==================== 6. 打分维度实现 ====================
def calc_market_cap_score(circ_cap):
    if circ_cap <= 0:
        return 0
    if 80.0 <= circ_cap <= 300.0:
        return 10
    elif (50.0 <= circ_cap < 80.0) or (300.0 < circ_cap <= 500.0):
        return 6
    elif (30.0 <= circ_cap < 50.0) or (500.0 < circ_cap <= 800.0):
        return 2
    elif circ_cap < 20.0 or circ_cap > 1200.0:
        return -6
    return -2


def calc_volume_price_score(metrics):
    score = 0
    vol_ratio = metrics['vol_ratio_5']
    day_amp = metrics['day_amplitude']
    day_pct = metrics['day_pct']

    if 1.5 <= vol_ratio <= 3.0:
        score += 10
    elif 1.2 <= vol_ratio < 1.5:
        score += 6
    elif vol_ratio > 3.0:
        if day_amp < 4.0 and day_pct >= -1.0:
            score += 10
        elif day_amp >= 4.0 and day_pct >= 5.0:
            score += 8
        else:
            score += 3

    r_g_ratio = metrics['red_green_ratio_5']
    if r_g_ratio >= 1.3:
        score += 8
    elif r_g_ratio >= 1.0:
        score += 4

    close_str = metrics['close_strength']
    if close_str >= 0.7:
        score += 6
    elif close_str >= 0.55:
        score += 3

    if 2.0 <= day_pct <= 7.0:
        score += 6
    elif 0.0 <= day_pct < 2.0:
        score += 3
    elif day_pct > 9.0:
        score += 1

    return score


def calc_position_score(metrics):
    score = 0
    if metrics['breakout_20']:
        score += 10

    bias_ma5 = metrics['bias_ma5']
    if 0.0 <= bias_ma5 <= 6.0:
        score += 8
    elif 6.0 < bias_ma5 <= 10.0:
        score += 4

    pos60 = metrics['pos60']
    if 0.2 <= pos60 <= 0.65:
        score += 7
    elif 0.65 < pos60 <= 0.8:
        score += 3

    return score


def calc_risk_penalty(metrics):
    penalty = 0
    if metrics['upper_shadow_ratio'] > 4.0:
        penalty -= 8
    if metrics['vol_ratio_5'] > 3.0 and metrics['day_pct'] < 2.0 and metrics['day_amplitude'] >= 4.0:
        penalty -= 10
    if metrics['pct3'] > 18.0:
        penalty -= 8
    if metrics['close_strength'] < 0.4:
        penalty -= 6
    return penalty
