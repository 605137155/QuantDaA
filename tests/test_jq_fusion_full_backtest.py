"""
首板断板融合策略 - 本地完整模拟回测
输出与聚宽一模一样的详细日志。
"""

import sys, os, logging
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from src.data_providers.sina_tencent_provider import SinaTencentMarketProvider
from src.repositories.jq_fusion_repo import JQFusionRepo
from src.strategies.jq_fusion_strategy import JQFusionStrategy, FusionParams

logging.basicConfig(level=logging.WARNING)

TEST_DATES = ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05']

def jq(code): return f"{code}.XSHG" if code.startswith(('6','9','688')) else f"{code}.XSHE"
def local(code): return code.split('.')[0]


def classify_stock(bars, code):
    """形态分类：返回 'setup1' / 'setup2' / None"""
    if len(bars) < 4:
        return None
    ratio = 0.20 if code.startswith(('300','301','688')) else 0.10
    flags = [False]
    for i in range(1, len(bars)):
        prev_c = bars[i-1].close
        flags.append(bars[i].close >= prev_c * (1 + ratio) * 0.998)
    d1, d2, d3 = flags[-1], flags[-2], flags[-3]
    c1, c2 = bars[-1].close, bars[-2].close
    if d1 and not d2:
        return 'setup1'
    if d2 and not d3 and not d1 and c2 > 0 and c1 >= c2 * 0.95:
        return 'setup2'
    return None


def apply_filters(code, bars, limit_bars):
    """4道过滤"""
    # 1. 10日内一字/T字涨停>=3
    recent10 = limit_bars[-10:]
    extreme = sum(1 for b in recent10 if b['is_limit'] and (b['low']==b['hl'] or b['open']==b['hl']))
    if extreme >= 3: return False, "10日内一字/T字>=3"
    # 2. 5日波动>40%
    r5 = limit_bars[-5:]
    h5 = [b['high'] for b in r5]; l5 = [b['low'] for b in r5]
    if max(h5)>0 and min(l5)>0 and (max(h5)-min(l5))/min(l5) > 0.4:
        return False, "5日波动>40%"
    # 3. 5日涨停>=4
    if sum(1 for b in r5 if b['is_limit']) >= 4: return False, "5日涨停>=4"
    # 4. 百日高位
    all_h = [b['high'] for b in limit_bars]; all_c = [b['close'] for b in limit_bars]
    if len(all_h)>=2 and max(all_h[:-1])>0 and all_c[-1] < max(all_h[:-1])*0.9:
        return False, "低于100日高点90%"
    return True, ""


def run_backtest():
    provider = SinaTencentMarketProvider()
    repo = JQFusionRepo(db_path="data/jq_fusion_backtest.db")
    params = FusionParams()

    print("正在获取全A股票列表...")
    all_stocks = provider.get_universe()
    main_stocks = [s for s in all_stocks if not s.code.startswith(('3','4','8','9','68'))]
    print(f"主板股票池: {len(main_stocks)}只")

    print("正在批量加载日线数据...")
    bars_cache = {}
    for i, stock in enumerate(main_stocks):
        try:
            bars = provider.get_daily_bars(stock.code, limit=15)
            if bars and len(bars) >= 4:
                bars_cache[stock.code] = {'bars': bars, 'name': stock.name}
        except: pass
        if (i+1) % 500 == 0: print(f"  {i+1}/{len(main_stocks)}...")
    print(f"完成: {len(bars_cache)}只\n")

    # 追踪池
    tracking = {}  # code -> {entry_date, base_price, max_price, setup_type}

    for test_date in TEST_DATES:
        print(f"\n{'='*80}")
        print(f"【盘前选股】昨日: {test_date}")
        print(f"{'='*80}")

        # 形态分类
        raw_s1, raw_s2 = [], []
        for code, info in bars_cache.items():
            bars = info['bars']
            bar_dates = [str(b.ts)[:10] for b in bars]
            idx = None
            for j, d in enumerate(bar_dates):
                if d == test_date: idx = j; break
            if idx is None or idx < 3: continue

            bars_before = bars[:idx]
            result = classify_stock(bars_before, code)
            if result == 'setup1': raw_s1.append((code, info, bars_before))
            elif result == 'setup2': raw_s2.append((code, info, bars_before))

        print(f"[选股] 形态初筛完成. Setup 1 (1进2): {len(raw_s1)}只 | Setup 2 (断板反包): {len(raw_s2)}只")

        # 过滤
        s1_filtered, s2_filtered = [], []
        def make_limit_bars(bl, code):
            ratio = 0.20 if code.startswith(('300','301','688')) else 0.10
            result = []
            for i, b in enumerate(bl):
                prev_c = bl[i-1].close if i > 0 else 0
                hl = prev_c * (1 + ratio)
                result.append({'is_limit': b.close >= hl * 0.998, 'high': b.high, 'low': b.low, 'open': b.open, 'hl': hl, 'close': b.close})
            return result

        for code, info, bl in raw_s1:
            ok, reason = apply_filters(code, bl, make_limit_bars(bl, code))
            if ok: s1_filtered.append((code, info, bl))
        for code, info, bl in raw_s2:
            ok, reason = apply_filters(code, bl, make_limit_bars(bl, code))
            if ok: s2_filtered.append((code, info, bl))

        print(f"[过滤后] Setup1: {len(s1_filtered)}只 | Setup2: {len(s2_filtered)}只")

        if s1_filtered:
            print(f"今日选股池 (Setup 1 1进2-{len(s1_filtered)}只): " +
                  ", ".join(f"{jq(c)}({i['name']})" for c,i,_ in s1_filtered))
        if s2_filtered:
            print(f"今日选股池 (Setup 2 断板反包-{len(s2_filtered)}只): " +
                  ", ".join(f"{jq(c)}({i['name']})" for c,i,_ in s2_filtered))

        # 注册追踪池
        for code, info, bl in s1_filtered + s2_filtered:
            if code not in tracking:
                base = bl[-1].close
                tracking[code] = {'entry_date': test_date, 'base_price': base, 'max_price': base, 'setup_type': '1进2' if (code,info,bl) in s1_filtered else '断板反包', 'name': info['name']}

        # 竞价匹配（用开盘价近似）
        print(f"\n{'─'*80}")
        print(f"【竞价开始】共有 {len(s1_filtered)}只 Setup 1 候选，{len(s2_filtered)}只 Setup 2 候选")
        print(f"{'─'*80}")

        qualified = []
        for code, info, bl in s1_filtered + s2_filtered:
            bars = info['bars']
            bar_dates = [str(b.ts)[:10] for b in bars]
            idx = bar_dates.index(test_date)
            today_bar = bars[idx]
            yst_close = bl[-1].close
            if yst_close <= 0: continue

            open_gap = (today_bar.open - yst_close) / yst_close
            is_s1 = (code, info, bl) in s1_filtered
            setup_type = '1进2' if is_s1 else '断板反包'

            # 基础过滤
            if today_bar.open < 3:
                print(f"  [排除] {jq(code)}({info['name']}) 开盘价 {today_bar.open:.2f} <= 3")
                continue

            # 计算量比（今日成交量/昨日成交量）
            yst_vol = bl[-1].volume if hasattr(bl[-1], 'volume') else 0
            vol_ratio = today_bar.volume / yst_vol if yst_vol > 0 else 0

            # 规则匹配
            matched = None
            if is_s1:
                rules = params.setup1_rules
                cur = open_gap
            else:
                rules = params.setup2_rules
                cur = open_gap

            for rule in rules:
                if rule['open_lo'] < cur <= rule['open_hi'] and rule['vol_lo'] <= vol_ratio <= rule['vol_hi']:
                    matched = rule['name']
                    break

            if matched is None:
                print(f"  [排除] {jq(code)}({info['name']}) 竞价未匹配: 涨幅={open_gap*100:+.2f}% 量比={vol_ratio*100:.2f}%")
                continue

            # 打分
            turnover = getattr(today_bar, 'pct_chg', 0) or 0
            amount_score = min(today_bar.amount / 1e8, 10) / 10 if hasattr(today_bar, 'amount') else 0.5
            score = open_gap*100*1.2 + vol_ratio*100*0.8 + turnover*(1+open_gap)*1.5 + amount_score*2.0
            if is_s1 and cur >= 0.098: score += 15
            if not is_s1 and cur >= 0.08: score += 12
            if not is_s1 and cur < -0.03: score += 5

            # 涨幅因子
            tb = tracking.get(code, {})
            gain = (tb.get('max_price',0) - tb.get('base_price',1)) / tb.get('base_price',1) * 100 if tb.get('base_price',0) > 0 else 0
            bonus = 0
            if gain >= 40: bonus = 10
            elif gain >= 30: bonus = 15
            elif gain >= 20: bonus = 20
            elif gain >= 10: bonus = 10
            elif gain >= 5: bonus = 5
            score += bonus

            if bonus > 0:
                print(f"  [涨幅因子] {jq(code)}({info['name']}) 自选至今涨幅加分: +{bonus}")

            qualified.append({'code':code, 'name':info['name'], 'setup_type':setup_type,
                            'condition':matched, 'score':score, 'open_gap':open_gap*100,
                            'vol_ratio':vol_ratio*100, 'tracked_bonus':bonus})

            print(f"[OK] {jq(code)}({info['name']}) 符合 {setup_type}，命中条件: {matched} | 得分: {score:.2f}")

        # 重排选优
        qualified.sort(key=lambda x: x['score'], reverse=True)
        final = qualified[:params.max_buy_count]

        print(f"\n竞价终筛结果：符合竞价过滤条件的个股共 {len(qualified)} 只")
        if final:
            print(f"【重排选优】排序前 {len(final)} 只龙头股票：")
            for i, r in enumerate(final):
                print(f"  -{i+1}. {jq(r['code'])}({r['name']}) | 得分: {r['score']:.2f} | 类型: {r['setup_type']}({r['condition']})")
            for r in final:
                print(f"下单买入: {jq(r['code'])}({r['name']}) | 得分: {r['score']:.2f} | 条件: {r['setup_type']}({r['condition']})")

        # 更新追踪池最高价
        for code, info, bl in s1_filtered + s2_filtered:
            bars = info['bars']
            bar_dates = [str(b.ts)[:10] for b in bars]
            idx = bar_dates.index(test_date)
            today_bar = bars[idx]
            if code in tracking and today_bar.high > tracking[code]['max_price']:
                tracking[code]['max_price'] = today_bar.high

        # 排行榜
        board = sorted(tracking.values(), key=lambda x: (x['max_price']-x['base_price'])/x['base_price']*100 if x['base_price']>0 else 0, reverse=True)
        print(f"\n{'='*80}")
        print(f"【自候选至今最高涨幅排行榜】累计追踪候选股: {len(board)}只")
        print(f"{'─'*80}")
        print(f"{'排名':>4} {'代码':<12} {'名称':<8} {'候选日期':<11} {'类型':<8} {'基准价':>8} {'最高价':>8} {'最大涨幅':>10}")
        print(f"{'─'*80}")
        for i, t in enumerate(board[:30]):
            pct = (t['max_price']-t['base_price'])/t['base_price']*100 if t['base_price']>0 else 0
            code = [k for k,v in tracking.items() if v is t][0]
            print(f"{i+1:>4} {jq(code):<12} {t['name']:<8} {t['entry_date']:<11} {t['setup_type']:<8} "
                  f"{t['base_price']:>8.2f} {t['max_price']:>8.2f} {pct:>9.2f}%")
        print(f"{'='*80}")

        # 漏选分析
        bought_codes = [r['code'] for r in final]
        missed_codes = [c for c,i,_ in s1_filtered+s2_filtered if c not in bought_codes]
        if missed_codes:
            results = []
            for code, info, bl in s1_filtered+s2_filtered:
                if code in bought_codes: continue
                bars = info['bars']
                bar_dates = [str(b.ts)[:10] for b in bars]
                idx = bar_dates.index(test_date)
                today_bar = bars[idx]
                base = bl[-1].close
                if base <= 0: continue
                is_s1 = (code, info, bl) in s1_filtered
                results.append({
                    'code': code, 'name': info['name'],
                    'setup_type': '1进2' if is_s1 else '断板反包',
                    'open_pct': (today_bar.open-base)/base*100,
                    'high_pct': (today_bar.high-base)/base*100,
                    'close_pct': (today_bar.close-base)/base*100,
                    'is_limit': today_bar.close >= today_bar.high*0.998 if today_bar.high>0 else False,
                })
            results.sort(key=lambda x: x['close_pct'], reverse=True)
            n = len(results)
            n_up = sum(1 for r in results if r['close_pct']>0)
            n_lu = sum(1 for r in results if r['is_limit'])
            avg = sum(r['close_pct'] for r in results)/n
            mx = max(r['close_pct'] for r in results)
            mn = min(r['close_pct'] for r in results)
            print(f"\n{'─'*80}")
            print(f"[漏选分析] 共{n}只候选未买入 | 上涨{n_up}只({n_up/n:.0%}) | 涨停{n_lu}只 | 均涨{avg:.2f}% | 最高{mx:.2f}% | 最低{mn:.2f}%")
            print(f"{'─'*80}")
            print(f"{'排名':>4} {'代码':<12} {'名称':<8} {'类型':<8} {'开盘涨幅':>8} {'最高涨幅':>8} {'收盘涨幅':>8}")
            print(f"{'─'*80}")
            for i, r in enumerate(results[:20]):
                print(f"{i+1:>4} {jq(r['code']):<12} {r['name']:<8} {r['setup_type']:<8} "
                      f"{r['open_pct']:>+7.2f}% {r['high_pct']:>+7.2f}% {r['close_pct']:>+7.2f}%")
            lu = [r for r in results if r['is_limit']]
            if lu:
                print(f"\n[!!] 有{len(lu)}只漏选股票今日涨停:")
                for r in lu:
                    print(f"    {jq(r['code'])}({r['name']}) {r['setup_type']} 涨幅={r['close_pct']:+.2f}%")
            print(f"{'─'*80}")


if __name__ == '__main__':
    run_backtest()
