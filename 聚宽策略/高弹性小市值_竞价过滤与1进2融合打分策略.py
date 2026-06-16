# 克隆/复制该脚本到聚宽（JoinQuant）平台的回测/研究环境中运行
# 策略融合点：
# 1. 选股策略完全使用原始【首板1一进二】策略：
#    - 盘前筛选（09:10前）：主板股票池筛选 + 昨日涨幅>7% + 过滤前10日有3+一字/T字 + 过滤前5日波动>40% + 过滤前5日涨停>=4天 + 前100日最高价突破过滤。
#    - 竞价确认（09:26）：昨日成交额1~15亿限额 + 昨日涨幅强度检验 + 昨日放量突破 + 市值股价硬限制 + 集合竞价 A-F 规则深度量价比匹配。
# 2. 交易时机完全使用你的策略：
#    - 早盘一次性买入：09:30 集合竞价成交或开盘价等权买入通过筛选的标的（最多4只）。
#    - 尾盘清仓卖出：14:50 将满足持股周期的股票清仓（推荐 T+1 以免爆发题材大幅折损，可在全局配置中自由切换）。

from jqdata import *
import numpy as np
import pandas as pd
import math
import datetime
from datetime import datetime as dt

# 竞价规则条件 A - F
CONDITION_RULES = [
    ('A: 昨日成交额1~5亿 | 竞价涨幅7~9% | 竞昨比10~20%',  1.07, 1.09, 0.10, 0.20),
    ('B: 昨日成交额5~15亿 | 竞价涨幅7~9% | 竞昨比10~20%', 1.07, 1.09, 0.10, 0.20),
    ('C: 昨日成交额5~15亿 | 竞价涨幅4~7% | 竞昨比3~7%',   1.04, 1.07, 0.03, 0.07),
    ('D: 昨日成交额5~15亿 | 竞价涨幅4~7% | 竞昨比10~20%', 1.04, 1.07, 0.10, 0.20),
    ('E: 昨日成交额5~15亿 | 竞价涨幅0~4% | 竞昨比3~7%',   1.00, 1.04, 0.03, 0.07),
    ('F: 昨日成交额5~15亿 | 竞价涨幅0~4% | 竞昨比7~10%',  1.00, 1.04, 0.07, 0.10),
]

# ==================== 1. 初始化设置 ====================
def initialize(context):
    # 设置基准和基础交易配置
    set_benchmark('399303.XSHE') # 主打弹性中下市值，以深证综指为基准
    set_option('use_real_price', True)
    log.set_level('order', 'error')
    
    # 交易佣金与税费设置
    set_order_cost(OrderCost(
        open_tax=0, 
        close_tax=0.001, 
        open_commission=0.0003, 
        close_commission=0.0003, 
        close_today_commission=0, 
        min_commission=5
    ), type='stock')
    
    # 策略核心全局参数
    g.max_stocks = 4          # 最大持仓股票数
    g.pre_target_list = []    # 09:10 盘前选出的候选股
    g.final_buy_list = []     # 09:26 竞价确认后的买入目标股
    g.buy_date_dict = {}      # 记录买入日期用于持仓周期控制
    
    # ------------------ 策略持仓天数配置 ------------------
    # 建议设为 1 即 T+1 (今天买，明天下午卖) 避免首板一进二失效后大幅回撤。
    # 若需维持你的原策略周期，可修改为 2 (即 T+2，今天买，后天下午卖)。
    g.hold_days = 1           
    # -----------------------------------------------------
    
    # 注册每日定时任务
    # 1. 09:26 执行竞价规则校验与重排
    run_daily(my_auction_confirm, time='09:26')
    # 2. 09:30 早上开盘执行全仓等权买入
    run_daily(my_morning_trade, time='open')
    # 3. 14:50 下午尾盘执行清仓卖出
    run_daily(my_afternoon_trade, time='14:50')

# ==================== 2. 盘前选股阶段 (09:10 运行) ====================
def before_trading_start(context):
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"--- 正在执行 {yesterday} 的 1进2 盘前选股过滤 ---")
    
    # 1. 筛选基础可交易主板股票池 (排除 ST/次新/创业/科创/停牌)
    initial_list = prepare_stock_list(context)
    log.info(f"[盘前选股] 初始主板池数量: {len(initial_list)}只")
    
    # 2. 筛选昨日涨幅 > 7% 的首板/强板股票
    targets = get_stocks_with_high_increase(initial_list, yesterday)
    log.info(f"[盘前选股] 昨日涨幅>7%候选股: {len(targets)}只")
    
    # 3. 过滤筹码断层 (近10日一字板或T字涨停数 >= 3 被排除)
    targets = filter_excessive_limit_up(targets, yesterday)
    log.info(f"[盘前选股] 过滤10日内频繁一字/T字板后: {len(targets)}只")
    
    # 4. 过滤过度暴炒 (近5日振幅波动率 > 40% 被排除)
    targets = filter_excessive_increase(targets, yesterday)
    log.info(f"[盘前选股] 过滤近5日波动率>40%后: {len(targets)}只")
    
    # 5. 过滤高位板 (近5日涨停天数 >= 4天 被排除，只做首板进二板)
    targets = filter_excessive_limit_days(targets, yesterday)
    log.info(f"[盘前选股] 过滤近5日高位连续涨停后: {len(targets)}只")
    
    # 6. 过滤低位跟风股 (昨日收盘价 < 过去100日最高价的 90% 被排除，即必须是百日新高强势突破)
    g.pre_target_list = filter_below_n_high(targets, yesterday, days=100)
    log.info(f"[盘前选股] 过滤低于100日最高点90%后（百日高位突破）: {len(g.pre_target_list)}只")
    
    # 打印最终入围的候选股
    if g.pre_target_list:
        stock_names = []
        for s in g.pre_target_list:
            try:
                stock_names.append(f"{s}({get_security_info(s).display_name})")
            except:
                stock_names.append(f"{s}(未知)")
        log.info(f"[盘前选股最终结果] 共有 {len(g.pre_target_list)} 只股票符合条件:\n" + "\n".join(stock_names))
    else:
        log.info("[盘前选股最终结果] 今日无符合基本盘前选股条件的股票")

# ==================== 3. 竞价确认与买入执行 ====================
def my_auction_confirm(context):
    """09:26 获取集合竞价数据并执行 A-F 规则量价匹配"""
    g.final_buy_list = []
    if not g.pre_target_list:
        log.info("【竞价确认】今日无盘前候选，放弃竞价匹配")
        return
        
    qualified_stocks = []
    current_data = get_current_data()
    y_day = context.previous_date.strftime('%Y-%m-%d')
    t_day = context.current_dt.strftime("%Y-%m-%d")
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'
    
    # 获取行情缓存
    try:
        prev_df = get_price(
            g.pre_target_list, end_date=y_day, frequency='daily',
            fields=['close', 'volume', 'money'], count=1, panel=False,
            fill_paused=False, skip_paused=True
        )
        prev_map = {row['code']: row for _, row in prev_df.iterrows()}
    except Exception as e:
        log.warn(f"【竞价确认异常】批量获取昨日行情失败：{str(e)}")
        return
        
    # 获取市值基础数据
    try:
        val_df = get_fundamentals(
            query(valuation.code, valuation.market_cap, valuation.circulating_market_cap)
            .filter(valuation.code.in_(g.pre_target_list)),
            date=str(y_day)[:10]
        )
        val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}
    except Exception as e:
        log.warn(f"【竞价确认异常】批量获取市值数据失败：{str(e)}")
        val_map = {}
        
    # 设定基准收盘价字典
    hl_base = {s: current_data[s].high_limit / 1.1 for s in g.pre_target_list}
    
    for s in g.pre_target_list:
        try:
            prev = prev_map.get(s)
            if prev is None:
                continue
                
            open_price = current_data[s].day_open
            # 如果竞价无价格或处于停牌状态，剔除
            if open_price is None or open_price <= 3.0:
                continue
                
            # 昨日成交额硬性校验：必须介于 1 亿到 15 亿之间
            yesterday_money = prev['money']
            if yesterday_money < 1e8 or yesterday_money > 15e8:
                continue
                
            # 市值硬性约束：总市值>10亿，且流通市值<=520亿
            val = val_map.get(s)
            if val is None or val['market_cap'] < 10.0 or val['circulating_market_cap'] > 520.0:
                continue
                
            # 首板强度校验：昨日均价换算的收盘涨幅是否 >= 7%（确认首板成色）
            avg_chg = yesterday_money / prev['volume'] / prev['close'] * 1.1 - 1
            if avg_chg < 0.07:
                continue
                
            # 放量突破校验：昨日成交量必须大于过去 N 日最大成交量的 90%
            zyts = calculate_zyts(s, context)
            vol_data = attribute_history(s, zyts, '1d', fields=['volume'], skip_paused=True)
            if len(vol_data) < 2:
                continue
            if vol_data['volume'][-1] <= max(vol_data['volume'][:-1]) * 0.9:
                continue
                
            # 获取 9:15 到 9:26 的集合竞价数据
            auction = get_call_auction(s, start_date=start, end_date=end, fields=['time', 'volume', 'current'])
            if auction.empty:
                continue
                
            # 计算集合竞价高开涨幅(cur_ratio) 和 竞昨比(auction_ratio)
            cur_ratio = auction['current'][0] / hl_base[s]
            auction_ratio = auction['volume'][0] / vol_data['volume'][-1]
            
            is_1_5 = yesterday_money < 5e8
            is_5_15 = not is_1_5
            
            # 严格匹配竞价量比与涨幅区间 A-F 条件
            matched_condition = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES:
                if cond_name.startswith('A') and not is_1_5:
                    continue
                if not cond_name.startswith('A') and not is_5_15:
                    continue
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched_condition = cond_name
                    break
                    
            if matched_condition is None:
                continue
                
            # 符合所有筛选条件的股票入围最终选择池 (附带成交额数据以便多子集限制排序)
            qualified_stocks.append((s, yesterday_money))
            log.info(f"✅ {s}({get_security_info(s).display_name}) 通过竞价匹配，命中条件: {matched_condition}")
        except Exception as e:
            log.warn(f"【竞价确认出错】{s}: {str(e)}")
            
    # 按昨日成交额降序排序，成交额大的个股说明资金参与度更高。最多取 4 只作为买入目标。
    qualified_stocks = sorted(qualified_stocks, key=lambda x: x[1], reverse=True)
    g.final_buy_list = [item[0] for item in qualified_stocks][:g.max_stocks]
    log.info(f"【竞价确认最终买入目标】共计 {len(g.final_buy_list)} 只: {g.final_buy_list}")

def my_morning_trade(context):
    """早盘 09:30 买入执行逻辑"""
    buy_list = g.final_buy_list
    if not buy_list:
        log.info("【早盘买入跳过】今日集合竞价未命中任何合格标的，空仓观望")
        return
        
    # 计算可用买入资金 (预留 1% 滑点和佣金空间)
    available_cash = context.portfolio.available_cash * 0.99
    cash_per_stock = available_cash / len(buy_list)
    
    if cash_per_stock <= 0:
        log.info("【早盘买入跳过】当前账户可用余额不足，放弃交易")
        return
        
    for stock in buy_list:
        current_data = get_current_data()
        price_info = current_data[stock]
        if price_info.paused:
            continue
            
        # 排除竞价直接封死一字涨停或跌停的股票，确保属于可交易机会
        if price_info.last_price >= price_info.high_limit - 0.01:
            log.info(f"【早盘买入跳过】{stock} 开盘已涨停")
            continue
        if price_info.last_price <= price_info.low_limit + 0.01:
            log.info(f"【早盘买入跳过】{stock} 开盘已跌停")
            continue
            
        # 以开盘价格一次性市价委托下单
        order_value(stock, cash_per_stock, MarketOrderStyle(price_info.day_open))
        g.buy_date_dict[stock] = context.current_dt.date()
        log.info(f"【早盘买入下单】{stock} 买入资金 {cash_per_stock:.2f} 元")

# ==================== 4. 尾盘平仓卖出阶段 (14:50 运行) ====================
def my_afternoon_trade(context):
    """下午 14:50 仅作平仓处理，决不买入新股"""
    current_holdings = list(context.portfolio.positions.keys())
    today = context.current_dt.date()
    
    for stock in current_holdings:
        position = context.portfolio.positions[stock]
        if position.closeable_amount > 0:
            buy_date = g.buy_date_dict.get(stock)
            if buy_date is None:
                # 兼容性处理：如果没有找到买入记录，将其视为今日买入并跳过
                g.buy_date_dict[stock] = today
                continue
                
            # 获取这只股票的持有交易日天数
            trade_days = get_trade_days(start_date=buy_date, end_date=today)
            if not should_sell_after_hold_days(buy_date, today, trade_days, g.hold_days):
                log.info(f"【尾盘持有】{stock}，从买入日 {buy_date} 起持有未满 {g.hold_days} 个交易日，继续持有")
                continue
                
            # 执行清仓下单
            order(stock, -position.closeable_amount)
            g.buy_date_dict.pop(stock, None)
            log.info(f"【尾盘出货】{stock}，持有达标，卖出平仓共 {position.closeable_amount} 股")

# ==================== 5. 辅助与选股特征计算函数 ====================
def prepare_stock_list(context):
    """获取基础股票池，严格限制为沪深主板，排除ST/停牌/Delisting"""
    by_date = get_trade_days(end_date=context.previous_date, count=50)[0]
    all_s = get_all_securities(['stock'], date=by_date).index
    c_data = get_current_data()
    
    base_stocks = []
    for s in all_s:
        # 排除 创业板(3)、北交所(4/8/9)、科创板(68)
        if s[0] in ('3', '4', '8', '9') or s.startswith('68'):
            continue
        # 排除 ST, 停牌,Delisting
        if c_data[s].is_st or c_data[s].paused:
            continue
        if '退' in c_data[s].name or 'ST' in c_data[s].name:
            continue
        base_stocks.append(s)
    return base_stocks

def get_stocks_with_high_increase(initial_list, y_day):
    """获取昨日收盘涨幅超过 7% 的股票"""
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

def get_hl_count_df(hl_list, y_day, watch_days):
    """统计个股的历史涨停、一字板、T字板情况"""
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

def filter_excessive_limit_up(stock_list, y_day):
    """过滤前10日内有一字/T字涨停数大于等于 3 次的筹码断层股"""
    extreme_hl_df = get_hl_count_df(stock_list, y_day, 10)
    qualified_stocks = extreme_hl_df[extreme_hl_df['extreme_count'] < 3].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"[过滤筹码断层] 因前10日有3+一字/T字涨停被剔除: {len(excluded)}只")
    return qualified_stocks

def filter_excessive_increase(stock_list, y_day):
    """过滤近 5 日内振幅大于 40% 的过热股"""
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
        log.info(f"[过滤过度炒作] 因近5日最高振幅>40%被剔除: {excluded_n}只")
    return qualified

def filter_excessive_limit_days(stock_list, y_day):
    """过滤近 5 日内涨停天数 >= 4 的高位股"""
    limit_up_df = get_hl_count_df(stock_list, y_day, 5)
    qualified_stocks = limit_up_df[limit_up_df['count'] < 4].index.tolist()
    excluded = set(stock_list) - set(qualified_stocks)
    if excluded:
        log.info(f"[过滤高位连续涨停] 因近5日涨停天数>=4被剔除: {len(excluded)}只")
    return qualified_stocks

def filter_below_n_high(stock_list, y_day, days=100, min_ratio=0.9):
    """过滤低于 100 日最高位 90% 的低位跟风股（保证大突破）"""
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
    log.info(f"[过滤百日低位突破] 百日最高价过滤后保留: {len(qualified)}/{len(stock_list)}只")
    return qualified

def calculate_zyts(s, context):
    """动态计算历史周期参考天数"""
    high_prices = attribute_history(s, 101, '1d', fields=['high'], skip_paused=True)['high']
    prev_high = high_prices.iloc[-1]
    zyts_0 = next((i-1 for i, high in enumerate(high_prices[-3::-1], 2) if high >= prev_high), 100)
    return zyts_0 + 5

def _normalize_trade_date(value):
    if hasattr(value, 'date'):
        value = value.date()
    if isinstance(value, datetime.date):
        return value
    return dt.strptime(str(value)[:10], '%Y-%m-%d').date()

def should_sell_after_hold_days(buy_date, current_date, trade_days, hold_days=1):
    """是否满足卖出持股周期"""
    if buy_date is None or current_date is None:
        return False
    buy_day = _normalize_trade_date(buy_date)
    current_day = _normalize_trade_date(current_date)
    normalized_days = [_normalize_trade_date(day) for day in trade_days]
    if buy_day not in normalized_days or current_day not in normalized_days:
        return False
    return normalized_days.index(current_day) - normalized_days.index(buy_day) >= hold_days
