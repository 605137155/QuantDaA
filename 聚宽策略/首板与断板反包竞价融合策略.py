# 克隆与修改自：首板一进二 优化版 v17_ML代价敏感
# 融合策略名称：首板与断板反包竞价融合策略
# ========================================================================
# 核心思路：
#   1. 候选池构建：每日寻找最近6日内有涨停记录的热门股；
#   2. 分类处理：
#      - Setup 1 (首板1进2)：昨日首板，今日竞价走强，匹配 CONDITION_RULES_SETUP1。
#      - Setup 2 (断板反包)：前日首板，昨日断板（且回调小于5%），今日竞价强势，匹配 CONDITION_RULES_SETUP2。
#   3. 风控体系：完全整合原版ML风控与净值曲线动量控制。
# ========================================================================

from jqdata import *
import pandas as pd
import numpy as np

# ========================================================================
# ██ 可调参数区 ██
# ========================================================================

# -------------------- 1. 形态筛选条件 --------------------
LIMIT_UP_RATIO = 0.998             # 涨停比例阈值 (收盘价 >= 涨停价 * 0.998)
MIN_YESTERDAY_CLOSE_RATIO = 0.95   # 针对断板反包：昨日收盘价/前日收盘价的最低占比（即跌幅不超过5%）

# -------------------- 2. 竞价过滤规则 --------------------
# 2.1 Setup 1 (首板1进2) 的竞价规则
# 修复天花板效应：取消9%硬顶，增加一字板/准一字板规则
CONDITION_RULES_SETUP1 = [
    ('E: 一字板/准一字 竞价涨幅>=9.8% | 竞昨比>=0.5%', 1.098, 1.11, 0.005, 1.0),
    ('A: 竞价高开7~9% | 竞昨比2.5~25%',  1.07, 1.098, 0.025, 0.25),
    ('B: 竞价高开4~7% | 竞昨比2~25%',   1.04, 1.07, 0.02, 0.25),
    ('C: 竞价平开至小高开0~4% | 竞昨比1.5~15%', 1.00, 1.04, 0.015, 0.15),
]

# 2.2 Setup 2 (断板反包) 的竞价规则
# 修复天花板效应：提高上限至12%，降低竞昨比下限至0.5%，增加深低开规则
CONDITION_RULES_SETUP2 = [
    ('反包E: 竞价高开8~12% | 竞昨比0.5~25%', 1.08, 1.12, 0.005, 0.25),
    ('反包A: 竞价高开4~8% | 竞昨比0.5~20%', 1.04, 1.08, 0.005, 0.20),
    ('反包B: 竞价高开2~4% | 竞昨比0.5~15%', 1.02, 1.04, 0.005, 0.15),
    ('反包C: 竞价平开至小高开0~2% | 竞昨比0.5~12%', 1.00, 1.02, 0.005, 0.12),
    ('反包D: 竞价低开-3~0% | 竞昨比0.5~12%', 0.97, 1.00, 0.005, 0.12),
    ('反包F: 深低开-5~-3% | 竞昨比0.5~10%', 0.95, 0.97, 0.005, 0.10),
]

# -------------------- 3. 市值与流通性过滤 --------------------
MIN_CAP = 10                       # 最小总市值 (10亿)
MAX_CAP = 1200                     # 最大流通市值 (上调至1200亿，防止误杀中国长城、宏和科技等行业龙头)
MIN_AMOUNT = 1e8                   # 成交额下限 (1亿)
MAX_AMOUNT = 100e8                 # 成交额上限 (上调至100亿，包含超大体量龙头中军)

# -------------------- 4. 止盈止损风控 --------------------
DROP_PERCENT = 0.05                # 跌幅止损百分比 (5%)
MA5_STOP_LOSS_BUFFER = 0.02        # 5日线跌破止损缓冲 (2%)

# -------------------- 5. 竞价重排与持仓控制 --------------------
MAX_BUY_COUNT = 2                  # 每日最大买入股票数量 (限制数量以集中仓位，过滤排名靠后的杂毛)

# ========================================================================
# ██ 初始化与框架逻辑 ██
# ========================================================================

def initialize(context):
    log.set_level('order', 'error')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(FixedSlippage(0.005))
    set_order_cost(OrderCost(open_tax=0, close_tax=0.0005, open_commission=0.0002, close_commission=0.0002, min_commission=5), type='stock')
    set_benchmark('399303.XSHE')
    
    g.information = {}
    g.condition_stats = {}
    g.name_cache = {}
    
    # 净值曲线动量
    g.consecutive_loss_days = 0     # 连续亏损天数
    g.skip_buy = False              # 暂停买入标志
    g.peak_value = 0                # 近期净值高点
    g.drawdown_reduction = 1.0      # 回撤减仓系数
    g.prev_day_value = 0            # 昨日总资产
    
    # ML在线学习
    g.ml_features = []             # 历史特征列表
    g.ml_labels = []               # 历史标签
    g.ml_weights = None            # 模型权重
    g.ml_pred_reduction = 1.0      # ML预测的买入系数
    g.recent_pnls = []             # 近5日收益
    g.yesterday_buy_count = 0      # 昨日买入数
    g.pending_features = None      # 待标注的特征
    g.day_count = 0
    
    # 目标股票池分类存储
    g.target_setup1 = []           # Setup 1: 1进2候选
    g.target_setup2 = []           # Setup 2: 断板反包候选（前日涨停）
    g.target_setup3 = []           # Setup 3: 三日断板反包候选（三日前涨停）
    
    g.tracked_candidates = {}      # 追踪所有候选股至今的最高价和涨幅
    g.bought_stocks = set()        # 今日实际买入的股票（用于漏选分析）
    
    run_daily(before_market_open, time='09:10')
    run_daily(get_buy, '09:26')
    run_daily(get_close_sell, time='11:25')
    run_daily(get_close_sell, time='13:30')
    run_daily(eod_stats, time='15:00')


def before_market_open(context): 
    y_day = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"\n{'='*80}")
    log.info(f"【盘前选股】昨日: {y_day}")
    log.info(f"{'='*80}")

    initial_list = prepare_stock_list(context)
    log.info(f"[选股] 初始过滤股票池: {len(initial_list)}只")
    
    # 获取过去9个交易日的数据（Setup 3需要d_4）
    trade_days = get_trade_days(end_date=context.previous_date, count=9)
    if len(trade_days) < 9:
        log.info("[选股] 交易历史不足9天，跳过选股")
        g.target_setup1 = []
        g.target_setup2 = []
        g.target_setup3 = []
        return

    t_minus_9 = trade_days[0]
    t_minus_1 = trade_days[-1]

    price_df = get_price(
        initial_list, start_date=t_minus_9, end_date=t_minus_1, frequency='1d',
        fields=['close', 'high_limit', 'money', 'volume', 'high', 'low'], panel=False
    )
    if price_df.empty:
        log.info("[选股] 行情数据为空，跳过选股")
        g.target_setup1 = []
        g.target_setup2 = []
        return
        
    price_df['is_limit'] = price_df['close'] >= price_df['high_limit'] * LIMIT_UP_RATIO
    stock_groups = price_df.groupby('code')
    
    d_1 = trade_days[8].strftime('%Y-%m-%d') # T-1 (昨日)
    d_2 = trade_days[7].strftime('%Y-%m-%d') # T-2 (前日)
    d_3 = trade_days[6].strftime('%Y-%m-%d') # T-3 (大前日)
    d_4 = trade_days[5].strftime('%Y-%m-%d') # T-4 (三日前)
    recent_6_days = [d.strftime('%Y-%m-%d') for d in trade_days[3:9]] # T-6 到 T-1

    raw_setup1 = []
    raw_setup2 = []
    raw_setup3 = []
    yesterday_close_dict = {}  # 缓存昨日收盘价
    
    for code, group in stock_groups:
        group_sorted = group.sort_values('time')
        if len(group_sorted) < 8:
            continue
            
        limit_map = dict(zip(group_sorted['time'].dt.strftime('%Y-%m-%d'), group_sorted['is_limit']))
        close_map = dict(zip(group_sorted['time'].dt.strftime('%Y-%m-%d'), group_sorted['close']))
        
        # 1. 检查最近6个交易日是否至少有一次收盘涨停
        hit_limit_recently = any(limit_map.get(day, False) for day in recent_6_days)
        if not hit_limit_recently:
            continue
            
        yesterday_close_dict[code] = close_map.get(d_1, 0)
        
        # 2. 形态分类
        # Setup 1 (首板1进2)：昨日是首板（昨日涨停，前日未涨停）
        is_setup1 = limit_map.get(d_1, False) and not limit_map.get(d_2, False)
        
        # Setup 2 (断板反包)：前日是首板（前日涨停，大前日未涨停），昨日断板且收盘跌幅小于5%
        is_setup2 = (
            limit_map.get(d_2, False) and not limit_map.get(d_3, False)
            and not limit_map.get(d_1, False)
            and close_map.get(d_1, 0) >= close_map.get(d_2, 0) * MIN_YESTERDAY_CLOSE_RATIO
        )

        # Setup 3 (三日断板反包)：三日前是首板（三日前涨停，四日前未涨停），前日和昨日均未涨停，昨日跌幅小于5%
        is_setup3 = (
            limit_map.get(d_4, False) and not limit_map.get(trade_days[4].strftime('%Y-%m-%d'), False)
            and not limit_map.get(d_3, False) and not limit_map.get(d_2, False)
            and not limit_map.get(d_1, False)
            and close_map.get(d_1, 0) >= close_map.get(d_4, 0) * MIN_YESTERDAY_CLOSE_RATIO
        )

        if is_setup1:
            raw_setup1.append(code)
        elif is_setup2:
            raw_setup2.append(code)
        elif is_setup3:
            raw_setup3.append(code)
            
    log.info(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_setup1)}只 | Setup 2 (断板反包): {len(raw_setup2)}只 | Setup 3 (三日断板): {len(raw_setup3)}只")

    # 3. 应用过滤规则 (与原版ML风控一致)
    g.target_setup1 = filter_excessive_limit_up(raw_setup1, y_day)
    g.target_setup1 = filter_excessive_increase(g.target_setup1, y_day)
    g.target_setup1 = filter_excessive_limit_days(g.target_setup1, y_day)
    g.target_setup1 = filter_below_n_high(g.target_setup1, y_day, days=100)

    g.target_setup2 = filter_excessive_limit_up(raw_setup2, y_day)
    g.target_setup2 = filter_excessive_increase(g.target_setup2, y_day)
    g.target_setup2 = filter_excessive_limit_days(g.target_setup2, y_day)
    g.target_setup2 = filter_below_n_high(g.target_setup2, y_day, days=100)

    g.target_setup3 = filter_excessive_limit_up(raw_setup3, y_day)
    g.target_setup3 = filter_excessive_increase(g.target_setup3, y_day)
    g.target_setup3 = filter_excessive_limit_days(g.target_setup3, y_day)
    g.target_setup3 = filter_below_n_high(g.target_setup3, y_day, days=100)
    
    # 净值曲线动量判断
    if g.skip_buy:
        g.skip_buy = False  # 只暂停1天
        log.info("[净值动量] 冷静期结束，恢复交易")
    
    if g.peak_value > 0:
        current_dd = (context.portfolio.total_value / g.peak_value - 1)
        if current_dd < -0.08:
            g.drawdown_reduction = 0.5
            log.info(f"[净值动量] 净值从高点回撤{current_dd:.1%}，买入减半")
        else:
            g.drawdown_reduction = 1.0
            
    # ML风控预测
    if g.ml_weights is not None and g.day_count >= 60:
        try:
            today_features = compute_ml_features(context)
            if today_features is not None:
                score = sigmoid(np.dot(g.ml_weights, today_features))
                if score > 0.7:
                    g.ml_pred_reduction = 0.0  # 预测亏损概率>70%，跳过
                    log.info(f"[ML风控] 预测今日亏损概率{score:.1%}，跳过买入")
                elif score > 0.5:
                    g.ml_pred_reduction = 0.5  # 预测今日亏损概率50-70%，减半
                    log.info(f"[ML风控] 预测今日亏损概率{score:.1%}，减半买入")
                else:
                    g.ml_pred_reduction = 1.0
                    log.info(f"[ML风控] 预测今日亏损概率{score:.1%}，正常买入")
        except Exception as e:
            g.ml_pred_reduction = 1.0
            log.info(f"[ML风控] 预测异常: {e}")
    else:
        g.ml_pred_reduction = 1.0
        
    # 构建名字缓存与输出日志
    g.name_cache = {}
    all_targets = g.target_setup1 + g.target_setup2 + g.target_setup3
    for s in all_targets:
        try:
            g.name_cache[s] = get_security_info(s).display_name
        except:
            g.name_cache[s] = '未知'

    log.info(f"今日选股池 (Setup 1 1进2 - {len(g.target_setup1)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup1]))
    log.info(f"今日选股池 (Setup 2 断板反包 - {len(g.target_setup2)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup2]))
    log.info(f"今日选股池 (Setup 3 三日断板 - {len(g.target_setup3)}只): " + ", ".join([f"{s}({g.name_cache.get(s, '未知')})" for s in g.target_setup3]))

    # 注册新候选股到全局追踪池
    today_str = context.current_dt.strftime('%Y-%m-%d')
    for s in g.target_setup1:
        if s not in g.tracked_candidates:
            base_p = yesterday_close_dict.get(s, 0)
            if base_p <= 0:
                try:
                    base_p = get_price(s, end_date=y_day, frequency='daily', fields=['close'], count=1).iloc[0]['close']
                except:
                    base_p = 1.0
            g.tracked_candidates[s] = {
                'entry_date': today_str,
                'base_price': base_p,
                'max_price': base_p,
                'setup_type': '1进2'
            }
    for s in g.target_setup2:
        if s not in g.tracked_candidates:
            base_p = yesterday_close_dict.get(s, 0)
            if base_p <= 0:
                try:
                    base_p = get_price(s, end_date=y_day, frequency='daily', fields=['close'], count=1).iloc[0]['close']
                except:
                    base_p = 1.0
            g.tracked_candidates[s] = {
                'entry_date': today_str,
                'base_price': base_p,
                'max_price': base_p,
                'setup_type': '断板反包'
            }
    for s in g.target_setup3:
        if s not in g.tracked_candidates:
            base_p = yesterday_close_dict.get(s, 0)
            if base_p <= 0:
                try:
                    base_p = get_price(s, end_date=y_day, frequency='daily', fields=['close'], count=1).iloc[0]['close']
                except:
                    base_p = 1.0
            g.tracked_candidates[s] = {
                'entry_date': today_str,
                'base_price': base_p,
                'max_price': base_p,
                'setup_type': '三日断板'
            }


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

    all_targets = g.target_setup1 + g.target_setup2 + g.target_setup3
    if not all_targets:
        log.info("[竞价] 今日无任何候选股票")
        return

    # 批量拉取前一日日线数据
    prev_df = get_price(
        all_targets, end_date=y_day, frequency='daily',
        fields=['close', 'volume', 'money'], count=1, panel=False,
        fill_paused=False, skip_paused=True
    )
    prev_map = {row['code']: row for _, row in prev_df.iterrows()}

    # 批量拉取市值与换手率数据
    val_df = get_fundamentals(
        query(valuation.code, valuation.market_cap, valuation.circulating_market_cap, valuation.turnover_ratio)
        .filter(valuation.code.in_(all_targets)),
        date=str(y_day)[:10]
    )
    val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

    hl_base = {s: current_data[s].high_limit / 1.1 for s in all_targets}

    log.info(f"【竞价开始】共有 {len(g.target_setup1)}只 Setup 1 候选，{len(g.target_setup2)}只 Setup 2 候选，{len(g.target_setup3)}只 Setup 3 候选")

    # ==================== 1. 过滤 & 匹配 Setup 1 (首板1进2) ====================
    for s in g.target_setup1:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None:
                log.info(f"  [排除-1] {s}({name}) 未能获取昨日日线数据")
                continue
            # 计算昨日VWAP均价相对于前日收盘的涨幅，判断昨日板的硬度
            avg_chg = prev['money'] / prev['volume'] / prev['close'] * 1.1 - 1
            money = prev['money']
            open_price = current_data[s].day_open
            val = val_map.get(s)

            if avg_chg < 0.035:
                log.info(f"  [排除-1] {s}({name}) 昨日均价涨幅 {avg_chg:.2%} < 3.5%")
                continue
            if open_price <= 3:
                log.info(f"  [排除-1] {s}({name}) 开盘价 {open_price} <= 3")
                continue
            if val is None:
                log.info(f"  [排除-1] {s}({name}) 未能获取市值数据")
                continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                log.info(f"  [排除-1] {s}({name}) 市值不符: 总市值={val['market_cap']:.1f}亿, 流通={val['circulating_market_cap']:.1f}亿")
                continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT:
                log.info(f"  [排除-1] {s}({name}) 昨日成交额 {money/1e8:.2f}亿 不在 [{MIN_AMOUNT/1e8:.1f}亿, {MAX_AMOUNT/1e8:.1f}亿] 区间")
                continue
        except Exception as e:
            log.info(f"  [排除-1] {s}({name}) 基础过滤异常: {e}")
            continue

        try:
            zyts = calculate_zyts(s, context)
            vol_data = attribute_history(s, zyts, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                log.info(f"  [排除-1] {s}({name}) 历史成交量天数不足")
                continue
            # 已放宽取消昨日放量要求（防止误杀缩量强板）
            pass
        except Exception as e:
            log.info(f"  [排除-1] {s}({name}) 成交量过滤异常: {e}")
            continue

        try:
            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val['turnover_ratio'])) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty:
                log.info(f"  [排除-1] {s}({name}) 获取竞价数据为空")
                continue
            cur_ratio = auction['current'][0] / hl_base[s]
            auction_ratio = auction['volume'][0] / vol_data['volume'][-1]

            # 计算买卖盘力量对比 (OBI)
            buymoney = 0.0
            sellmoney = 0.0
            for i in range(1, 6):
                ap = f'a{i}_p'
                av = f'a{i}_v'
                bp = f'b{i}_p'
                bv = f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]
                    val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av):
                        sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]
                    val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv):
                        buymoney += val_bp * val_bv
            
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            # OBI 力量比过滤：买方力量必须强于卖方力量，过滤抛压过大的个股
            if obi_ratio < 0.6:
                log.info(f"  [排除-1] {s}({name}) 竞价买卖比不符: 买方资金={buymoney/1e4:.1f}万, 卖方资金={sellmoney/1e4:.1f}万, 比例={obi_ratio:.2f} < 0.6")
                continue

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP1:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log.info(f"  [排除-1] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}% (成交额={money/1e8:.2f}亿)")
                continue

            # 弱转强与打分重排
            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            # 一字板/准一字板加分：竞价涨幅>=9.8%说明买方极度看好
            if cur_ratio >= 1.098:
                score += 15.0
            # 自选涨幅因子：曾入选且至今涨幅显著的股票加分
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log.info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")
            qualified_stocks.append({
                'code': s,
                'name': name,
                'score': score,
                'type': f"1进2({matched_condition})"
            })
            log.info(f"✅ {s}({name}) 符合 Setup 1(1进2)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            log.info(f"  [排除-1] {s}({name}) 竞价匹配异常: {e}")
            continue

    # ==================== 2. 过滤 & 匹配 Setup 2 (断板反包) ====================
    for s in g.target_setup2:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None:
                log.info(f"  [排除-2] {s}({name}) 未能获取昨日日线数据")
                continue
            
            # 昨日是断板回调，昨日成交额和市值大小正常即可
            money = prev['money']
            open_price = current_data[s].day_open
            val = val_map.get(s)

            if open_price <= 3:
                log.info(f"  [排除-2] {s}({name}) 开盘价 {open_price} <= 3")
                continue
            if val is None:
                log.info(f"  [排除-2] {s}({name}) 未能获取市值数据")
                continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                log.info(f"  [排除-2] {s}({name}) 市值不符: 总市值={val['market_cap']:.1f}亿, 流通={val['circulating_market_cap']:.1f}亿")
                continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT:
                log.info(f"  [排除-2] {s}({name}) 昨日成交额 {money/1e8:.2f}亿 不在 [{MIN_AMOUNT/1e8:.1f}亿, {MAX_AMOUNT/1e8:.1f}亿] 区间")
                continue
        except Exception as e:
            log.info(f"  [排除-2] {s}({name}) 基础过滤异常: {e}")
            continue

        try:
            zyts = calculate_zyts(s, context)
            vol_data = attribute_history(s, zyts, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                log.info(f"  [排除-2] {s}({name}) 历史成交量天数不足")
                continue
        except Exception as e:
            log.info(f"  [排除-2] {s}({name}) 成交量过滤异常: {e}")
            continue

        try:
            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val['turnover_ratio'])) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty:
                log.info(f"  [排除-2] {s}({name}) 获取竞价数据为空")
                continue
            cur_ratio = auction['current'][0] / prev['close']
            auction_ratio = auction['volume'][0] / vol_data['volume'][-1]

            # 计算买卖盘力量对比 (OBI)
            buymoney = 0.0
            sellmoney = 0.0
            for i in range(1, 6):
                ap = f'a{i}_p'
                av = f'a{i}_v'
                bp = f'b{i}_p'
                bv = f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]
                    val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av):
                        sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]
                    val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv):
                        buymoney += val_bp * val_bv
            
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            # OBI 力量比过滤：买方力量必须强于卖方力量，过滤抛压过大的个股
            if obi_ratio < 0.6:
                log.info(f"  [排除-2] {s}({name}) 竞价买卖比不符: 买方资金={buymoney/1e4:.1f}万, 卖方资金={sellmoney/1e4:.1f}万, 比例={obi_ratio:.2f} < 0.6")
                continue

            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP2:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log.info(f"  [排除-2] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}% (成交额={money/1e8:.2f}亿)")
                continue

            # 弱转强与打分重排
            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            # 高开反包加分：竞价涨幅>=8%说明反包意愿极强
            if cur_ratio >= 1.08:
                score += 12.0
            # 深低开反包加分：竞价涨幅<-3%但仍有资金承接，是经典反包形态
            elif cur_ratio < 0.97:
                score += 5.0
            # 自选涨幅因子：曾入选且至今涨幅显著的股票加分
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log.info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")
            qualified_stocks.append({
                'code': s,
                'name': name,
                'score': score,
                'type': f"断板反包({matched_condition})"
            })
            log.info(f"✅ {s}({name}) 符合 Setup 2(断板反包)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            log.info(f"  [排除-2] {s}({name}) 竞价匹配异常: {e}")
            continue

    # ==================== 2.5 过滤 & 匹配 Setup 3 (三日断板反包) ====================
    for s in g.target_setup3:
        name = g.name_cache.get(s, '未知')
        try:
            prev = prev_map.get(s)
            if prev is None:
                log.info(f"  [排除-3] {s}({name}) 未能获取昨日日线数据")
                continue

            money = prev['money']
            open_price = current_data[s].day_open
            val = val_map.get(s)

            if open_price <= 3:
                log.info(f"  [排除-3] {s}({name}) 开盘价 {open_price} <= 3")
                continue
            if val is None:
                log.info(f"  [排除-3] {s}({name}) 未能获取市值数据")
                continue
            if val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                log.info(f"  [排除-3] {s}({name}) 市值不符: 总市值={val['market_cap']:.1f}亿, 流通={val['circulating_market_cap']:.1f}亿")
                continue
            if money < MIN_AMOUNT or money > MAX_AMOUNT:
                log.info(f"  [排除-3] {s}({name}) 昨日成交额 {money/1e8:.2f}亿 不在 [{MIN_AMOUNT/1e8:.1f}亿, {MAX_AMOUNT/1e8:.1f}亿] 区间")
                continue
        except Exception as e:
            log.info(f"  [排除-3] {s}({name}) 基础过滤异常: {e}")
            continue

        try:
            zyts = calculate_zyts(s, context)
            vol_data = attribute_history(s, zyts, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                log.info(f"  [排除-3] {s}({name}) 历史成交量天数不足")
                continue
        except Exception as e:
            log.info(f"  [排除-3] {s}({name}) 成交量过滤异常: {e}")
            continue

        try:
            turnover_ratio = val['turnover_ratio'] if (val is not None and not pd.isna(val['turnover_ratio'])) else 0.0
            auction = get_call_auction(s, start_date=start, end_date=end, fields=[
                'time', 'volume', 'current',
                'a1_p','a2_p','a3_p','a4_p','a5_p', 'a1_v','a2_v','a3_v','a4_v','a5_v',
                'b1_p','b2_p','b3_p','b4_p','b5_p', 'b1_v','b2_v','b3_v','b4_v','b5_v'
            ])
            if auction.empty:
                log.info(f"  [排除-3] {s}({name}) 获取竞价数据为空")
                continue
            cur_ratio = auction['current'][0] / prev['close']
            auction_ratio = auction['volume'][0] / vol_data['volume'][-1]

            buymoney = 0.0
            sellmoney = 0.0
            for i in range(1, 6):
                ap = f'a{i}_p'
                av = f'a{i}_v'
                bp = f'b{i}_p'
                bv = f'b{i}_v'
                if ap in auction.columns and av in auction.columns:
                    val_ap = auction[ap].iloc[0]
                    val_av = auction[av].iloc[0]
                    if not pd.isna(val_ap) and not pd.isna(val_av):
                        sellmoney += val_ap * val_av
                if bp in auction.columns and bv in auction.columns:
                    val_bp = auction[bp].iloc[0]
                    val_bv = auction[bv].iloc[0]
                    if not pd.isna(val_bp) and not pd.isna(val_bv):
                        buymoney += val_bp * val_bv

            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            if obi_ratio < 0.6:
                log.info(f"  [排除-3] {s}({name}) 竞价买卖比不符: 买方资金={buymoney/1e4:.1f}万, 卖方资金={sellmoney/1e4:.1f}万, 比例={obi_ratio:.2f} < 0.6")
                continue

            # 三日断板反包复用断板反包的竞价规则
            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES_SETUP2:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break

            if matched_condition is None:
                log.info(f"  [排除-3] {s}({name}) 竞价未匹配成功: 竞价涨幅={(cur_ratio-1)*100:.2f}%, 竞昨比={auction_ratio*100:.2f}% (成交额={money/1e8:.2f}亿)")
                continue

            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi_ratio * 2.0
            if cur_ratio >= 1.08:
                score += 12.0
            elif cur_ratio < 0.97:
                score += 5.0
            tracked_bonus = calc_tracked_bonus(s)
            if tracked_bonus > 0:
                score += tracked_bonus
                log.info(f"  [涨幅因子] {s}({name}) 自选至今涨幅加分: +{tracked_bonus:.0f}")
            qualified_stocks.append({
                'code': s,
                'name': name,
                'score': score,
                'type': f"三日断板({matched_condition})"
            })
            log.info(f"✅ {s}({name}) 符合 Setup 3(三日断板)，命中条件: {matched_condition} | 得分: {score:.2f} | 换手: {turnover_ratio:.2f}% | OBI: {obi_ratio:.2f}")
        except Exception as e:
            log.info(f"  [排除-3] {s}({name}) 竞价匹配异常: {e}")
            continue

    # ==================== 3. 统一开仓买入 ====================
    log.info(f"竞价终筛结果：符合竞价过滤条件的个股共 {len(qualified_stocks)} 只")

    # 按得分从高到低排序，过滤靠后的弱势股（杂毛），集中仓位到龙头股
    qualified_stocks.sort(key=lambda x: x['score'], reverse=True)
    
    # 限制每日最大买入只数
    final_buy_list = qualified_stocks[:MAX_BUY_COUNT]
    if final_buy_list:
        log.info(f"【重排选优】排序前 {len(final_buy_list)} 只龙头股票：")
        for idx, item in enumerate(final_buy_list):
            log.info(f"  - {idx+1}. {item['code']}({item['name']}) | 得分: {item['score']:.2f} | 类型: {item['type']}")

    buy_count = 0
    g.bought_stocks = set()  # 重置今日已买记录
    if final_buy_list and context.portfolio.available_cash / context.portfolio.total_value > 0.3:
        value_per_stock = DTJiner / len(final_buy_list)
        for item in final_buy_list:
            s = item['code']
            price = current_data[s].last_price
            shares = int(value_per_stock / price / 100) * 100
            if shares >= 100:
                order_value(s, value_per_stock, MarketOrderStyle(current_data[s].day_open))
                buy_count += 1
                g.bought_stocks.add(s)
                g.information[s] = item['type']
                log.info(f"下单买入: {s}({g.name_cache.get(s,'未知')}) | 金额: {value_per_stock:.2f} | 开盘竞价: {price} | 得分: {item['score']:.2f} | 条件: {item['type']}")
    
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
            
            # 1. 涨停不卖
            if closeable != 0 and last_price >= high_limit - 0.01:
                log.info(f'涨停持有: {s}({g.name_cache[s]})')
                continue
            
            # 2. 未涨停且盈利，执行止盈
            if closeable != 0 and last_price < high_limit and last_price > avg_cost:
                get_record_sell(context, s, '未涨停止盈')
                order_target_value(s, 0)
                log.info(f'止盈卖出: {s}({g.name_cache[s]})')
            
            # 3. 价格破5日线，执行止损
            elif closeable != 0 and last_price < MA5 * (1 - MA5_STOP_LOSS_BUFFER):
                get_record_sell(context, s, '跌破5日线止损')
                order_target_value(s, 0)
                log.info(f'价格跌破5日线止损卖出: {s}({g.name_cache[s]})')
            
            # 4. 日跌幅过大止损
            elif closeable != 0:
                yst_close = yst_close_map.get(s)
                if yst_close and yst_close > 0:
                    drop_ratio = (yst_close - last_price) / yst_close
                    if drop_ratio >= DROP_PERCENT:
                        get_record_sell(context, s, '跌幅止损')
                        order_target_value(s, 0)
                        log.info(f'跌幅止损卖出: {s}({g.name_cache[s]})，跌幅 {-drop_ratio:.2%}')


def eod_stats(context):
    total_value = context.portfolio.total_value
    daily_pnl = 0
    
    # 更新净值高点
    g.peak_value = max(g.peak_value, total_value)
    
    # 计算每日盈亏与连续亏损
    if g.prev_day_value > 0:
        daily_pnl = (total_value / g.prev_day_value - 1)
        g.recent_pnls.append(daily_pnl)
        if len(g.recent_pnls) > 5:
            g.recent_pnls = g.recent_pnls[-5:]
        if daily_pnl < -0.005:  # 亏损超0.5%计入连亏
            g.consecutive_loss_days += 1
        else:
            g.consecutive_loss_days = 0
        
        # 连亏2天，明日冷静暂停
        if g.consecutive_loss_days >= 2:
            g.skip_buy = True
            log.info(f"[净值动量] 连亏{g.consecutive_loss_days}天，明日暂停买入")
            
    # ========== ML在线学习模块 ==========
    g.day_count += 1
    
    # 标注昨日特征
    if g.pending_features is not None and g.prev_day_value > 0:
        label = 1.0 if daily_pnl > 0 else 0.0
        g.ml_features.append(g.pending_features)
        g.ml_labels.append(label)
        if len(g.ml_features) > 120:
            g.ml_features = g.ml_features[-120:]
            g.ml_labels = g.ml_labels[-120:]
            
    # 采集今日特征供明日标注
    if g.day_count >= 3:
        try:
            today_f = compute_ml_features(context)
            if today_f is not None:
                g.pending_features = today_f
        except:
            g.pending_features = None
            
    # 每5天重训逻辑回归模型
    if len(g.ml_features) >= 60 and g.day_count % 5 == 0:
        try:
            train_ml_model()
        except Exception as e:
            log.info(f"[ML训练] 异常: {e}")
            
    g.prev_day_value = total_value
    
    ml_info = f"ML权重={'已训练' if g.ml_weights is not None else '未训练'} 样本={len(g.ml_features)}" if len(g.ml_features) > 0 else "ML=无数据"
    log.info(f"=== 盘后 === 总资产:{total_value:,.0f} | 日收益:{daily_pnl:.2%} | 持仓:{len(context.portfolio.positions)} | "
             f"连亏:{g.consecutive_loss_days}天 | 净值高点回撤:{(total_value/g.peak_value-1):.1%} | {ml_info}")

    # ========== 候选股自入选至今最高涨幅统计 ==========
    if g.tracked_candidates:
        tracked_codes = list(g.tracked_candidates.keys())
        today_str = context.current_dt.strftime('%Y-%m-%d')
        # 批量获取今日的最高价以更新最高值
        try:
            today_data = get_price(
                tracked_codes, start_date=today_str, end_date=today_str,
                frequency='daily', fields=['high'], panel=False
            )
            if not today_data.empty:
                for _, row in today_data.iterrows():
                    code = row['code']
                    high_p = row['high']
                    if not pd.isna(high_p) and high_p > 0:
                        if code in g.tracked_candidates:
                            cand = g.tracked_candidates[code]
                            if high_p > cand['max_price']:
                                cand['max_price'] = high_p
        except Exception as e:
            log.info(f"[最高价更新] 异常: {e}")

        # 计算涨幅并排行
        ranking_list = []
        for code, cand in g.tracked_candidates.items():
            base = cand['base_price']
            max_p = cand['max_price']
            gain = (max_p - base) / base * 100 if base > 0 else 0.0
            cand['max_pct'] = gain
            
            name = g.name_cache.get(code, '未知')
            if name == '未知':
                try:
                    name = get_security_info(code).display_name
                    g.name_cache[code] = name
                except:
                    pass
            
            ranking_list.append({
                'code': code,
                'name': name,
                'entry_date': cand['entry_date'],
                'setup_type': cand['setup_type'],
                'base_price': base,
                'max_price': max_p,
                'max_pct': gain
            })
            
        ranking_list.sort(key=lambda x: x['max_pct'], reverse=True)
        
        log.info(f"\n{'='*80}")
        log.info(f"【自候选至今最高涨幅排行榜】累计追踪候选股: {len(ranking_list)}只")
        log.info(f"{'─'*80}")
        log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'候选日期':<11} {'类型':<8} {'基准价':>8} {'最高价':>8} {'最大涨幅':>10}")
        log.info(f"{'─'*80}")
        for i, item in enumerate(ranking_list[:30]):
            log.info(f"{i+1:>4} {item['code']:<10} {item['name']:<8} "
                     f"{item['entry_date']:<11} {item['setup_type']:<8} "
                     f"{item['base_price']:>8.2f} {item['max_price']:>8.2f} "
                     f"{item['max_pct']:>9.2f}%")
        log.info(f"{'='*80}")

    # ========== 漏选股票分析：今日候选但未买入的股票当日表现 ==========
    analyze_missed_candidates(context, today_str)


# ========================================================================
# ██ 漏选分析模块 ██
# ========================================================================

def analyze_missed_candidates(context, today_str):
    """分析今日候选但未买入的股票当日表现"""
    # 找出今日新增的候选（entry_date == today_str）且未买入的
    missed_today = []
    for code, cand in g.tracked_candidates.items():
        if cand['entry_date'] == today_str and code not in g.bought_stocks:
            missed_today.append({
                'code': code,
                'setup_type': cand['setup_type'],
                'base_price': cand['base_price'],
            })

    if not missed_today:
        log.info("[漏选分析] 今日所有候选均已买入或无候选，无漏选")
        return

    missed_codes = [m['code'] for m in missed_today]
    missed_map = {m['code']: m for m in missed_today}

    # 获取今日行情（收盘价、开盘价、最高价）
    try:
        today_df = get_price(
            missed_codes, start_date=today_str, end_date=today_str,
            frequency='daily', fields=['close', 'open', 'high'], panel=False
        )
        if today_df.empty:
            return
    except:
        return

    results = []
    for _, row in today_df.iterrows():
        code = row['code']
        if code not in missed_map:
            continue
        m = missed_map[code]
        base = m['base_price']
        close_p = row['close']
        open_p = row['open']
        high_p = row['high']
        if pd.isna(close_p) or pd.isna(base) or base <= 0:
            continue

        close_pct = (close_p - base) / base * 100
        open_pct = (open_p - base) / base * 100 if not pd.isna(open_p) else 0.0
        high_pct = (high_p - base) / base * 100 if not pd.isna(high_p) else 0.0

        name = g.name_cache.get(code, '未知')
        if name == '未知':
            try:
                name = get_security_info(code).display_name
                g.name_cache[code] = name
            except:
                pass

        results.append({
            'code': code,
            'name': name,
            'setup_type': m['setup_type'],
            'open_pct': open_pct,
            'high_pct': high_pct,
            'close_pct': close_pct,
        })

    if not results:
        return

    results.sort(key=lambda x: x['close_pct'], reverse=True)

    n = len(results)
    n_positive = sum(1 for r in results if r['close_pct'] > 0)
    n_limit_up = sum(1 for r in results if r['close_pct'] >= 9.8)
    avg_pct = sum(r['close_pct'] for r in results) / n
    max_pct = max(r['close_pct'] for r in results)
    min_pct = min(r['close_pct'] for r in results)

    log.info(f"\n{'─'*80}")
    log.info(f"[漏选分析] 共{n}只候选未买入 | 上涨{n_positive}只({n_positive/n:.0%}) | "
             f"涨停{n_limit_up}只 | 均涨{avg_pct:.2f}% | 最高{max_pct:.2f}% | 最低{min_pct:.2f}%")
    log.info(f"{'─'*80}")
    log.info(f"{'排名':>4} {'代码':<10} {'名称':<8} {'类型':<8} {'开盘涨幅':>8} {'最高涨幅':>8} {'收盘涨幅':>8}")
    log.info(f"{'─'*80}")

    for i, r in enumerate(results[:20]):
        emoji = "+" if r['close_pct'] > 0 else ""
        log.info(f"{i+1:>4} {r['code']:<10} {r['name']:<8} {r['setup_type']:<8} "
                 f"{r['open_pct']:>+7.2f}% {r['high_pct']:>+7.2f}% {r['close_pct']:>+7.2f}%")

    limit_up_missed = [r for r in results if r['close_pct'] >= 9.8]
    if limit_up_missed:
        log.info(f"\n!! 有{len(limit_up_missed)}只漏选股票今日涨停:")
        for r in limit_up_missed:
            log.info(f"    {r['code']}({r['name']}) {r['setup_type']} 涨幅={r['close_pct']:+.2f}%")

    log.info(f"{'─'*80}\n")


# ========================================================================
# ██ 自选涨幅因子模块 ██
# ========================================================================

def calc_tracked_bonus(code):
    """计算候选股自入选至今最高涨幅的加分因子
    逻辑：如果该股票曾被选为候选且至今涨幅显著，说明选股逻辑得到市场验证，
    当它再次出现时给予加分，优先买入已被验证的强势股。
    加分阶梯：
        >= 40%  → +10分（涨幅过大，追高风险增加，降低加分）
        30~40%  → +15分
        20~30%  → +20分（甜蜜区间，加分最高）
        10~20%  → +10分
         5~10%  → +5分
          < 5%  → +0分
    """
    cand = g.tracked_candidates.get(code)
    if cand is None:
        return 0.0

    base = cand.get('base_price', 0)
    max_p = cand.get('max_price', 0)
    if base <= 0 or max_p <= 0:
        return 0.0

    gain_pct = (max_p - base) / base * 100

    if gain_pct >= 40:
        return 10.0
    elif gain_pct >= 30:
        return 15.0
    elif gain_pct >= 20:
        return 20.0
    elif gain_pct >= 10:
        return 10.0
    elif gain_pct >= 5:
        return 5.0
    else:
        return 0.0


# ========================================================================
# ██ 风控与机器学习辅助模块 ██
# ========================================================================

def compute_ml_features(context):
    """提取13个与市场、策略状态相关的特征"""
    hs300 = '000300.XSHG'
    zz1000 = '000852.XSHG'
    
    # 指数位置
    hs300_hist = attribute_history(hs300, 60, '1d', ['close'], df=False)
    hs300_c = hs300_hist['close'][-1]
    f1 = 1.0 if hs300_c > np.mean(hs300_hist['close'][-20:]) else 0.0
    f2 = 1.0 if hs300_c > np.mean(hs300_hist['close'][-60:]) else 0.0
    
    zz1000_hist = attribute_history(zz1000, 20, '1d', ['close'], df=False)
    f3 = 1.0 if zz1000_hist['close'][-1] > np.mean(zz1000_hist['close'][-20:]) else 0.0
    
    # 候选股票热度
    f4 = float(len(g.target_setup1) + len(g.target_setup2) + len(g.target_setup3))
    
    # 市场波动率
    rets = np.diff(hs300_hist['close'][-10:]) / hs300_hist['close'][-10:-1]
    f5 = float(np.std(rets) * np.sqrt(252))
    
    # 胜率与连亏
    if len(g.recent_pnls) >= 3:
        f6 = float(sum(1 for p in g.recent_pnls if p > 0) / len(g.recent_pnls))
    else:
        f6 = 0.5
    f7 = float(g.consecutive_loss_days)
    f8 = float((context.portfolio.total_value / g.peak_value - 1)) if g.peak_value > 0 else 0.0
    
    # 昨日策略交易状态
    f9 = float(g.yesterday_buy_count)
    f10 = float(context.portfolio.available_cash / max(context.portfolio.total_value, 1))
    
    # 短期动量
    f11 = float(hs300_c / hs300_hist['close'][-5] - 1) if len(hs300_hist['close']) >= 5 else 0.0
    f12 = float(zz1000_hist['close'][-1] / zz1000_hist['close'][-5] - 1) if len(zz1000_hist['close']) >= 5 else 0.0
    f13 = float(f11 - f12) # 大盘强弱 relative to 小盘
    
    return np.array([1.0, f1, f2, f3, f4/50.0, f5, f6, f7/5.0, f8, f9/5.0, f10, f11*10.0, f12*10.0, f13*10.0])


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


def train_ml_model():
    """IRLS 优化算法在线拟合逻辑回归，复制负样本以实现代价敏感"""
    X = np.array(g.ml_features)
    y = np.array(g.ml_labels)
    
    # 亏损日权重加倍
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
    log.info(f"[ML在线训练] 代价敏感回归重构：样本数={len(y)} 准确率={acc:.1%} 亏损召回={loss_recall:.1%}")


# ========================================================================
# ██ 辅助数据获取与核心过滤模块 ██
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


def filter_excessive_limit_up(stock_list, y_day):
    extreme_hl_df = get_hl_count_df(stock_list, y_day, 10)
    qualified_stocks = extreme_hl_df[extreme_hl_df['extreme_count'] < 3].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"因前10日有3+一字/T字涨停被排除: {len(excluded)}只")
    return qualified_stocks


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
