# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略核心：中大市值股 + 放量启动 + 原始量价趋势打分（融合创新策略）
# 融合创新点：
# 1. 股票池保持原版：延续你原有策略的市值规模筛选，排除科创/北交不作为硬性限制，上市满90天即可，通过昨日成交额前100锁定最活跃的热门股。
# 2. 市值打分复原：使用原策略的市值偏好打分（首选80-300亿流通市值，惩罚偏极端的微盘股与超大市值股）。
# 3. 创新量价打分：还原原始量价、位置打分，寻找主流中大市值股的放量上升黄金仓位。
# 4. 避险清仓机制：每年1月和4月的业绩爆雷期，自动触发全仓空仓机制，回避财报期黑天鹅风险。
# 5. 涨停不卖机制：昨日涨停的持仓股票，14:50尾盘如果依然维持封板，不进行强制清仓卖出，保住连板龙头持仓。

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
    set_option("avoid_future_data", True) # 开启防未来函数
    set_slippage(FixedSlippage(0.0)) # 滑点设置
    log.set_level('order', 'error') # 隐藏交易日志，保持清晰
    
    # 佣金与印花税设置（印花税卖出千分之一，买入万分之三）
    set_order_cost(OrderCost(
        open_tax=0, 
        close_tax=0.001, 
        open_commission=0.0003, 
        close_commission=0.0003, 
        close_today_commission=0, 
        min_commission=5
    ), type='stock')
    
    # 策略全局变量
    g.max_stocks = 4          # 最大持仓股票数（使用原有策略设置）
    g.target_list = []        # 每日目标买入列表
    g.no_trading_today_signal = False # 是否处于空仓避雷期
    
    # 定时运行任务：
    # 1. 每天早上 9:00 检查是否处于1/4月的空仓避雷期
    run_daily(check_period, time='9:00')
    # 2. 每天早上 9:30 执行竞价买入今日半仓
    run_daily(my_morning_trade, time='open')
    # 3. 每天下午 14:50 执行尾盘平仓/买入交易
    run_daily(my_afternoon_trade, time='14:50')

# ==================== 2. 每日交易前准备（选股打分） ====================
def before_trading_start(context):
    # 如果检测到今天属于空仓避险时段，直接跳过选股，目标列表清空
    if g.no_trading_today_signal:
        g.target_list = []
        log.info("【空仓避险】今日处于1月或4月高风险期，跳过选股，保持空仓。")
        return

    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"--- 正在执行 {yesterday} 的中大市值放量选股打分 ---")
    
    # 兼容 str 和 datetime.date
    if isinstance(yesterday, str):
        date_obj = dt.strptime(yesterday, '%Y-%m-%d').date()
    else:
        date_obj = yesterday
    
    # 1. 获取基础股票池（全A股，排除ST、退市、上市未满90天的次新股，保留科创板与主板）
    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    
    # 获取全市场候选股的成交额以筛选前100只最活跃的热门股 (对应你软件中 monitor_pool 资金池的筛选逻辑)
    h_amount_all = history(1, '1d', 'money', filtered_stocks, df=False)
    amount_all_dict = {code: float(h_amount_all[code][0]) for code in filtered_stocks if len(h_amount_all[code]) > 0}
    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    
    # 只取成交额排名前 100 的股票作为核心监控池 (monitor_pool)
    hot_candidate_pool = [item[0] for item in sorted_by_amount_all[:100]]
    
    # 2. 补全这100只热门股的流通市值数据，用于市值打分
    q = query(
        valuation.code,
        valuation.circulating_market_cap # 流通市值（亿元）
    ).filter(
        valuation.code.in_(hot_candidate_pool)
    )
    df_cap = get_fundamentals(q, date=yesterday)
    candidate_pool = df_cap['code'].tolist()
    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))
    
    if not candidate_pool:
        g.target_list = []
        return
        
    # 建立股票名称本地映射，避免循环内网络调用
    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))
    
    # 热门股成交额排名 (用作 Heat Score 热度打分)
    amount_ranks = {code: idx + 1 for idx, code in enumerate(hot_candidate_pool)}
    
    # 3. 批量获取候选热门股的65天日线数据（缩短为65天，原超跌策略需要105天数据）
    h_open = history(65, '1d', 'open', candidate_pool, df=False)
    h_close = history(65, '1d', 'close', candidate_pool, df=False)
    h_high = history(65, '1d', 'high', candidate_pool, df=False)
    h_low = history(65, '1d', 'low', candidate_pool, df=False)
    h_volume = history(65, '1d', 'volume', candidate_pool, df=False)
    h_money = history(65, '1d', 'money', candidate_pool, df=False)
    
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
                    'money': h_money[code][i]
                })
            history_data[code] = bars
            
    # 4. 遍历个股，进行五维打分
    scored_candidates = []
    
    for code in candidate_pool:
        # 获取个股日线序列
        bars = history_data.get(code)
        if bars is None or len(bars) < 21: # 还原为21天数据校验
            continue
            
        # 提取近21天的基础量价序列
        close_seq = [bar['close'] for bar in bars]
        open_seq = [bar['open'] for bar in bars]
        high_seq = [bar['high'] for bar in bars]
        low_seq = [bar['low'] for bar in bars]
        vol_seq = [bar['volume'] for bar in bars]
        money_seq = [bar['money'] for bar in bars]
        
        # 计算基本量价特征
        metrics = calc_metrics(close_seq[-21:], open_seq[-21:], high_seq[-21:], low_seq[-21:], vol_seq[-21:], money_seq[-21:])
        if not metrics:
            continue
            
        # A. 热度得分 (昨日成交额排名，前排得高分) -> Max 25分
        rank_no = amount_ranks.get(code, 999)
        heat_score = max(0, int(25 * (1 - min(rank_no, 100) / 100.0)))
        
        # B. 市值打分 (复原：优先选择80亿~300亿市值，惩罚偏小或偏大) -> Max 10分
        circ_cap = cap_dict.get(code, 0.0) # 单位：亿
        market_cap_score = calc_market_cap_score(circ_cap)
        
        # C. 原始量价得分 (技术指标得分) -> Max 30分
        volume_price_score = calc_volume_price_score(metrics)
        
        # D. 位置得分 (MA均线及近期突破) -> Max 25分
        position_score = calc_position_score(metrics)
        
        # E. 风险扣分 (长上影、爆量滞涨等惩罚) -> Subtractive
        risk_penalty = calc_risk_penalty(metrics)
        
        # 汇总总分
        total_score = heat_score + market_cap_score + volume_price_score + position_score + risk_penalty
        total_score = max(0, min(100, total_score))
        
        scored_candidates.append({
            'code': code,
            'score': total_score,
            'name': name_dict.get(code, code)
        })
        
    # 按总分降序排列
    scored_candidates = sorted(scored_candidates, key=lambda x: x['score'], reverse=True)
    
    # 打印排名前10的复盘候选票
    log.info(f"融合策略得分排名前10的候选票：")
    for i, item in enumerate(scored_candidates[:10]):
        log.info(f"Top {i+1}: {item['code']} ({item['name']}) - 得分: {item['score']}")
        
    # 筛选前 g.max_stocks 的股票作为目标买入股票
    g.target_list = [item['code'] for item in scored_candidates][:g.max_stocks]
    log.info(f"今日最终确定的目标买入股票：{g.target_list}")

# ==================== 3. 交易执行 ====================
def my_morning_trade(context):
    """早上9:30集合竞价买入逻辑 (买入50%资金，等权分配给目标列表)"""
    if g.no_trading_today_signal:
        return
        
    if not g.target_list:
        return
        
    # 计算竞价买入每只股票所需的标的资金量 (资产 of 50% 平分给最多 g.max_stocks 只股票)
    cash_per_stock = context.portfolio.total_value * 0.5 / g.max_stocks
    
    # 检查当前账户实际可用的流动资金
    available_cash = context.portfolio.available_cash
    if available_cash < cash_per_stock:
        # 若不够则平分当前所有的实际可用现金
        cash_per_stock = max(0.0, available_cash / len(g.target_list))
        
    if cash_per_stock <= 0:
        log.info("【竞价跳过】当前账户可用资金不足，放弃竞价买入")
        return
        
    for stock in g.target_list:
        current_data = get_current_data()
        price_info = current_data[stock]
        if price_info.paused:
            continue
        # 竞价已涨停或跌停的股票跳过
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"【竞价跳过】{stock} 竞价涨停，无法买入")
            continue
        if price_info.last_price <= price_info.low_limit + 0.01:
            log.info(f"【竞价跳过】{stock} 竞价跌停，无法买入")
            continue
            
        # 使用 order_value 增量建仓买入
        order_value(stock, cash_per_stock)
        log.info(f"【竞价买入】{stock} 增量买入资金 {cash_per_stock:.2f}")

def my_afternoon_trade(context):
    """下午14:50尾盘交易逻辑 (避险清仓/昨日涨停继续持有/买入尾盘仓位)"""
    current_holdings = list(context.portfolio.positions.keys())
    
    # 如果检测到空仓避雷期，清空所有持仓
    if g.no_trading_today_signal:
        if current_holdings:
            for stock in current_holdings:
                position = context.portfolio.positions[stock]
                if position.closeable_amount > 0:
                    order_target_value(stock, 0)
                    log.info(f"【空仓避险清仓】清仓卖出持仓：{stock}")
        return

    # 获取昨日收盘行情，识别昨日涨停股票
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    yesterday_HL_list = []
    if current_holdings:
        df_yesterday = get_price(current_holdings, end_date=yesterday, frequency='daily', fields=['close','high_limit'], count=1, panel=False)
        if not df_yesterday.empty:
            df_HL = df_yesterday[df_yesterday['close'] == df_yesterday['high_limit']]
            yesterday_HL_list = df_HL['code'].tolist()
            
    # 1. 尾盘卖出：按规则清仓可卖持仓
    estimated_freed_cash = 0.0
    current_data = get_current_data()
    
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        if position.closeable_amount <= 0:
            continue
            
        # 如果股票昨日涨停，且今天14:50依然封死涨停，选择继续持有，不卖出
        if stock in yesterday_HL_list:
            if current_data[stock].last_price >= current_data[stock].high_limit - 0.01:
                log.info(f"【继续持有】昨日涨停股 [{stock}] 今日尾盘依然封板，继续持有！")
                continue
            else:
                log.info(f"【涨停打开卖出】昨日涨停股 [{stock}] 今日尾盘封板打开，坚决卖出")
        
        # 正常清仓非封板股票
        order(stock, -position.closeable_amount)
        estimated_freed_cash += position.closeable_amount * current_data[stock].last_price
        log.info(f"【尾盘清仓】{stock} 卖出可卖持仓股数 {position.closeable_amount}")
            
    # 2. 尾盘买入：买入今日高分目标个股
    if not g.target_list:
        return
        
    # 计算估算的总可用资金（当前可用现金 + 刚刚卖出腾出的估算现金）
    total_available_cash = context.portfolio.available_cash + estimated_freed_cash
    
    # 尾盘买入半仓资金分派
    target_total_buy = min(context.portfolio.total_value * 0.5, total_available_cash)
    cash_per_stock = target_total_buy / g.max_stocks
    
    if cash_per_stock <= 0:
        log.info("【尾盘跳过】可用资金不足，放弃尾盘买入")
        return
        
    for stock in g.target_list:
        price_info = current_data[stock]
        if price_info.paused:
            continue
        # 尾盘已涨停或跌停的股票跳过
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"【尾盘跳过】{stock} 已涨停，放弃买入")
            continue
        if price_info.last_price <= price_info.low_limit + 0.01:
            log.info(f"【尾盘跳过】{stock} 已跌停，放弃买入")
            continue
            
        # 增量买入
        order_value(stock, cash_per_stock)
        log.info(f"【尾盘买入】{stock} 增量买入资金 {cash_per_stock:.2f}")

# ==================== 4. 空仓避险周期检查 ====================
def check_period(context):
    """每年 1 月 and 4 月进行强制空仓，回避年报和业绩预告爆雷期"""
    today = context.current_dt.strftime('%m-%d')
    if ('01-04' <= today <= '01-31') or ('04-05' <= today <= '04-30'):
        g.no_trading_today_signal = True
    else:
        g.no_trading_today_signal = False

# ==================== 5. 辅助特征与打分计算函数 ====================
def filter_basic_stocks(all_stocks_df, date_obj):
    """过滤ST、退市及上市未满90天的次新股 (保持原版基础过滤，不限制科创与北交)"""
    cutoff_date = date_obj - datetime.timedelta(days=90)
    df_filtered = all_stocks_df[
        (~all_stocks_df['display_name'].str.startswith('ST')) &
        (~all_stocks_df['display_name'].str.startswith('*ST')) &
        (~all_stocks_df['display_name'].str.startswith('退')) &
        (all_stocks_df['start_date'] < cutoff_date)
    ]
    return df_filtered.index.tolist()

def calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq):
    """提取近21天各项技术量价指标"""
    if len(close_seq) < 21:
        return None
        
    c_t = close_seq[-1]
    o_t = open_seq[-1]
    h_t = high_seq[-1]
    l_t = low_seq[-1]
    v_t = vol_seq[-1]
    m_t = money_seq[-1]
    c_y = close_seq[-2]
    
    # 1. 量比 (5日)
    v_prev5 = vol_seq[-6:-1]
    avg5_vol = np.mean(v_prev5) if v_prev5 else 1.0
    vol_ratio_5 = v_t / avg5_vol if avg5_vol > 0 else 0.0
    
    # 2. 红肥绿瘦比 (5日内阳线量 / 阴线量)
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
    breakout_gap_20 = (c_t - prev20_high) / prev20_high * 100.0 if prev20_high > 0 else 0.0
    
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
    
    # 10. 2日成交额持续性
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
    """市值打分逻辑 (首选80-300亿流通市值，惩罚偏小/超大市值)"""
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
    """技术指标得分 (最高30分，恢复原版权重)"""
    score = 0
    vol_ratio = metrics['vol_ratio_5']
    day_amp = metrics['day_amplitude']
    day_pct = metrics['day_pct']
    
    # 1. 量比评分 (含窄幅吸筹与突破)
    if 1.5 <= vol_ratio <= 3.0:
        score += 10 # 温和放量
    elif 1.2 <= vol_ratio < 1.5:
        score += 6
    elif vol_ratio > 3.0:
        if day_amp < 4.0 and day_pct >= -1.0:
            score += 10 # 爆量窄幅吸筹
        elif day_amp >= 4.0 and day_pct >= 5.0:
            score += 8  # 爆量突破
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
    """位置评分 (最高25分，恢复原版权重)"""
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
    """风险惩罚扣分"""
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
