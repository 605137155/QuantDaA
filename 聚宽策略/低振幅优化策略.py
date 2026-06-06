# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略核心：大A超短复盘候选选股策略
# 交易优化：尾盘使用原始高分买入，早盘增量竞价时“专挑分高且昨日振幅小”的Top5个股买入 (前一日低振幅代表筹码稳定、窄幅吸筹，爆发力更强)

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
    log.set_level('order', 'error') # 隐藏交易日志，保持清晰
    
    # 设置佣金与印花税 (超短线高频交易印花税十分关键)
    # 买入万三，卖出万三加千分之一印花税，最低5元
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
    g.scored_candidates_all = [] # 缓存的所有候选股打分，供早盘重新排序
    
    # 定时运行：
    # 1. 每天早上 9:30 执行集合竞价重排买入
    run_daily(my_morning_trade, time='9:30')
    # 2. 每天下午 14:50 执行尾盘清仓昨日持仓 (仅卖出)
    run_daily(my_afternoon_trade, time='14:50')

# ==================== 2. 每日交易前准备（选股阶段） ====================
def before_trading_start(context):
    # 今天是交易日，我们需要基于“昨天(上一个交易日)”的数据来复盘选股
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"--- 正在执行 {yesterday} 的复盘选股策略 ---")
    
    # 兼容 str 和 datetime.date
    if isinstance(yesterday, str):
        date_obj = dt.strptime(yesterday, '%Y-%m-%d').date()
    else:
        date_obj = yesterday
    
    # 1. 获取基础股票池（全A股，排除ST、退市、上市未满90天的次新股）
    all_stocks_df = get_all_securities(['stock'], date=yesterday)
    filtered_stocks = filter_basic_stocks(all_stocks_df, date_obj)
    log.info(f"基础过滤后股票数: {len(filtered_stocks)}")
    
    # 2. 批量获取全市场的流通市值，然后再筛选出 100 亿到 300 亿之间并在基础池中的股票
    q = query(
        valuation.code,
        valuation.circulating_market_cap
    )
    df_cap = get_fundamentals(q, date=yesterday)
    df_cap = df_cap[df_cap['code'].isin(filtered_stocks)]
    df_cap = df_cap[(df_cap['circulating_market_cap'] >= 100.0) & (df_cap['circulating_market_cap'] <= 300.0)]
    cap_filtered_stocks = df_cap['code'].tolist()
    log.info(f"流通市值筛选后股票数: {len(cap_filtered_stocks)}")
    
    if not cap_filtered_stocks:
        g.scored_candidates_all = []
        return
        
    # 3. 获取昨日成交额，以从符合市值要求的股票中筛选前100只最活跃的热门股作为核心监控池
    h_amount_all = history(1, '1d', 'money', cap_filtered_stocks, df=False)
    amount_all_dict = {code: float(h_amount_all[code][0]) for code in cap_filtered_stocks if len(h_amount_all[code]) > 0}
    sorted_by_amount_all = sorted(amount_all_dict.items(), key=lambda x: x[1], reverse=True)
    
    # 只取成交额排名前 100 的股票作为最终候选池
    candidate_pool = [item[0] for item in sorted_by_amount_all[:100]]
    log.info(f"成交额排名前100过滤后，最终候选池股票数: {len(candidate_pool)}")
    cap_dict = dict(zip(df_cap['code'], df_cap['circulating_market_cap']))
    
    # 建立股票名称本地映射，避免循环内网络调用
    name_dict = dict(zip(all_stocks_df.index, all_stocks_df['display_name']))
    
    # 4. 热门股成交额排名 (用作 Heat Score 热度打分)
    amount_ranks = {code: idx + 1 for idx, code in enumerate(candidate_pool)}
    
    # 4. 批量获取候选热门股的65天日线数据
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
            
    # 5. 遍历个股，进行原版软件的五大维度打分
    scored_candidates = []
    
    for code in candidate_pool:
        # 获取个股日线序列
        bars = history_data.get(code)
        if bars is None or len(bars) < 21: # 至少需要21天数据
            continue
            
        # 提取指标
        close_seq = [bar['close'] for bar in bars]
        open_seq = [bar['open'] for bar in bars]
        high_seq = [bar['high'] for bar in bars]
        low_seq = [bar['low'] for bar in bars]
        vol_seq = [bar['volume'] for bar in bars]
        money_seq = [bar['money'] for bar in bars]
        
        # 计算基本量价特征
        metrics = calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq)
        if not metrics:
            continue
            
        # A. 热度得分 (昨日成交额排名) -> Max 25分
        rank_no = amount_ranks.get(code, 999)
        heat_score = max(0, int(25 * (1 - min(rank_no, 100) / 100.0)))
        
        # B. 流通市值打分 -> Max 10分
        circ_cap = cap_dict.get(code, 0.0) # 单位：亿
        market_cap_score = calc_market_cap_score(circ_cap)
        
        # C. 量价得分 -> Max 30分
        volume_price_score = calc_volume_price_score(metrics)
        
        # D. 位置得分 -> Max 25分
        position_score = calc_position_score(metrics)
        
        # E. 风险扣分 -> Subtractive
        risk_penalty = calc_risk_penalty(metrics)
        
        # 汇总总分 (原版五大基本维度打分)
        total_score = heat_score + market_cap_score + volume_price_score + position_score + risk_penalty
        total_score = max(0, min(100, total_score))
        
        scored_candidates.append({
            'code': code,
            'score': total_score,
            'name': name_dict.get(code, code),
            'yesterday_close': close_seq[-1], # 昨天收盘价
            'yesterday_volume': vol_seq[-1],   # 昨天成交量
            'metrics': metrics
        })
        
    # 按总分降序排列
    scored_candidates = sorted(scored_candidates, key=lambda x: x['score'], reverse=True)
    
    # 打印排名前10的复盘候选票
    log.info(f"复盘得分排名前10的候选票：")
    for i, item in enumerate(scored_candidates[:10]):
        log.info(f"Top {i+1}: {item['code']} ({item['name']}) - 得分: {item['score']}")
        
    # 缓存全部的打分候选股，供早盘重新排序
    g.scored_candidates_all = scored_candidates
    log.info(f"复盘选股完成，已缓存 {len(g.scored_candidates_all)} 只候选股到 g.scored_candidates_all")

def my_morning_trade(context):
    """早盘9:30交易逻辑 (结合集合竞价表现重排打分，开盘立即买入)"""
    if not g.scored_candidates_all:
        return
        
    today_str = context.current_dt.date().strftime('%Y-%m-%d')
    codes = [item['code'] for item in g.scored_candidates_all]
    
    # 安全获取竞价数据
    auction_map = get_today_auction_map(codes, today_str)
    current_data = get_current_data()
    morning_scored_list = []
    
    for item in g.scored_candidates_all:
        stock = item['code']
        price_info = current_data[stock]
        
        # 若停牌则直接跳过
        if price_info.paused:
            continue
            
        yesterday_close = item['yesterday_close']
        day_open = price_info.day_open
        
        if yesterday_close <= 0 or day_open <= 0:
            continue
            
        # 获取竞价数据并进行打分
        auction = auction_map.get(stock, {})
        yesterday_volume = item.get('yesterday_volume', 0.0)
        auction_score, auction_pct, auction_money = calc_auction_score(yesterday_close, auction, price_info, yesterday_volume)
        
        # 复合得分 = 盘前得分 + 竞价打分 - 0.5 * 昨日振幅 (软化振幅惩罚，避免错失龙头)
        composite_score = item['score'] + auction_score - 0.5 * item['metrics']['day_amplitude']
        
        morning_scored_list.append({
            'code': stock,
            'name': item['name'],
            'original_score': item['score'],
            'auction_score': auction_score,
            'gap_pct': auction_pct,
            'auction_money': auction_money,
            'amplitude': item['metrics']['day_amplitude'],
            'composite_score': composite_score,
            'day_open': day_open
        })
        
    if not morning_scored_list:
        log.info("【交易跳过】今日无符合交易条件的候选个股")
        return
        
    # 按照复合得分降序排列
    sorted_candidates = sorted(morning_scored_list, key=lambda x: x['composite_score'], reverse=True)
    
    # 选取复合得分前 4 的个股作为买入目标
    morning_target_list = [item['code'] for item in sorted_candidates][:g.max_stocks]
    
    # 打印选定买入个股的详细信息
    log.info(f"今日早盘 9:30 竞价重排结果（前{g.max_stocks}）：")
    for i, code in enumerate(morning_target_list):
        item = next(x for x in sorted_candidates if x['code'] == code)
        log.info(f"选定买入第 {i+1} 顺位: {code} ({item['name']}) - 复合得分: {item['composite_score']:.2f} - 原始分: {item['original_score']} - 竞价分: {item['auction_score']:.2f} - 高开幅度: {item['gap_pct']:.2f}% - 昨日振幅: {item['amplitude']:.2f}% - 开盘价: {item['day_open']:.2f}")
        
    # 获取可用资金（昨日下午清仓后释放的 100% 资金，保留 1% 缓冲应对手续费）
    available_cash = context.portfolio.available_cash
    cash_per_stock = (available_cash * 0.99) / len(morning_target_list) if morning_target_list else 0.0
    
    if cash_per_stock <= 0:
        log.info("【交易跳过】可用资金不足，放弃早盘买入")
        return
        
    for stock in morning_target_list:
        price_info = current_data[stock]
        
        # 判断开盘价是否涨跌停，涨跌停不买
        if price_info.day_open >= price_info.high_limit - 0.01:
            log.info(f"【交易跳过】{stock} 开盘涨停，放弃买入")
            continue
        if price_info.day_open <= price_info.low_limit + 0.01:
            log.info(f"【交易跳过】{stock} 开盘跌停，放弃买入")
            continue
            
        # 使用 order_value 买入
        order_value(stock, cash_per_stock)
        log.info(f"【早盘竞价买入】{stock} 买入资金 {cash_per_stock:.2f}")

def my_afternoon_trade(context):
    """下午14:50尾盘交易逻辑 (仅卖出可卖出的昨日股票，释放资金供次日竞价买入)"""
    current_holdings = list(context.portfolio.positions.keys())
    
    # 尾盘卖出：只卖出昨天或之前买入的“可卖股票”释放资金
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        # 只卖出已过锁定期可卖的持仓 (包含昨日买的)
        if position.closeable_amount > 0:
            order(stock, -position.closeable_amount)
            log.info(f"【尾盘卖出】{stock} 清仓可卖持仓股数 {position.closeable_amount}")

# ==================== 4. 辅助特征计算函数 ====================

def filter_basic_stocks(all_stocks_df, date_obj):
    """过滤基本面极差及新股 (全向量化加速版，耗时从几百秒缩短至几毫秒)"""
    cutoff_date = date_obj - datetime.timedelta(days=90)
    
    # 过滤ST, *ST, 退市及上市未满90天的新股
    df_filtered = all_stocks_df[
        (~all_stocks_df['display_name'].str.startswith('ST')) &
        (~all_stocks_df['display_name'].str.startswith('*ST')) &
        (~all_stocks_df['display_name'].str.startswith('退')) &
        (all_stocks_df['start_date'] < cutoff_date)
    ]
    return df_filtered.index.tolist()

def calc_metrics(close_seq, open_seq, high_seq, low_seq, vol_seq, money_seq):
    """提取各项技术量价指标"""
    if len(close_seq) < 21:
        return None
        
    # T日（昨天）的数据，即序列最后一个值
    c_t = close_seq[-1]
    o_t = open_seq[-1]
    h_t = high_seq[-1]
    l_t = low_seq[-1]
    v_t = vol_seq[-1]
    m_t = money_seq[-1]
    
    c_y = close_seq[-2] # T-1日
    
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

# ==================== 5. 打分维度实现 ====================

def calc_market_cap_score(circ_cap):
    """市值打分逻辑 (流通市值以亿元为单位)"""
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
    """量价评分逻辑"""
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
    """位置评分逻辑"""
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
    # A. 长上影线扣分
    if metrics['upper_shadow_ratio'] > 4.0:
        penalty -= 8
    # B. 爆量滞涨扣分
    if metrics['vol_ratio_5'] > 3.0 and metrics['day_pct'] < 2.0 and metrics['day_amplitude'] >= 4.0:
        penalty -= 10
    # C. 连续加速风险
    if metrics['pct3'] > 18.0:
        penalty -= 8
    # E. 收盘偏弱扣分
    if metrics['close_strength'] < 0.4:
        penalty -= 6
    return penalty

# ==================== 6. 集合竞价辅助函数 ====================

def get_today_auction_map(codes, today):
    """安全获取当日集合竞价数据；异常时返回空映射，后续自动用开盘价兜底。"""
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
        if df is not None and len(df) > 0 and hasattr(df, 'iterrows'):
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
    except Exception as exc:
        log.info(f"【竞价数据】get_call_auction 接口异常，使用开盘价兜底评分：{exc}")
    return result

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

def calc_auction_score(yesterday_close, auction, price_info, yesterday_volume=0.0):
    """集合竞价打分：结合开盘涨幅、竞价量能比、竞价成交活跃度、买一/卖一盘强弱对比。"""
    auction_price = auction.get('current', 0.0)
    if auction_price <= 0:
        auction_price = price_info.day_open

    # 1. 竞价涨幅打分
    auction_pct = (auction_price - yesterday_close) / yesterday_close * 100.0 if yesterday_close > 0 else 0.0
    gap_score = 0.0
    if 2.0 <= auction_pct <= 5.5:
        gap_score += 15.0  # 黄金高开区间
    elif (0.0 <= auction_pct < 2.0) or (5.5 < auction_pct <= 7.0):
        gap_score += 8.0   # 温和高开
    elif auction_pct < 0.0:
        gap_score -= 15.0  # 低开/平开惩罚
    # 超高开 >7.0% 不加分也不扣分 (0分)

    # 2. 竞价量能比校验（防止无量假高开诱多）
    # 当高开超过 1.0% 时，如果竞价成交量占昨日总成交量低于 0.3%，则视为无量诱多假高开，强行扣除 20 分。
    if auction_pct > 1.0 and yesterday_volume > 0:
        auction_volume = auction.get('volume', 0.0)
        # 若获取不到集合竞价成交量，使用成交额/开盘价估算
        if auction_volume <= 0 and auction_price > 0:
            auction_volume = auction.get('money', 0.0) / auction_price
            
        vol_ratio = (auction_volume / yesterday_volume) * 100.0
        if vol_ratio < 0.3:
            gap_score -= 20.0

    # 3. 竞价成交额打分 (优先有大资金真实参与的股票)
    auction_money = auction.get('money', 0.0)
    money_score = 0.0
    if auction_money >= 30000000.0:     # >= 3000万
        money_score += 8.0
    elif auction_money >= 10000000.0:   # >= 1000万
        money_score += 5.0
    elif auction_money >= 3000000.0:    # >= 300万
        money_score += 2.0

    # 4. 买一卖一挂单比例打分
    buy_v = auction.get('b1_v', 0.0)
    sell_v = auction.get('a1_v', 0.0)
    ratio_score = 0.0
    if sell_v > 0:
        ratio = buy_v / sell_v
        if ratio >= 2.0:
            ratio_score += 6.0
        elif ratio >= 1.2:
            ratio_score += 3.0
    elif buy_v > 0:
        ratio_score += 6.0  # 卖一挂单为空，极强

    total_auction_score = gap_score + money_score + ratio_score
    return total_auction_score, auction_pct, auction_money
