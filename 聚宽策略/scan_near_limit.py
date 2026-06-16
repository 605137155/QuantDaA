import sys
sys.stdout.reconfigure(encoding='utf-8')
import requests
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

s = requests.Session()
s.trust_env = False
s.verify = False

headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}

def ths_fetch(date):
    stocks = set()
    page = 1
    while True:
        url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page={page}&limit=200&field=code&order=desc&date={date}'
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get('status_code') != 0 or not data.get('data',{}).get('info'):
            break
        stocks |= {s['code'] for s in data['data']['info']}
        if len(stocks) >= data['data']['page']['total']:
            break
        page += 1
    return stocks

def to_jq(c):
    return f'{c}.XSHG' if c.startswith('6') else f'{c}.XSHE'

# 获取涨停列表
ths_0528 = ths_fetch('20260528')
ths_0527 = ths_fetch('20260527')
ths_0529 = ths_fetch('20260529')
print(f'05-28涨停(同花顺): {len(ths_0528)}只 | 05-27: {len(ths_0527)}只 | 05-29: {len(ths_0529)}只')

# 获取全A主板股票
all_stocks_df = ak.stock_info_a_code_name()
all_codes = [str(c).zfill(6) for c in all_stocks_df['code'].tolist()]
main_codes = [c for c in all_codes if not c.startswith(('3','4','8','9','68'))]
print(f'主板股票: {len(main_codes)}只')

# 扫描全市场，找出05-28收盘>=涨停价*0.95的股票
print('扫描05-28接近涨停的股票...')
limit_0528 = set(ths_0528)
near_limit = set()
count = 0

for i, code in enumerate(main_codes):
    if code in limit_0528:
        continue
    try:
        df = ak.stock_zh_a_hist(symbol=code, period='daily', adjust='',
                                start_date='20260520', end_date='20260529', timeout=5)
        if df is None or len(df) < 2:
            continue
        # 找到05-28和05-27的数据
        d28 = df[df['日期'] == '2026-05-28']
        d27 = df[df['日期'] == '2026-05-27']
        if d28.empty or d27.empty:
            continue
        close_28 = d28.iloc[0]['收盘']
        close_27 = d27.iloc[0]['收盘']
        if close_27 <= 0:
            continue
        ratio = 0.20 if code.startswith(('300','301','688')) else 0.10
        hl_price = close_27 * (1 + ratio)
        if close_28 >= hl_price * 0.95:
            near_limit.add(code)
            count += 1
            if count <= 10:
                print(f'  补充: {code} 收盘={close_28:.2f} 涨停价={hl_price:.2f} 比值={close_28/hl_price:.3f}')
    except:
        pass
    if (i+1) % 200 == 0:
        print(f'  已扫描 {i+1}/{len(main_codes)}... 补充{count}只')

all_limit_0528 = limit_0528 | near_limit
print(f'\n05-28涨停+近涨停: {len(all_limit_0528)}只 (同花顺{len(limit_0528)} + 补充{len(near_limit)})')

# 断板反包候选：前日(05-28)涨停+昨日(05-29)未涨停+跌幅<5%
reversal = []
for code in all_limit_0528:
    if code.startswith(('3','4','8','9','68')): continue
    if code in ths_0529: continue
    try:
        df = ak.stock_zh_a_hist(symbol=code, period='daily', adjust='',
                                start_date='20260525', end_date='20260529', timeout=5)
        if df is None or len(df) < 2: continue
        d29 = df[df['日期'] == '2026-05-29']
        d28 = df[df['日期'] == '2026-05-28']
        if d29.empty or d28.empty: continue
        c29 = d29.iloc[0]['收盘']
        c28 = d28.iloc[0]['收盘']
        if c28 > 0 and c29 >= c28 * 0.95:
            reversal.append((code, c28, c29, (c29-c28)/c28*100))
    except:
        continue

# 获取名称
symbols = []
for c, _, _, _ in reversal:
    symbols.append(f'sh{c}' if c.startswith('6') else f'sz{c}')
resp = s.get(f'https://qt.gtimg.cn/q={",".join(symbols[:100])}', timeout=10)
names = {}
for line in resp.text.strip().split(chr(10)):
    if '~' not in line: continue
    parts = line.split('~')
    if len(parts) > 2:
        names[parts[2]] = parts[1]

print(f'\n断板反包候选(跌幅<5%): {len(reversal)}只')
print()
print(f'{"序号":<4} {"代码":<14} {"名称":<10} {"前日收盘":>8} {"昨日收盘":>8} {"跌幅":>8}')
print('-' * 55)
for i, (c, d28, d29, drop) in enumerate(sorted(reversal, key=lambda x: x[3])):
    name = names.get(c, '?')
    print(f'{i+1:<4} {to_jq(c):<14} {name:<10} {d28:>8.2f} {d29:>8.2f} {drop:>+7.2f}%')
