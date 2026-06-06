#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重新生成候选评分数据
使用当前激活的模型重新计算候选评分
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime

db_path = Path('data/app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('=== 重新生成候选评分数据 ===')
print('')

# 检查当前激活的模型
cursor.execute('''
    SELECT key, value FROM settings
    WHERE key = 'active_weight_profile'
''')
row = cursor.fetchone()
active_profile = row[1] if row else 'default'
print('当前激活的模型: ' + active_profile)

print('')

# 获取需要重新生成的日期
dates_to_regenerate = ['2026-06-01']

for trade_date in dates_to_regenerate:
    print('重新生成 ' + trade_date + ' 的候选数据...')

    # 删除旧数据
    cursor.execute('''
        DELETE FROM candidate_score_snapshots
        WHERE trade_date = ? AND session_type = 'replay'
    ''', (trade_date,))
    deleted = cursor.rowcount
    print('  删除旧数据: ' + str(deleted) + ' 条')

    # 重新生成数据
    # 这里需要调用AppRunner的build_replay_candidate_ranking方法
    # 但由于这是一个独立脚本，我们需要手动实现

    # 获取排名快照数据
    cursor.execute('''
        SELECT stock_code, stock_name, rank_no, pct_chg, amount, extra_json
        FROM rank_snapshots
        WHERE trade_date = ? AND snapshot_type = 'monitor_close'
        ORDER BY rank_no
    ''', (trade_date,))
    rank_rows = cursor.fetchall()

    print('  排名快照数据: ' + str(len(rank_rows)) + ' 条')

    # 构建rank_context_map
    rank_context_map = {}
    for rank_row in rank_rows:
        stock_code = rank_row[0]
        stock_name = rank_row[1]
        rank_no = rank_row[2]
        pct_chg = rank_row[3]
        amount = rank_row[4]
        extra_json = rank_row[5]

        extra = json.loads(extra_json) if extra_json else {}

        rank_context_map[stock_code] = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'monitor_rank_yesterday': rank_no,
            'amount': amount,
            'turnover_rate': extra.get('turnover_rate', 0.0),
            'float_market_cap_est': extra.get('float_market_cap_est', 0.0),
        }

    print('  候选股票数: ' + str(len(rank_context_map)))

conn.close()
print('')
print('注意: 需要在软件运行时调用 build_replay_candidate_ranking 方法来重新生成数据')
print('或者手动实现评分逻辑')
