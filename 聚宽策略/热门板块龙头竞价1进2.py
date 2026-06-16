# ========================================================================
# 策略名称：热门板块龙头 竞价1进2
# ========================================================================
# 核心思路：
#   1. 先找热门板块（资金聚集）
#   2. 再找板块龙头（最强票）
#   3. 用1进2的竞价逻辑筛选（买入时机）
#   4. 适当时候离场（止盈止损）
#
# 目标：像火炬电子那样，买在启动前，吃完整波段
# ========================================================================


# ========================================================================
# ██ 可调参数区 ██
# ========================================================================

# -------------------- 1. 板块参数 --------------------
TOP_SECTORS = 10              # 选取热门板块数量
TOP_STOCKS_PER_SECTOR = 3     # 每个板块选取龙头数量
LIMIT_UP_WEIGHT = 3.0         # 涨停数权重
AMOUNT_RANK_WEIGHT = 1.0      # 成交额排名权重

# -------------------- 2. 龙头票筛选 --------------------
MIN_SECTOR_AMOUNT = 5e8       # 龙头票最小成交额（5亿）
MIN_SECTOR_PCT = 0.02         # 龙头票最小涨幅（2%）

# -------------------- 3. 竞价条件（1进2核心） --------------------
# 最强板块龙头：宽松条件（只要不大幅低开）
TOP_SECTOR_MIN_AUCTION_PCT = -0.02   # 最强板块龙头最低竞价涨幅（-2%）

# 其它板块：需要竞价转强
OTHER_SECTOR_MIN_AUCTION_PCT = 0.02  # 其它板块最低竞价涨幅（2%）

# -------------------- 4. 风控参数 --------------------
DROP_STOP_LOSS = 0.05         # 跌幅止损阈值
DRAWDOWN_THRESHOLD = 0.08     # 净值回撤减仓阈值
CONSECUTIVE_LOSS_PAUSE = 2    # 连亏天数暂停阈值
MA5_STOP_LOSS_BUFFER = 0.05   # 5日线止损加成

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
    g.condition_stats = {}

    # 净值动量
    g.consecutive_loss_days = 0
    g.skip_buy = False
    g.peak_value = 0
    g.drawdown_reduction = 1.0
    g.prev_day_value = 0

    # 选股结果
    g.hot_sectors = []
    g.leader_stocks = []
    g.target_list = []

    run_daily(before_market_open, time='09:10')
    run_daily(get_buy, '09:26')
    run_daily(get_close_sell, time='11:25')
    run_daily(get_close_sell, time='13:30')
    run_daily(eod_stats, time='15:00')


def before_market_open(context):
    """盘前：找热门板块龙头票"""
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

    # ===== 第一步：获取所有行业板块 =====
    sectors = get_industries(name='sw_l1', date=y_day)
    log.info(f"申万一级行业：{len(sectors)}个")

    # ===== 第二步：计算每个板块的热度 =====
    sector_scores = []

    for idx, row in sectors.iterrows():
        sector_code = row.name
        sector_name = row['name']

        sector_stocks = get_industry_stocks(sector_code, date=y_day)
        if not sector_stocks:
            continue

        try:
            price_data = get_price(
                sector_stocks, end_date=y_day, frequency='1d',
                fields=['close', 'high_limit', 'money'], count=1, panel=False,
                fill_paused=False, skip_paused=True
            )
            if price_data.empty:
                continue

            limit_up_count = len(price_data[price_data['close'] >= price_data['high_limit'] - 0.01])
            total_amount = price_data['money'].sum()
            heat_score = limit_up_count * LIMIT_UP_WEIGHT + (total_amount / 1e10) * AMOUNT_RANK_WEIGHT

            sector_scores.append({
                'sector_code': sector_code,
                'sector_name': sector_name,
                'heat_score': heat_score,
                'limit_up_count': limit_up_count,
                'total_amount': total_amount,
            })
        except:
            continue

    # ===== 第三步：选出前N热门板块 =====
    sector_scores.sort(key=lambda x: x['heat_score'], reverse=True)
    g.hot_sectors = sector_scores[:TOP_SECTORS]

    log.info(f"\n{'─'*80}")
    log.info(f"【热门板块 Top{TOP_SECTORS}】")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'板块':<10} {'热度':>6} {'涨停数':>6} {'成交额(亿)':>10}")
    log.info(f"{'─'*80}")
    for i, sector in enumerate(g.hot_sectors):
        log.info(f"{i+1:>4} {sector['sector_name']:<10} {sector['heat_score']:>6.1f} "
                 f"{sector['limit_up_count']:>6} {sector['total_amount']/1e8:>10.1f}")

    # ===== 第四步：每个板块找龙头票 =====
    g.leader_stocks = []
    g.name_cache = {}

    for sector in g.hot_sectors:
        sector_code = sector['sector_code']
        sector_name = sector['sector_name']

        sector_stocks = get_industry_stocks(sector_code, date=y_day)
        if not sector_stocks:
            continue

        try:
            price_data = get_price(
                sector_stocks, end_date=y_day, frequency='1d',
                fields=['close', 'open', 'high', 'low', 'high_limit', 'volume', 'money'], count=2, panel=False,
                fill_paused=False, skip_paused=True
            )
            if price_data.empty:
                continue

            # 获取今日数据
            today_data = price_data.groupby('code').last()

            # 过滤：成交额>5亿
            today_data = today_data[today_data['money'] >= MIN_SECTOR_AMOUNT]

            # 计算涨幅
            if len(price_data) >= 2:
                prev_data = price_data.groupby('code').nth(-2)
                today_data['pct'] = (today_data['close'] - prev_data['close']) / prev_data['close']
                today_data = today_data[today_data['pct'] >= MIN_SECTOR_PCT]

            # 按涨幅排序，取前N只
            today_data = today_data.sort_values('pct', ascending=False)
            leaders = today_data.head(TOP_STOCKS_PER_SECTOR)

            for code in leaders.index:
                try:
                    name = get_security_info(code).display_name
                except:
                    name = '未知'

                g.name_cache[code] = name
                g.leader_stocks.append({
                    'code': code,
                    'name': name,
                    'sector_name': sector_name,
                    'sector_rank': g.hot_sectors.index(sector),
                    'pct': leaders.loc[code, 'pct'],
                    'amount': leaders.loc[code, 'money'],
                    'close': leaders.loc[code, 'close'],
                    'volume': leaders.loc[code, 'volume'],
                })
        except:
            continue

    # 打印龙头票
    log.info(f"\n{'─'*80}")
    log.info(f"【板块龙头票】共{len(g.leader_stocks)}只")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'板块':<8} {'涨幅':>8} {'成交额(万)':>10}")
    log.info(f"{'─'*80}")
    for i, stock in enumerate(g.leader_stocks[:30]):
        log.info(f"{i+1:>4} {stock['code']:<10} {stock['name']:<8} {stock['sector_name']:<8} "
                 f"{stock['pct']*100:>8.2f}% {stock['amount']/10000:>10.0f}")


def get_buy(context):
    """竞价阶段：对龙头票做1进2竞价筛选"""
    if g.skip_buy:
        log.info("[净值动量] 冷静期，不买入")
        return

    if not g.leader_stocks:
        log.info("[竞价] 无龙头票")
        return

    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'
    current_data = get_current_data()

    log.info(f"\n{'─'*80}")
    log.info(f"【竞价筛选】{t_day}，龙头票池：{len(g.leader_stocks)}只")
    log.info(f"{'─'*80}")

    qualified_stocks = []
    top_sector_name = g.hot_sectors[0]['sector_name'] if g.hot_sectors else ''

    for stock in g.leader_stocks:
        code = stock['code']
        name = stock['name']
        sector_name = stock['sector_name']
        yesterday_close = stock['close']
        yesterday_volume = stock['volume']

        try:
            # 获取竞价数据
            auction = get_call_auction(code, start_date=start, end_date=end, fields=['time', 'volume', 'current'])
            if auction.empty:
                continue

            auction_price = auction['current'].iloc[-1]
            auction_volume = auction['volume'].sum()

            # 计算竞价指标
            auction_pct = (auction_price - yesterday_close) / yesterday_close if yesterday_close > 0 else 0
            auction_vol_ratio = auction_volume / yesterday_volume if yesterday_volume > 0 else 0

            # 判断是否最强板块
            is_top_sector = (sector_name == top_sector_name)

            # ===== 1进2核心逻辑：竞价条件筛选 =====
            if is_top_sector:
                # 最强板块龙头：宽松条件
                if auction_pct < TOP_SECTOR_MIN_AUCTION_PCT:
                    log.info(f"  ❌ {code}({name}) [{sector_name}] 最强板块龙头，但竞价低开{auction_pct*100:.2f}%，排除")
                    continue
                condition = f"最强板块[{sector_name}]龙头"
            else:
                # 其它板块：需要竞价转强
                if auction_pct < OTHER_SECTOR_MIN_AUCTION_PCT:
                    continue
                condition = f"[{sector_name}]竞价转强"

            # 通过筛选
            qualified_stocks.append({
                'code': code,
                'name': name,
                'sector_name': sector_name,
                'sector_rank': stock['sector_rank'],
                'auction_pct': auction_pct * 100,
                'auction_vol_ratio': auction_vol_ratio * 100,
                'condition': condition,
                'is_top_sector': is_top_sector,
            })

            log.info(f"  ✅ {code}({name}) [{sector_name}] {condition} "
                     f"竞价涨幅={auction_pct*100:.2f}% 竞昨比={auction_vol_ratio*100:.2f}%")

        except Exception as e:
            continue

    # 按板块热度排序（最强板块优先）
    qualified_stocks.sort(key=lambda x: (not x['is_top_sector'], x['sector_rank']))

    g.target_list = qualified_stocks

    log.info(f"\n{'─'*80}")
    log.info(f"【最终买入目标】{len(g.target_list)}只")
    log.info(f"{'─'*80}")
    for i, stock in enumerate(g.target_list):
        log.info(f"  {i+1}. {stock['code']}({stock['name']}) [{stock['sector_name']}] "
                 f"涨幅={stock['auction_pct']:.2f}% 竞昨比={stock['auction_vol_ratio']:.2f}% "
                 f"条件={stock['condition']}")


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
