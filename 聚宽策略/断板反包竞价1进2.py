# ========================================================================
# 策略名称：断板反包 竞价1进2
# ========================================================================
# 核心思路：
#   1. 两日前涨停（有资金关注）
#   2. 昨日断板（洗盘/调整，没有涨停）
#   3. 今日竞价上涨（资金再次启动）
#   4. 买入后持有，等待上涨
#   5. 止盈止损离场
#
# 这种形态叫"断板反包"，是强势股的经典启动形态
# 目标：买在第二波启动前，吃完整波段
# ========================================================================


# ========================================================================
# ██ 可调参数区 ██
# ========================================================================

# -------------------- 1. 选股条件 --------------------

# 涨停判断阈值（收盘价 >= 涨停价 * 此比例）
LIMIT_UP_RATIO = 0.998  # 0.998 = 99.8%

# 昨日断板条件：
# 1. 昨日没有涨停
# 2. 昨日收盘价 > 前日收盘价 * 此比例（不能跌太多）
MIN_YESTERDAY_CLOSE_RATIO = 0.95  # 昨日收盘 >= 前日收盘 * 95%

# 今日竞价上涨条件：
# 竞价涨幅 >= 此值才买入
MIN_AUCTION_PCT = 0.00  # 竞价涨幅 >= 0%（平开或高开）

# -------------------- 2. 成交额过滤 --------------------
MIN_AMOUNT = 3e8          # 最小成交额（3亿）
MAX_AMOUNT = 50e8         # 最大成交额（50亿）

# -------------------- 3. 风控参数 --------------------
DROP_STOP_LOSS = 0.05     # 跌幅止损阈值（5%）
MA5_STOP_LOSS_BUFFER = 0.05  # 5日线止损加成
DRAWDOWN_THRESHOLD = 0.08    # 净值回撤减仓阈值
CONSECUTIVE_LOSS_PAUSE = 2   # 连亏天数暂停阈值

# ========================================================================
# ██ 可调参数区结束 ██
# ========================================================================


from jqdata import *
import pandas as pd
import numpy as np


def initialize(context):
    log.set_level('order', 'error')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(FixedSlippage(0.005))
    set_order_cost(OrderCost(open_tax=0, close_tax=0.0005, open_commission=0.0002, close_commission=0.0002, min_commission=5), type='stock')
    set_benchmark('399303.XSHE')

    g.information = {}
    g.name_cache = {}
    g.target_list = []

    # 净值动量
    g.consecutive_loss_days = 0
    g.skip_buy = False
    g.peak_value = 0
    g.drawdown_reduction = 1.0
    g.prev_day_value = 0

    run_daily(before_market_open, time='09:10')
    run_daily(get_buy, '09:26')
    run_daily(get_close_sell, time='11:25')
    run_daily(get_close_sell, time='13:30')
    run_daily(eod_stats, time='15:00')


def before_market_open(context):
    """盘前：找断板反包形态的股票"""
    y_day = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"\n{'='*80}")
    log.info(f"【盘前选股】{y_day}")
    log.info(f"{'='*80}")

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

    # ===== 第一步：先找两日前涨停的股票 =====
    # 获取所有股票
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

    # 获取近3日行情数据（只取收盘价和涨停价，减少数据量）
    price_data = get_price(
        base_stocks, end_date=y_day, frequency='1d',
        fields=['close', 'high_limit'], count=3, panel=False,
        fill_paused=False, skip_paused=True
    )

    if price_data.empty:
        log.info("无行情数据")
        g.target_list = []
        return

    # 找出两日前涨停的股票
    limit_up_stocks = []
    for code in base_stocks:
        stock_data = price_data[price_data['code'] == code].sort_values('time')
        if len(stock_data) < 3:
            continue
        day_minus_2 = stock_data.iloc[0]
        # 两日前涨停
        if day_minus_2['close'] >= day_minus_2['high_limit'] * LIMIT_UP_RATIO:
            limit_up_stocks.append(code)

    log.info(f"两日前涨停股票：{len(limit_up_stocks)}只")

    if not limit_up_stocks:
        log.info("无两日前涨停股票")
        g.target_list = []
        return

    # ===== 第二步：对涨停股票做详细筛选 =====
    # 获取详细行情数据
    detail_data = get_price(
        limit_up_stocks, end_date=y_day, frequency='1d',
        fields=['close', 'open', 'high', 'low', 'high_limit', 'volume', 'money'], count=3, panel=False,
        fill_paused=False, skip_paused=True
    )

    candidates = []
    g.name_cache = {}

    for code in limit_up_stocks:
        stock_data = detail_data[detail_data['code'] == code].sort_values('time')
        if len(stock_data) < 3:
            continue

        day_minus_2 = stock_data.iloc[0]  # 两日前
        day_minus_1 = stock_data.iloc[1]  # 昨日

        # 条件2：昨日断板（没有涨停）
        if day_minus_1['close'] >= day_minus_1['high_limit'] * LIMIT_UP_RATIO:
            continue

        # 条件3：昨日收盘不能跌太多
        if day_minus_1['close'] < day_minus_2['close'] * MIN_YESTERDAY_CLOSE_RATIO:
            continue

        # 条件4：成交额过滤
        if day_minus_1['money'] < MIN_AMOUNT or day_minus_1['money'] > MAX_AMOUNT:
            continue

        # 通过筛选
        try:
            name = get_security_info(code).display_name
        except:
            name = '未知'

        g.name_cache[code] = name
        yesterday_pct = (day_minus_1['close'] - day_minus_2['close']) / day_minus_2['close'] * 100
        candidates.append({
            'code': code,
            'name': name,
            'day_minus_2_close': day_minus_2['close'],
            'day_minus_2_pct': 10.0,  # 涨停
            'day_minus_1_close': day_minus_1['close'],
            'day_minus_1_pct': yesterday_pct,
            'day_minus_1_amount': day_minus_1['money'],
            'yesterday_amplitude': (day_minus_1['high'] - day_minus_1['low']) / day_minus_2['close'] * 100,
        })

    # 按昨日振幅排序（振幅小的优先，说明洗盘充分）
    candidates.sort(key=lambda x: x['yesterday_amplitude'])

    log.info(f"\n{'─'*80}")
    log.info(f"【断板反包候选】共{len(candidates)}只")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'前日涨幅':>8} {'昨日涨幅':>8} {'昨日振幅':>8} {'成交额(万)':>10}")
    log.info(f"{'─'*80}")
    for i, stock in enumerate(candidates[:30]):
        log.info(f"{i+1:>4} {stock['code']:<10} {stock['name']:<8} "
                 f"{stock['day_minus_2_pct']:>8.2f}% {stock['day_minus_1_pct']:>8.2f}% "
                 f"{stock['yesterday_amplitude']:>8.2f}% {stock['day_minus_1_amount']/10000:>10.0f}")

    # 保存候选列表
    g.target_list = candidates


def get_buy(context):
    """竞价阶段：对断板反包候选做竞价筛选"""
    if g.skip_buy:
        log.info("[净值动量] 冷静期，不买入")
        return

    if not g.target_list:
        log.info("[竞价] 无断板反包候选")
        return

    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'
    current_data = get_current_data()

    log.info(f"\n{'─'*80}")
    log.info(f"【竞价筛选】{t_day}，候选池：{len(g.target_list)}只")
    log.info(f"{'─'*80}")

    qualified_stocks = []

    for stock in g.target_list:
        code = stock['code']
        name = stock['name']
        yesterday_close = stock['day_minus_1_close']

        try:
            # 获取竞价数据
            auction = get_call_auction(code, start_date=start, end_date=end, fields=['time', 'volume', 'current'])
            if auction.empty:
                continue

            auction_price = auction['current'].iloc[-1]
            auction_volume = auction['volume'].sum()

            # 计算竞价涨幅
            auction_pct = (auction_price - yesterday_close) / yesterday_close if yesterday_close > 0 else 0

            # 竞价条件：竞价涨幅 >= MIN_AUCTION_PCT
            if auction_pct < MIN_AUCTION_PCT:
                continue

            # 通过筛选
            qualified_stocks.append({
                'code': code,
                'name': name,
                'auction_pct': auction_pct * 100,
                'yesterday_amplitude': stock['yesterday_amplitude'],
            })

            log.info(f"  ✅ {code}({name}) 断板反包 竞价涨幅={auction_pct*100:.2f}% 昨日振幅={stock['yesterday_amplitude']:.2f}%")

        except Exception as e:
            continue

    # 按竞价涨幅排序（涨幅高的优先）
    qualified_stocks.sort(key=lambda x: x['auction_pct'], reverse=True)

    log.info(f"\n{'─'*80}")
    log.info(f"【最终买入目标】{len(qualified_stocks)}只")
    log.info(f"{'─'*80}")
    for i, stock in enumerate(qualified_stocks):
        log.info(f"  {i+1}. {stock['code']}({stock['name']}) 竞价涨幅={stock['auction_pct']:.2f}%")

    # 更新目标列表
    g.target_list = qualified_stocks


def get_close_sell(context):
    """盘中止盈止损"""
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
                pnl = (last_price - avg_cost) / avg_cost * 100
                order_target_value(s, 0)
                log.info(f'止盈卖出 {s}({g.name_cache[s]}) 盈利{pnl:.2f}%')

            # 止损：跌破5日线
            elif closeable != 0 and last_price < (MA5 + MA5 * MA5_STOP_LOSS_BUFFER):
                pnl = (last_price - avg_cost) / avg_cost * 100
                order_target_value(s, 0)
                log.info(f'跌破5日线止损 {s}({g.name_cache[s]}) 亏损{pnl:.2f}%')

            # 跌幅止损
            elif closeable != 0:
                yst_close = yst_close_map.get(s)
                if yst_close and yst_close > 0:
                    drop_ratio = (yst_close - last_price) / yst_close
                    if drop_ratio >= DROP_STOP_LOSS:
                        pnl = (last_price - avg_cost) / avg_cost * 100
                        order_target_value(s, 0)
                        log.info(f'跌幅止损 {s}({g.name_cache[s]}) 跌幅{drop_ratio:.2%} 亏损{pnl:.2f}%')


def eod_stats(context):
    """盘后统计"""
    total_value = context.portfolio.total_value
    daily_pnl = 0

    g.peak_value = max(g.peak_value, total_value)

    if g.prev_day_value > 0:
        daily_pnl = (total_value / g.prev_day_value - 1)
        if daily_pnl < -0.005:
            g.consecutive_loss_days += 1
        else:
            g.consecutive_loss_days = 0

        if g.consecutive_loss_days >= CONSECUTIVE_LOSS_PAUSE:
            g.skip_buy = True
            log.info(f"[净值动量] 连亏{g.consecutive_loss_days}天，明日暂停买入")

    g.prev_day_value = total_value
    log.info(f"=== 盘后 === 总资产:{total_value:,.0f} | 日收益:{daily_pnl:.2%} | 持仓:{len(context.portfolio.positions)} | "
             f"连亏:{g.consecutive_loss_days}天 | 净值高点回撤:{(total_value/g.peak_value-1):.1%}")
