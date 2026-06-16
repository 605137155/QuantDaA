"""
同花顺涨停板候选获取器
数据源: 同花顺数据中心API + 腾讯行情API
用途: 获取每日首板(Setup1)和断板反包(Setup2)候选池
"""
import requests
import logging

logger = logging.getLogger(__name__)

_tc = requests.Session()
_tc.trust_env = False
_tc.verify = False
_ths_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}


def ths_limit_up(date_str):
    """从同花顺获取涨停板股票列表
    Args:
        date_str: 日期，格式 'YYYY-MM-DD'
    Returns:
        set: 涨停股票代码集合（纯数字，如 '600301'）
    """
    date_fmt = date_str.replace('-', '')
    stocks = set()
    page = 1
    while True:
        url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page={page}&limit=200&field=code&order=desc&date={date_fmt}'
        try:
            resp = _tc.get(url, headers=_ths_headers, timeout=10)
            data = resp.json()
            if data.get('status_code') != 0 or not data.get('data', {}).get('info'):
                break
            stocks |= {s['code'] for s in data['data']['info']}
            if len(stocks) >= data['data']['page']['total']:
                break
            page += 1
        except Exception as e:
            logger.warning(f"同花顺API请求失败({date_str}): {e}")
            break
    return stocks


def to_jq_code(code):
    """纯数字代码转聚宽格式: 600301 -> 600301.XSHG"""
    if code.startswith(('6', '9', '688')):
        return f'{code}.XSHG'
    return f'{code}.XSHE'


def get_stock_names(codes):
    """腾讯API获取股票中文名称
    Args:
        codes: 纯数字代码列表
    Returns:
        dict: {code: name}
    """
    names = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        try:
            resp = _tc.get(f'https://qt.gtimg.cn/q={",".join(syms)}', timeout=10)
            for line in resp.text.strip().split('\n'):
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) > 2:
                    names[parts[2]] = parts[1]
        except:
            pass
    return names


def fetch_candidates(trade_dates, test_date):
    """获取某日的首板和断板反包候选池
    Args:
        trade_dates: 交易日列表（升序）
        test_date: 目标日期 'YYYY-MM-DD'
    Returns:
        dict: {
            'date': test_date,
            'yesterday': y_day,
            'setup1': [jq_code, ...],   # 首板1进2候选（聚宽格式）
            'setup2': [jq_code, ...],   # 断板反包候选（聚宽格式）
            'names': {code: name},      # 股票名称（纯数字code）
            'raw_s1_count': int,        # 原始S1数量
            'raw_s2_count': int,        # 原始S2数量
        }
    """
    if test_date not in trade_dates:
        return None

    idx = trade_dates.index(test_date)
    if idx < 5:
        return None

    y_day = trade_dates[idx - 1]
    d_2 = trade_dates[idx - 2]
    d_3 = trade_dates[idx - 3]
    d_4 = trade_dates[idx - 4]
    d_5 = trade_dates[idx - 5]

    # 获取涨停板
    limit_1 = ths_limit_up(y_day)
    limit_2 = ths_limit_up(d_2)
    limit_3 = ths_limit_up(d_3)
    limit_4 = ths_limit_up(d_4)
    limit_5 = ths_limit_up(d_5)

    # 形态分类
    # Setup 1 (1进2): 昨日首板（昨日涨停，前日未涨停）
    raw_s1 = sorted([c for c in limit_1 if not c.startswith(('3', '4', '8', '9', '68')) and c not in limit_2])
    # Setup 2 (断板反包): 前日首板（前日涨停，大前日未涨停），昨日断板
    raw_s2 = sorted([c for c in limit_2 if not c.startswith(('3', '4', '8', '9', '68')) and c not in limit_3 and c not in limit_1])
    # Setup 3 (三日断板): 三日前首板（三日前涨停，四日前未涨停），前日和昨日均未涨停
    raw_s3 = sorted([c for c in limit_4 if not c.startswith(('3', '4', '8', '9', '68')) and c not in limit_5 and c not in limit_3 and c not in limit_2 and c not in limit_1])

    # 获取名称
    all_codes = raw_s1 + raw_s2 + raw_s3
    names = get_stock_names(all_codes)

    return {
        'date': test_date,
        'yesterday': y_day,
        'setup1': [to_jq_code(c) for c in raw_s1],
        'setup2': [to_jq_code(c) for c in raw_s2],
        'setup3': [to_jq_code(c) for c in raw_s3],
        'names': names,
        'raw_s1_count': len(raw_s1),
        'raw_s2_count': len(raw_s2),
        'raw_s3_count': len(raw_s3),
    }
