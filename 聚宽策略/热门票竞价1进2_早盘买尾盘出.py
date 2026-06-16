# ========================================================================
# 策略名称：热门票竞价1进2 早盘买尾盘出 v3（条件匹配+风控版）
# ========================================================================
# 策略逻辑：
#   1. 盘前：五维评分选出Top50热门票池
#   2. 竞价阶段(09:26)：条件矩阵筛选（类似首板1进2）
#   3. 早盘(09:30)：买入符合条件的前3只
#   4. 盘中(11:25/13:30)：止盈止损检查
#   5. 尾盘(14:50)：卖出可卖持仓（涨停不卖）
#
# 融合首板1进2精髓：
#   - 条件矩阵匹配（不是评分，是条件筛选）
#   - 量能确认（昨日放量）
#   - 三重风控（止损+涨停不卖+盘中检查）
# ========================================================================

from jqdata import *
import numpy as np
import pandas as pd
import datetime
from datetime import datetime as dt


# ==================== 竞价条件矩阵 ====================
# 热门票竞价条件矩阵（根据实际数据调整：大部分竞价涨幅在-3%~+3%，竞昨比在0.5%~3%）
# 格式：(条件名, 竞价涨幅下限, 竞价涨幅上限, 竞昨比下限, 竞昨比上限)
CONDITION_RULES = [
    # A类：小市值热门票（成交额10~40亿）
    ('A: 涨幅2~7% | 竞昨比1~5%',    1.02, 1.07, 0.01, 0.05),
    ('A: 涨幅0~2% | 竞昨比1~5%',    1.00, 1.02, 0.01, 0.05),
    ('A: 涨幅-2~0% | 竞昨比2~8%',   0.98, 1.00, 0.02, 0.08),

    # B类：中市值热门票（成交额40~200亿）
    ('B: 涨幅2~7% | 竞昨比1~5%',    1.02, 1.07, 0.01, 0.05),
    ('B: 涨幅0~2% | 竞昨比1~5%',    1.00, 1.02, 0.01, 0.05),
    ('B: 涨幅-2~0% | 竞昨比2~8%',   0.98, 1.00, 0.02, 0.08),
    ('B: 涨幅3~7% | 竞昨比2~6%',    1.03, 1.07, 0.02, 0.06),
]


# ==================== 1. 初始化设置 ====================
def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    log.set_level('order', 'error')

    # 佣金与印花税（超短线）
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0.001,
        open_commission=0.0003,
        close_commission=0.0003,
        close_today_commission=0,
        min_commission=5
    ), type='stock')

    # 策略参数
    g.hot_pool_size = 50       # 热门票池大小
    g.buy_count = 3            # 每日买入股票数
    g.target_list = []         # 最终买入目标
    g.hot_candidates = []      # 盘前五维评分选出的热门票池
    g.limit_ups_dict = {}      # 缓存个股近10日涨停次数
    g.condition_stats = {}     # 条件盈亏统计
    g.name_cache = {}          # 股票名称缓存
    g.drop_percent = 0.05      # 跌幅止损阈值5%
    g.information = {}         # 记录每只股票命中的条件

    # 定时任务
    run_daily(before_market_open, time='09:10')    # 盘前选股
    run_daily(auction_scoring, time='09:26')       # 竞价评分
    run_daily(morning_buy, time='open')            # 早盘买入
    run_daily(afternoon_sell, time='14:50')        # 尾盘卖出（涨停不卖）


# ==================== 2. 盘前选股：五维评分选出热门票池 ====================
def before_market_open(context):
    """盘前：用五维评分筛选热门票池"""
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"\n{'='*80}")
    log.info(f"【盘前选股】{yesterday}")
    log.info(f"{'='*80}")
    g.limit_ups_dict = {}
    g.name_cache = {}

    if isinstance(yesterday, str):
        date_obj = dt.strptime(yesterday, '%Y-%m-%d').date()
    else:
        date_obj = yesterday

    # 1. 获取基础股票池（排除ST、退市、次新股）
    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    log.info(f"基础股票池：{len(filtered_stocks)}只（排除ST、退市、次新股）")

    # 2. 获取成交额，筛选前150只热门股
    h_amount_all = history(1, '1d', 'money', filtered_stocks, df=False)
    amount_all_dict = {code: float(h_amount_all[code][0]) for code in filtered_stocks if len(h_amount_all[code]) > 0}
    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    hot_150 = [item[0] for item in sorted_by_amount_all[:150]]
    log.info(f"成交额Top150热门股筛选完成")

    # 3. 补全流通市值数据
    q = query(valuation.code, valuation.circulating_market_cap).filter(valuation.code.in_(hot_150))
    df_cap = get_fundamentals(q, date=yesterday)
    candidate_pool = df_cap['code'].tolist()
    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))

    if not candidate_pool:
        g.hot_candidates = []
        return

    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))
    amount_ranks = {code: idx + 1 for idx, code in enumerate(hot_150)}

    # 4. 批量获取101天日线数据
    log.info(f"正在获取 {len(candidate_pool)} 只股票的历史数据...")
    h_open = history(101, '1d', 'open', candidate_pool, df=False)
    h_close = history(101, '1d', 'close', candidate_pool, df=False)
    h_high = history(101, '1d', 'high', candidate_pool, df=False)
    h_low = history(101, '1d', 'low', candidate_pool, df=False)
    h_volume = history(101, '1d', 'volume', candidate_pool, df=False)
    h_money = history(101, '1d', 'money', candidate_pool, df=False)
    h_high_limit = history(101, '1d', 'high_limit', candidate_pool, df=False)

    history_data = {}
    for code in candidate_pool:
        if code in h_open:
            bars = []
            for i in range(len(h_open[code])):
                bars.append({
                    'open': h_open[code][i],
                    'close': h_close[code][i],
                    'high': h_high[code][i],
                    'low': h_low[code][i],
                    'volume': h_volume[code][i],
                    'money': h_money[code][i],
                    'high_limit': h_high_limit[code][i] if code in h_high_limit else 0.0
                })
            history_data[code] = bars

    # 5. 五维评分
    log.info(f"\n{'─'*80}")
    log.info(f"【五维评分开始】")
    log.info(f"{'─'*80}")
    temp_candidates = []
    for code in candidate_pool:
        bars = history_data.get(code)
        if bars is None or len(bars) < 21:
            continue

        close_seq = [bar['close'] for bar in bars]
        open_seq = [bar['open'] for bar in bars]
        high_seq = [bar['high'] for bar in bars]
        low_seq = [bar['low'] for bar in bars]
        vol_seq = [bar['volume'] for bar in bars]
        money_seq = [bar['money'] for bar in bars]
        high_limit_seq = [bar['high_limit'] for bar in bars]

        metrics = calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq)
        if not metrics:
            continue

        # === 新增：近10日涨幅>5%的天数 ===
        big_gain_days_10 = 0
        for i in range(-10, 0):
            if i-1 >= -len(close_seq):
                daily_ret = (close_seq[i] - close_seq[i-1]) / close_seq[i-1] * 100.0
                if daily_ret > 5.0:
                    big_gain_days_10 += 1
        metrics['big_gain_days_10'] = big_gain_days_10

        # 近5日振幅
        high_5 = high_seq[-5:]
        low_5 = low_seq[-5:]
        min_l_5 = min(low_5)
        volatility_5 = (max(high_5) - min_l_5) / min_l_5 * 100.0 if min_l_5 > 0 else 0.0

        # 近5日涨停数
        limit_ups_5 = 0
        close_5 = close_seq[-5:]
        high_limit_5 = high_limit_seq[-5:]
        for i in range(len(close_5)):
            if close_5[i] >= high_limit_5[i] - 0.01:
                limit_ups_5 += 1

        # 近10日一字/T字涨停数
        extreme_limit_ups = 0
        close_10_ex = close_seq[-10:]
        open_10_ex = open_seq[-10:]
        low_10_ex = low_seq[-10:]
        high_limit_10_ex = high_limit_seq[-10:]
        for i in range(len(close_10_ex)):
            if close_10_ex[i] >= high_limit_10_ex[i] - 0.01:
                if (low_10_ex[i] >= high_limit_10_ex[i] - 0.01) or (open_10_ex[i] >= high_limit_10_ex[i] - 0.01):
                    extreme_limit_ups += 1

        # 百日高位突破
        max_high_100 = max(high_seq[:-1]) if len(high_seq) > 1 else high_seq[0]
        is_high_position = close_seq[-1] >= max_high_100 * 0.9

        # 昨日涨幅
        is_yesterday_strong = (close_seq[-1] - close_seq[-2]) / close_seq[-2] * 100.0 > 7.0 if len(close_seq) >= 2 else False

        metrics['extreme_limit_ups'] = extreme_limit_ups
        metrics['volatility_5'] = volatility_5
        metrics['limit_ups_5'] = limit_ups_5

        circ_cap = cap_dict.get(code, 0.0)

        # 大市值弹性校验
        if circ_cap > 500.0:
            continue

        # 近10日涨停天数
        limit_ups = 0
        close_10_raw = close_seq[-10:]
        high_limit_10_raw = high_limit_seq[-10:]
        for i in range(len(close_10_raw)):
            if close_10_raw[i] >= high_limit_10_raw[i] - 0.01:
                limit_ups += 1

        # 价格过滤
        cur_price = close_seq[-1]
        if cur_price < 2.0:
            continue
        if cur_price > 50.0 and limit_ups < 2:
            continue

        # ===== 五维评分 =====
        # A. 热度得分（25分）：成交额排名 + 近10日大涨幅天数
        rank_no = amount_ranks.get(code, 999)
        heat_amount_score = max(0, int(15 * (1 - min(rank_no, 150) / 150.0)))
        if big_gain_days_10 >= 7:
            heat_big_gain_score = 10
        elif big_gain_days_10 >= 5:
            heat_big_gain_score = 7
        elif big_gain_days_10 >= 3:
            heat_big_gain_score = 4
        else:
            heat_big_gain_score = 0
        heat_score = heat_amount_score + heat_big_gain_score

        # B. 流通市值打分（10分）
        market_cap_score = calc_market_cap_score_v2(circ_cap)

        # C. 量价得分（30分）
        volume_price_score = calc_volume_price_score(metrics)

        # D. 位置得分（25分）
        position_score = calc_position_score(metrics)

        # E. 均线粘合度（10分）
        ma_cohesion_score = calc_ma_cohesion_score(close_seq)

        # F. 风险扣分
        risk_penalty = calc_risk_penalty(metrics)

        # 加分项
        limit_up_bonus = 10 if limit_ups >= 2 else (5 if limit_ups == 1 else 0)
        low_price_bonus = 5 if 2.0 <= cur_price <= 20.0 else 0
        ema_score = calc_ema_alignment_score(close_seq)
        trend_bonus = 10 if ema_score == 100 else (5 if ema_score == 80 else 0)
        high_position_bonus = 5 if is_high_position else 0
        yesterday_strong_bonus = 5 if is_yesterday_strong else 0

        # 汇总总分
        total_score = heat_score + market_cap_score + volume_price_score + position_score + ma_cohesion_score + risk_penalty + limit_up_bonus + low_price_bonus + trend_bonus + high_position_bonus + yesterday_strong_bonus
        total_score = max(0, min(100, total_score))

        # 保存各项评分明细
        score_details = {
            'heat_amount': heat_amount_score,
            'heat_big_gain': heat_big_gain_score,
            'market_cap': market_cap_score,
            'volume_price': volume_price_score,
            'position': position_score,
            'ma_cohesion': ma_cohesion_score,
            'risk_penalty': risk_penalty,
            'limit_up_bonus': limit_up_bonus,
            'low_price_bonus': low_price_bonus,
            'trend_bonus': trend_bonus,
            'high_position_bonus': high_position_bonus,
            'yesterday_strong_bonus': yesterday_strong_bonus,
        }

        # 计算昨日振幅（振幅越小越好）
        yesterday_amplitude = (high_seq[-1] - low_seq[-1]) / close_seq[-2] * 100.0 if close_seq[-2] > 0 else 0.0

        temp_candidates.append({
            'code': code,
            'score': total_score,
            'ema_score': ema_score,
            'name': name_dict.get(code, code),
            'yesterday_close': close_seq[-1],
            'yesterday_volume': vol_seq[-1],
            'yesterday_money': money_seq[-1],
            'high_limit': high_limit_seq[-1],
            'circ_cap': circ_cap,
            'big_gain_days_10': big_gain_days_10,
            'limit_ups': limit_ups,
            'score_details': score_details,
            'metrics': metrics,
            'yesterday_amplitude': yesterday_amplitude,  # 新增：昨日振幅
        })

        g.limit_ups_dict[code] = limit_ups
        g.name_cache[code] = name_dict.get(code, '未知')

    # EMA趋势过滤
    scored_candidates = [item for item in temp_candidates if item['ema_score'] >= 40]
    if len(scored_candidates) < g.hot_pool_size:
        scored_candidates = temp_candidates

    # 按总分降序排列，取前50只作为热门票池
    scored_candidates = sorted(scored_candidates, key=lambda x: x['score'], reverse=True)
    g.hot_candidates = scored_candidates[:g.hot_pool_size]

    # 打印五维评分Top15
    log.info(f"\n{'─'*80}")
    log.info(f"【五维评分榜 Top15】热门票池：{len(g.hot_candidates)}只")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'五维分':>6} {'热度':>4} {'大涨幅':>4} {'市值':>4} {'量价':>4} {'位置':>4} {'粘合':>4} {'风险':>4} {'加分':>4} {'昨振幅%':>6} {'流通市值':>8}")
    log.info(f"{'─'*80}")
    for i, item in enumerate(g.hot_candidates[:15]):
        sd = item['score_details']
        bonus_total = sd['limit_up_bonus'] + sd['low_price_bonus'] + sd['trend_bonus'] + sd['high_position_bonus'] + sd['yesterday_strong_bonus']
        log.info(f"{i+1:>4} {item['code']:<10} {item['name']:<8} {item['score']:>6.1f} "
                 f"{sd['heat_amount']+sd['heat_big_gain']:>4} {sd['heat_big_gain']:>4} "
                 f"{sd['market_cap']:>4} {sd['volume_price']:>4} {sd['position']:>4} "
                 f"{sd['ma_cohesion']:>4.0f} {sd['risk_penalty']:>4} {bonus_total:>4} "
                 f"{item['yesterday_amplitude']:>6.2f} {item['circ_cap']:>8.1f}亿")


# ==================== 3. 竞价评分：条件矩阵筛选 ====================
def auction_scoring(context):
    """竞价阶段(09:26)：用条件矩阵筛选（类似首板1进2）"""
    if not g.hot_candidates:
        g.target_list = []
        return

    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'

    current_data = get_current_data()

    log.info(f"\n{'='*80}")
    log.info(f"【竞价条件筛选开始】{t_day}")
    log.info(f"{'='*80}")

    # 条件筛选结果
    qualified_stocks = []
    debug_count = 0  # 调试计数

    for item in g.hot_candidates:
        code = item['code']
        name = item['name']
        yesterday_close = item['yesterday_close']
        yesterday_volume = item['yesterday_volume']
        yesterday_money = item['yesterday_money']
        high_limit = item['high_limit']
        circ_cap = item['circ_cap']

        try:
            # ===== 第一层：基础过滤 =====
            # 价格过滤
            open_price = current_data[code].day_open
            if open_price <= 3:
                continue

            # 市值过滤（放宽版）
            if circ_cap < 20 or circ_cap > 500:
                continue

            # 成交额过滤（10亿~200亿）
            if yesterday_money < 10e8 or yesterday_money > 200e8:
                continue

            # ===== 第二层：量能确认 =====
            # 昨日成交量必须大于近5日均量的90%（确认放量）
            vol_data = attribute_history(code, 6, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                continue
            avg_vol_5 = np.mean(vol_data['volume'][:-1])
            if vol_data['volume'][-1] <= avg_vol_5 * 0.9:
                continue

            # ===== 第三层：昨日振幅前置条件 =====
            # 振幅越小越好：振幅<3%优先，振幅<5%可接受，振幅>=8%排除
            yesterday_amplitude = item.get('yesterday_amplitude', 999)
            if yesterday_amplitude >= 8.0:
                continue  # 振幅太大，排除
            # 振幅<3%的优先级更高（在后续排序中体现）

            # ===== 第四层：竞价条件矩阵 =====
            auction = get_call_auction(
                code,
                start_date=start,
                end_date=end,
                fields=['time', 'volume', 'current']
            )

            if auction.empty:
                continue

            # 竞价价格
            auction_price = auction['current'].iloc[-1]
            # 竞价成交量
            auction_volume = auction['volume'].sum()

            # 计算竞价指标
            # 1. 竞价涨幅（相对于昨日收盘）
            cur_ratio = auction_price / yesterday_close

            # 2. 竞昨比（竞价量 / 昨日成交量）
            auction_ratio = auction_volume / yesterday_volume if yesterday_volume > 0 else 0.0

            # 3. 判断成交额区间（根据实际数据：热门票成交额在30~70亿）
            is_small = yesterday_money < 40e8   # 10~40亿
            is_large = not is_small             # 40~200亿

            # ===== 条件矩阵匹配 =====
            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES:
                # 根据成交额区间筛选条件
                if cond_name.startswith('A') and not is_small:
                    continue
                if cond_name.startswith('B') and not is_large:
                    continue

                # 检查是否满足条件
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            # ===== 调试日志：显示前10只的竞价数据 =====
            debug_count += 1
            if debug_count <= 10:
                log.info(f"  [调试] {code}({name}) 竞价涨幅={((cur_ratio-1)*100):.2f}% "
                         f"竞昨比={auction_ratio*100:.2f}% "
                         f"成交额={yesterday_money/1e8:.1f}亿 "
                         f"区间={'小(10~40亿)' if is_small else '中(40~200亿)'} "
                         f"命中={'✅ '+matched_condition if matched_condition else '❌ 未命中'}")

            # 没有命中任何条件，跳过
            if matched_condition is None:
                continue

            # ===== 通过所有筛选 =====
            qualified_stocks.append({
                'code': code,
                'name': name,
                'auction_price': auction_price,
                'auction_pct': (cur_ratio - 1) * 100,
                'auction_vol_ratio': auction_ratio * 100,
                'auction_money': auction_price * auction_volume,
                'yesterday_money': yesterday_money,
                'circ_cap': circ_cap,
                'five_dim_score': item['score'],
                'condition': matched_condition,
                'limit_ups': item['limit_ups'],
                'yesterday_amplitude': yesterday_amplitude,  # 新增：昨日振幅
            })

            log.info(f"  ✅ {code}({name}) 通过筛选，命中: {matched_condition} "
                     f"竞价涨幅={((cur_ratio-1)*100):.2f}% 竞昨比={auction_ratio*100:.2f}%")

        except Exception as e:
            log.warn(f"[竞价筛选异常] {code}({name}): {e}")
            continue

    # 按振幅升序 + 五维评分降序排列（振幅小的优先，振幅相同时五维分高的优先）
    qualified_stocks = sorted(qualified_stocks, key=lambda x: (x['yesterday_amplitude'], -x['five_dim_score']))

    # 打印符合条件的股票
    log.info(f"\n{'─'*80}")
    log.info(f"【竞价条件筛选结果】共{len(qualified_stocks)}只股票通过条件矩阵")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'五维分':>6} {'昨振幅%':>6} {'竞价涨幅':>8} {'竞昨比%':>8} {'成交额(万)':>10} {'流通市值':>8} {'命中条件':<30}")
    log.info(f"{'─'*80}")
    for i, item in enumerate(qualified_stocks[:15]):
        log.info(f"{i+1:>4} {item['code']:<10} {item['name']:<8} {item['five_dim_score']:>6.1f} "
                 f"{item['yesterday_amplitude']:>6.2f} "
                 f"{item['auction_pct']:>8.2f}% {item['auction_vol_ratio']:>8.2f}% "
                 f"{item['auction_money']/10000:>10.0f} {item['circ_cap']:>8.1f}亿 {item['condition']:<30}")

    # 选出前3只作为最终买入目标
    g.target_list = qualified_stocks[:g.buy_count]

    log.info(f"\n{'─'*80}")
    log.info(f"【最终买入目标】前{len(g.target_list)}只")
    log.info(f"{'─'*80}")
    for i, item in enumerate(g.target_list):
        log.info(f"  {i+1}. {item['code']}({item['name']}) 五维={item['five_dim_score']:.1f} "
                 f"昨振幅={item['yesterday_amplitude']:.2f}% "
                 f"涨幅={item['auction_pct']:.2f}% 竞昨比={item['auction_vol_ratio']:.2f}% "
                 f"条件={item['condition']}")


# ==================== 4. 早盘买入 ====================
def morning_buy(context):
    """早盘(09:30)：买入符合条件的前3只股票"""
    if not g.target_list:
        return

    # 计算可用资金（留1%缓冲）
    available_cash = context.portfolio.available_cash * 0.99
    cash_per_stock = available_cash / len(g.target_list)

    if cash_per_stock <= 0:
        log.info("[早盘买入跳过] 可用资金不足")
        return

    current_data = get_current_data()

    log.info(f"\n{'─'*80}")
    log.info(f"【早盘买入】可用资金：{available_cash:.0f}，每只：{cash_per_stock:.0f}")
    log.info(f"{'─'*80}")

    for item in g.target_list:
        code = item['code']
        name = item['name']
        price_info = current_data[code]

        # 跳过停牌
        if price_info.paused:
            log.info(f"  [跳过] {code}({name}) 停牌")
            continue

        # 跳过已涨停
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"  [跳过] {code}({name}) 竞价已涨停")
            continue

        # 跳过已跌停
        if price_info.last_price <= price_info.low_limit + 0.01:
            log.info(f"  [跳过] {code}({name}) 竞价已跌停")
            continue

        # 买入
        order_value(code, cash_per_stock)
        g.information[code] = item['condition']
        log.info(f"  [买入] {code}({name}) 资金={cash_per_stock:.0f} 条件={item['condition']}")


# ==================== 5. 盘中止盈止损 ====================
def morning_check(context):
    """盘中(11:25)：止盈止损检查"""
    _check_positions(context, "11:25")

def afternoon_check(context):
    """盘中(13:30)：止盈止损检查"""
    _check_positions(context, "13:30")

def _check_positions(context, time_label):
    """盘中止盈止损检查"""
    current_data = get_current_data()
    positions = context.portfolio.positions

    if not positions:
        return

    log.info(f"\n{'─'*80}")
    log.info(f"【盘中检查 {time_label}】持仓：{len(positions)}只")
    log.info(f"{'─'*80}")

    y_day = context.previous_date.strftime('%Y-%m-%d')

    # 获取昨日收盘价
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

    for code in list(positions):
        pos = positions[code]
        last_price = current_data[code].last_price
        high_limit = current_data[code].high_limit
        avg_cost = pos.avg_cost
        closeable = pos.closeable_amount
        name = g.name_cache.get(code, '未知')

        # 计算5日均线
        try:
            close_data = attribute_history(code, 5, '1d', ['close'])
            ma5 = close_data['close'].mean()
        except:
            ma5 = last_price

        # 止盈：未涨停但盈利
        if closeable != 0 and last_price < high_limit and last_price > avg_cost:
            pnl_pct = (last_price - avg_cost) / avg_cost * 100
            order_target_value(code, 0)
            log.info(f"  [止盈] {code}({name}) 价格={last_price:.2f} 盈亏={pnl_pct:.2f}%")
            continue

        # 止损：跌破5日线+5%
        if closeable != 0 and last_price < (ma5 + ma5 * 0.05):
            pnl_pct = (last_price - avg_cost) / avg_cost * 100
            order_target_value(code, 0)
            log.info(f"  [跌破5日线止损] {code}({name}) 价格={last_price:.2f} 5日线={ma5:.2f} 盈亏={pnl_pct:.2f}%")
            continue

        # 跌幅止损：较昨日收盘跌幅>=5%
        yst_close = yst_close_map.get(code)
        if yst_close and yst_close > 0:
            drop_ratio = (yst_close - last_price) / yst_close
            if drop_ratio >= g.drop_percent:
                pnl_pct = (last_price - avg_cost) / avg_cost * 100
                order_target_value(code, 0)
                log.info(f"  [跌幅止损] {code}({name}) 价格={last_price:.2f} 跌幅={drop_ratio:.2%} 盈亏={pnl_pct:.2f}%")


# ==================== 6. 尾盘卖出 ====================
def afternoon_sell(context):
    """尾盘(14:50)：卖出可卖持仓，涨停不卖"""
    current_data = get_current_data()
    positions = context.portfolio.positions

    # 获取可卖持仓（已过T+1锁定期）
    sell_list = []
    for code in positions:
        pos = positions[code]
        if pos.closeable_amount > 0:
            sell_list.append(code)

    if not sell_list:
        log.info("[尾盘卖出] 无可卖持仓")
        return

    log.info(f"\n{'─'*80}")
    log.info(f"【尾盘卖出】可卖持仓：{len(sell_list)}只")
    log.info(f"{'─'*80}")

    for code in sell_list:
        pos = positions[code]
        price_info = current_data[code]
        name = g.name_cache.get(code, '未知')

        # 涨停不卖
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"  [涨停持有] {code}({name}) 涨停封板，不卖出")
            continue

        # 卖出
        pnl_pct = (price_info.last_price - pos.avg_cost) / pos.avg_cost * 100 if pos.avg_cost > 0 else 0
        order(code, -pos.closeable_amount)
        log.info(f"  [卖出] {code}({name}) 数量={pos.closeable_amount} 价格={price_info.last_price:.2f} 盈亏={pnl_pct:.2f}%")

        # 记录条件盈亏统计
        cond = g.information.get(code, '未知')
        if cond not in g.condition_stats:
            g.condition_stats[cond] = {'win': 0, 'loss': 0, 'win_pct': 0.0, 'loss_pct': 0.0}
        st = g.condition_stats[cond]
        if pnl_pct >= 0:
            st['win'] += 1
            st['win_pct'] += pnl_pct
        else:
            st['loss'] += 1
            st['loss_pct'] += pnl_pct

    # 打印条件盈亏汇总
    if g.condition_stats:
        log.info(f"\n{'─'*80}")
        log.info(f"【条件盈亏汇总】")
        log.info(f"{'─'*80}")
        for cond, st in g.condition_stats.items():
            total = st['win'] + st['loss']
            avg_win = st['win_pct'] / st['win'] if st['win'] > 0 else 0
            avg_loss = st['loss_pct'] / st['loss'] if st['loss'] > 0 else 0
            log.info(f"  {cond}: 盈{st['win']}笔(均{avg_win:.2f}%) 亏{st['loss']}笔(均{avg_loss:.2f}%) 共{total}笔")


# ==================== 7. 辅助函数 ====================
def filter_basic_stocks(all_stocks_df, date_obj):
    """过滤ST、退市、次新股"""
    cutoff_date = date_obj - datetime.timedelta(days=90)
    df_filtered = all_stocks_df[
        (~all_stocks_df['display_name'].str.startswith('ST')) &
        (~all_stocks_df['display_name'].str.startswith('*ST')) &
        (~all_stocks_df['display_name'].str.startswith('退')) &
        (all_stocks_df['start_date'] < cutoff_date)
    ]
    return df_filtered.index.tolist()


def calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq):
    """计算量价指标"""
    if len(close_seq) < 21:
        return None

    c_t = close_seq[-1]
    o_t = open_seq[-1]
    h_t = high_seq[-1]
    l_t = low_seq[-1]
    v_t = vol_seq[-1]
    m_t = money_seq[-1]
    c_y = close_seq[-2]

    v_prev5 = vol_seq[-6:-1]
    avg5_vol = np.mean(v_prev5) if v_prev5 else 1.0
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
    breakout_gap_20 = (c_t - prev20_high) / prev20_high * 100.0 if prev20_high > 0 else 0.0

    ma5 = np.mean(close_seq[-5:])
    bias_ma5 = (c_t - ma5) / ma5 * 100.0 if ma5 > 0 else 0.0

    recent60_high = max(high_seq[-60:]) if len(high_seq) >= 60 else max(high_seq)
    recent60_low = min(low_seq[-60:]) if len(low_seq) >= 60 else min(low_seq)
    pos60 = (c_t - recent60_low) / max(recent60_high - recent60_low, 0.0001)

    upper_shadow_ratio = (h_t - max(o_t, c_t)) / c_t * 100.0 if c_t > 0 else 0.0

    c_prev3 = close_seq[-4] if len(close_seq) >= 4 else close_seq[0]
    pct3 = (c_t - c_prev3) / c_prev3 * 100.0 if c_prev3 > 0 else 0.0

    prev5_money = money_seq[-7:-2]
    avg5_money = np.mean(prev5_money) if prev5_money else 1.0
    amount_continuity_2d = min(m_t, money_seq[-2]) / avg5_money if avg5_money > 0 else 0.0

    return {
        'vol_ratio_5': vol_ratio_5,
        'red_green_ratio_5': red_green_ratio_5,
        'close_strength': close_strength,
        'day_pct': day_pct,
        'day_amplitude': day_amplitude,
        'breakout_20': breakout_20,
        'breakout_gap_20': breakout_gap_20,
        'bias_ma5': bias_ma5,
        'pos60': pos60,
        'upper_shadow_ratio': upper_shadow_ratio,
        'pct3': pct3,
        'amount_continuity_2d': amount_continuity_2d,
    }


def calc_market_cap_score_v2(circ_cap):
    """市值打分（放宽版：20亿~500亿）"""
    if circ_cap <= 0:
        return 0
    if 20.0 <= circ_cap <= 500.0:
        return 10
    elif 10.0 <= circ_cap < 20.0:
        return 6
    elif 500.0 < circ_cap <= 800.0:
        return 6
    elif circ_cap < 10.0:
        return -4
    elif circ_cap > 800.0:
        return -4
    return 0


def calc_volume_price_score(metrics):
    """量价评分"""
    score = 0
    vol_ratio = metrics['vol_ratio_5']
    day_amp = metrics['day_amplitude']
    day_pct = metrics['day_pct']

    if 1.5 <= vol_ratio <= 3.0:
        score += 12
    elif 1.2 <= vol_ratio < 1.5:
        score += 8
    elif vol_ratio > 3.0:
        if day_amp < 4.0 and day_pct >= -1.0:
            score += 12
        elif day_amp >= 4.0 and day_pct >= 5.0:
            score += 10
        else:
            score += 4
    elif vol_ratio < 1.0:
        score += 2

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
        score += 4
    elif 0.0 <= day_pct < 2.0:
        score += 2
    elif day_pct > 9.0:
        score += 1

    return score


def calc_position_score(metrics):
    """位置评分"""
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
    """风险扣分"""
    penalty = 0
    if metrics['upper_shadow_ratio'] > 4.0:
        penalty -= 8
    if metrics['vol_ratio_5'] > 3.0 and metrics['day_pct'] < 2.0 and metrics['day_amplitude'] >= 4.0:
        penalty -= 10
    if metrics['pct3'] > 18.0:
        penalty -= 8
    if metrics['close_strength'] < 0.4:
        penalty -= 6
    if metrics['bias_ma5'] > 8.0:
        penalty -= 8

    if metrics.get('volatility_5', 0) > 40.0:
        penalty -= 10
    if metrics.get('limit_ups_5', 0) >= 4:
        penalty -= 10

    return penalty


def calc_ma_cohesion_score(close_seq):
    """均线粘合度评分"""
    if len(close_seq) < 30:
        return 0.0

    ma5 = np.mean(close_seq[-5:])
    ma10 = np.mean(close_seq[-10:])
    ma20 = np.mean(close_seq[-20:])
    ma30 = np.mean(close_seq[-30:])

    ma_list = [ma5, ma10, ma20, ma30]
    max_ma = max(ma_list)
    min_ma = min(ma_list)
    ma_mean = np.mean(ma_list)

    if ma_mean <= 0:
        return 0.0

    ma_spread = (max_ma - min_ma) / ma_mean
    c_t = close_seq[-1]

    if c_t < min_ma * 0.98:
        return 0.0

    if ma_spread <= 0.02:
        return 10.0
    elif ma_spread <= 0.04:
        return 6.0
    elif ma_spread <= 0.06:
        return 3.0

    return 0.0


def calc_ema_alignment_score(close_seq):
    """EMA趋势评分"""
    if len(close_seq) < 55:
        return 0

    close_series = pd.Series(close_seq)
    ema_8 = close_series.ewm(span=8, adjust=False).mean().iloc[-1]
    ema_21 = close_series.ewm(span=21, adjust=False).mean().iloc[-1]
    ema_55 = close_series.ewm(span=55, adjust=False).mean().iloc[-1]
    current_price = close_seq[-1]

    score = 0
    if current_price > ema_8:
        score += 50
    if ema_8 > ema_21:
        score += 30
    if ema_21 > ema_55:
        score += 20

    return score
