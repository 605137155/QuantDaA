# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略核心：基础复盘选股策略优化版
# 交易逻辑：盘前基于上一个交易日数据复盘选股，14:50 先卖出可卖持仓，再用实际可用资金买入目标池

from jqdata import *
import numpy as np
import datetime
from datetime import datetime as dt


# ==================== 1. 初始化设置 ====================
def initialize(context):
    # 设置回测参数
    set_benchmark('000300.XSHG')  # 基准
    set_option('use_real_price', True)  # 真实价格交易
    log.set_level('order', 'error')  # 隐藏交易日志，保持清晰

    # 设置佣金与印花税：买入万三，卖出万三加千分之一印花税，最低5元
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0.001,
        open_commission=0.0003,
        close_commission=0.0003,
        close_today_commission=0,
        min_commission=5
    ), type='stock')

    # 策略全局变量
    g.max_stocks = 4
    g.target_list = []

    # 每天下午 14:50 执行尾盘轮动：先卖后买
    run_daily(my_afternoon_trade, time='14:50')


# ==================== 2. 每日交易前准备（选股阶段） ====================
def before_trading_start(context):
    # 当前交易日盘前，只能稳定使用上一个交易日的完整日线数据。
    previous_date = context.previous_date
    if isinstance(previous_date, str):
        yesterday = previous_date
        date_obj = dt.strptime(previous_date, '%Y-%m-%d').date()
    else:
        date_obj = previous_date
        yesterday = date_obj.strftime('%Y-%m-%d')

    log.info(f"--- 正在执行 {yesterday} 的复盘选股策略 ---")

    # 1. 获取基础股票池（全A股，排除ST、退市、上市未满90天的次新股）
    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    if not filtered_stocks:
        g.target_list = []
        log.info("基础股票池为空，今日不交易")
        return

    # 2. 获取成交额排名前 100 的热门股作为核心监控池
    h_amount_all = history(1, '1d', 'money', filtered_stocks, df=False)
    amount_all_dict = {}
    for code in filtered_stocks:
        amount_series = get_history_series(h_amount_all, code)
        if len(amount_series) > 0:
            amount_all_dict[code] = float(amount_series[-1])

    if not amount_all_dict:
        g.target_list = []
        log.info("无法获取成交额数据，今日不交易")
        return

    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    hot_candidate_pool = [item[0] for item in sorted_by_amount_all[:100]]

    # 3. 补全热门股的流通市值数据，用于市值打分
    q = query(
        valuation.code,
        valuation.circulating_market_cap
    ).filter(
        valuation.code.in_(hot_candidate_pool)
    )
    df_cap = get_fundamentals(q, date=yesterday)
    if df_cap is None or df_cap.empty:
        g.target_list = []
        log.info("无法获取流通市值数据，今日不交易")
        return

    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))
    candidate_pool = [code for code in hot_candidate_pool if code in cap_dict]
    if not candidate_pool:
        g.target_list = []
        log.info("候选股票池为空，今日不交易")
        return

    # 建立股票名称本地映射，避免循环内网络调用
    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))

    # 热门股成交额排名，用作 Heat Score 热度打分
    amount_ranks = {code: idx + 1 for idx, code in enumerate(hot_candidate_pool)}

    # 4. 批量获取候选热门股的65天日线数据；去掉未参与评分的 money 序列，减少一次历史请求
    h_open = history(65, '1d', 'open', candidate_pool, df=False)
    h_close = history(65, '1d', 'close', candidate_pool, df=False)
    h_high = history(65, '1d', 'high', candidate_pool, df=False)
    h_low = history(65, '1d', 'low', candidate_pool, df=False)
    h_volume = history(65, '1d', 'volume', candidate_pool, df=False)

    # 5. 遍历个股，进行五大维度打分
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

        # A. 热度得分（昨日成交额排名） -> Max 25分
        rank_no = amount_ranks.get(code, 999)
        heat_score = max(0, int(25 * (100 - min(rank_no, 100)) / 99.0))

        # B. 流通市值打分 -> Max 10分
        circ_cap = cap_dict.get(code, 0.0)
        market_cap_score = calc_market_cap_score(circ_cap)

        # C. 量价得分 -> Max 30分
        volume_price_score = calc_volume_price_score(metrics)

        # D. 位置得分 -> Max 25分
        position_score = calc_position_score(metrics)

        # E. 风险扣分 -> Subtractive
        risk_penalty = calc_risk_penalty(metrics)

        total_score = heat_score + market_cap_score + volume_price_score + position_score + risk_penalty
        total_score = max(0, min(100, total_score))

        scored_candidates.append({
            'code': code,
            'score': total_score,
            'heat_score': heat_score,
            'market_cap_score': market_cap_score,
            'volume_price_score': volume_price_score,
            'position_score': position_score,
            'risk_penalty': risk_penalty,
            'name': name_dict.get(code, code)
        })

    scored_candidates = sorted(scored_candidates, key=lambda x: x['score'], reverse=True)

    log.info("复盘得分排名前10的候选票：")
    for i, item in enumerate(scored_candidates[:10]):
        log.info(
            f"Top {i + 1}: {item['code']} ({item['name']}) - "
            f"总分: {item['score']} | 热度: {item['heat_score']} | "
            f"市值: {item['market_cap_score']} | 量价: {item['volume_price_score']} | "
            f"位置: {item['position_score']} | 风险: {item['risk_penalty']}"
        )

    g.target_list = [item['code'] for item in scored_candidates[:g.max_stocks]]
    log.info(f"今日尾盘目标买入股票：{g.target_list}")


# ==================== 3. 交易执行 ====================
def my_afternoon_trade(context):
    """下午14:50尾盘交易逻辑：先卖出可卖持仓，再按实际可用资金买入目标股。"""
    current_holdings = list(context.portfolio.positions.keys())
    current_data = get_current_data()

    # 1. 尾盘卖出：只卖出已过锁定期且非停牌/跌停的可卖持仓
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        if position.closeable_amount <= 0:
            continue
        if not is_sellable_stock(stock, current_data):
            continue

        order(stock, -position.closeable_amount)
        log.info(f"【尾盘卖出】{stock} 清仓可卖持仓股数 {position.closeable_amount}")

    # 2. 尾盘买入：只使用当前账户实际可用资金，避免把未成交卖单当作现金
    if not g.target_list:
        return

    buy_targets = []
    for stock in g.target_list:
        if is_buyable_stock(stock, current_data, '尾盘'):
            buy_targets.append(stock)

    if not buy_targets:
        log.info("【尾盘跳过】目标股均不可买入")
        return

    available_cash = max(0.0, context.portfolio.available_cash)
    target_total_buy = available_cash * 0.99  # 保留手续费和滑点缓冲
    cash_per_stock = target_total_buy / len(buy_targets)

    if cash_per_stock <= 0:
        log.info("【尾盘跳过】可用资金不足，放弃尾盘买入")
        return

    for stock in buy_targets:
        order_value(stock, cash_per_stock)
        log.info(f"【尾盘买入】{stock} 增量买入资金 {cash_per_stock:.2f}")


# ==================== 4. 辅助函数 ====================
def get_history_series(history_map, code):
    """从聚宽 history(df=False) 返回值中安全提取某只股票的序列。"""
    if history_map is None or code not in history_map:
        return []

    values = history_map[code]
    if values is None:
        return []
    return list(values)


def is_sellable_stock(stock, current_data):
    price_info = current_data[stock]
    if price_info.paused:
        log.info(f"【尾盘卖出跳过】{stock} 停牌，无法卖出")
        return False

    last_price = getattr(price_info, 'last_price', None)
    low_limit = getattr(price_info, 'low_limit', None)
    if last_price is None or low_limit is None or last_price <= 0:
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

    last_price = getattr(price_info, 'last_price', None)
    high_limit = getattr(price_info, 'high_limit', None)
    low_limit = getattr(price_info, 'low_limit', None)
    if last_price is None or high_limit is None or low_limit is None or last_price <= 0:
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
    """过滤ST、退市及上市未满90天的新股。"""
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
    """提取各项技术量价指标。"""
    if len(close_seq) < 21:
        return None

    # T日（上一个交易日）的数据，即序列最后一个值
    c_t = close_seq[-1]
    o_t = open_seq[-1]
    h_t = high_seq[-1]
    l_t = low_seq[-1]
    v_t = vol_seq[-1]
    c_y = close_seq[-2]

    # 1. 量比（5日）
    v_prev5 = vol_seq[-6:-1]
    avg5_vol = np.mean(v_prev5) if len(v_prev5) > 0 else 1.0
    vol_ratio_5 = v_t / avg5_vol if avg5_vol > 0 else 0.0

    # 2. 红肥绿瘦比（5日内阳线量 / 阴线量）
    recent5_close = close_seq[-5:]
    recent5_open = open_seq[-5:]
    recent5_vol = vol_seq[-5:]
    up_vol = sum(v for c, o, v in zip(recent5_close, recent5_open, recent5_vol) if c >= o)
    down_vol = sum(v for c, o, v in zip(recent5_close, recent5_open, recent5_vol) if c < o)
    red_green_ratio_5 = up_vol / down_vol if down_vol > 0 else 999.0

    # 3. 收盘强度
    close_strength = (c_t - l_t) / max(h_t - l_t, 0.0001)

    # 4. 单日涨幅与振幅
    day_pct = (c_t - c_y) / c_y * 100.0 if c_y > 0 else 0.0
    day_amplitude = (h_t - l_t) / c_y * 100.0 if c_y > 0 else 0.0

    # 5. 突破20日高点
    prev20_high = max(high_seq[-21:-1]) if len(high_seq) >= 21 else h_t
    breakout_20 = c_t > prev20_high

    # 6. MA5 乖离率
    ma5 = np.mean(close_seq[-5:])
    bias_ma5 = (c_t - ma5) / ma5 * 100.0 if ma5 > 0 else 0.0

    # 7. 60日位置
    recent60_high = max(high_seq[-60:]) if len(high_seq) >= 60 else max(high_seq)
    recent60_low = min(low_seq[-60:]) if len(low_seq) >= 60 else min(low_seq)
    pos60 = (c_t - recent60_low) / max(recent60_high - recent60_low, 0.0001)

    # 8. 上影线比例
    upper_shadow_ratio = (h_t - max(o_t, c_t)) / c_t * 100.0 if c_t > 0 else 0.0

    # 9. 3日累计涨幅
    c_prev3 = close_seq[-4] if len(close_seq) >= 4 else close_seq[0]
    pct3 = (c_t - c_prev3) / c_prev3 * 100.0 if c_prev3 > 0 else 0.0

    return {
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


# ==================== 5. 打分维度实现 ====================
def calc_market_cap_score(circ_cap):
    """市值打分逻辑（流通市值以亿元为单位）。"""
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
    """量价评分逻辑。"""
    score = 0
    vol_ratio = metrics['vol_ratio_5']
    day_amp = metrics['day_amplitude']
    day_pct = metrics['day_pct']

    # 1. 量比评分（含窄幅吸筹与突破）
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

    # 2. 红肥绿瘦
    r_g_ratio = metrics['red_green_ratio_5']
    if r_g_ratio >= 1.3:
        score += 8
    elif r_g_ratio >= 1.0:
        score += 4

    # 3. 收盘强度
    close_str = metrics['close_strength']
    if close_str >= 0.7:
        score += 6
    elif close_str >= 0.55:
        score += 3

    # 4. 涨幅区间
    if 2.0 <= day_pct <= 7.0:
        score += 6
    elif 0.0 <= day_pct < 2.0:
        score += 3
    elif day_pct > 9.0:
        score += 1

    return score


def calc_position_score(metrics):
    """位置评分逻辑。"""
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
    """风险惩罚扣分。"""
    penalty = 0
    # A. 长上影线扣分
    if metrics['upper_shadow_ratio'] > 4.0:
        penalty -= 8
    # B. 爆量滞涨扣分
    if metrics['vol_ratio_5'] > 3.0 and metrics['day_pct'] < 2.0 and metrics['day_amplitude'] >= 4.0:
        penalty -= 10
    # C. 连续加速风险
    if metrics['pct3'] > 18.0:
        penalty -= 8
    # D. 收盘偏弱扣分
    if metrics['close_strength'] < 0.4:
        penalty -= 6
    return penalty
