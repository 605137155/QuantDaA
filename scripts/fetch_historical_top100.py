#!/usr/bin/env python3
"""
获取历史成交额前100名数据
使用腾讯行情API逐个获取股票历史K线数据
排除ST股票和688开头的科创板股票
只保留主板（60开头）和创业板（300开头）
"""

import requests
import json
import time
import sqlite3
from pathlib import Path
from datetime import datetime

# 禁用SSL警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_stock_list_from_db():
    """从数据库获取股票列表（排除ST和688）"""
    db_path = Path('data/app.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 获取股票列表，排除ST和688
    cursor.execute('''
        SELECT stock_code, stock_name
        FROM stocks
        WHERE is_active = 1
        AND stock_name NOT LIKE '%ST%'
        AND stock_name NOT LIKE '%st%'
        AND stock_code NOT LIKE '688%'
        AND (stock_code LIKE '60%' OR stock_code LIKE '300%' OR stock_code LIKE '00%')
    ''')
    stocks = cursor.fetchall()
    conn.close()

    return stocks

def get_historical_data(stock_code, trade_date):
    """获取单只股票的历史数据"""
    session = requests.Session()
    session.verify = False

    # 判断市场
    if stock_code.startswith(('5', '6', '9')) or stock_code.startswith('688'):
        symbol = f'sh{stock_code}'
    else:
        symbol = f'sz{stock_code}'

    url = 'https://ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {
        '_var': 'kline_dayqfq',
        'param': f'{symbol},day,{trade_date},{trade_date},1,qfq',
        'r': '0.123456'
    }

    try:
        response = session.get(url, params=params, timeout=10)
        payload = response.text.split('=', 1)[-1]
        data = json.loads(payload)

        if 'data' in data and symbol in data['data']:
            stock_data = data['data'][symbol]

            if 'qfqday' in stock_data and len(stock_data['qfqday']) > 0:
                day = stock_data['qfqday'][0]
                if day[0] == trade_date:
                    # 腾讯API的volume单位是手，需要×100转换为股
                    volume = float(day[5]) * 100
                    close = float(day[2])
                    open_price = float(day[1])
                    high = float(day[3])
                    low = float(day[4])
                    amount = volume * close
                    # 计算涨跌幅
                    pct_chg = ((close - open_price) / open_price) * 100 if open_price > 0 else 0.0

                    return {
                        'code': stock_code,
                        'amount': amount,
                        'close': close,
                        'volume': volume,
                        'pct_chg': round(pct_chg, 2),
                        'open': open_price,
                        'high': high,
                        'low': low
                    }
    except Exception as e:
        pass

    return None

def save_to_database(trade_date, top100):
    """保存到数据库"""
    db_path = Path('data/app.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 先删除该日期的旧数据
    cursor.execute('''
        DELETE FROM rank_snapshots
        WHERE trade_date = ? AND snapshot_type = 'monitor_close'
    ''', (trade_date,))

    # 插入新数据
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for i, r in enumerate(top100, 1):
        cursor.execute('''
            INSERT INTO rank_snapshots (trade_date, snapshot_type, rank_no, stock_code, stock_name, pct_chg, amount, extra_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_date,
            'monitor_close',
            i,
            r['code'],
            r['name'],
            r['pct_chg'],
            r['amount'],
            '{}',
            now
        ))

    conn.commit()
    conn.close()
    print(f'Saved {len(top100)} records to database')

def main():
    trade_date = '2026-06-04'
    print(f'Fetching top 100 stocks by amount for {trade_date}...')
    print('Excluding ST stocks and 688 (科创板)')
    print('Only keeping 主板 (60) and 创业板 (300)')

    # 从数据库获取股票列表
    stock_list = get_stock_list_from_db()
    print(f'Total stocks to process: {len(stock_list)}')

    results = []
    processed = 0
    errors = 0

    for stock_code, stock_name in stock_list:
        try:
            data = get_historical_data(stock_code, trade_date)
            if data:
                data['name'] = stock_name
                results.append(data)

            processed += 1
            if processed % 100 == 0:
                print(f'  Processed {processed}/{len(stock_list)} stocks...')

            time.sleep(0.05)  # 避免请求过快

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f'  Error processing {stock_code}: {e}')

    print(f'\nProcessing complete: {processed} stocks, {errors} errors')
    print(f'Stocks with data: {len(results)}')

    # 排序并取前100名
    results.sort(key=lambda x: x['amount'], reverse=True)
    top100 = results[:100]

    print(f'\n=== {trade_date} Top 100 by Amount ===')
    print(f'{"Rank":<6} {"Code":<8} {"Name":<12} {"Amount (亿)":<12} {"Pct Chg":<10} {"Close":<10}')
    print('-' * 60)

    for i, r in enumerate(top100, 1):
        print(f'{i:<6} {r["code"]:<8} {r["name"]:<12} {r["amount"]/100000000:<12.2f} {r["pct_chg"]:+<10.2f} {r["close"]:<10.2f}')

    # 保存到数据库
    save_to_database(trade_date, top100)

    # 保存到CSV
    import csv
    output_path = Path('exports') / f'top100_{trade_date}.csv'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['rank', 'code', 'name', 'amount', 'pct_chg', 'close', 'volume'])
        writer.writeheader()
        for i, r in enumerate(top100, 1):
            writer.writerow({
                'rank': i,
                'code': r['code'],
                'name': r['name'],
                'amount': r['amount'],
                'pct_chg': r['pct_chg'],
                'close': r['close'],
                'volume': r['volume']
            })

    print(f'\nSaved to {output_path}')

if __name__ == '__main__':
    main()
