#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成归一化训练数据
将不同板块的涨幅归一化到[-1, 1]区间
"""

import sqlite3
import csv
import json
from pathlib import Path

db_path = Path('data/app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('=== 生成归一化训练数据 ===')
print('')

# 1. 获取6月3日的replay候选数据
print('--- 1. 获取6月3日replay候选数据 ---')
cursor.execute('''
    SELECT stock_code, stock_name, total_score, grade,
           heat_score, market_cap_score, volume_price_score, position_score, risk_penalty,
           flags_json, risks_json, metrics_json
    FROM candidate_score_snapshots
    WHERE trade_date = '2026-06-03' AND session_type = 'replay'
    ORDER BY total_score DESC
''')
candidates = cursor.fetchall()
print('Got ' + str(len(candidates)) + ' candidates')

print('')

# 2. 获取这些股票在6月5日的表现，并归一化
print('--- 2. 获取6月5日表现并归一化 ---')

def get_limit_pct(stock_code, stock_name):
    '''获取涨跌幅限制'''
    name = (stock_name or '').upper()
    if 'ST' in name:
        return 5.0
    if stock_code.startswith(('30', '68')):
        return 20.0
    if stock_code.startswith(('8', '4')):
        return 30.0
    return 10.0

results = []
for candidate in candidates:
    stock_code = candidate[0]
    stock_name = candidate[1]
    total_score = candidate[2]
    grade = candidate[3]
    heat_score = candidate[4]
    market_cap_score = candidate[5]
    volume_price_score = candidate[6]
    position_score = candidate[7]
    risk_penalty = candidate[8]
    flags_json = candidate[9]
    risks_json = candidate[10]
    metrics_json = candidate[11]

    # 获取6月3日的收盘价
    cursor.execute('''
        SELECT close FROM daily_bars
        WHERE stock_code = ? AND trade_date = '2026-06-03'
    ''', (stock_code,))
    row_0603 = cursor.fetchone()

    # 获取6月5日的收盘价
    cursor.execute('''
        SELECT close FROM daily_bars
        WHERE stock_code = ? AND trade_date = '2026-06-05'
    ''', (stock_code,))
    row_0605 = cursor.fetchone()

    if row_0603 and row_0605:
        close_0603 = row_0603[0]
        close_0605 = row_0605[0]
        pct_change = ((close_0605 - close_0603) / close_0603) * 100

        # 归一化涨幅
        limit_pct = get_limit_pct(stock_code, stock_name)
        normalized_pct = pct_change / limit_pct

        # 解析metrics_json
        metrics = json.loads(metrics_json) if metrics_json else {}

        results.append({
            'stock_code': stock_code,
            'stock_name': stock_name,
            'total_score': total_score,
            'grade': grade,
            'heat_score': heat_score,
            'market_cap_score': market_cap_score,
            'volume_price_score': volume_price_score,
            'position_score': position_score,
            'risk_penalty': risk_penalty,
            'close_20260603': close_0603,
            'close_20260605': close_0605,
            'pct_change_2d': round(pct_change, 2),
            'limit_pct': limit_pct,
            'normalized_pct': round(normalized_pct, 4),
            'metrics': metrics
        })

print('Got ' + str(len(results)) + ' results with 6月5日 data')

print('')

# 3. 生成CSV
print('--- 3. 生成CSV ---')
output_path = Path('exports') / 'train_2026-06-03_normalized.csv'
output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)

    # 写入表头（兼容训练脚本格式）
    writer.writerow([
        'stock_code', 'stock_name', 'total_score', 'grade',
        'heat_score', 'market_cap_score', 'volume_price_score', 'position_score', 'risk_penalty',
        'close_20260603', 'close_20260605', 'next_day_pct', 'limit_pct', 'normalized_pct',
        'metric_vol_ratio_5', 'metric_red_green_ratio_5', 'metric_close_strength', 'metric_day_pct', 'metric_day_amplitude',
        'metric_body_ratio', 'metric_signed_body_pct', 'metric_breakout_20', 'metric_breakout_gap_20',
        'metric_bias_ma5', 'metric_pos60', 'metric_upper_shadow_ratio', 'metric_pct3', 'metric_amount_continuity_2d',
        'metric_float_market_cap_est', 'metric_reference_price',
        'metric_monitor_rank_yesterday', 'metric_ths_rank_yesterday', 'metric_ths_value_rank_yesterday', 'metric_kpl_rank_yesterday',
        'label_live_pct'
    ])

    # 写入数据
    for r in results:
        metrics = r['metrics']
        writer.writerow([
            r['stock_code'], r['stock_name'], r['total_score'], r['grade'],
            r['heat_score'], r['market_cap_score'], r['volume_price_score'], r['position_score'], r['risk_penalty'],
            r['close_20260603'], r['close_20260605'], r['pct_change_2d'], r['limit_pct'], r['normalized_pct'],
            metrics.get('vol_ratio_5', ''), metrics.get('red_green_ratio_5', ''),
            metrics.get('close_strength', ''), metrics.get('day_pct', ''), metrics.get('day_amplitude', ''),
            metrics.get('body_ratio', ''), metrics.get('signed_body_pct', ''),
            metrics.get('breakout_20', ''), metrics.get('breakout_gap_20', ''),
            metrics.get('bias_ma5', ''), metrics.get('pos60', ''),
            metrics.get('upper_shadow_ratio', ''), metrics.get('pct3', ''),
            metrics.get('amount_continuity_2d', ''),
            metrics.get('float_market_cap_est', ''), metrics.get('reference_price', ''),
            metrics.get('monitor_rank_yesterday', ''), metrics.get('ths_rank_yesterday', ''),
            metrics.get('ths_value_rank_yesterday', ''), metrics.get('kpl_rank_yesterday', ''),
            r['normalized_pct']  # label_live_pct
        ])

print('Saved to ' + str(output_path))

print('')

# 4. 统计
print('--- 4. 统计 ---')
up_count = sum(1 for r in results if r['normalized_pct'] > 0)
down_count = sum(1 for r in results if r['normalized_pct'] < 0)
flat_count = sum(1 for r in results if r['normalized_pct'] == 0)
avg_normalized = sum(r['normalized_pct'] for r in results) / len(results) if results else 0

print('上涨: ' + str(up_count) + ' 只')
print('下跌: ' + str(down_count) + ' 只')
print('平盘: ' + str(flat_count) + ' 只')
print('平均归一化涨幅: ' + str(round(avg_normalized, 4)))

print('')

# 5. 按归一化涨幅排序，显示前10和后10
print('--- 5. 归一化涨幅前10名 ---')
results_sorted = sorted(results, key=lambda x: x['normalized_pct'], reverse=True)
print('排名 | 代码 | 名称 | 归一化涨幅 | 原始涨幅 | 涨幅限制')
print('-' * 60)
for i, r in enumerate(results_sorted[:10], 1):
    print(str(i).rjust(3) + ' | ' + r['stock_code'] + ' | ' + r['stock_name'].ljust(10) + ' | ' + str(r['normalized_pct']).rjust(8) + ' | ' + str(r['pct_change_2d']).rjust(6) + '% | ' + str(r['limit_pct']).rjust(4) + '%')

print('')
print('--- 6. 归一化涨幅后10名 ---')
print('排名 | 代码 | 名称 | 归一化涨幅 | 原始涨幅 | 涨幅限制')
print('-' * 60)
for i, r in enumerate(results_sorted[-10:], len(results_sorted) - 9):
    print(str(i).rjust(3) + ' | ' + r['stock_code'] + ' | ' + r['stock_name'].ljust(10) + ' | ' + str(r['normalized_pct']).rjust(8) + ' | ' + str(r['pct_change_2d']).rjust(6) + '% | ' + str(r['limit_pct']).rjust(4) + '%')

conn.close()
