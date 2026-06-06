#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量训练所有可训练日期的模型
训练方法：归一化训练（与optimized_0603相同）
"""

import sqlite3
import csv
import json
import subprocess
from pathlib import Path
from datetime import datetime

db_path = Path('data/app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('=== 批量训练所有可训练日期的模型 ===')
print('')

# 1. 获取所有有候选评分的日期
cursor.execute('''
    SELECT DISTINCT trade_date
    FROM candidate_score_snapshots
    WHERE session_type = 'replay'
    ORDER BY trade_date
''')
snapshot_dates = [row[0] for row in cursor.fetchall()]

# 2. 获取所有有日线数据的日期
cursor.execute('''
    SELECT DISTINCT trade_date
    FROM daily_bars
    ORDER BY trade_date
''')
daily_dates = [row[0] for row in cursor.fetchall()]

# 3. 找出可训练的日期
print('--- 检查可训练日期 ---')
trainable_dates = []
for snap_date in snapshot_dates:
    # 找到下一个交易日
    next_dates = [d for d in daily_dates if d > snap_date]
    if next_dates:
        label_date = next_dates[0]

        # 检查有效样本数
        cursor.execute('''
            SELECT COUNT(DISTINCT css.stock_code)
            FROM candidate_score_snapshots css
            JOIN daily_bars db ON css.stock_code = db.stock_code
            WHERE css.trade_date = ? AND css.session_type = 'replay'
            AND db.trade_date = ?
        ''', (snap_date, label_date))
        overlap_count = cursor.fetchone()[0]

        if overlap_count >= 5:  # 至少5个样本
            trainable_dates.append((snap_date, label_date, overlap_count))
            print(snap_date + ' -> ' + label_date + ' (' + str(overlap_count) + ' samples)')
        else:
            print(snap_date + ' -> ' + label_date + ' (SKIP: only ' + str(overlap_count) + ' samples)')
    else:
        print(snap_date + ' -> SKIP: no next day data')

print('')
print('可训练日期数: ' + str(len(trainable_dates)))
print('')

# 4. 为每个可训练日期生成CSV并训练模型
print('--- 开始训练 ---')
print('')

results = []
for snap_date, label_date, sample_count in trainable_dates:
    print('========================================')
    print('训练: ' + snap_date + ' -> ' + label_date)
    print('========================================')

    # 生成CSV
    csv_path = Path('exports') / f'train_{snap_date}_to_{label_date}.csv'
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取候选数据
    cursor.execute('''
        SELECT stock_code, stock_name, total_score, grade,
               heat_score, market_cap_score, volume_price_score, position_score, risk_penalty,
               metrics_json
        FROM candidate_score_snapshots
        WHERE trade_date = ? AND session_type = 'replay'
        ORDER BY total_score DESC
    ''', (snap_date,))
    candidates = cursor.fetchall()

    # 获取下一日表现并归一化
    def get_limit_pct(stock_code, stock_name):
        name = (stock_name or '').upper()
        if 'ST' in name:
            return 5.0
        if stock_code.startswith(('30', '68')):
            return 20.0
        if stock_code.startswith(('8', '4')):
            return 30.0
        return 10.0

    rows = []
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
        metrics_json = candidate[9]

        # 获取快照日收盘价
        cursor.execute('''
            SELECT close FROM daily_bars
            WHERE stock_code = ? AND trade_date = ?
        ''', (stock_code, snap_date))
        row_snap = cursor.fetchone()

        # 获取标签日收盘价
        cursor.execute('''
            SELECT close FROM daily_bars
            WHERE stock_code = ? AND trade_date = ?
        ''', (stock_code, label_date))
        row_label = cursor.fetchone()

        if row_snap and row_label:
            close_snap = row_snap[0]
            close_label = row_label[0]
            pct_change = ((close_label - close_snap) / close_snap) * 100

            # 归一化涨幅（与训练脚本一致，以10%为基准）
            limit_pct = get_limit_pct(stock_code, stock_name)
            normalized_pct = pct_change * 10.0 / limit_pct

            # 解析metrics_json
            metrics = json.loads(metrics_json) if metrics_json else {}

            rows.append({
                'stock_code': stock_code,
                'stock_name': stock_name,
                'total_score': total_score,
                'grade': grade,
                'heat_score': heat_score,
                'market_cap_score': market_cap_score,
                'volume_price_score': volume_price_score,
                'position_score': position_score,
                'risk_penalty': risk_penalty,
                'metrics': metrics,
                'pct_change': pct_change,
                'normalized_pct': normalized_pct,
            })

    # 写入CSV
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow([
            'stock_code', 'stock_name', 'total_score', 'grade',
            'heat_score', 'market_cap_score', 'volume_price_score', 'position_score', 'risk_penalty',
            'next_day_pct', 'normalized_pct', 'label_live_pct',
            'metric_vol_ratio_5', 'metric_red_green_ratio_5', 'metric_close_strength',
            'metric_day_pct', 'metric_day_amplitude', 'metric_body_ratio', 'metric_signed_body_pct',
            'metric_breakout_20', 'metric_breakout_gap_20', 'metric_bias_ma5', 'metric_pos60',
            'metric_upper_shadow_ratio', 'metric_pct3', 'metric_amount_continuity_2d',
            'metric_float_market_cap_est', 'metric_reference_price',
            'metric_monitor_rank_yesterday', 'metric_ths_rank_yesterday',
            'metric_ths_value_rank_yesterday', 'metric_kpl_rank_yesterday'
        ])

        for r in rows:
            metrics = r['metrics']
            writer.writerow([
                r['stock_code'], r['stock_name'], r['total_score'], r['grade'],
                r['heat_score'], r['market_cap_score'], r['volume_price_score'], r['position_score'], r['risk_penalty'],
                round(r['pct_change'], 2), round(r['normalized_pct'], 4), round(r['normalized_pct'], 4),
                metrics.get('vol_ratio_5', ''), metrics.get('red_green_ratio_5', ''),
                metrics.get('close_strength', ''), metrics.get('day_pct', ''), metrics.get('day_amplitude', ''),
                metrics.get('body_ratio', ''), metrics.get('signed_body_pct', ''),
                metrics.get('breakout_20', ''), metrics.get('breakout_gap_20', ''),
                metrics.get('bias_ma5', ''), metrics.get('pos60', ''),
                metrics.get('upper_shadow_ratio', ''), metrics.get('pct3', ''),
                metrics.get('amount_continuity_2d', ''),
                metrics.get('float_market_cap_est', ''), metrics.get('reference_price', ''),
                metrics.get('monitor_rank_yesterday', ''), metrics.get('ths_rank_yesterday', ''),
                metrics.get('ths_value_rank_yesterday', ''), metrics.get('kpl_rank_yesterday', '')
            ])

    print('CSV已生成: ' + str(csv_path) + ' (' + str(len(rows)) + ' samples)')

    # 训练模型
    profile_name = 'optimized_' + snap_date.replace('-', '')
    cmd = [
        'python', 'app.py',
        '--optimize-replay-weights-csv', str(csv_path),
        '--profile-name', profile_name
    ]

    print('训练模型: ' + profile_name)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            # 解析输出
            output = result.stdout
            print(output)

            # 提取Spearman和Top10平均
            for line in output.split('\n'):
                if 'spearman:' in line:
                    spearman = line.split('->')[-1].strip()
                if 'top10_avg:' in line:
                    top10_avg = line.split('->')[-1].strip()

            results.append({
                'snap_date': snap_date,
                'label_date': label_date,
                'profile_name': profile_name,
                'samples': len(rows),
                'spearman': spearman,
                'top10_avg': top10_avg,
            })
        else:
            print('训练失败: ' + result.stderr)
    except Exception as e:
        print('训练异常: ' + str(e))

    print('')

# 5. 汇总结果
print('========================================')
print('训练完成汇总')
print('========================================')
print('')
header = '{:<12} {:<12} {:<35} {:<8} {:<10} {:<10}'.format('快照日期', '标签日期', 'Profile名称', '样本数', 'Spearman', 'Top10平均')
print(header)
print('-' * 90)

for r in results:
    line = '{:<12} {:<12} {:<35} {:<8} {:<10} {:<10}'.format(
        r['snap_date'], r['label_date'], r['profile_name'], r['samples'], r['spearman'], r['top10_avg']
    )
    print(line)

conn.close()
