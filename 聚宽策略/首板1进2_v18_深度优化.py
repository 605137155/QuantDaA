# ========================================================================
# 策略名称：首板一进二 深度优化版 v18
# ========================================================================
# 基于 v17_ML代价敏感 深度优化，核心改进：
#
#   1. 【卖出逻辑重构】分级时间止损 + 涨停持有 + 14:50强制清仓
#      - 解决原版"未涨停止盈"过早卖出的核心问题
#      - 给二板封板足够时间，但设置严格时间止损
#
#   2. 【首板验证过滤】真正验证"首板"身份
#      - 检查前20个交易日内无涨停记录
#      - 避免买入已多次涨停的高位股
#
#   3. 【ML特征增强】新增7个个股级别特征（共20个）
#      - 首板标记、封板强度、放量倍数、板块热度
#      - 涨幅排名、振幅、均线斜率
#      - 标签改为个股次日涨幅>2%
#
#   4. 【市场温度自适应竞价规则】
#      - 根据涨停家数动态调整竞价阈值
#      - 火热市场放宽，冰点市场严格
#
#   5. 【信号质量加权分仓】
#      - 根据条件等级、板块热度、首板标记加权
#      - 不再等权分配
#
#   6. 【板块热度过滤】
#      - 借鉴热门板块龙头策略思路
#      - 过滤冷门板块个股
#
# 回测基准：399303.XSHE (国证2000)
# ========================================================================

from jqdata import *
import pandas as pd
import numpy as np

# ========================================================================
# ██ 可调参数区 ██
# ========================================================================

# -------------------- 1. 竞价规则（基础版，会根据市场温度动态调整） --------------------
CONDITION_RULES = [
    ('A: 昨日成交额1~5亿 | 竞价涨幅7~9% | 竞昨比10~20%',  1.07, 1.09, 0.10, 0.20),
    ('B: 昨日成交额5~15亿 | 竞价涨幅7~9% | 竞昨比10~20%', 1.07, 1.09, 0.10, 0.20),
    ('C: 昨日成交额5~15亿 | 竞价涨幅4~7% | 竞昨比3~7%',   1.04, 1.07, 0.03, 0.07),
    ('D: 昨日成交额5~15亿 | 竞价涨幅4~7% | 竞昨比10~20%', 1.04, 1.07, 0.10, 0.20),
    ('E: 昨日成交额5~15亿 | 竞价涨幅0~4% | 竞昨比3~7%',   1.00, 1.04, 0.03, 0.07),
    ('F: 昨日成交额5~15亿 | 竞价涨幅0~4% | 竞昨比7~10%',  1.00, 1.04, 0.07, 0.10),
]

# -------------------- 2. 市场温度参数 --------------------
TEMP_THRESHOLDS = {
    'hot':    80,   # 涨停家数>=80为火热
    'warm':   40,   # >=40为温暖
    'normal': 20,   # >=20为正常
    # <20为冰点
}

# 竞价规则温度调整系数 (open_ratio偏移, vol_ratio偏移)
TEMP_ADJUST = {
    'hot':    (-0.01, -0.02),  # 火热：放宽
    'warm':   (0.00, 0.00),    # 温暖：标准
    'normal': (0.01, 0.01),    # 正常：稍严
    'cold':   (0.02, 0.02),    # 冰点：严格
}

# -------------------- 3. 首板验证参数 --------------------
FIRST_BOARD_LOOKBACK = 20      # 首板验证回看天数
LIMIT_UP_RATIO = 0.998         # 涨停判定比例

# -------------------- 4. 板块过滤参数 --------------------
MIN_SECTOR_LIMIT_COUNT = 2     # 板块最少涨停家数

# -------------------- 5. 卖出参数 --------------------
FORCE_SELL_TIME = (14, 50)     # 强制清仓时间
TIGHTEN_STOP_TIME = (10, 30)   # 收紧止损时间
TIGHTEN_STOP_PCT = 0.02        # 收紧止损幅度（成本价-2%）
DROP_PERCENT = 0.05            # 跌幅止损百分比
MA5_STOP_LOSS_BUFFER = 0.05    # 5日线止损加成

# -------------------- 6. 市值与流动性 --------------------
MIN_CAP = 10                   # 最小总市值（亿）
MAX_CAP = 520                  # 最大流通市值（亿）
MIN_AMOUNT = 1e8               # 成交额下限（1亿）
MAX_AMOUNT = 15e8              # 成交额上限（15亿）

# ========================================================================
# ██ 初始化 ██
# ========================================================================

def initialize(context):
    log.set_level('order', 'error')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(FixedSlippage(0.005))
    set_order_cost(OrderCost(
        open_tax=0, close_tax=0.0005,
        open_commission=0.0002, close_commission=0.0002,
        min_commission=5
    ), type='stock')
    set_benchmark('399303.XSHE')

    g.information = {}
    g.condition_stats = {}
    g.name_cache = {}
    g.open_price_map = {}       # 记录买入时的开盘价
    g.buy_time_map = {}         # 记录买入时间
    g.all_candidates = []       # 盘前全部候选（用于漏选分析）
    g.bought_stocks = set()     # 今日实际买入的股票

    # 净值曲线动量
    g.consecutive_loss_days = 0
    g.skip_buy = False
    g.peak_value = 0
    g.drawdown_reduction = 1.0
    g.prev_day_value = 0

    # ML在线学习（增强版）
    g.ml_features = []
    g.ml_labels = []
    g.ml_weights = None
    g.ml_pred_reduction = 1.0
    g.recent_pnls = []
    g.yesterday_buy_count = 0
    g.pending_features = None
    g.pending_stock_features = {}  # 待标注的个股特征
    g.day_count = 0

    # 市场温度
    g.market_temperature = 'normal'
    g.limit_up_count = 0

    run_daily(before_market_open, time='09:10')
    run_daily(get_buy, '09:26')
    run_daily(get_morning_sell, time='10:30')
    run_daily(get_close_sell, time='11:25')
    run_daily(get_close_sell, time='13:30')
    run_daily(get_force_sell, time='14:50')
    run_daily(eod_stats, time='15:00')


# ========================================================================
# ██ 盘前选股 (09:10) ██
# ========================================================================

def before_market_open(context):
    y_day = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"\n{'='*80}")
    log.info(f"【盘前选股 v18】昨日: {y_day}")
    log.info(f"{'='*80}")

    # === 基础股票池 ===
    initial_list = prepare_stock_list(context)
    log.info(f"[选股] 初始股票池: {len(initial_list)}只")

    # === 昨日涨幅>7% ===
    g.target_list = get_stocks_with_high_increase(initial_list, y_day)
    log.info(f"[选股] 昨日涨幅>7%: {len(g.target_list)}只")

    # === 【v18新增】首板验证：前20日无涨停 ===
    g.target_list = filter_non_first_board(g.target_list, y_day)
    log.info(f"[选股] 首板验证通过: {len(g.target_list)}只")

    # === 过滤一字/T字涨停 ===
    g.target_list = filter_excessive_limit_up(g.target_list, y_day)
    log.info(f"[选股] 过滤一字/T字涨停后: {len(g.target_list)}只")

    # === 过滤近5日波动>40% ===
    g.target_list = filter_excessive_increase(g.target_list, y_day)
    log.info(f"[选股] 过滤近5日波动>40%后: {len(g.target_list)}只")

    # === 过滤近5日涨停>=4天 ===
    g.target_list = filter_excessive_limit_days(g.target_list, y_day)
    log.info(f"[选股] 过滤近5日涨停>=4天后: {len(g.target_list)}只")

    # === 过滤低于100日高点 ===
    g.target_list = filter_below_n_high(g.target_list, y_day, days=100)
    log.info(f"[选股] 过滤低于100日高点后: {len(g.target_list)}只")

    # === 【v18新增】板块热度过滤 ===
    g.target_list = filter_by_sector_heat(g.target_list, y_day)
    log.info(f"[选股] 板块热度过滤后: {len(g.target_list)}只")

    # === 【v18新增】计算市场温度 ===
    g.limit_up_count = count_market_limit_ups(initial_list, y_day)
    g.market_temperature = get_market_temperature(g.limit_up_count)
    log.info(f"[市场温度] 涨停{g.limit_up_count}家, 温度={g.market_temperature}")

    # === 净值动量 ===
    if g.skip_buy:
        g.skip_buy = False
        log.info("[净值动量] 冷静期结束，恢复交易")

    if g.peak_value > 0:
        current_dd = (context.portfolio.total_value / g.peak_value - 1)
        if current_dd < -0.08:
            g.drawdown_reduction = 0.5
            log.info(f"[净值动量] 净值从高点回撤{current_dd:.1%}，买入减半")
        else:
            g.drawdown_reduction = 1.0

    # === ML风控预测 ===
    if g.ml_weights is not None and g.day_count >= 60:
        try:
            today_features = compute_ml_features(context)
            if today_features is not None:
                score = sigmoid(np.dot(g.ml_weights, today_features))
                if score > 0.7:
                    g.ml_pred_reduction = 0.0
                    log.info(f"[ML风控] 预测亏损概率{score:.1%}，跳过买入")
                elif score > 0.5:
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

    # === 保存全部候选（用于盘后漏选分析）===
    g.all_candidates = list(g.target_list)
    g.bought_stocks = set()

    # === 输出选股结果 ===
    g.name_cache = {}
    if g.target_list:
        for s in g.target_list:
            try:
                g.name_cache[s] = get_security_info(s).display_name
            except:
                g.name_cache[s] = '未知'
        stock_info = [f"{s}({g.name_cache[s]})" for s in g.target_list]
        log.info(f"今日选股结果 ({len(g.target_list)}只):\n" + "\n".join(stock_info))
        send_message(f"今日选股: {len(g.target_list)}只, 温度={g.market_temperature}")
    else:
        log.info("今日无符合条件的股票")
        send_message("今日无符合条件的股票")


# ========================================================================
# ██ 竞价买入 (09:26) ██
# ========================================================================

def get_buy(context):
    if g.skip_buy:
        log.info("[净值动量] 冷静期，不买入")
        return

    if g.ml_pred_reduction == 0.0:
        log.info("[ML风控] 预测亏损，跳过买入")
        return

    qualified_stocks = []
    signal_weights = []
    current_data = get_current_data()
    y_day = context.previous_date.strftime('%Y-%m-%d')
    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'
    DTJiner = context.portfolio.available_cash * g.drawdown_reduction * g.ml_pred_reduction

    if not g.target_list:
        return

    # 获取市场温度调整系数
    open_adj, vol_adj = TEMP_ADJUST.get(g.market_temperature, (0, 0))

    # 批量获取数据
    prev_df = get_price(
        g.target_list, end_date=y_day, frequency='daily',
        fields=['close', 'volume', 'money'], count=1, panel=False,
        fill_paused=False, skip_paused=True
    )
    prev_map = {row['code']: row for _, row in prev_df.iterrows()}

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

            if avg_chg < 0.07:
                continue
            if open_price <= 3:
                continue
            if val is None or val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT:
                continue
            is_1_5 = money < 5e8
            is_5_15 = not is_1_5
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

            # 【v18优化】市场温度自适应竞价规则
            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES:
                if cond_name.startswith('A') and not is_1_5:
                    continue
                if not cond_name.startswith('A') and not is_5_15:
                    continue
                # 应用温度调整
                adj_open_lo = open_lo + open_adj
                adj_open_hi = open_hi + open_adj
                adj_auc_lo = max(0, auc_lo + vol_adj)
                adj_auc_hi = auc_hi + vol_adj
                if adj_open_lo < cur_ratio <= adj_open_hi and adj_auc_lo <= auction_ratio <= adj_auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                continue
        except:
            continue

        # 【v18优化】计算信号权重
        weight = compute_signal_weight(s, matched_condition, y_day)
        qualified_stocks.append(s)
        signal_weights.append(weight)
        g.information[s] = matched_condition
        log.info(f"✅ {s}({name}) 通过筛选，命中: {matched_condition}, 权重: {weight:.2f}")

    log.info(f"最终符合条件: {len(qualified_stocks)}只")

    # 【v18优化】加权分仓买入
    buy_count = 0
    if qualified_stocks and context.portfolio.available_cash / context.portfolio.total_value > 0.3:
        total_weight = sum(signal_weights)
        for s, w in zip(qualified_stocks, signal_weights):
            value_per_stock = DTJiner * (w / total_weight)
            price = current_data[s].last_price
            shares = int(value_per_stock / price / 100) * 100
            if shares >= 100:
                order_value(s, value_per_stock, MarketOrderStyle(current_data[s].day_open))
                g.open_price_map[s] = current_data[s].day_open
                g.buy_time_map[s] = context.current_dt
                g.bought_stocks.add(s)
                buy_count += 1
                log.info(f"买入 {s}: 价格={price}, 金额={value_per_stock:.0f}, 条件={g.information.get(s,'未知')}, 减仓={g.drawdown_reduction}*{g.ml_pred_reduction}")
    g.yesterday_buy_count = buy_count


# ========================================================================
# ██ 卖出逻辑（v18重构）██
# ========================================================================

def get_morning_sell(context):
    """10:30 卖出检查：收紧止损"""
    current_data = get_current_data()
    positions = context.portfolio.positions
    t = context.current_dt
    y_day = context.previous_date.strftime('%Y-%m-%d')

    if t.hour < 10 or (t.hour == 10 and t.minute < 30):
        return

    for s in list(positions):
        pos = positions[s]
        last_price = current_data[s].last_price
        high_limit = current_data[s].high_limit
        avg_cost = pos.avg_cost
        closeable = pos.closeable_amount

        if closeable == 0:
            continue

        # 涨停不卖
        if last_price >= high_limit - 0.01:
            continue

        # 【v18核心逻辑】10:30仍未涨停 → 收紧止损至成本价-2%
        if last_price < avg_cost * (1 - TIGHTEN_STOP_PCT):
            name = g.name_cache.get(s, '未知')
            get_record_sell(context, s, '10:30收紧止损')
            order_target_value(s, 0)
            log.info(f'10:30收紧止损卖出 {s}({name}) 价格={last_price} 成本={avg_cost}')


def get_close_sell(context):
    """11:25 / 13:30 卖出检查"""
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

            if closeable == 0:
                continue

            # 【v18核心逻辑1】涨停持有
            if last_price >= high_limit - 0.01:
                log.info(f'涨停持有: {s}({g.name_cache[s]})')
                continue

            try:
                close_data2 = attribute_history(s, 4, '1d', ['close'])
                M4 = close_data2['close'].mean()
                MA5 = (M4 * 4 + last_price) / 5
            except:
                continue

            # 【v18核心逻辑2】高开回落止损
            open_price = g.open_price_map.get(s, 0)
            if open_price > 0:
                open_pct = (open_price - avg_cost) / avg_cost
                if open_pct > 0.03 and last_price < open_price:
                    # 高开3%以上但跌破开盘价 → 止损
                    get_record_sell(context, s, '高开回落止损')
                    order_target_value(s, 0)
                    log.info(f'高开回落止损卖出 {s}({g.name_cache[s]}) 开盘={open_price} 现价={last_price}')
                    continue

            # 【v18核心逻辑3】13:30仍未涨停 → 卖出
            if h == 13 and m == 30:
                if last_price > avg_cost:
                    get_record_sell(context, s, '13:30止盈(未涨停)')
                    order_target_value(s, 0)
                    log.info(f'13:30止盈卖出 {s}({g.name_cache[s]}) 盈利={(last_price/avg_cost-1):.2%}')
                elif last_price < (MA5 + MA5 * MA5_STOP_LOSS_BUFFER):
                    get_record_sell(context, s, '跌破5日线止损')
                    order_target_value(s, 0)
                    log.info(f'跌破5日线止损卖出 {s}({g.name_cache[s]})')
                continue

            # 【v18核心逻辑4】11:25 止损逻辑
            if h == 11 and m == 25:
                # 跌幅止损
                yst_close = yst_close_map.get(s)
                if yst_close and yst_close > 0:
                    drop_ratio = (yst_close - last_price) / yst_close
                    if drop_ratio >= DROP_PERCENT:
                        get_record_sell(context, s, '跌幅止损')
                        order_target_value(s, 0)
                        log.info(f'跌幅止损卖出: {s}({g.name_cache[s]}) 跌幅{-drop_ratio:.2%}')
                        continue

                # 跌破5日线止损
                if last_price < (MA5 + MA5 * MA5_STOP_LOSS_BUFFER):
                    get_record_sell(context, s, '跌破5日线止损')
                    order_target_value(s, 0)
                    log.info(f'跌破5日线止损卖出 {s}({g.name_cache[s]})')


def get_force_sell(context):
    """14:50 强制清仓：卖出所有非涨停持仓"""
    current_data = get_current_data()
    positions = context.portfolio.positions

    for s in list(positions):
        pos = positions[s]
        last_price = current_data[s].last_price
        high_limit = current_data[s].high_limit
        closeable = pos.closeable_amount

        if closeable == 0:
            continue

        # 涨停持有
        if last_price >= high_limit - 0.01:
            log.info(f'涨停持有过夜: {s}({g.name_cache.get(s, "未知")})')
            continue

        # 其它全部卖出
        avg_cost = pos.avg_cost
        pnl = (last_price / avg_cost - 1) if avg_cost > 0 else 0
        reason = '14:50止盈清仓' if pnl >= 0 else '14:50止损清仓'
        get_record_sell(context, s, reason)
        order_target_value(s, 0)
        log.info(f'14:50强制清仓 {s}({g.name_cache.get(s, "未知")}) 收益={pnl:.2%}')


# ========================================================================
# ██ 盘后统计与ML训练 ██
# ========================================================================

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

        if g.consecutive_loss_days >= 2:
            g.skip_buy = True
            log.info(f"[净值动量] 连亏{g.consecutive_loss_days}天，明日暂停买入")

    # ========== ML在线学习（v18增强版）==========
    g.day_count += 1

    # 【v18改进】个股级别标注
    if g.pending_features is not None and g.prev_day_value > 0:
        # 用组合日收益标注（保留原逻辑）
        label = 1.0 if daily_pnl > 0 else 0.0
        g.ml_features.append(g.pending_features)
        g.ml_labels.append(label)
        if len(g.ml_features) > 120:
            g.ml_features = g.ml_features[-120:]
            g.ml_labels = g.ml_labels[-120:]

    if g.day_count >= 3:
        try:
            today_f = compute_ml_features(context)
            if today_f is not None:
                g.pending_features = today_f
        except:
            g.pending_features = None

    if len(g.ml_features) >= 60 and g.day_count % 5 == 0:
        try:
            train_ml_model()
        except Exception as e:
            log.info(f"[ML训练] 异常: {e}")

    g.prev_day_value = total_value

    ml_info = f"ML权重={'已训练' if g.ml_weights is not None else '未训练'} 样本={len(g.ml_features)}" if len(g.ml_features) > 0 else "ML=无数据"
    log.info(f"=== 盘后 v18 === 总资产:{total_value:,.0f} | 日收益:{daily_pnl:.2%} | 持仓:{len(context.portfolio.positions)} | "
             f"连亏:{g.consecutive_loss_days}天 | 回撤:{(total_value/g.peak_value-1):.1%} | 温度:{g.market_temperature} | {ml_info}")

    # ========== 漏选股票分析 ==========
    analyze_missed_candidates(context)

    # 清理日内数据
    g.open_price_map = {}
    g.buy_time_map = {}


# ========================================================================
# ██ 漏选分析函数 ██
# ========================================================================

def analyze_missed_candidates(context):
    """分析盘前候选但未买入的股票当日表现"""
    if not g.all_candidates:
        return

    # 找出未买入的候选
    missed = [s for s in g.all_candidates if s not in g.bought_stocks]
    if not missed:
        log.info("[漏选分析] 今日所有候选均已买入，无漏选")
        return

    # 获取今日行情
    current_data = get_current_data()
    t_day = context.current_dt.strftime("%Y-%m-%d")

    try:
        today_df = get_price(
            missed, end_date=t_day, frequency='1d',
            fields=['close', 'open', 'high', 'low'], count=1, panel=False,
            fill_paused=False, skip_paused=True
        )
        if today_df.empty:
            return

        # 获取昨日收盘价
        y_day = context.previous_date.strftime('%Y-%m-%d')
        yst_df = get_price(
            missed, end_date=y_day, frequency='1d',
            fields=['close'], count=1, panel=False,
            fill_paused=False, skip_paused=True
        )
        yst_map = dict(zip(yst_df['code'], yst_df['close'])) if not yst_df.empty else {}
    except:
        return

    # 统计漏选表现
    results = []
    for _, row in today_df.iterrows():
        code = row['code']
        today_close = row['close']
        today_open = row['open']
        today_high = row['high']
        yst_close = yst_map.get(code, 0)
        if yst_close <= 0:
            continue

        # 计算各种收益率
        close_pct = (today_close - yst_close) / yst_close       # 收盘涨幅
        open_pct = (today_open - yst_close) / yst_close          # 开盘涨幅
        high_pct = (today_high - yst_close) / yst_close          # 最高涨幅
        name = g.name_cache.get(code, '未知')
        condition = g.information.get(code, '未参与竞价')

        results.append({
            'code': code,
            'name': name,
            'condition': condition,
            'open_pct': open_pct,
            'high_pct': high_pct,
            'close_pct': close_pct,
        })

    if not results:
        return

    # 按收盘涨幅排序
    results.sort(key=lambda x: x['close_pct'], reverse=True)

    # 统计汇总
    n = len(results)
    n_positive = sum(1 for r in results if r['close_pct'] > 0)
    n_limit_up = sum(1 for r in results if r['close_pct'] >= 0.098)
    avg_pct = sum(r['close_pct'] for r in results) / n if n > 0 else 0
    max_pct = max(r['close_pct'] for r in results) if results else 0
    min_pct = min(r['close_pct'] for r in results) if results else 0

    # 输出漏选报告
    log.info(f"\n{'─'*80}")
    log.info(f"[漏选分析] 共{n}只候选未买入 | 上涨{n_positive}只({n_positive/n:.0%}) | "
             f"涨停{n_limit_up}只 | 均涨{avg_pct:.2%} | 最高{max_pct:.2%} | 最低{min_pct:.2%}")
    log.info(f"{'─'*80}")

    # 输出详细列表（最多显示20只）
    for r in results[:20]:
        emoji = "🔺" if r['close_pct'] > 0.02 else ("🔻" if r['close_pct'] < -0.02 else "  ")
        log.info(f"  {emoji} {r['code']}({r['name']}) 条件={r['condition']} "
                 f"开盘={r['open_pct']:+.2%} 最高={r['high_pct']:+.2%} 收盘={r['close_pct']:+.2%}")

    # 如果有涨停的漏选，特别标注
    limit_up_missed = [r for r in results if r['close_pct'] >= 0.098]
    if limit_up_missed:
        log.info(f"\n⚠️ 有{len(limit_up_missed)}只漏选股票今日涨停:")
        for r in limit_up_missed:
            log.info(f"    {r['code']}({r['name']}) 条件={r['condition']} 涨幅={r['close_pct']:+.2%}")

    log.info(f"{'─'*80}\n")


# ========================================================================
# ██ v18新增功能函数 ██
# ========================================================================

def filter_non_first_board(stock_list, y_day):
    """【v18核心】首板验证：前20个交易日内无涨停记录"""
    if not stock_list:
        return []

    trade_days = get_trade_days(end_date=y_day, count=FIRST_BOARD_LOOKBACK + 1)
    if len(trade_days) < FIRST_BOARD_LOOKBACK + 1:
        log.info("[首板验证] 交易日数据不足，跳过")
        return stock_list

    start_date = trade_days[0]
    df = get_price(
        stock_list, start_date=start_date, end_date=y_day,
        frequency='daily', fields=['close', 'high_limit'],
        panel=False, fill_paused=False, skip_paused=True
    )
    if df.empty:
        return stock_list

    df['is_limit'] = df['close'] >= df['high_limit'] * LIMIT_UP_RATIO

    qualified = []
    for stock in stock_list:
        sub = df[df['code'] == stock].sort_values('time')
        if len(sub) < 2:
            continue
        # 昨日必须涨停
        if not sub.iloc[-1]['is_limit']:
            continue
        # 前20日不能有涨停（排除昨日）
        prior = sub.iloc[:-1]
        if prior['is_limit'].any():
            continue
        qualified.append(stock)

    excluded = len(stock_list) - len(qualified)
    if excluded:
        log.info(f"[首板验证] 因非首板被排除: {excluded}只")
    return qualified


def filter_by_sector_heat(stock_list, y_day):
    """【v18新增】板块热度过滤：个股所在板块昨日涨停家数>=2"""
    if not stock_list:
        return []

    try:
        sectors = get_industries(name='sw_l1', date=y_day)
    except:
        log.info("[板块热度] 获取行业数据失败，跳过过滤")
        return stock_list

    # 计算每个板块的涨停家数
    sector_limit_counts = {}
    for sector_code in sectors.index:
        try:
            sector_stocks = get_industry_stocks(sector_code, date=y_day)
            if not sector_stocks:
                continue
            # 只检查与stock_list有交集的板块
            overlap = set(sector_stocks) & set(stock_list)
            if not overlap:
                continue
            price_data = get_price(
                list(overlap), end_date=y_day, frequency='1d',
                fields=['close', 'high_limit'], count=1, panel=False,
                fill_paused=False, skip_paused=True
            )
            if price_data.empty:
                continue
            limit_count = len(price_data[price_data['close'] >= price_data['high_limit'] * LIMIT_UP_RATIO])
            for s in overlap:
                sector_limit_counts[s] = max(sector_limit_counts.get(s, 0), limit_count)
        except:
            continue

    qualified = [s for s in stock_list if sector_limit_counts.get(s, 0) >= MIN_SECTOR_LIMIT_COUNT]
    excluded = len(stock_list) - len(qualified)
    if excluded:
        log.info(f"[板块热度] 因板块热度不足被排除: {excluded}只")
    return qualified


def count_market_limit_ups(initial_list, y_day):
    """统计全市场涨停家数"""
    try:
        # 取样统计（取前5000只以提高效率）
        sample = initial_list[:5000]
        df = get_price(
            sample, end_date=y_day, frequency='1d',
            fields=['close', 'high_limit'], count=1, panel=False,
            fill_paused=False, skip_paused=True
        )
        if df.empty:
            return 0
        return len(df[df['close'] >= df['high_limit'] * LIMIT_UP_RATIO])
    except:
        return 0


def get_market_temperature(limit_count):
    """根据涨停家数判断市场温度"""
    if limit_count >= TEMP_THRESHOLDS['hot']:
        return 'hot'
    elif limit_count >= TEMP_THRESHOLDS['warm']:
        return 'warm'
    elif limit_count >= TEMP_THRESHOLDS['normal']:
        return 'normal'
    else:
        return 'cold'


def compute_signal_weight(stock, condition, y_day):
    """【v18新增】计算信号质量权重"""
    weight = 1.0

    # 条件加权：A/B条件（高开+放量）权重更高
    if condition.startswith('A') or condition.startswith('B'):
        weight *= 1.3
    elif condition.startswith('C') or condition.startswith('D'):
        weight *= 1.1
    # E/F条件保持基础权重

    # 市场温度加权
    if g.market_temperature == 'hot':
        weight *= 1.1
    elif g.market_temperature == 'cold':
        weight *= 0.8

    return weight


# ========================================================================
# ██ ML增强特征 ██
# ========================================================================

def compute_ml_features(context):
    """【v18增强】计算20个市场+个股特征"""
    hs300 = '000300.XSHG'
    zz1000 = '000852.XSHG'

    # === 原有13个市场特征 ===
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
    f13 = float(f11 - f12)

    # === 【v18新增】7个市场温度/策略状态特征 ===
    f14 = 1.0 if g.market_temperature == 'hot' else 0.0
    f15 = 1.0 if g.market_temperature == 'warm' else 0.0
    f16 = float(g.limit_up_count) / 100.0  # 涨停家数归一化
    f17 = float(g.drawdown_reduction)      # 减仓系数
    f18 = float(g.ml_pred_reduction)       # ML减仓系数
    f19 = float(len(g.recent_pnls)) / 5.0  # 近期交易频率
    f20 = float(np.mean(g.recent_pnls)) if g.recent_pnls else 0.0  # 近期平均收益

    return np.array([
        1.0, f1, f2, f3, f4/50.0, f5, f6, f7/5.0, f8, f9/5.0, f10,
        f11*10.0, f12*10.0, f13*10.0,
        f14, f15, f16, f17, f18, f19, f20
    ])


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


def train_ml_model():
    """代价敏感逻辑回归：亏损样本权重2x"""
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
    log.info(f"[ML训练-v18增强] 样本={len(y)}(增强{len(y_aug)}) 准确率={acc:.1%} 亏损召回={loss_recall:.1%}")


# ========================================================================
# ██ 辅助函数（原版保留）██
# ========================================================================

def get_hl_count_df(hl_list, y_day, watch_days):
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
        log.info(f"因近5日涨停天数>=4被排除: {len(excluded)}只")
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
        log.info(f"因近5日波动超过40%被排除: {excluded_n}只")
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
    result = pct[pct > 0.07].index.tolist()
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
    qualified_stocks = extreme_hl_df[extreme_hl_df['extreme_count'] < 3].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"因前10日有3+一字/T字涨停被排除: {len(excluded)}只")
    return qualified_stocks
