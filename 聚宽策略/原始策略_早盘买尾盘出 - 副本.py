# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略核心：原始五维打分选股 + 早盘一次性买入（100%仓位） + 尾盘只出不进（只安排出货）
# 交易逻辑：
# 1. 盘前：基于前一日行情数据计算五维得分，选出今日目标 Top4 股票。
# 2. 早盘 9:30（my_morning_trade）：用昨日尾盘卖出腾出的 100% 资金，等权买入今日的 Top4 目标股。
# 3. 尾盘 14:50（my_afternoon_trade）：只安排出货！将所有已过 T+1 锁定期的可卖持仓清仓卖出，释放 100% 资金，尾盘坚决不买入任何新股。

from jqdata import *
import numpy as np
import pandas as pd
import math
import datetime
from datetime import datetime as dt

# ==================== 1. 初始化设置 ====================
def initialize(context):
    # 设置回测参数
    set_benchmark('000300.XSHG') # 基准
    set_option('use_real_price', True) # 真实价格交易
    log.set_level('order', 'error') # 隐藏交易日志
    
    # 佣金与印花税设置（买入万三，卖出万三加千分之一印花税，最低5元）
    set_order_cost(OrderCost(
        open_tax=0, 
        close_tax=0.001, 
        open_commission=0.0003, 
        close_commission=0.0003, 
        close_today_commission=0, 
        min_commission=5
    ), type='stock')
    
    # 策略全局变量
    g.max_stocks = 4          # 最大持仓股票数
    g.target_list = []        # 每日目标买入列表
    g.limit_ups_dict = {}     # 缓存个股近10日涨停次数
    
    # 定时运行：
    # 1. 每天早上 9:30 执行早盘买入逻辑
    run_daily(my_morning_trade, time='open')
    # 2. 每天下午 14:50 执行尾盘清仓逻辑（只安排出货，不买入）
    run_daily(my_afternoon_trade, time='14:50')

# ==================== 2. 每日交易前准备（选股阶段） ====================
def before_trading_start(context):
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"--- 正在执行 {yesterday} 的复盘选股策略 ---")
    g.limit_ups_dict = {}  # 每日盘前选股重置缓存
    
    # 兼容 str 和 datetime.date
    if isinstance(yesterday, str):
        date_obj = dt.strptime(yesterday, '%Y-%m-%d').date()
    else:
        date_obj = yesterday
    
    # 1. 获取基础股票池（全A股，排除ST、退市、上市未满90天的次新股）
    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    
    # 获取成交额以筛选前150只最活跃的热门股 (monitor_pool)
    h_amount_all = history(1, '1d', 'money', filtered_stocks, df=False)
    amount_all_dict = {code: float(h_amount_all[code][0]) for code in filtered_stocks if len(h_amount_all[code]) > 0}
    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    
    # 取成交额排名前 150 的股票作为核心监控池 (扩大备选池范围)
    hot_candidate_pool = [item[0] for item in sorted_by_amount_all[:150]]
    
    # 2. 补全这150只热门股的流通市值数据，用于市值打分
    q = query(
        valuation.code,
        valuation.circulating_market_cap
    ).filter(
        valuation.code.in_(hot_candidate_pool)
    )
    df_cap = get_fundamentals(q, date=yesterday)
    candidate_pool = df_cap['code'].tolist()
    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))
    
    if not candidate_pool:
        g.target_list = []
        return
        
    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))
    
    # 热门股成交额排名 (用作 Heat Score 热度打分)
    amount_ranks = {code: idx + 1 for idx, code in enumerate(hot_candidate_pool)}
    
    # 4. 批量获取候选热门股的 101 天日线数据及涨停板数据
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
            
    # 5. 遍历个股，进行原版软件的五大维度打分
    temp_candidates = []
    
    for code in candidate_pool:
        # 获取个股日线序列
        bars = history_data.get(code)
        if bars is None or len(bars) < 21: # 至少需要21天数据
            continue
            
        close_seq = [bar['close'] for bar in bars]
        open_seq = [bar['open'] for bar in bars]
        high_seq = [bar['high'] for bar in bars]
        low_seq = [bar['low'] for bar in bars]
        vol_seq = [bar['volume'] for bar in bars]
        money_seq = [bar['money'] for bar in bars]
        high_limit_seq = [bar['high_limit'] for bar in bars]
        
        # 计算基本量价特征
        metrics = calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq)
        if not metrics:
            continue
            
        # ==================== 新增：从首板1进2策略盘前选股借鉴的指标 ====================
        # 1. 近5日振幅 (volatility_5)
        high_5 = high_seq[-5:]
        low_5 = low_seq[-5:]
        min_l_5 = min(low_5)
        volatility_5 = (max(high_5) - min_l_5) / min_l_5 * 100.0 if min_l_5 > 0 else 0.0
        
        # 2. 近5日涨停数 (limit_ups_5)
        limit_ups_5 = 0
        close_5 = close_seq[-5:]
        high_limit_5 = high_limit_seq[-5:]
        for i in range(len(close_5)):
            if close_5[i] >= high_limit_5[i] - 0.01:
                limit_ups_5 += 1
                
        # 3. 近10日一字/T字涨停数 (extreme_limit_ups)
        extreme_limit_ups = 0
        close_10_ex = close_seq[-10:]
        open_10_ex = open_seq[-10:]
        low_10_ex = low_seq[-10:]
        high_limit_10_ex = high_limit_seq[-10:]
        for i in range(len(close_10_ex)):
            if close_10_ex[i] >= high_limit_10_ex[i] - 0.01:
                if (low_10_ex[i] >= high_limit_10_ex[i] - 0.01) or (open_10_ex[i] >= high_limit_10_ex[i] - 0.01):
                    extreme_limit_ups += 1
                    
        # 4. 百日高位强势突破加分状态 (昨日收盘价是否在过去100日最高价的90%以上)
        max_high_100 = max(high_seq[:-1]) if len(high_seq) > 1 else high_seq[0]
        is_high_position = close_seq[-1] >= max_high_100 * 0.9
        
        # 5. 昨日大涨/首板加成状态 (昨日涨幅 > 7%)
        is_yesterday_strong = (close_seq[-1] - close_seq[-2]) / close_seq[-2] * 100.0 > 7.0 if len(close_seq) >= 2 else False
        
        # 将新增特征注入 metrics 供扣分函数使用
        metrics['extreme_limit_ups'] = extreme_limit_ups
        metrics['volatility_5'] = volatility_5
        metrics['limit_ups_5'] = limit_ups_5
        # ==============================================================================

        circ_cap = cap_dict.get(code, 0.0) # 单位：亿
        
        # ==================== 新增：大市值股性/弹性校验 ====================
        if circ_cap > 300.0: # 流通市值大于300亿的超大市值股票
            # 取最近21天的收盘价，计算过去20天的日收益率
            close_20 = close_seq[-21:]
            big_gain_days = 0
            if len(close_20) >= 2:
                for i in range(1, len(close_20)):
                    daily_ret = (close_20[i] - close_20[i-1]) / close_20[i-1] * 100
                    if daily_ret >= 5.0:
                        big_gain_days += 1
            # 过去20个交易日中一次大涨(>=5%)都没有，说明股性极差，直接过滤淘汰
            if big_gain_days < 1:
                continue
        # =================================================================
            
        # 计算过去 10 个交易日内的精确涨停天数 (使用 high_limit)
        limit_ups = 0
        close_10_raw = close_seq[-10:]
        high_limit_10_raw = high_limit_seq[-10:]
        for i in range(len(close_10_raw)):
            if close_10_raw[i] >= high_limit_10_raw[i] - 0.01:
                limit_ups += 1
                    
        # ==================== 新增：带连板豁免的价格过滤校验 ====================
        cur_price = close_seq[-1]
        if cur_price < 2.0:
            continue
        if cur_price > 50.0:
            # 价格大于 50 元的标的，只有当保持 10 天涨停天数 >= 2 时，才允许豁免并保留
            if limit_ups < 2:
                continue
        # =====================================================================
        
        # A. 热度得分 -> Max 25分
        rank_no = amount_ranks.get(code, 999)
        heat_score = max(0, int(25 * (1 - min(rank_no, 150) / 150.0)))
        
        # B. 流通市值打分 -> Max 10分
        market_cap_score = calc_market_cap_score(circ_cap)
        
        # C. 量价得分 -> Max 30分
        volume_price_score = calc_volume_price_score(metrics)
        
        # D. 位置得分 -> Max 25分
        position_score = calc_position_score(metrics)
        
        # E. 新增均线粘合度打分（基于MA5/10/20/30的收敛度，最高10分）
        ma_cohesion_score = calc_ma_cohesion_score(close_seq)
        
        # F. 风险扣分 -> Subtractive
        risk_penalty = calc_risk_penalty(metrics)
        
        # 连板/强势股加分
        limit_up_bonus = 10 if limit_ups >= 2 else (5 if limit_ups == 1 else 0)
        
        # 黄金低价区间加分 (2.0元 ~ 20.0元)
        low_price_bonus = 5 if 2.0 <= cur_price <= 20.0 else 0
        
        # 计算 EMA 趋势排列得分 (用于过滤下行空头趋势)
        ema_score = calc_ema_alignment_score(close_seq)
        
        # 完美多头趋势加分
        trend_bonus = 10 if ema_score == 100 else (5 if ema_score == 80 else 0)
        
        # 百日高位强势突破加分
        high_position_bonus = 5 if is_high_position else 0
        
        # 昨日大涨/首板加成
        yesterday_strong_bonus = 5 if is_yesterday_strong else 0

        # 汇总总分 (正向得分加总 + 连板奖分 + 低价奖分 + 趋势奖分 + 高位突破奖分 + 昨日大涨奖分，限制在 0 - 100 之间)
        total_score = heat_score + market_cap_score + volume_price_score + position_score + ma_cohesion_score + risk_penalty + limit_up_bonus + low_price_bonus + trend_bonus + high_position_bonus + yesterday_strong_bonus
        total_score = max(0, min(100, total_score))
        
        temp_candidates.append({
            'code': code,
            'score': total_score,
            'ema_score': ema_score,
            'name': name_dict.get(code, code)
        })
        
        # 缓存个股近 10 日内涨停数，供交易时偏离度校验过滤使用
        g.limit_ups_dict[code] = limit_ups
        
    # EMA 趋势过滤：剔除得分低于 40 分的空头通道股
    scored_candidates = [item for item in temp_candidates if item['ema_score'] >= 40]
    filtered_by_ema_count = len(temp_candidates) - len(scored_candidates)
    
    if len(scored_candidates) < g.max_stocks:
        log.info(f"【EMA筛选触发兜底】均线趋势过滤后仅剩 {len(scored_candidates)} 只，不足 {g.max_stocks} 只。自动取消 EMA 趋势过滤，保留全部候选股。")
        scored_candidates = temp_candidates
    else:
        log.info(f"【EMA趋势过滤】初选 {len(temp_candidates)} 只股票中，已成功剔除 {filtered_by_ema_count} 只 EMA 趋势分 < 40 的空头趋势股。")
        
    # 按总分降序排列
    scored_candidates = sorted(scored_candidates, key=lambda x: x['score'], reverse=True)
    
    # 打印排名前10的复盘候选票
    log.info(f"复盘得分排名前10的候选票：")
    for i, item in enumerate(scored_candidates[:10]):
        log.info(f"Top {i+1}: {item['code']} ({item['name']}) - 得分: {item['score']} (EMA趋势分: {item['ema_score']})")
        
    # 筛选前 g.max_stocks 的股票作为买入目标
    g.target_list = [item['code'] for item in scored_candidates][:g.max_stocks]
    log.info(f"今日最终确定的目标买入股票：{g.target_list}")

# ==================== 3. 交易执行 ====================
def my_morning_trade(context):
    """早盘9:30一次性全仓买入逻辑"""
    if not g.target_list:
        return
        
    # 获取可用现金，扣除 1% 摩擦损耗缓冲，等权平分给目标股
    available_cash = context.portfolio.available_cash * 0.99
    cash_per_stock = available_cash / len(g.target_list)
        
    if cash_per_stock <= 0:
        log.info("【早盘买入跳过】当前账户可用资金不足，放弃早盘买入")
        return
        
    for stock in g.target_list:
        current_data = get_current_data()
        price_info = current_data[stock]
        if price_info.paused:
            continue
        # 竞价已涨停或跌停的股票跳过
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"【早盘买入跳过】{stock} 开盘已涨停，无法买入")
            continue
        if price_info.last_price <= price_info.low_limit + 0.01:
            log.info(f"【早盘买入跳过】{stock} 开盘已跌停，无法买入")
            continue
            
        # 开盘价偏离 5 日线校验，防止高开接盘
        limit_ups = g.limit_ups_dict.get(stock, 0)
        try:
            # 获取包含今天在内的最多 6 天日线收盘价，以滤除今天的未收盘 Bar
            hist = attribute_history(stock, 6, '1d', ['close'])
            if len(hist) > 0:
                today_date = context.current_dt.date()
                valid_closes = []
                for idx_val in hist.index:
                    if hasattr(idx_val, 'date'):
                        idx_date = idx_val.date()
                    else:
                        idx_date = idx_val
                    if idx_date != today_date:
                        valid_closes.append(hist.loc[idx_val, 'close'])
                
                valid_closes = [c for c in valid_closes if not (np.isnan(c) or c <= 0)]
                if len(valid_closes) >= 5:
                    ma5 = np.mean(valid_closes[-5:])
                    if ma5 > 0:
                        open_bias = (price_info.last_price - ma5) / ma5 * 100.0
                        # 如果不是连板强股 (近10日内涨停天数 < 2) 且 开盘偏离 5 日线 > 8.0%，则拦截放弃买入
                        if limit_ups < 2 and open_bias > 8.0:
                            log.info(f"【早盘买入跳过】{stock} 非连板妖股 (近10日涨停{limit_ups}次)，且开盘偏离5日线过远 ({open_bias:.1f}%)，放弃买入")
                            continue
        except Exception as e:
            log.warn(f"[开盘偏离度校验异常] {stock}: {str(e)}")
            
        # 一次性买入全额资金
        order_value(stock, cash_per_stock)
        log.info(f"【早盘买入】{stock} 买入资金 {cash_per_stock:.2f}")

def my_afternoon_trade(context):
    """下午14:50尾盘交易逻辑 (只安排出货，不买入新股)"""
    current_holdings = list(context.portfolio.positions.keys())
    
    # 尾盘卖出：只清理已过锁定期可卖的持仓（当天早盘新买入的因为 T+1 限制，今日不可卖，会自动留到下一个交易日尾盘）
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        if position.closeable_amount > 0:
            order(stock, -position.closeable_amount)
            log.info(f"【尾盘出货】{stock} 卖出清理持仓股数 {position.closeable_amount}")

# ==================== 4. 辅助特征计算函数 ====================
def filter_basic_stocks(all_stocks_df, date_obj):
    cutoff_date = date_obj - datetime.timedelta(days=90)
    df_filtered = all_stocks_df[
        (~all_stocks_df['display_name'].str.startswith('ST')) &
        (~all_stocks_df['display_name'].str.startswith('*ST')) &
        (~all_stocks_df['display_name'].str.startswith('退')) &
        (all_stocks_df['start_date'] < cutoff_date)
    ]
    return df_filtered.index.tolist()

def calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq):
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
        'amount_continuity_2d': amount_continuity_2d
    }

def calc_market_cap_score(circ_cap):
    if circ_cap <= 0:
        return 0
    # 黄金弹性区：30亿 ~ 150亿
    if 30.0 <= circ_cap <= 150.0:
        return 10
    # 中盘弹性区：150亿 ~ 300亿
    elif 150.0 < circ_cap <= 300.0:
        return 6
    # 微盘/次微盘防流动性陷阱：小于 15亿
    elif circ_cap < 15.0:
        return -6
    # 大市值大盘股偏好抑制：大于 300亿
    elif circ_cap > 300.0:
        return -4
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
    # 防超买：如果前一日收盘偏离5日线过远，说明涨势过急，容易第二天冲高回落
    if metrics['bias_ma5'] > 8.0:
        penalty -= 8
        
    # ==================== 新增首板1进2风控惩罚 ====================
    # 1. 一字/T字板筹码断层扣分
    if metrics.get('extreme_limit_ups', 0) >= 3:
        penalty -= 10
    # 2. 近5日振幅异常过大扣分
    if metrics.get('volatility_5', 0.0) > 40.0:
        penalty -= 10
    # 3. 近5日频繁涨停见顶风险扣分
    if metrics.get('limit_ups_5', 0) >= 4:
        penalty -= 10
    # =============================================================
    
    return penalty

def calc_ma_cohesion_score(close_seq):
    """计算均线粘合度加分 (基于MA5, MA10, MA20, MA30的收敛度，最高10分)"""
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
        
    # 计算均线最大偏离价差比率 (最宽均线间距占比)
    ma_spread = (max_ma - min_ma) / ma_mean
    
    # 过滤：只有当前收盘价没有跌破均线群下方时（收盘价 >= 最小均线的98%），粘合度加分才生效
    c_t = close_seq[-1]
    if c_t < min_ma * 0.98:
        return 0.0
        
    # 根据粘合紧密程度阶梯加分
    if ma_spread <= 0.02:      # 均线极度粘合（间距在2%以内）
        return 10.0
    elif ma_spread <= 0.04:    # 均线中度粘合（间距在4%以内）
        return 6.0
    elif ma_spread <= 0.06:    # 均线轻度粘合（间距在6%以内）
        return 3.0
        
    return 0.0

def calc_ema_alignment_score(close_seq):
    """
    计算均线多头排列评分，基于 EMA8, EMA21, EMA55
    """
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
