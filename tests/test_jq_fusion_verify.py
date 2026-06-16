"""
验证本地首板断板融合策略的形态识别逻辑。
只对聚宽回测中实际出现的股票做验证，避免遍历全市场。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from src.data_providers.sina_tencent_provider import SinaTencentMarketProvider


# ========================================================================
# 聚宽回测的实际选股结果（从日志中提取）
# ========================================================================

JQ_EXPECTED = {
    '2026-06-01': {
        'setup1': [
            '000151.XSHE', '000608.XSHE', '000767.XSHE', '000926.XSHE', '001269.XSHE',
            '002608.XSHE', '600011.XSHG', '600857.XSHG', '600863.XSHG', '600869.XSHG',
            '603060.XSHG', '603303.XSHG', '603439.XSHG', '603618.XSHG', '603890.XSHG',
            '605177.XSHG', '605277.XSHG',
        ],
        'setup2': [
            '000600.XSHE', '000636.XSHE', '000692.XSHE', '000791.XSHE', '002348.XSHE',
            '002350.XSHE', '002636.XSHE', '002859.XSHE', '002951.XSHE', '002975.XSHE',
            '003018.XSHE', '600176.XSHG', '600237.XSHG', '600246.XSHG', '600330.XSHG',
            '600360.XSHG', '600367.XSHG', '601101.XSHG', '601588.XSHG', '603006.XSHG',
            '603031.XSHG', '603050.XSHG', '603256.XSHG', '603267.XSHG', '603678.XSHG',
            '603682.XSHG', '603931.XSHG', '605376.XSHG',
        ],
    },
    '2026-06-02': {
        'setup1': [
            '000727.XSHE', '001210.XSHE', '001314.XSHE', '002068.XSHE', '002404.XSHE',
            '002613.XSHE', '002647.XSHE', '002700.XSHE', '002848.XSHE', '002871.XSHE',
            '002951.XSHE', '003004.XSHE', '003030.XSHE', '600188.XSHG', '600367.XSHG',
            '600503.XSHG', '600546.XSHG', '600596.XSHG', '600727.XSHG', '601001.XSHG',
            '601101.XSHG', '601699.XSHG', '601918.XSHG', '603193.XSHG', '603296.XSHG',
            '603678.XSHG', '603721.XSHG', '603725.XSHG', '603773.XSHG', '603823.XSHG',
            '605566.XSHG',
        ],
        'setup2': [
            '000151.XSHE', '000608.XSHE', '000926.XSHE', '001269.XSHE',
            '600857.XSHG', '600869.XSHG', '603060.XSHG', '603439.XSHG',
            '605177.XSHG', '605277.XSHG',
        ],
    },
    '2026-06-03': {
        'setup1': [
            '000601.XSHE', '000700.XSHE', '002137.XSHE', '002200.XSHE', '002384.XSHE',
            '002806.XSHE', '002851.XSHE', '002876.XSHE', '002897.XSHE', '002957.XSHE',
            '002962.XSHE', '002975.XSHE', '600114.XSHG', '600255.XSHG', '600360.XSHG',
            '600487.XSHG', '600500.XSHG', '600667.XSHG', '600869.XSHG', '600936.XSHG',
            '601137.XSHG', '601869.XSHG', '603267.XSHG', '603311.XSHG', '603373.XSHG',
            '603618.XSHG', '603997.XSHG', '605589.XSHG',
        ],
        'setup2': [
            '000727.XSHE', '002613.XSHE', '002647.XSHE', '002700.XSHE', '002871.XSHE',
            '002925.XSHE', '003004.XSHE', '600188.XSHG', '600503.XSHG', '600596.XSHG',
            '600727.XSHG', '601001.XSHG', '601699.XSHG', '601918.XSHG', '603296.XSHG',
            '603721.XSHG', '603725.XSHG', '603773.XSHG', '603823.XSHG', '605566.XSHG',
        ],
    },
}


def jq_to_local(jq_code: str) -> str:
    """聚宽代码转本地: 000151.XSHE -> 000151"""
    return jq_code.split('.')[0]


def local_to_jq(code: str) -> str:
    """本地代码转聚宽: 000151 -> 000151.XSHE"""
    if code.startswith(('6', '9', '688')):
        return f"{code}.XSHG"
    return f"{code}.XSHE"


def classify_stock(bars, code):
    """
    对单只股票做形态分类。
    bars: 日线数据列表 (按时间升序)，至少4条
    code: 股票代码（本地格式，如000151）
    返回: 'setup1' / 'setup2' / None
    """
    if len(bars) < 4:
        return None

    # 涨停比例
    limit_ratio = 0.20 if code.startswith(('300', '301', '688')) else 0.10

    # 计算涨停标记：当日收盘 >= 前日收盘 * (1 + limit_ratio) * 0.998
    limit_flags = []
    for i in range(len(bars)):
        if i == 0:
            limit_flags.append(False)  # 第一天无法判断
            continue
        prev_close = bars[i - 1].close
        limit_up_price = prev_close * (1 + limit_ratio)
        is_limit = bars[i].close >= limit_up_price * 0.998
        limit_flags.append(is_limit)

    # 最近3天
    d1_limit = limit_flags[-1]  # 昨日涨停
    d2_limit = limit_flags[-2]  # 前日涨停
    d3_limit = limit_flags[-3]  # 大前日涨停

    d1_close = bars[-1].close
    d2_close = bars[-2].close

    # Setup 1: 昨日涨停 + 前日未涨停
    if d1_limit and not d2_limit:
        return 'setup1'

    # Setup 2: 前日涨停 + 大前日未涨停 + 昨日未涨停 + 跌幅<5%
    if d2_limit and not d3_limit and not d1_limit:
        if d2_close > 0 and d1_close >= d2_close * 0.95:
            return 'setup2'

    return None


def run_verification():
    """运行验证"""
    print("=" * 80)
    print("首板断板融合策略 - 形态识别验证")
    print("=" * 80)

    provider = SinaTencentMarketProvider()

    # 收集所有需要验证的股票代码
    all_codes = set()
    for date, expected in JQ_EXPECTED.items():
        for c in expected['setup1']:
            all_codes.add(jq_to_local(c))
        for c in expected['setup2']:
            all_codes.add(jq_to_local(c))

    print(f"\n需要验证的股票: {len(all_codes)}只")
    print("正在批量获取日线数据...")

    # 批量获取日线数据
    bars_cache = {}
    errors = []
    for i, code in enumerate(sorted(all_codes)):
        try:
            bars = provider.get_daily_bars(code, limit=15)
            if bars and len(bars) >= 4:
                bars_cache[code] = bars
            else:
                errors.append((code, f"数据不足: {len(bars) if bars else 0}条"))
        except Exception as e:
            errors.append((code, str(e)))

        if (i + 1) % 20 == 0:
            print(f"  已获取 {i+1}/{len(all_codes)} ...")

    print(f"  完成: 成功{len(bars_cache)}只, 失败{len(errors)}只")
    if errors:
        print(f"  失败: {errors[:5]}...")

    # 逐日验证
    results_summary = []

    for test_date, expected in JQ_EXPECTED.items():
        print(f"\n{'='*80}")
        print(f"验证日期: {test_date}")
        print(f"{'='*80}")

        jq_setup1 = set(jq_to_local(c) for c in expected['setup1'])
        jq_setup2 = set(jq_to_local(c) for c in expected['setup2'])
        jq_all = jq_setup1 | jq_setup2

        print(f"聚宽预期: Setup1={len(jq_setup1)}只, Setup2={len(jq_setup2)}只")

        # 对每只股票做形态分类
        local_setup1 = set()
        local_setup2 = set()
        classify_details = []

        for code in sorted(jq_all):
            bars = bars_cache.get(code)
            if bars is None:
                classify_details.append((code, 'no_data', '-', '-'))
                continue

            # 找到test_date对应的bar索引
            bar_dates = []
            for b in bars:
                d = b.ts if isinstance(b.ts, str) else b.ts.strftime('%Y-%m-%d')
                bar_dates.append(d[:10])

            # 在聚宽中，before_market_open在09:10运行，用的是test_date之前的数据
            # test_date是选股日（如06-01），昨日=test_date的前一天（05-29）
            # bars中test_date当天的bar不应该包含在形态分类中
            idx = None
            for j, d in enumerate(bar_dates):
                if d == test_date:
                    idx = j
                    break

            if idx is None or idx < 3:
                classify_details.append((code, 'no_date_match', '-', '-'))
                continue

            # 取test_date之前的bars做分类（不含test_date当天）
            bars_before = bars[:idx]

            if len(bars_before) < 3:
                classify_details.append((code, 'insufficient_bars', '-', '-'))
                continue

            result = classify_stock(bars_before, code)

            jq_in_s1 = code in jq_setup1
            jq_in_s2 = code in jq_setup2

            if result == 'setup1':
                local_setup1.add(code)
            elif result == 'setup2':
                local_setup2.add(code)

            match = 'OK' if (
                (result == 'setup1' and jq_in_s1) or
                (result == 'setup2' and jq_in_s2) or
                (result is None and not jq_in_s1 and not jq_in_s2)
            ) else 'MISMATCH'

            jq_type = 'S1' if jq_in_s1 else ('S2' if jq_in_s2 else '-')
            local_type = 'S1' if result == 'setup1' else ('S2' if result == 'setup2' else '-')
            classify_details.append((code, match, jq_type, local_type))

        # 对比结果
        s1_match = jq_setup1 & local_setup1
        s2_match = jq_setup2 & local_setup2
        total_match = s1_match | s2_match

        s1_only_jq = jq_setup1 - local_setup1
        s1_only_local = local_setup1 - jq_setup1
        s2_only_jq = jq_setup2 - local_setup2
        s2_only_local = local_setup2 - jq_setup2

        print(f"本地结果: Setup1={len(local_setup1)}只, Setup2={len(local_setup2)}只")
        print(f"\n  Setup1: 一致 {len(s1_match)}/{len(jq_setup1)}")
        if s1_only_jq:
            print(f"    聚宽有本地无: {', '.join(sorted(s1_only_jq))}")
        if s1_only_local:
            print(f"    本地有聚宽无: {', '.join(sorted(s1_only_local))}")

        print(f"\n  Setup2: 一致 {len(s2_match)}/{len(jq_setup2)}")
        if s2_only_jq:
            print(f"    聚宽有本地无: {', '.join(sorted(s2_only_jq))}")
        if s2_only_local:
            print(f"    本地有聚宽无: {', '.join(sorted(s2_only_local))}")

        match_rate = len(total_match) / max(len(jq_all), 1) * 100
        print(f"\n  总体一致率: {len(total_match)}/{len(jq_all)} ({match_rate:.0f}%)")

        # 打印详细分类结果
        print(f"\n  详细分类:")
        print(f"  {'代码':<10} {'结果':<12} {'聚宽':>6} {'本地':>6}")
        print(f"  {'-'*36}")
        for code, match, jq_t, local_t in classify_details:
            marker = '  ' if match == 'OK' else '!!'
            print(f"  {marker}{code:<8} {match:<12} {jq_t:>6} {local_t:>6}")

        results_summary.append({
            'date': test_date,
            'jq_count': len(jq_all),
            'local_count': len(local_setup1 | local_setup2),
            'match_count': len(total_match),
            'match_rate': match_rate,
        })

    # 汇总
    print(f"\n{'='*80}")
    print("验证汇总")
    print(f"{'='*80}")
    print(f"{'日期':<12} {'聚宽':>6} {'本地':>6} {'一致':>6} {'一致率':>8}")
    print(f"{'-'*40}")
    for r in results_summary:
        print(f"{r['date']:<12} {r['jq_count']:>6} {r['local_count']:>6} {r['match_count']:>6} {r['match_rate']:>7.0f}%")


if __name__ == '__main__':
    run_verification()
