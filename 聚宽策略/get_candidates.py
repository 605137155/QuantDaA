import sys
sys.stdout.reconfigure(encoding='utf-8')
import requests
import warnings
warnings.filterwarnings('ignore')

_tc = requests.Session()
_tc.trust_env = False
_tc.verify = False
_h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}

def ths(d):
    s = set()
    p = 1
    while True:
        u = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page={p}&limit=200&field=code&order=desc&date={d}'
        try:
            r = _tc.get(u, headers=_h, timeout=10)
            data = r.json()
            if data.get('status_code') != 0 or not data.get('data', {}).get('info'):
                break
            s |= {x['code'] for x in data['data']['info']}
            if len(s) >= data['data']['page']['total']:
                break
            p += 1
        except:
            break
    return s

def j(c):
    return f'{c}.XSHG' if c.startswith('6') else f'{c}.XSHE'

import akshare as ak
df = ak.tool_trade_date_hist_sina()
td = [str(d)[:10] for d in df['trade_date']]
td = [d for d in td if '2026-05-20' <= d <= '2026-06-10']

for test in ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04', '2026-06-05', '2026-06-06', '2026-06-08']:
    if test not in td:
        print(f'{test}: non-trading day')
        continue
    i = td.index(test)
    y = td[i - 1]
    d2 = td[i - 2]
    d3 = td[i - 3]
    l1 = ths(y)
    l2 = ths(d2)
    l3 = ths(d3)
    mp = [c for c in (l1 | l2) if not c.startswith(('3', '4', '8', '9', '68'))]
    s1 = sorted([c for c in mp if c in l1 and c not in l2])
    s2 = sorted([c for c in mp if c in l2 and c not in l3 and c not in l1])
    print(f'{test}: S1={len(s1)} S2={len(s2)}')
    print(f'  S1: {" ".join([j(c) for c in s1])}')
    print(f'  S2: {" ".join([j(c) for c in s2])}')
    print()
