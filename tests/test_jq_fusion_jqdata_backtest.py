"""
首板断板融合策略 - JQData回测
使用聚宽官方数据源，输出与聚宽回测一模一样的详细日志。
数据范围: 2026-03-02 ~ 2026-03-06
"""

import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jqdatasdk import auth, get_price, get_call_auction, get_fundamentals, get_all_securities, get_trade_days
from jqdatasdk import valuation, query
import numpy as np

# ========================================================================
# 聚宽认证
# ========================================================================
auth('18813368263', 'Forymq_10')

# ========================================================================
# 参数
# ========================================================================
LIMIT_UP_RATIO = 0.998
MIN_YESTERDAY_CLOSE_RATIO = 0.95
MIN_CAP = 10
MAX_CAP = 1200
MIN_AMOUNT = 1e8
MAX_AMOUNT = 100e8
MIN_PRICE = 3.0
MIN_AVG_CHG = 0.035
OBI_MIN = 0.6
MAX_BUY_COUNT = 2
DROP_PERCENT = 0.05
MA5_STOP_LOSS_BUFFER = 0.02

CONDITION_RULES_SETUP1 = [
    ('E: 一字板/准一字 竞价>=9.8%', 1.098, 1.11, 0.005, 1.0),
    ('A: 竞价高开7~9%', 1.07, 1.098, 0.025, 0.25),
    ('B: 竞价高开4~7%', 1.04, 1.07, 0.02, 0.25),
    ('C: 竞价平开至小高开0~4%', 1.00, 1.04, 0.015, 0.15),
]

CONDITION_RULES_SETUP2 = [
    ('反包E: 竞价高开8~12%', 1.08, 1.12, 0.005, 0.25),
    ('反包A: 竞价高开4~8%', 1.04, 1.08, 0.005, 0.20),
    ('反包B: 竞价高开2~4%', 1.02, 1.04, 0.005, 0.15),
    ('反包C: 竞价平开至小高开0~2%', 1.00, 1.02, 0.005, 0.12),
    ('反包D: 竞价低开-3~0%', 0.97, 1.00, 0.005, 0.12),
    ('反包F: 深低开-5~-3%', 0.95, 0.97, 0.005, 0.10),
]

# 涨幅因子阶梯
TIER_40 = 10; TIER_30 = 15; TIER_20 = 20; TIER_10 = 10; TIER_5 = 5

# 打分权重
W_OPEN_GAP = 1.2; W_VOL_RATIO = 0.8; W_TURNOVER_GAP = 1.5; W_AMOUNT_SCORE = 2.0
BONUS_YIZI = 15; BONUS_HIGH_OPEN = 12; BONUS_DEEP_LOW = 5

# ========================================================================
# 追踪池
# ========================================================================
tracking = {}  # code -> {entry_date, base_price, max_price, setup_type, name}


def get_stock_name(code):
    try:
        info = get_all_securities(types=['stock']).loc[code]
        return info['display_name'] if 'display_name' in info.index else code
    except:
        return code


def scan_premarket(test_date, y_day):
    """盘前选股：形态分类 + 4道过滤"""
    trade_days = get_trade_days(end_date=y_day, count=9)
    if len(trade_days) < 9:
        return [], []

    start_date = trade_days[0].strftime('%Y-%m-%d')

    # 获取全A主板股票
    all_stocks = get_all_securities(types=['stock'], date=y_day)
    main_codes = [c for c in all_stocks.index
                  if c[0] not in ('3', '4', '8', '9') and not c.startswith('68')]

    # 批量获取日线
    price_df = get_price(
        main_codes, start_date=start_date, end_date=y_day,
        frequency='1d', fields=['close', 'high_limit', 'money', 'volume', 'high', 'low', 'open'],
        panel=False
    )
    if price_df.empty:
        return [], []

    price_df['is_limit'] = price_df['close'] >= price_df['high_limit'] * LIMIT_UP_RATIO
    stock_groups = price_df.groupby('code')

    d_1 = trade_days[8].strftime('%Y-%m-%d')
    d_2 = trade_days[7].strftime('%Y-%m-%d')
    d_3 = trade_days[6].strftime('%Y-%m-%d')
    d_4 = trade_days[5].strftime('%Y-%m-%d')
    recent_6 = [d.strftime('%Y-%m-%d') for d in trade_days[3:9]]

    raw_s1, raw_s2 = [], []
    yesterday_close_dict = {}

    for code, group in stock_groups:
        gs = group.sort_values('time')
        if len(gs) < 9:
            continue

        limit_map = dict(zip(gs['time'].dt.strftime('%Y-%m-%d'), gs['is_limit']))
        close_map = dict(zip(gs['time'].dt.strftime('%Y-%m-%d'), gs['close']))

        if not any(limit_map.get(d, False) for d in recent_6):
            continue

        yesterday_close_dict[code] = close_map.get(d_1, 0)

        # Setup 1: 昨日涨停 + 前日未涨停
        if limit_map.get(d_1, False) and not limit_map.get(d_2, False):
            raw_s1.append(code)
        # Setup 2: 前日涨停 + 大前日未涨停 + 昨日未涨停 + 跌幅<5%
        elif (limit_map.get(d_2, False) and not limit_map.get(d_3, False)
              and not limit_map.get(d_1, False)
              and close_map.get(d_1, 0) >= close_map.get(d_2, 0) * MIN_YESTERDAY_CLOSE_RATIO):
            raw_s2.append(code)

    print(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_s1)}只 | Setup 2 (断板反包): {len(raw_s2)}只")

    # 4道过滤
    def apply_filters(codes):
        if not codes:
            return []
        # 一字/T字过滤
        hl_df = get_price(codes, end_date=y_day, frequency='daily',
                          fields=['close', 'high_limit', 'low', 'open'], count=10,
                          panel=False, fill_paused=False, skip_paused=False)
        if hl_df.empty:
            return codes
        hl_df['is_limit'] = hl_df['close'] == hl_df['high_limit']
        hl_df['is_yizi'] = (hl_df['low'] == hl_df['high_limit']) & hl_df['is_limit']
        hl_df['is_tzi'] = (hl_df['open'] == hl_df['high_limit']) & hl_df['is_limit'] & (hl_df['low'] < hl_df['high_limit'])
        hl_df['is_extreme'] = hl_df['is_yizi'] | hl_df['is_tzi']
        counts = hl_df.groupby('code')[['is_limit', 'is_extreme']].sum().astype(int)
        counts.columns = ['count', 'extreme_count']
        qualified = counts[counts['extreme_count'] < 3].index.tolist()
        excluded = len(codes) - len(qualified)
        if excluded:
            print(f"  因前10日有3+一字/T字涨停被排除: {excluded}只")
        codes = qualified

        # 波动率过滤
        vol_df = get_price(codes, end_date=y_day, frequency='daily',
                           fields=['high', 'low'], count=5, panel=False,
                           fill_paused=False, skip_paused=True)
        if not vol_df.empty:
            grp = vol_df.groupby('code')
            chg = (grp['high'].max() - grp['low'].min()) / grp['low'].min()
            qualified = chg[chg <= 0.4].index.tolist()
            excluded = len(codes) - len(qualified)
            if excluded:
                print(f"  因近5日波动超过40%被排除: {excluded}只")
            codes = qualified

        # 涨停天数过滤
        if codes:
            ld_df = get_price(codes, end_date=y_day, frequency='daily',
                              fields=['close', 'high_limit'], count=5, panel=False,
                              fill_paused=False, skip_paused=False)
            if not ld_df.empty:
                ld_df['is_limit'] = ld_df['close'] == ld_df['high_limit']
                ld_counts = ld_df.groupby('code')['is_limit'].sum()
                qualified = ld_counts[ld_counts < 4].index.tolist()
                codes = qualified

        # 百日高位过滤
        if codes:
            hp_df = get_price(codes, end_date=y_day, frequency='daily',
                              fields=['high', 'close'], count=101, panel=False,
                              fill_paused=False, skip_paused=True, fq='pre')
            if not hp_df.empty:
                qualified = []
                for code in codes:
                    sub = hp_df[hp_df['code'] == code]
                    if len(sub) < 101:
                        continue
                    sub = sub.tail(101)
                    max_high = sub['high'].iloc[:-1].max()
                    yst_close = sub['close'].iloc[-1]
                    if yst_close >= max_high * 0.9:
                        qualified.append(code)
                print(f"  前100日最高价过滤: 保留{len(qualified)}/{len(codes)}只")
                codes = qualified

        return codes

    s1_filtered = apply_filters(raw_s1)
    s2_filtered = apply_filters(raw_s2)

    print(f"[过滤后] Setup1: {len(s1_filtered)}只 | Setup2: {len(s2_filtered)}只")
    return s1_filtered, s2_filtered, yesterday_close_dict


def match_auction(test_date, s1_codes, s2_codes, yst_close_dict):
    """竞价匹配"""
    all_codes = s1_codes + s2_codes
    if not all_codes:
        return []

    t_day = test_date
    start = t_day + ' 09:15:00'
    end = t_day + ' 09:26:00'

    # 获取昨日数据
    prev_df = get_price(all_codes, end_date=y_day, frequency='daily',
                        fields=['close', 'volume', 'money'], count=1, panel=False,
                        fill_paused=False, skip_paused=True)
    prev_map = {row['code']: row for _, row in prev_df.iterrows()}

    # 获取市值
    val_df = get_fundamentals(
        query(valuation.code, valuation.market_cap, valuation.circulating_market_cap, valuation.turnover_ratio)
        .filter(valuation.code.in_(all_codes)),
        date=y_day
    )
    val_map = {row['code']: row for _, row in val_df.iterrows()} if not val_df.empty else {}

    # 获取涨停价
    current_data_codes = all_codes
    hl_base = {}
    for code in all_codes:
        try:
            # 用昨日收盘价计算涨停价
            yst_close = yst_close_dict.get(code, 0)
            if yst_close > 0:
                ratio = 0.20 if code.startswith(('300', '301', '688')) else 0.10
                hl_base[code] = yst_close * (1 + ratio)
        except:
            pass

    qualified = []

    for code in all_codes:
        name = get_stock_name(code)
        is_s1 = code in s1_codes
        setup_type = '1进2' if is_s1 else '断板反包'

        try:
            prev = prev_map.get(code)
            if prev is None:
                continue
            yst_close = prev['close']
            yst_volume = prev['volume']
            money = prev['money']

            # 基础过滤
            if money < MIN_AMOUNT or money > MAX_AMOUNT:
                continue
            val = val_map.get(code)
            if val is None or val['market_cap'] < MIN_CAP or val['circulating_market_cap'] > MAX_CAP:
                continue

            # VWAP硬度（仅Setup1）
            if is_s1:
                avg_chg = money / yst_volume / yst_close * 1.1 - 1 if yst_volume > 0 and yst_close > 0 else 0
                if avg_chg < MIN_AVG_CHG:
                    print(f"  [排除-1] {code}({name}) 昨日均价涨幅 {avg_chg:.2%} < {MIN_AVG_CHG:.0%}")
                    continue

            # 获取竞价数据（含五档盘口）
            auction = get_call_auction(code, start_date=start, end_date=end,
                                        fields=['time', 'volume', 'current',
                                                'a1_p', 'a2_p', 'a3_p', 'a4_p', 'a5_p',
                                                'a1_v', 'a2_v', 'a3_v', 'a4_v', 'a5_v',
                                                'b1_p', 'b2_p', 'b3_p', 'b4_p', 'b5_p',
                                                'b1_v', 'b2_v', 'b3_v', 'b4_v', 'b5_v'])
            if auction.empty:
                continue

            auction_price = auction['current'].iloc[0]
            auction_volume = auction['volume'].iloc[0]

            # 开盘价检查
            if auction_price <= MIN_PRICE:
                print(f"  [排除] {code}({name}) 开盘价 {auction_price:.2f} <= {MIN_PRICE}")
                continue

            # 计算竞价涨幅（与聚宽一致：auction_price / yesterday_close）
            cur_ratio = auction_price / yst_close if yst_close > 0 else 0

            # 计算竞昨比
            auction_ratio = auction_volume / yst_volume if yst_volume > 0 else 0

            # 计算OBI（五档盘口买卖力量比）
            buymoney = 0.0
            sellmoney = 0.0
            for i in range(1, 6):
                bp = auction[f'b{i}_p'].iloc[0] if f'b{i}_p' in auction.columns else 0
                bv = auction[f'b{i}_v'].iloc[0] if f'b{i}_v' in auction.columns else 0
                ap = auction[f'a{i}_p'].iloc[0] if f'a{i}_p' in auction.columns else 0
                av = auction[f'a{i}_v'].iloc[0] if f'a{i}_v' in auction.columns else 0
                if not np.isnan(bp) and not np.isnan(bv):
                    buymoney += bp * bv
                if not np.isnan(ap) and not np.isnan(av):
                    sellmoney += ap * av
            obi_ratio = buymoney / sellmoney if sellmoney > 0 else (5.0 if buymoney > 0 else 1.0)

            # OBI过滤
            if obi_ratio < OBI_MIN:
                print(f"  [排除] {code}({name}) OBI={obi_ratio:.2f} < {OBI_MIN}")
                continue

            # 规则匹配
            rules = CONDITION_RULES_SETUP1 if is_s1 else CONDITION_RULES_SETUP2
            matched = None
            for cond_name, open_lo, open_hi, auc_lo, auc_hi in rules:
                if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
                    matched = cond_name
                    break

            if matched is None:
                print(f"  [排除] {code}({name}) 竞价未匹配: 涨幅={cur_ratio*100:+.2f}% 竞昨比={auction_ratio*100:.2f}%")
                continue

            # 打分
            turnover = val['turnover_ratio'] if val is not None and not np.isnan(val.get('turnover_ratio', np.nan)) else 0
            amount_score = min(money / 1e8, 10) / 10
            wts_factor = turnover * (1 + cur_ratio)
            score = cur_ratio * 100 * W_OPEN_GAP + auction_ratio * 100 * W_VOL_RATIO + wts_factor * W_TURNOVER_GAP + amount_score * W_AMOUNT_SCORE

            # 加分
            if is_s1 and cur_ratio >= 0.098:
                score += BONUS_YIZI
            if not is_s1 and cur_ratio >= 0.08:
                score += BONUS_HIGH_OPEN
            if not is_s1 and cur_ratio < -0.03:
                score += BONUS_DEEP_LOW

            # 涨幅因子
            tb = tracking.get(code, {})
            gain = (tb.get('max_price', 0) - tb.get('base_price', 1)) / tb.get('base_price', 1) * 100 if tb.get('base_price', 0) > 0 else 0
            bonus = 0
            if gain >= 40: bonus = TIER_40
            elif gain >= 30: bonus = TIER_30
            elif gain >= 20: bonus = TIER_20
            elif gain >= 10: bonus = TIER_10
            elif gain >= 5: bonus = TIER_5
            score += bonus

            if bonus > 0:
                print(f"  [涨幅因子] {code}({name}) 自选至今涨幅加分: +{bonus}")

            qualified.append({
                'code': code, 'name': name, 'setup_type': setup_type,
                'condition': matched, 'score': round(score, 2),
                'open_gap': cur_ratio * 100, 'vol_ratio': auction_ratio * 100,
                'turnover': turnover, 'obi': obi_ratio, 'tracked_bonus': bonus,
            })
            print(f"[OK] {code}({name}) 符合 {setup_type}，命中条件: {matched} | 得分: {score:.2f} | OBI: {obi_ratio:.2f}")

        except Exception as e:
            continue

    qualified.sort(key=lambda x: x['score'], reverse=True)
    return qualified


# ========================================================================
# 主流程
# ========================================================================
print("=" * 80)
print("首板断板融合策略 - JQData回测")
print("数据范围: 2026-03-02 ~ 2026-03-06")
print("=" * 80)

trade_days = get_trade_days(start_date='2026-03-01', end_date='2026-03-08')
test_dates = [d.strftime('%Y-%m-%d') for d in trade_days]

for test_date in test_dates:
    y_day = get_trade_days(end_date=test_date, count=2)[0].strftime('%Y-%m-%d')

    print(f"\n{'='*80}")
    print(f"【盘前选股】昨日: {y_day}")
    print(f"{'='*80}")

    # 盘前扫描
    s1, s2, yst_close_dict = scan_premarket(test_date, y_day)

    # 显示选股池
    if s1:
        names = [f"{c}({get_stock_name(c)})" for c in s1]
        print(f"今日选股池 (Setup 1 1进2-{len(s1)}只): {', '.join(names)}")
    if s2:
        names = [f"{c}({get_stock_name(c)})" for c in s2]
        print(f"今日选股池 (Setup 2 断板反包-{len(s2)}只): {', '.join(names)}")

    # 注册追踪池
    for code in s1 + s2:
        if code not in tracking:
            base = yst_close_dict.get(code, 0)
            if base <= 0:
                try:
                    base = get_price(code, end_date=y_day, frequency='daily', fields=['close'], count=1).iloc[0]['close']
                except:
                    base = 1.0
            tracking[code] = {
                'entry_date': test_date, 'base_price': base, 'max_price': base,
                'setup_type': '1进2' if code in s1 else '断板反包',
                'name': get_stock_name(code),
            }

    # 竞价匹配
    print(f"\n{'─'*80}")
    print(f"【竞价开始】共有 {len(s1)}只 Setup 1 候选，{len(s2)}只 Setup 2 候选")
    print(f"{'─'*80}")

    results = match_auction(test_date, s1, s2, yst_close_dict)

    # 重排选优
    final = results[:MAX_BUY_COUNT]
    print(f"\n竞价终筛结果：符合竞价过滤条件的个股共 {len(results)} 只")
    if final:
        print(f"【重排选优】排序前 {len(final)} 只龙头股票：")
        for i, r in enumerate(final):
            print(f"  -{i+1}. {r['code']}({r['name']}) | 得分: {r['score']:.2f} | 类型: {r['setup_type']}({r['condition']})")
        for r in final:
            print(f"下单买入: {r['code']}({r['name']}) | 得分: {r['score']:.2f} | 条件: {r['setup_type']}({r['condition']})")

    # 更新追踪池最高价
    today_data = get_price(list(tracking.keys()), start_date=test_date, end_date=test_date,
                           frequency='daily', fields=['high'], panel=False)
    if not today_data.empty:
        for _, row in today_data.iterrows():
            code = row['code']
            if code in tracking and not np.isnan(row['high']) and row['high'] > tracking[code]['max_price']:
                tracking[code]['max_price'] = row['high']

    # 排行榜
    board = sorted(tracking.values(), key=lambda x: (x['max_price'] - x['base_price']) / x['base_price'] * 100 if x['base_price'] > 0 else 0, reverse=True)
    print(f"\n{'='*80}")
    print(f"【自候选至今最高涨幅排行榜】累计追踪候选股: {len(board)}只")
    print(f"{'─'*80}")
    print(f"{'排名':>4} {'代码':<12} {'名称':<8} {'候选日期':<11} {'类型':<8} {'基准价':>8} {'最高价':>8} {'最大涨幅':>10}")
    print(f"{'─'*80}")
    for i, t in enumerate(board[:30]):
        pct = (t['max_price'] - t['base_price']) / t['base_price'] * 100 if t['base_price'] > 0 else 0
        code = [k for k, v in tracking.items() if v is t][0]
        print(f"{i+1:>4} {code:<12} {t['name']:<8} {t['entry_date']:<11} {t['setup_type']:<8} "
              f"{t['base_price']:>8.2f} {t['max_price']:>8.2f} {pct:>9.2f}%")
    print(f"{'='*80}")

    # 漏选分析
    bought_codes = [r['code'] for r in final]
    missed_codes = [c for c in s1 + s2 if c not in bought_codes]
    if missed_codes:
        missed_data = get_price(missed_codes, start_date=test_date, end_date=test_date,
                                frequency='daily', fields=['close', 'open', 'high'], panel=False)
        missed_results = []
        if not missed_data.empty:
            for _, row in missed_data.iterrows():
                code = row['code']
                base = yst_close_dict.get(code, 0)
                if base <= 0:
                    continue
                is_s1 = code in s1
                missed_results.append({
                    'code': code, 'name': get_stock_name(code),
                    'setup_type': '1进2' if is_s1 else '断板反包',
                    'open_pct': (row['open'] - base) / base * 100,
                    'high_pct': (row['high'] - base) / base * 100,
                    'close_pct': (row['close'] - base) / base * 100,
                    'is_limit': row['close'] >= row['high'] * 0.998 if row['high'] > 0 else False,
                })
        missed_results.sort(key=lambda x: x['close_pct'], reverse=True)

        if missed_results:
            n = len(missed_results)
            n_up = sum(1 for r in missed_results if r['close_pct'] > 0)
            n_lu = sum(1 for r in missed_results if r['is_limit'])
            avg = sum(r['close_pct'] for r in missed_results) / n
            mx = max(r['close_pct'] for r in missed_results)
            mn = min(r['close_pct'] for r in missed_results)
            print(f"\n{'─'*80}")
            print(f"[漏选分析] 共{n}只候选未买入 | 上涨{n_up}只({n_up/n:.0%}) | 涨停{n_lu}只 | 均涨{avg:.2f}% | 最高{mx:.2f}% | 最低{mn:.2f}%")
            print(f"{'─'*80}")
            print(f"{'排名':>4} {'代码':<12} {'名称':<8} {'类型':<8} {'开盘%':>8} {'最高%':>8} {'收盘%':>8}")
            print(f"{'─'*80}")
            for i, r in enumerate(missed_results[:20]):
                print(f"{i+1:>4} {r['code']:<12} {r['name']:<8} {r['setup_type']:<8} "
                      f"{r['open_pct']:>+7.2f}% {r['high_pct']:>+7.2f}% {r['close_pct']:>+7.2f}%")
            lu = [r for r in missed_results if r['is_limit']]
            if lu:
                print(f"\n[!!] 有{len(lu)}只漏选股票今日涨停:")
                for r in lu:
                    print(f"    {r['code']}({r['name']}) {r['setup_type']} 涨幅={r['close_pct']:+.2f}%")
            print(f"{'─'*80}")

print("\n\n回测完成！")
