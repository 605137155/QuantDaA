# 克隆/复制该脚本到聚宽（JoinQuant）平台运行
# 策略核心：大A首板与二板突破“扫板”策略 (分钟级回测)
# 交易逻辑：盘中每分钟扫描核心人气监控池，当个股拉升至临近涨停（1.5%以内，含已涨停排队）且未封死时，执行扫板买入。次日10:00自动清仓以回转资金。

from jqdata import *
import numpy as np
import pandas as pd
import datetime
import math

# ==================== 1. 初始化设置 ====================
def initialize(context):
    set_benchmark('000300.XSHG') # 基准
    set_option('use_real_price', True) # 真实价格交易
    log.set_level('order', 'error') # 隐藏交易日志
    
    # 设置佣金与印花税 (打板高频交易印花税十分关键)
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
    g.max_stocks = 5            # 最大持仓股票数
    g.buy_dates = {}            # 记录购买日期，用于次日清仓
    g.watch_list = []           # 盘中每日核心扫描池 (最活跃的150只热门股)
    
    # 每天 10:00 准时卖出昨日持仓 (经典打板次日冲高/溢价变现出局逻辑)
    run_daily(sell_yesterday_holdings, time='10:00')

# ==================== 2. 每日交易前筛选（缩减监控池，防止盘中分钟扫描超时） ====================
def before_trading_start(context):
    yesterday = context.previous_date.strftime('%Y-%m-%d')
    log.info(f"=== 正在生成 {yesterday} 的盘中打板监控池 ===")
    
    # 1. 获取基础股票池（全A股，排除ST、退市、上市未满90天的新股）
    all_stocks = get_all_securities(['stock'], date=yesterday)
    
    # 过滤ST、次新等
    cutoff_date = context.previous_date - datetime.timedelta(days=90)
    df_filtered = all_stocks[
        (~all_stocks['display_name'].str.startswith('ST')) &
        (~all_stocks['display_name'].str.startswith('*ST')) &
        (~all_stocks['display_name'].str.startswith('退')) &
        (all_stocks['start_date'] < cutoff_date)
    ]
    basic_pool = df_filtered.index.tolist()
    
    # 2. 市值筛选（流通市值在 30亿 - 450亿 之间，该区间的个股更容易被游资接力拉升）
    q = query(
        valuation.code,
        valuation.circulating_market_cap
    ).filter(
        valuation.code.in_(basic_pool),
        valuation.circulating_market_cap >= 30.0,
        valuation.circulating_market_cap <= 450.0
    )
    df_cap = get_fundamentals(q, date=yesterday)
    cap_filtered = df_cap['code'].tolist()
    
    if not cap_filtered:
        g.watch_list = []
        return
        
    # 3. 排除昨日已经涨停封死的个股，我们只做“首板突破”或者“昨日涨停未封死（炸板）的弱转强二板”
    # 一键批量拉取2日收盘价，大幅优化运行速度，防止 nested-loop 调用超时
    h_2d_close = history(2, '1d', 'close', cap_filtered, df=False)
    
    valid_candidates = []
    for code in cap_filtered:
        if code not in h_2d_close or len(h_2d_close[code]) < 2:
            continue
        
        prev_prev_close = h_2d_close[code][0]
        yesterday_close = h_2d_close[code][1]
        
        if prev_prev_close <= 0 or yesterday_close <= 0:
            continue
            
        pct = 1.20 if code.startswith(('300', '301', '688')) else 1.10
        limit_up_yesterday = round(prev_prev_close * pct, 2)
        
        # 如果昨日收盘价已经大于等于昨日涨停价，视为连板，排除之
        if yesterday_close >= limit_up_yesterday - 0.015:
            continue
        
        valid_candidates.append(code)
        
    # 4. 筛选昨日成交额前 150 名的极度活跃个股作为盘中扫描监控池
    # 这是最关键的性能优化：如果每分钟扫描几千只股票会造成聚宽超时报错！
    if not valid_candidates:
        g.watch_list = []
        return
        
    h_amount = history(1, '1d', 'money', valid_candidates, df=False)
    amount_dict = {code: float(h_amount[code][0]) for code in valid_candidates if len(h_amount[code]) > 0}
    sorted_by_amount = sorted(amount_dict.items(), key=lambda x: x[1], reverse=True)
    
    g.watch_list = [item[0] for item in sorted_by_amount[:150]]
    log.info(f"已确定今日打板监控池股票数量：{len(g.watch_list)} 只热门标的。")

# ==================== 3. 盘中每分钟扫描（扫板核心执行） ====================
def handle_data(context, data):
    current_time = context.current_dt.strftime('%H:%M')
    
    # 9:30-9:31 竞价阶段和 14:57 尾盘集合竞价不执行扫板
    if current_time <= '09:31' or current_time >= '14:56':
        return
        
    # 检查持仓是否已满
    current_holdings = list(context.portfolio.positions.keys())
    if len(current_holdings) >= g.max_stocks:
        return
        
    # 计算每个持仓可分配的资金 (总资产的 20%)
    cash_per_stock = context.portfolio.total_value / g.max_stocks
    
    # 获取实时快照数据
    current_data = get_current_data()
    available_cash = context.portfolio.available_cash
    
    # 如果可用资金连买一只都不够，则直接返回
    if available_cash < cash_per_stock * 0.9: # 预留点滑点空间
        return
        
    for stock in g.watch_list:
        # 如果已经持有了该股，跳过
        if stock in current_holdings:
            continue
            
        # 盘中获取分钟价必须从 data 字典中获取 (data[stock].close)
        # 聚宽的 get_current_data() 返回的 CurrentData 对象没有 last_price 属性，获取会得到 None/NaN 从而被过滤掉
        if stock not in data:
            continue
            
        price_info = current_data[stock]
        if price_info.paused:
            continue
            
        last_price = data[stock].close
        limit_up_price = price_info.high_limit
        
        # 安全性过滤，防止 NaN 或是未取到数据
        if last_price is None or limit_up_price is None or math.isnan(last_price) or math.isnan(limit_up_price):
            continue
        if last_price <= 0 or limit_up_price <= 0:
            continue
            
        # 【扫板核心临界点判定】：
        # 股价处于涨停价的 1.5% 以内（含已达到涨停价 0.0% 挂单排队扫板）
        pct_to_limit = (limit_up_price - last_price) / limit_up_price
        
        if 0.0 <= pct_to_limit <= 0.015:
            # 满足扫板条件，以涨停价下单（保证排队队列优先成交，即“扫板/排板”）
            order_value(stock, cash_per_stock)
            g.buy_dates[stock] = context.current_dt.date()
            log.info(f"【扫板买入】触发时间: {current_time}，个股: {stock}，当前价: {last_price}，涨停价: {limit_up_price}，成交额: {cash_per_stock:.2f}")
            
            # 更新已持仓列表，防止本分钟内重复下单
            current_holdings.append(stock)
            # 重新计算可用资金，防止超买
            available_cash -= cash_per_stock
            if available_cash < cash_per_stock * 0.9 or len(current_holdings) >= g.max_stocks:
                break

# ==================== 4. 交易卖出出局（次日清仓） ====================
def sell_yesterday_holdings(context):
    """【次日清仓逻辑】
    首板/接力板一般在次日冲高释放溢价，若次日10:00仍未封死涨停，选择一键清仓落袋为安/及时止损
    """
    current_date = context.current_dt.date()
    current_holdings = list(context.portfolio.positions.keys())
    current_data = get_current_data()
    
    for stock in current_holdings:
        # 判断是否为昨天或更早买入的持仓 (持仓天数 >= 1)
        if stock in g.buy_dates and g.buy_dates[stock] != current_date:
            price_info = current_data[stock]
            
            # 定时任务没有 data 参数，需要通过 attribute_history 获取当前最新的1分钟价格
            h = attribute_history(stock, 1, '1m', 'close', df=False)
            last_price = h['close'][0] if (h and 'close' in h and len(h['close']) > 0) else 0
            limit_up_price = price_info.high_limit
            
            # 风控优化：如果10:00该股正好处于封死涨停的状态，可以多拿一会儿（锁仓），否则一律清仓出局
            if last_price > 0 and limit_up_price > 0 and last_price >= limit_up_price - 0.01:
                log.info(f"【打板持仓】{stock} 10:00 封死涨停，选择锁仓暂不卖出。")
                continue
                
            order_target(stock, 0)
            log.info(f"【打板清仓】{stock} 次日 10:00 未封板，执行清仓出局。")
            if stock in g.buy_dates:
                del g.buy_dates[stock]
