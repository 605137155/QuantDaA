# ========================================================================
# 策略名称：首板一进二 放宽版 v1
# ========================================================================
# 基于原版首板1进2策略，放宽选股条件
# 保留三重风控：净值动量、ML风控、盘中止损
# 保留涨停不卖逻辑
# ========================================================================


# ========================================================================
# ██ 可调参数区 ██  你可以根据回测效果自行修改以下参数
# ========================================================================

# -------------------- 1. 选股条件 --------------------

# 选股模式：近N日有过涨停（原版只看昨日，现在看近4日）
# 建议范围：1~5，越大选出的票越多
LOOKBACK_DAYS = 4

# 近N日涨幅阈值（原版0.07=7%，放宽版0.05=5%）
# 只在 LOOKBACK_DAYS=1 时生效
MIN_YESTERDAY_PCT = 0.05

# 成交额范围（元）
# 排除僵尸股和小票，只参与活跃票
MIN_MONEY = 6e8       # 最小成交额 6亿
MAX_MONEY = 50e8      # 最大成交额 50亿

# 近5日平均成交额下限（元）
# 排除平时成交额小、突然爆量的危险票
MIN_AVG_MONEY_5D = 3e8  # 近5日日均成交额至少3亿

# 流通市值范围（亿）
# 原版：10~520亿，放宽版：10~600亿
MIN_MARKET_CAP = 10    # 最小总市值（亿）
MAX_CIRC_CAP = 600     # 最大流通市值（亿）（放宽）

# 最低股价（元）
MIN_PRICE = 3.0

# avg_chg 阈值（平均成本偏离度）
# 原版0.07，放宽版0.05
# 数值越小选出的票越多，建议范围：0.03~0.07
MIN_AVG_CHG = 0.05

# -------------------- 1.5 筹码拥挤度过滤 --------------------
# 拥挤度评分阈值（超过此值排除）
# 0~3分：不拥挤 | 4~6分：中度拥挤 | 7~10分：高度拥挤 | 11+分：极度拥挤
MAX_CROWDING_SCORE = 8

# 拥挤度子因子阈值
MAX_TURNOVER_5D = 0.50        # 近5日累计换手率上限（50%）
MAX_LIMIT_UP_COUNT_5D = 4     # 近5日涨停次数上限
MAX_EXTREME_LIMIT_COUNT = 3   # 近10日一字/T字涨停上限
MAX_AVG_AMPLITUDE_5D = 0.08   # 近5日平均振幅上限（8%）
MAX_VOL_RATIO = 3.0           # 量比上限
MAX_AUCTION_RATIO = 0.20      # 竞昨比上限（20%）
MAX_CONSECUTIVE_LIMIT_UP = 3  # 连板天数上限

# -------------------- 2. 过滤条件 --------------------

# 近5日涨停天数上限（超过则排除）
MAX_LIMIT_DAYS_5 = 4

# 近10日一字/T字涨停上限（超过则排除）
MAX_EXTREME_LIMIT_10 = 3

# 近5日最大波动幅度上限（超过则排除，0.4=40%）
MAX_VOLATILITY_5 = 0.4

# 昨日振幅上限（超过则排除，0.15=15%）
# 排除前一日振幅过大（炸板、冲高回落）的票
MAX_YESTERDAY_AMPLITUDE = 0.15

# 竞价低于预期排除
# 只排除竞价涨幅为负的情况（竞价低开）
# 竞价涨幅>=0%就通过，不再用比例判断
ENABLE_AUCTION_BELOW_EXPECTATION = True
BELOW_EXPECTATION_RATIO = 0.0  # 竞价涨幅 < 0 才排除

# 100日高点过滤（收盘价 >= 最高价 * 此比例才保留）
HIGH_POINT_RATIO = 0.9

# -------------------- 3. 竞价条件矩阵 --------------------
# 格式：(条件名, 竞价涨幅下限, 竞价涨幅上限, 竞昨比下限, 竞昨比上限)
# 竞价涨幅 = 竞价价 / 昨日收盘价
# 竞昨比 = 竞价成交量 / 昨日成交量
#
# 调整建议：
#   - 竞价涨幅区间：1.00~1.07 表示 0%~7%
#   - 竞昨比区间：0.02~0.15 表示 2%~15%
#   - 区间越宽选出的票越多
#
# 优化说明：
#   - 降低竞价涨幅上限到5%，避免追高
#   - 提高竞昨比下限，确保有资金关注

CONDITION_RULES = [
    # A类：中市值（成交额 6亿 ~ 15亿）
    ('A: 涨幅2~7% | 竞昨比2~12%',   1.02, 1.07, 0.02, 0.12),
    ('A: 涨幅0~2% | 竞昨比2~8%',    1.00, 1.02, 0.02, 0.08),
    ('A: 涨幅-2~0% | 竞昨比3~10%',  0.98, 1.00, 0.03, 0.10),

    # B类：大市值（成交额 15亿 ~ MAX_MONEY）
    ('B: 涨幅2~7% | 竞昨比2~12%',   1.02, 1.07, 0.02, 0.12),
    ('B: 涨幅0~2% | 竞昨比2~8%',    1.00, 1.02, 0.02, 0.08),
    ('B: 涨幅-2~0% | 竞昨比3~10%',  0.98, 1.00, 0.03, 0.10),
    ('B: 涨幅3~7% | 竞昨比2~6%',    1.03, 1.07, 0.02, 0.06),
]

# A类/B类的成交额分界线（亿）
MONEY_SPLIT = 15e8  # 15亿

# -------------------- 4. 风控参数 --------------------

# 跌幅止损阈值（0.05=5%，较昨日收盘跌幅超过此值触发止损）
DROP_STOP_LOSS = 0.05

# 净值回撤减仓阈值（0.08=8%，净值从高点回撤超过此值买入减半）
DRAWDOWN_THRESHOLD = 0.08

# 连亏天数暂停阈值（连亏N天后暂停买入1天）
CONSECUTIVE_LOSS_PAUSE = 2

# ML风控：预测亏损概率阈值
ML_SKIP_THRESHOLD = 0.7      # 超过此值跳过买入
ML_REDUCE_THRESHOLD = 0.5    # 超过此值买入减半

# ML训练样本窗口
ML_SAMPLE_WINDOW = 120

# ML训练最少样本数
ML_MIN_SAMPLES = 60

# 5日线止损加成（价格 < 5日线 * (1 + 此值) 触发止损）
MA5_STOP_LOSS_BUFFER = 0.05

# -------------------- 5. 交易参数 --------------------

# 滑点
SLIPPAGE = 0.005

# 佣金
OPEN_COMMISSION = 0.0002
CLOSE_COMMISSION = 0.0002
CLOSE_TAX = 0.0005
MIN_COMMISSION = 5

# ========================================================================
# ██ 可调参数区结束 ██  以下为策略代码，一般不需要修改
# ========================================================================


from jqdata import *
import pandas as pd
import numpy as np


def initialize(context):
    log.set_level('order', 'error')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(FixedSlippage(SLIPPAGE))
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=CLOSE_TAX,
        open_commission=OPEN_COMMISSION,
        close_commission=CLOSE_COMMISSION,
        min_commission=MIN_COMMISSION
    ), type='stock')
    set_benchmark('399303.XSHE')

    g.information = {}
    g.drop_percent = DROP_STOP_LOSS
    g.condition_stats = {}
    g.name_cache = {}

    # 净值曲线动量
    g.consecutive_loss_days = 0
    g.skip_buy = False
    g.peak_value = 0
    g.drawdown_reduction = 1.0
    g.prev_day_value = 0

    # ML在线学习
    g.ml_features = []
    g.ml_labels = []
    g.ml_weights = None
    g.ml_pred_reduction = 1.0
    g.recent_pnls = []
    g.yesterday_buy_count = 0
    g.pending_features = None
    g.day_count = 0

    run_daily(before_market_open, time='09:10')
    run_daily(get_buy, '09:26')
    run_daily(get_close_sell, time='11:25')
    run_daily(get_close_sell, time='13:30')
    run_daily(eod_stats, time='15:00')


def before_market_open(context):
    y_day = context.previous_date.strftime('%Y-%m-%d')

    initial_list = prepare_stock_list(context)
    log.info(f"[选股] 初始股票池: {len(initial_list)}只")

    if LOOKBACK_DAYS == 1:
        g.target_list = get_stocks_with_high_increase(initial_list, y_day)
        log.info(f"[选股] 昨日涨幅>{MIN_YESTERDAY_PCT*100:.0f}%: {len(g.target_list)}只")
    else:
        g.target_list = get_stocks_with_limit_up(initial_list, y_day, LOOKBACK_DAYS)
        log.info(f"[选股] 近{LOOKBACK_DAYS}日有过涨停: {len(g.target_list)}只")

    g.target_list = filter_excessive_limit_up(g.target_list, y_day)
    log.info(f"[选股] 过滤一字/T字涨停后: {len(g.target_list)}只")

    g.target_list = filter_excessive_increase(g.target_list, y_day)
    log.info(f"[选股] 过滤近5日波动>{MAX_VOLATILITY_5*100:.0f}%后: {len(g.target_list)}只")

    g.target_list = filter_excessive_limit_days(g.target_list, y_day)
    log.info(f"[选股] 过滤近5日涨停>={MAX_LIMIT_DAYS_5}天后: {len(g.target_list)}只")

    g.target_list = filter_below_n_high(g.target_list, y_day, days=100)
    log.info(f"[选股] 过滤低于100日高点后: {len(g.target_list)}只")

    # 净值动量判断
    if g.skip_buy:
        g.skip_buy = False
        log.info("[净值动量] 冷静期结束，恢复交易")

    if g.peak_value > 0:
        current_dd = (context.portfolio.total_value / g.peak_value - 1)
        if current_dd < -DRAWDOWN_THRESHOLD:
            g.drawdown_reduction = 0.5
            log.info(f"[净值动量] 净值从高点回撤{current_dd:.1%}，买入减半")
        else:
            g.drawdown_reduction = 1.0

    # ML风控预测
    if g.ml_weights is not None and g.day_count >= ML_MIN_SAMPLES:
        try:
            today_features = compute_ml_features(context)
            if today_features is not None:
                score = sigmoid(np.dot(g.ml_weights, today_features))
                if score > ML_SKIP_THRESHOLD:
                    g.ml_pred_reduction = 0.0
                    log.info(f"[ML风控] 预测亏损概率{score:.1%}，跳过买入")
                elif score > ML_REDUCE_THRESHOLD:
                    g.ml_pred_reduction = 0.5
                    log.info(f"[ML风控] 预测亏损概率{score:.1%}，减半买入")
                else:
                    g.ml_pred_reduction = 1.0
                    log.info(f"[ML风控] 预测亏损概率{score:.1%}，正常买入")
        except Exception as e:
            g.ml_pred_reduction = 1.0
            log.info(f"[ML风控] 预测异常: {e}")
    else:
        g.ml_pred_reduction = 1.0

    g.name_cache = {}
    if g.target_list:
        for s in g.target_list:
            try:
                g.name_cache[s] = get_security_info(s).display_name
            except:
                g.name_cache[s] = '未知'
        stock_info = [f"{s}({g.name_cache[s]})" for s in g.target_list]
        log.info(f"今日选股结果 ({len(g.target_list)}只):\n" + "\n".join(stock_info))
        send_message(f"今日选股: {len(g.target_list)}只")
    else:
        log.info("今日无符合条件的股票")
        send_message("今日无符合条件的股票")


def get_buy(context):
    if g.skip_buy:
        log.info("[净值动量] 冷静期，不买入")
        return

    if g.ml_pred_reduction == 0.0:
        log.info("[ML风控] 预测亏损，跳过买入")
        return

    qualified_stocks = []
    current_data = get_current_data()
    y_day = context.previous_date.strftime('%Y-%m-%d')
    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'
    DTJiner = context.portfolio.available_cash * g.drawdown_reduction * g.ml_pred_reduction

    if not g.target_list:
        return

    # 获取近2日数据（用于计算昨日涨幅和振幅）
    prev2_df = get_price(
        g.target_list, end_date=y_day, frequency='daily',
        fields=['close', 'high', 'low', 'volume', 'money'], count=2, panel=False,
        fill_paused=False, skip_paused=True
    )
    # 近5日成交额（用于计算平均成交额）
    prev5_df = get_price(
        g.target_list, end_date=y_day, frequency='daily',
        fields=['money'], count=5, panel=False,
        fill_paused=False, skip_paused=True
    )

    # 构建昨日数据映射
    prev_map = {}
    prev_amplitude_map = {}  # 昨日振幅
    prev_pct_map = {}  # 昨日涨幅
    avg_money_5d_map = {}  # 近5日平均成交额

    for s in g.target_list:
        stock_data = prev2_df[prev2_df['code'] == s]
        if len(stock_data) >= 2:
            yesterday = stock_data.iloc[-1]
            day_before = stock_data.iloc[-2]
            prev_map[s] = yesterday
            # 昨日振幅 = (最高-最低) / 前日收盘
            if day_before['close'] > 0:
                prev_amplitude_map[s] = (yesterday['high'] - yesterday['low']) / day_before['close']
                prev_pct_map[s] = (yesterday['close'] - day_before['close']) / day_before['close']
        elif len(stock_data) == 1:
            prev_map[s] = stock_data.iloc[-1]

        # 近5日平均成交额
        stock_5d = prev5_df[prev5_df['code'] == s]
        if len(stock_5d) > 0:
            avg_money_5d_map[s] = stock_5d['money'].mean()

    val_df = get_fundamentals(
        query(valuation.code, valuation.market_cap, valuation.circulating_market_cap)
        .filter(valuation.code.in_(g.target_list)),
        date=str(y_day)[:10]
    )
    val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

    hl_base = {s: current_data[s].high_limit / 1.1 for s in g.target_list}

    for s in g.target_list:
        name = g.name_cache.get(s, '未知')

        try:
            prev = prev_map.get(s)
            if prev is None:
                continue
            avg_chg = prev['money'] / prev['volume'] / prev['close'] * 1.1 - 1
            money = prev['money']
            open_price = current_data[s].day_open
            val = val_map.get(s)

            # 基础过滤
            if avg_chg < MIN_AVG_CHG:
                continue
            if open_price <= MIN_PRICE:
                continue
            if val is None or val['market_cap'] < MIN_MARKET_CAP or val['circulating_market_cap'] > MAX_CIRC_CAP:
                continue
            if money < MIN_MONEY or money > MAX_MONEY:
                continue

            # 近5日平均成交额过滤（排除僵尸股）
            avg_money_5d = avg_money_5d_map.get(s, 0)
            if avg_money_5d < MIN_AVG_MONEY_5D:
                log.info(f"❌ {s}({name}) 近5日日均成交额{avg_money_5d/1e8:.1f}亿<{MIN_AVG_MONEY_5D/1e8:.0f}亿，排除")
                continue

            # 昨日振幅过滤（排除炸板、冲高回落）
            yesterday_amplitude = prev_amplitude_map.get(s, 0)
            if yesterday_amplitude > MAX_YESTERDAY_AMPLITUDE:
                log.info(f"❌ {s}({name}) 昨日振幅{yesterday_amplitude*100:.1f}%>{MAX_YESTERDAY_AMPLITUDE*100:.0f}%，排除")
                continue

            # 竞价低于预期排除
            if ENABLE_AUCTION_BELOW_EXPECTATION:
                yesterday_pct = prev_pct_map.get(s, 0)
                auction_pct = current_data[s].day_open / prev['close'] - 1
                # 如果昨日涨幅>3%，但竞价涨幅不到昨日一半，说明低于预期
                if yesterday_pct > 0.03 and auction_pct < yesterday_pct * BELOW_EXPECTATION_RATIO:
                    log.info(f"❌ {s}({name}) 竞价低于预期：昨日涨{yesterday_pct*100:.1f}%，竞价涨{auction_pct*100:.1f}%，排除")
                    continue

            # 高开大阴线排除（前一天冲高回落，筹码不稳定）
            # 只排除严重的高开大阴线（高开>5%且阴线>3%）
            if len(prev2_df[prev2_df['code'] == s]) >= 2:
                stock_data = prev2_df[prev2_df['code'] == s]
                yesterday_bar = stock_data.iloc[-1]
                day_before_bar = stock_data.iloc[-2]
                # 高开：开盘价 > 前日收盘价
                # 大阴线：收盘价 < 开盘价，且阴线实体 > 3%
                if day_before_bar['close'] > 0:
                    open_gap = (yesterday_bar['open'] - day_before_bar['close']) / day_before_bar['close']
                    body_pct = (yesterday_bar['open'] - yesterday_bar['close']) / yesterday_bar['open'] if yesterday_bar['open'] > 0 else 0
                    if open_gap > 0.05 and body_pct > 0.03:
                        log.info(f"❌ {s}({name}) 前一天高开大阴线：高开{open_gap*100:.1f}%，阴线{body_pct*100:.1f}%，排除")
                        continue

            is_small = money < MONEY_SPLIT
            is_large = not is_small
        except:
            continue

        try:
            zyts = calculate_zyts(s, context)
            vol_data = attribute_history(s, zyts, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                continue
            if vol_data['volume'][-1] <= max(vol_data['volume'][:-1]) * 0.9:
                continue
        except:
            continue

        try:
            auction = get_call_auction(s, start_date=start, end_date=end, fields=['time', 'volume', 'current'])
            if auction.empty:
                continue
            cur_ratio = auction['current'][0] / hl_base[s]
            auction_ratio = auction['volume'][0] / vol_data['volume'][-1]

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES:
                if cond_name.startswith('A') and not is_small:
                    continue
                if cond_name.startswith('B') and not is_large:
                    continue
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                continue

            # 筹码拥挤度过滤
            # 获取历史K线数据用于计算拥挤度
            hist_bars = attribute_history(s, 15, '1d', fields=['close', 'high', 'low', 'volume', 'open', 'high_limit'], skip_paused=True)
            if len(hist_bars) >= 10:
                daily_bars = []
                for idx in hist_bars.index:
                    daily_bars.append({
                        'close': hist_bars.loc[idx, 'close'],
                        'high': hist_bars.loc[idx, 'high'],
                        'low': hist_bars.loc[idx, 'low'],
                        'volume': hist_bars.loc[idx, 'volume'],
                        'open': hist_bars.loc[idx, 'open'],
                        'high_limit': hist_bars.loc[idx, 'high_limit'],
                    })
                auction_volume = auction['volume'].iloc[0] if len(auction) > 0 else 0
                yesterday_volume = vol_data['volume'][-1]
                crowding_score, crowding_details = calc_crowding_score(s, context, daily_bars, auction_volume, yesterday_volume)

                if crowding_score > MAX_CROWDING_SCORE:
                    detail_str = ", ".join([f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in crowding_details.items()])
                    log.info(f"❌ {s}({name}) 筹码拥挤度{crowding_score}分>{MAX_CROWDING_SCORE}分，排除 [{detail_str}]")
                    continue

                log.info(f"📊 {s}({name}) 筹码拥挤度{crowding_score}分")
        except Exception as e:
            log.warn(f"[竞价筛选异常] {s}({name}): {e}")
            continue

        qualified_stocks.append(s)
        g.information[s] = matched_condition
        log.info(f"✅ {s}({name}) 通过筛选，命中: {matched_condition}")

    log.info(f"最终符合条件: {len(qualified_stocks)}只")

    buy_count = 0
    if qualified_stocks and context.portfolio.available_cash / context.portfolio.total_value > 0.3:
        value_per_stock = DTJiner / len(qualified_stocks)
        for s in qualified_stocks:
            price = current_data[s].last_price
            shares = int(value_per_stock / price / 100) * 100
            if shares >= 100:
                order_value(s, value_per_stock, MarketOrderStyle(current_data[s].day_open))
                buy_count += 1
                log.info(f"买入 {s}: 价格={price}, 数量={shares}, 条件={g.information.get(s,'未知')}, 减仓={g.drawdown_reduction}*{g.ml_pred_reduction}")
    g.yesterday_buy_count = buy_count


def get_close_sell(context):
    y_day = context.previous_date.strftime('%Y-%m-%d')
    current_data = get_current_data()
    positions = context.portfolio.positions

    t = context.current_dt
    h, m = t.hour, t.minute

    yst_close_map = {}
    if positions:
        try:
            yst_df = get_price(
                list(positions.keys()), end_date=y_day,
                frequency='daily', fields=['close'], count=1,
                panel=False, skip_paused=True
            )
            yst_close_map = dict(zip(yst_df['code'], yst_df['close']))
        except:
            pass

    for s in list(positions):
        if s not in g.name_cache:
            try:
                g.name_cache[s] = get_security_info(s).display_name
            except:
                g.name_cache[s] = '未知'

    if (h == 11 and m == 25) or (h == 13 and m == 30):
        for s in list(positions):
            pos = positions[s]
            last_price = current_data[s].last_price
            high_limit = current_data[s].high_limit
            avg_cost = pos.avg_cost
            closeable = pos.closeable_amount

            try:
                close_data2 = attribute_history(s, 4, '1d', ['close'])
                M4 = close_data2['close'].mean()
                MA5 = (M4 * 4 + last_price) / 5
            except:
                continue

            # 涨停不卖
            if closeable != 0 and last_price >= high_limit - 0.01:
                log.info(f'涨停持有 {s}({g.name_cache[s]})')
                continue

            # 止盈：未涨停但盈利
            if closeable != 0 and last_price < high_limit and last_price > avg_cost:
                get_record_sell(context, s, '未涨停止盈')
                order_target_value(s, 0)
                log.info(f'止盈卖出 {s}({g.name_cache[s]})')

            # 止损：跌破5日线
            elif closeable != 0 and last_price < (MA5 + MA5 * MA5_STOP_LOSS_BUFFER):
                get_record_sell(context, s, '跌破5日线止损')
                order_target_value(s, 0)
                log.info(f'价格跌破5日线止损卖出 {s}({g.name_cache[s]})')

            # 跌幅止损
            elif closeable != 0:
                yst_close = yst_close_map.get(s)
                if yst_close and yst_close > 0:
                    drop_ratio = (yst_close - last_price) / yst_close
                    if drop_ratio >= g.drop_percent:
                        get_record_sell(context, s, '跌幅止损')
                        order_target_value(s, 0)
                        log.info(f'跌幅止损卖出: {s}({g.name_cache[s]}) 跌幅{-drop_ratio:.2%}')


def eod_stats(context):
    total_value = context.portfolio.total_value
    daily_pnl = 0

    g.peak_value = max(g.peak_value, total_value)

    if g.prev_day_value > 0:
        daily_pnl = (total_value / g.prev_day_value - 1)
        g.recent_pnls.append(daily_pnl)
        if len(g.recent_pnls) > 5:
            g.recent_pnls = g.recent_pnls[-5:]
        if daily_pnl < -0.005:
            g.consecutive_loss_days += 1
        else:
            g.consecutive_loss_days = 0

        if g.consecutive_loss_days >= CONSECUTIVE_LOSS_PAUSE:
            g.skip_buy = True
            log.info(f"[净值动量] 连亏{g.consecutive_loss_days}天，明日暂停买入")

    g.day_count += 1

    if g.pending_features is not None and g.prev_day_value > 0:
        label = 1.0 if daily_pnl > 0 else 0.0
        g.ml_features.append(g.pending_features)
        g.ml_labels.append(label)
        if len(g.ml_features) > ML_SAMPLE_WINDOW:
            g.ml_features = g.ml_features[-ML_SAMPLE_WINDOW:]
            g.ml_labels = g.ml_labels[-ML_SAMPLE_WINDOW:]

    if g.day_count >= 3:
        try:
            today_f = compute_ml_features(context)
            if today_f is not None:
                g.pending_features = today_f
        except:
            g.pending_features = None

    if len(g.ml_features) >= ML_MIN_SAMPLES and g.day_count % 5 == 0:
        try:
            train_ml_model()
        except Exception as e:
            log.info(f"[ML训练] 异常: {e}")

    g.prev_day_value = total_value

    ml_info = f"ML权重={'已训练' if g.ml_weights is not None else '未训练'} 样本={len(g.ml_features)}" if len(g.ml_features) > 0 else "ML=无数据"
    log.info(f"=== 盘后 === 总资产:{total_value:,.0f} | 日收益:{daily_pnl:.2%} | 持仓:{len(context.portfolio.positions)} | "
             f"连亏:{g.consecutive_loss_days}天 | 净值高点回撤:{(total_value/g.peak_value-1):.1%} | {ml_info}")


def compute_ml_features(context):
    hs300 = '000300.XSHG'
    zz1000 = '000852.XSHG'

    hs300_hist = attribute_history(hs300, 60, '1d', ['close'], df=False)
    hs300_c = hs300_hist['close'][-1]
    f1 = 1.0 if hs300_c > np.mean(hs300_hist['close'][-20:]) else 0.0
    f2 = 1.0 if hs300_c > np.mean(hs300_hist['close'][-60:]) else 0.0

    zz1000_hist = attribute_history(zz1000, 20, '1d', ['close'], df=False)
    f3 = 1.0 if zz1000_hist['close'][-1] > np.mean(zz1000_hist['close'][-20:]) else 0.0

    f4 = float(len(g.target_list))

    rets = np.diff(hs300_hist['close'][-10:]) / hs300_hist['close'][-10:-1]
    f5 = float(np.std(rets) * np.sqrt(252))

    if len(g.recent_pnls) >= 3:
        f6 = float(sum(1 for p in g.recent_pnls if p > 0) / len(g.recent_pnls))
    else:
        f6 = 0.5

    f7 = float(g.consecutive_loss_days)
    f8 = float((context.portfolio.total_value / g.peak_value - 1)) if g.peak_value > 0 else 0.0
    f9 = float(g.yesterday_buy_count)
    f10 = float(context.portfolio.available_cash / max(context.portfolio.total_value, 1))

    f11 = float(hs300_c / hs300_hist['close'][-5] - 1) if len(hs300_hist['close']) >= 5 else 0.0
    f12 = float(zz1000_hist['close'][-1] / zz1000_hist['close'][-5] - 1) if len(zz1000_hist['close']) >= 5 else 0.0

    hs300_ret5 = (hs300_c / hs300_hist['close'][-5] - 1) if len(hs300_hist['close']) >= 5 else 0.0
    zz1000_ret5 = (zz1000_hist['close'][-1] / zz1000_hist['close'][-5] - 1) if len(zz1000_hist['close']) >= 5 else 0.0
    f13 = float(hs300_ret5 - zz1000_ret5)

    return np.array([1.0, f1, f2, f3, f4/50.0, f5, f6, f7/5.0, f8, f9/5.0, f10, f11*10.0, f12*10.0, f13*10.0])


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


def train_ml_model():
    X = np.array(g.ml_features)
    y = np.array(g.ml_labels)

    loss_mask = (y == 0)
    X_loss = X[loss_mask]
    y_loss = y[loss_mask]
    X_aug = np.vstack([X, X_loss])
    y_aug = np.concatenate([y, y_loss])

    w = np.zeros(X_aug.shape[1])

    for iteration in range(10):
        z = np.dot(X_aug, w)
        p = sigmoid(z)
        p = np.clip(p, 0.01, 0.99)

        grad = np.dot(X_aug.T, (p - y_aug))
        W = p * (1 - p)
        H = np.dot(X_aug.T * W, X_aug) + 0.01 * np.eye(X_aug.shape[1])

        try:
            w -= np.linalg.solve(H, grad)
        except:
            break

    g.ml_weights = w

    pred = sigmoid(np.dot(X, w))
    acc = np.mean((pred > 0.5) == (y > 0.5))
    loss_recall = np.mean((pred[loss_mask] < 0.5))
    pos_rate = np.mean(y)
    log.info(f"[ML训练-代价敏感] 样本={len(y)}(增强{len(y_aug)}) 准确率={acc:.1%} 亏损召回={loss_recall:.1%} 盈利日占比={pos_rate:.1%}")


# ==================== 辅助函数 ====================
def get_hl_count_df(hl_list, y_day, watch_days):
    if not hl_list:
        return pd.DataFrame(columns=['count', 'extreme_count'])
    df = get_price(hl_list, end_date=y_day, frequency='daily',
                   fields=['close', 'high_limit', 'low', 'open'],
                   count=watch_days, panel=False, fill_paused=False, skip_paused=False)
    if df.empty:
        return pd.DataFrame(index=hl_list, data={'count': 0, 'extreme_count': 0})
    df['is_limit'] = df['close'] == df['high_limit']
    df['is_yizi'] = (df['low'] == df['high_limit']) & df['is_limit']
    df['is_tzi'] = (df['open'] == df['high_limit']) & df['is_limit'] & (df['low'] < df['high_limit'])
    df['is_extreme'] = df['is_yizi'] | df['is_tzi']
    counts = df.groupby('code')[['is_limit', 'is_extreme']].sum().astype(int)
    counts.columns = ['count', 'extreme_count']
    counts = counts.reindex(hl_list, fill_value=0)
    return counts


def filter_excessive_limit_days(stock_list, y_day):
    limit_up_df = get_hl_count_df(stock_list, y_day, 5)
    qualified_stocks = limit_up_df[limit_up_df['count'] < MAX_LIMIT_DAYS_5].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"因近5日涨停天数>={MAX_LIMIT_DAYS_5}被排除: {len(excluded)}只")
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
    qualified = chg[chg <= MAX_VOLATILITY_5].index.tolist()
    excluded_n = len(stock_list) - len(qualified)
    if excluded_n:
        log.info(f"因近5日波动超过{MAX_VOLATILITY_5*100:.0f}%被排除: {excluded_n}只")
    return qualified


def filter_below_n_high(stock_list, y_day, days=100, min_ratio=None):
    if min_ratio is None:
        min_ratio = HIGH_POINT_RATIO
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
    log.info(f"前{days}日最高价过滤: 保留{len(qualified)}/{len(stock_list)}只")
    return qualified


def calculate_zyts(s, context):
    high_prices = attribute_history(s, 101, '1d', fields=['high'], skip_paused=True)['high']
    prev_high = high_prices.iloc[-1]
    zyts_0 = next((i-1 for i, high in enumerate(high_prices[-3::-1], 2) if high >= prev_high), 100)
    return zyts_0 + 5


def get_record_sell(context, stock, reason):
    try:
        pos = context.portfolio.positions.get(stock)
        if pos is None or pos.avg_cost <= 0:
            return
        current_data = get_current_data()
        price = current_data[stock].last_price
        cost = pos.avg_cost
        pct = (price - cost) / cost
        cond = g.information.get(stock, '未知条件')

        if cond not in g.condition_stats:
            g.condition_stats[cond] = {'win': 0, 'loss': 0, 'win_pct': 0.0, 'loss_pct': 0.0}

        st = g.condition_stats[cond]
        if pct >= 0:
            st['win'] += 1
            st['win_pct'] += pct
        else:
            st['loss'] += 1
            st['loss_pct'] += pct

        name = g.name_cache.get(stock, '未知')
        log.info(f"[卖出统计] {stock}({name}) 条件={cond} 收益={pct:.2%} 原因={reason}")

        lines = ['[条件盈亏汇总]']
        for c, st in g.condition_stats.items():
            total = st['win'] + st['loss']
            avg_win = st['win_pct'] / st['win'] if st['win'] > 0 else 0
            avg_loss = st['loss_pct'] / st['loss'] if st['loss'] > 0 else 0
            lines.append(f"  {c}: 盈{st['win']}笔(均{avg_win:.2%}) 亏{st['loss']}笔(均{avg_loss:.2%}) 共{total}笔")
        log.info('\n'.join(lines))
    except Exception as e:
        log.error(f"get_record_sell出错: {e}")


def get_stocks_with_high_increase(initial_list, y_day):
    """筛选昨日涨幅>阈值的股票"""
    price_data = get_price(
        initial_list, end_date=y_day, frequency='1d',
        fields=['close'], count=2, panel=False,
        fill_paused=False, skip_paused=True
    )
    if price_data.empty:
        return []
    df = price_data.pivot(index='time', columns='code', values='close')
    if len(df) < 2:
        return []
    pct = df.pct_change().iloc[-1]
    result = pct[pct > MIN_YESTERDAY_PCT].index.tolist()
    return result


def get_stocks_with_limit_up(initial_list, y_day, lookback_days=4):
    """筛选近N日有过涨停的股票（收盘价>=涨停价-0.01）- 批量获取版"""
    # 批量获取近N日的收盘价和涨停价
    price_data = get_price(
        initial_list, end_date=y_day, frequency='1d',
        fields=['close', 'high_limit'], count=lookback_days, panel=False,
        fill_paused=False, skip_paused=True
    )
    if price_data.empty:
        return []

    # 计算是否涨停：close >= high_limit - 0.01
    price_data['is_limit'] = price_data['close'] >= (price_data['high_limit'] - 0.01)

    # 按股票分组，检查近N日是否有任意一天涨停
    limit_stocks = price_data[price_data['is_limit']].groupby('code')['is_limit'].any()
    result = limit_stocks[limit_stocks].index.tolist()

    return result


def prepare_stock_list(context):
    by_date = get_trade_days(end_date=context.previous_date, count=50)[0]
    all_s = get_all_securities(['stock'], date=by_date).index
    c_data = get_current_data()
    base_stocks = [
        s for s in all_s
        if s[0] not in ('3', '4', '8', '9')
        and not s.startswith('68')
        and not c_data[s].is_st
        and not c_data[s].paused
        and '退' not in c_data[s].name
        and 'ST' not in c_data[s].name
    ]
    return base_stocks


def filter_excessive_limit_up(stock_list, y_day):
    extreme_hl_df = get_hl_count_df(stock_list, y_day, 10)
    qualified_stocks = extreme_hl_df[extreme_hl_df['extreme_count'] < MAX_EXTREME_LIMIT_10].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"因前10日有{MAX_EXTREME_LIMIT_10}+一字/T字涨停被排除: {len(excluded)}只")
    return qualified_stocks


def calc_crowding_score(code, context, daily_bars, auction_volume=0, yesterday_volume=0):
    """
    计算筹码拥挤度评分

    返回：(总评分, 详情字典)
    0~3分：不拥挤 | 4~6分：中度拥挤 | 7~10分：高度拥挤 | 11+分：极度拥挤
    """
    score = 0
    details = {}

    if len(daily_bars) < 10:
        return score, details

    close_seq = [bar['close'] for bar in daily_bars]
    high_seq = [bar['high'] for bar in daily_bars]
    low_seq = [bar['low'] for bar in daily_bars]
    volume_seq = [bar['volume'] for bar in daily_bars]
    high_limit_seq = [bar['high_limit'] for bar in daily_bars]

    # 1. 近5日累计换手率（用成交量近似）
    if len(volume_seq) >= 6:
        avg_vol_5 = np.mean(volume_seq[-6:-1])
        if avg_vol_5 > 0:
            turnover_5d = sum(volume_seq[-5:]) / (avg_vol_5 * 5)
            details['turnover_5d'] = turnover_5d
            if turnover_5d > MAX_TURNOVER_5D:
                score += 2
            elif turnover_5d > 0.30:
                score += 1

    # 2. 近5日涨停次数
    limit_up_count_5d = 0
    for i in range(-5, 0):
        if abs(i) <= len(close_seq):
            if close_seq[i] >= high_limit_seq[i] - 0.01:
                limit_up_count_5d += 1
    details['limit_up_count_5d'] = limit_up_count_5d
    if limit_up_count_5d >= MAX_LIMIT_UP_COUNT_5D:
        score += 3
    elif limit_up_count_5d >= 3:
        score += 2
    elif limit_up_count_5d >= 2:
        score += 1

    # 3. 近10日一字/T字涨停次数
    extreme_limit_count = 0
    for i in range(-10, 0):
        if abs(i) <= len(close_seq):
            if close_seq[i] >= high_limit_seq[i] - 0.01:
                if low_seq[i] >= high_limit_seq[i] - 0.01:
                    extreme_limit_count += 1
                elif abs(i) < len(close_seq) and daily_bars[i]['open'] >= high_limit_seq[i] - 0.01:
                    extreme_limit_count += 1
    details['extreme_limit_count'] = extreme_limit_count
    if extreme_limit_count >= MAX_EXTREME_LIMIT_COUNT:
        score += 3
    elif extreme_limit_count >= 2:
        score += 2

    # 4. 近5日平均振幅
    if len(close_seq) >= 6:
        amplitudes = []
        for i in range(-5, 0):
            if abs(i) <= len(close_seq) and close_seq[i-1] > 0:
                amp = (high_seq[i] - low_seq[i]) / close_seq[i-1]
                amplitudes.append(amp)
        if amplitudes:
            avg_amplitude_5d = np.mean(amplitudes)
            details['avg_amplitude_5d'] = avg_amplitude_5d
            if avg_amplitude_5d > MAX_AVG_AMPLITUDE_5D:
                score += 2
            elif avg_amplitude_5d > 0.05:
                score += 1

    # 5. 量比（今日成交量/近5日均量）
    if len(volume_seq) >= 6:
        avg_vol_5 = np.mean(volume_seq[-6:-1])
        if avg_vol_5 > 0:
            vol_ratio = volume_seq[-1] / avg_vol_5
            details['vol_ratio'] = vol_ratio
            if vol_ratio > MAX_VOL_RATIO:
                score += 2
            elif vol_ratio > 2.0:
                score += 1

    # 6. 竞昨比（竞价量/昨日量）
    if yesterday_volume > 0 and auction_volume > 0:
        auction_ratio = auction_volume / yesterday_volume
        details['auction_ratio'] = auction_ratio
        if auction_ratio > MAX_AUCTION_RATIO:
            score += 2
        elif auction_ratio > 0.10:
            score += 1

    # 7. 连板天数
    consecutive_limit_up = 0
    for i in range(len(close_seq)-1, max(len(close_seq)-10, 0), -1):
        if close_seq[i] >= high_limit_seq[i] - 0.01:
            consecutive_limit_up += 1
        else:
            break
    details['consecutive_limit_up'] = consecutive_limit_up
    if consecutive_limit_up >= MAX_CONSECUTIVE_LIMIT_UP:
        score += 3
    elif consecutive_limit_up >= 2:
        score += 2

    return score, details
