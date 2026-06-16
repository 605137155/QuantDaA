"""
首板与断板反包竞价融合策略 - 本地版引擎
数据源: 同花顺涨停板API + 腾讯实时行情
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# 腾讯请求session
_tc = requests.Session()
_tc.trust_env = False
_tc.verify = False


# ========================================================================
# 数据模型
# ========================================================================

@dataclass
class PremarketCandidate:
    code: str
    name: str
    setup_type: str  # '1进2' 或 '断板反包'


@dataclass
class AuctionResult:
    code: str
    name: str
    setup_type: str
    matched_condition: str
    score: float
    open_gap_pct: float
    vol_ratio: float
    obi: float
    tracked_bonus: float = 0.0


@dataclass
class TrackedStock:
    code: str
    name: str
    entry_date: str
    base_price: float
    max_price: float
    max_pct: float
    setup_type: str


# ========================================================================
# 策略引擎
# ========================================================================

class JQFusionStrategy:
    """首板断板融合竞价策略引擎"""

    def __init__(self):
        from src.repositories.jq_fusion_repo import JQFusionRepo
        self.repo = JQFusionRepo()
        self.name_cache = {}
        self.tracked = {}  # code -> TrackedStock
        self.load_tracking_from_db()

        # 竞价规则
        self.rules_s1 = [
            ('E: 一字板/准一字 竞价涨幅>=9.8% | 竞昨比>=0.5%', 1.098, 1.11, 0.005, 1.0),
            ('A: 竞价高开7~9% | 竞昨比2.5~25%', 1.07, 1.098, 0.025, 0.25),
            ('B: 竞价高开4~7% | 竞昨比2~25%', 1.04, 1.07, 0.02, 0.25),
            ('C: 竞价平开至小高开0~4% | 竞昨比1.5~15%', 1.00, 1.04, 0.015, 0.15),
        ]
        self.rules_s2 = [
            ('反包E: 竞价高开8~12% | 竞昨比0.5~25%', 1.08, 1.12, 0.005, 0.25),
            ('反包A: 竞价高开4~8% | 竞昨比0.5~20%', 1.04, 1.08, 0.005, 0.20),
            ('反包B: 竞价高开2~4% | 竞昨比0.5~15%', 1.02, 1.04, 0.005, 0.15),
            ('反包C: 竞价平开至小高开0~2% | 竞昨比0.5~12%', 1.00, 1.02, 0.005, 0.12),
            ('反包D: 竞价低开-3~0% | 竞昨比0.5~12%', 0.97, 1.00, 0.005, 0.12),
            ('反包F: 深低开-5~-3% | 竞昨比0.5~10%', 0.95, 0.97, 0.005, 0.10),
        ]

    def get_names(self, codes):
        """腾讯API获取股票名称"""
        names = {}
        for i in range(0, len(codes), 80):
            batch = codes[i:i+80]
            syms = []
            for c in batch:
                pure = c.split('.')[0]
                syms.append(f'sh{pure}' if pure.startswith('6') else f'sz{pure}')
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

    def get_realtime(self, codes):
        """腾讯实时行情（含五档盘口）"""
        result = {}
        pure_codes = [c.split('.')[0] for c in codes]
        for i in range(0, len(pure_codes), 80):
            batch = pure_codes[i:i+80]
            syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
            try:
                resp = _tc.get(f'https://qt.gtimg.cn/q={",".join(syms)}', timeout=10)
                for line in resp.text.strip().split('\n'):
                    if '~' not in line:
                        continue
                    parts = line.split('~')
                    if len(parts) < 50:
                        continue
                    code_raw = parts[2]
                    code = f'{code_raw}.XSHG' if code_raw.startswith('6') else f'{code_raw}.XSHE'

                    def sf(s, d=0.0):
                        try: return float(s) if s else d
                        except: return d

                    result[code] = {
                        'name': parts[1], 'open': sf(parts[5]), 'last': sf(parts[3]),
                        'volume': sf(parts[6]), 'turnover': sf(parts[38]),
                        'pct_chg': sf(parts[32]),  # 涨跌幅%（腾讯直接提供）
                        'high': sf(parts[33], sf(parts[3])),  # 今日最高价，兜底用最新价
                    }
                    for j in range(1, 6):
                        result[code][f'bid{j}_p'] = sf(parts[8+(j-1)*2])
                        result[code][f'bid{j}_v'] = sf(parts[9+(j-1)*2])
                        result[code][f'ask{j}_p'] = sf(parts[18+(j-1)*2])
                        result[code][f'ask{j}_v'] = sf(parts[19+(j-1)*2])
            except:
                pass
        return result

    def load_tracking_from_db(self):
        """从数据库加载活跃的候选追踪信息"""
        self.tracked = {}
        active = self.repo.get_active_tracking()
        for row in active:
            self.tracked[row['code']] = TrackedStock(
                code=row['code'],
                name=row.get('name', row['code']),
                entry_date=row['entry_date'],
                base_price=row['base_price'],
                max_price=row['max_price'],
                max_pct=row['max_pct'],
                setup_type=row['setup_type']
            )

    def get_saved_auction_display(self, date_str: str) -> list:
        """从数据库读取某日已存的竞价结果"""
        return self.repo.get_saved_auction_display(date_str)

    def match_auction(self, date_str, s1_codes, s2_codes, s3_codes, yst_close_map, yst_vol_map, yst_turnover_map, names):
        """竞价匹配
        Args:
            date_str: 当前交易日日期 格式 'YYYY-MM-DD'
            s1_codes: Setup1候选
            s2_codes: Setup2候选
            s3_codes: Setup3候选
            yst_close_map: 昨日收盘价
            yst_vol_map: 昨日成交量
            yst_turnover_map: 昨日换手率
            names: {pure_code: name} 股票名称
        Returns:
            list[AuctionResult]: 匹配结果
        """
        all_targets = s1_codes + s2_codes + s3_codes
        if not all_targets:
            return []

        # 优先读取数据库缓存
        db_bidding = self.repo.get_raw_bidding(date_str)
        
        bid_vol_map = {}
        bid_open_map = {}
        obi_map = {}
        
        api_targets = []
        for code in all_targets:
            if code in db_bidding:
                bid_open_map[code] = db_bidding[code]['open_price']
                bid_vol_map[code] = db_bidding[code]['bid_volume']
                obi_map[code] = db_bidding[code]['obi']
            else:
                api_targets.append(code)

        # 针对未缓存的股票发起 API 请求
        if api_targets:
            rt = self.get_realtime(api_targets)
            now = datetime.now()
            is_today = (date_str == now.strftime('%Y-%m-%d'))
            is_after_market_open = is_today and (now.hour > 9 or (now.hour == 9 and now.minute >= 26))
            if not is_today:
                is_after_market_open = True

            if is_after_market_open:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                def fetch_bid_tick(code):
                    pure = code.split('.')[0]
                    prefix = 'sh' if pure.startswith('6') else 'sz'
                    sym = f"{prefix}{pure}"
                    url = f"https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c={sym}&p=0"
                    try:
                        resp = _tc.get(url, timeout=5)
                        if resp.status_code == 200 and "v_detail_data" in resp.text:
                            text = resp.text
                            start_idx = text.find('[')
                            end_idx = text.rfind(']')
                            if start_idx != -1 and end_idx != -1:
                                lst_str = text[start_idx+1:end_idx]
                                ticks = lst_str.split('|')
                                if ticks:
                                    first_tick = ticks[0].replace('"', '').replace('\'', '')
                                    parts_tick = first_tick.split('/')
                                    if len(parts_tick) >= 6:
                                        return code, float(parts_tick[2]), float(parts_tick[4])
                    except:
                        pass
                    return code, None, None

                with ThreadPoolExecutor(max_workers=15) as executor:
                    futures = {executor.submit(fetch_bid_tick, c): c for c in api_targets}
                    for f in as_completed(futures):
                        code, op, b_vol = f.result()
                        if op is not None:
                            bid_open_map[code] = op
                            bid_vol_map[code] = b_vol
                            obi_map[code] = 1.0  # 盘后默认为 1.0

            # 填充没有成功获取 Tick 或是处于盘前竞价阶段的个股
            for code in api_targets:
                quote = rt.get(code)
                if not quote:
                    continue
                if code not in bid_open_map:
                    bid_open_map[code] = quote.get('open', 0.0)
                if code not in bid_vol_map:
                    bid_vol_map[code] = quote.get('volume', 0.0)
                if code not in obi_map:
                    if not is_after_market_open:
                        buymoney = sum(quote.get(f'bid{j}_p', 0.0) * quote.get(f'bid{j}_v', 0.0) * 100 for j in range(1, 6))
                        sellmoney = sum(quote.get(f'ask{j}_p', 0.0) * quote.get(f'ask{j}_v', 0.0) * 100 for j in range(1, 6))
                        obi_map[code] = buymoney / sellmoney if sellmoney > 0.0 else (5.0 if buymoney > 0.0 else 1.0)
                    else:
                        obi_map[code] = 1.0

            # 保存新获取的数据到数据库中
            new_bidding = []
            for code in api_targets:
                if code in bid_open_map and bid_open_map[code] > 0.0:
                    new_bidding.append({
                        'code': code,
                        'open_price': bid_open_map[code],
                        'bid_volume': bid_vol_map.get(code, 0.0),
                        'obi': obi_map.get(code, 1.0)
                    })
            if new_bidding:
                self.repo.save_raw_bidding(date_str, new_bidding)

        qualified = []
        for code in all_targets:
            pure = code.split('.')[0]
            name = names.get(pure, code)
            is_s1 = code in s1_codes
            is_s2 = code in s2_codes
            is_s3 = code in s3_codes

            yst_close = yst_close_map.get(code, 0.0)
            yst_vol = yst_vol_map.get(code, 0.0)

            # 必须拥有有效的开盘价、昨收和昨成交量
            if code not in bid_open_map or yst_close <= 0.0 or yst_vol <= 0.0:
                continue

            open_price = bid_open_map[code]
            bid_volume = bid_vol_map.get(code, 0.0)
            obi = obi_map.get(code, 1.0)

            if open_price <= 3.0:
                continue

            # 竞价指标
            cur_ratio = open_price / yst_close
            auction_ratio = bid_volume / yst_vol

            # OBI 过滤（仅限非缓存的盘前实际竞价时间段）
            now = datetime.now()
            is_today = (date_str == now.strftime('%Y-%m-%d'))
            is_after_market_open = is_today and (now.hour > 9 or (now.hour == 9 and now.minute >= 26))
            if not is_today:
                is_after_market_open = True

            if not is_after_market_open and obi < 0.6:
                continue

            # 规则匹配
            if is_s1:
                rules = self.rules_s1
                stype = '1进2'
            elif is_s2:
                rules = self.rules_s2
                stype = '断板反包'
            else:
                rules = self.rules_s2 # Setup 3 使用与 Setup 2 相同的规则
                stype = '三日断板'

            matched = None
            for cn, lo, hi, al, ah in rules:
                if lo < cur_ratio <= hi and al <= auction_ratio <= ah:
                    matched = cn
                    break

            matched_display = matched if matched is not None else "未命中"

            # 打分
            turnover_ratio = yst_turnover_map.get(code, 0.0)
            wts_factor = turnover_ratio * cur_ratio
            score = (cur_ratio - 1) * 100 * 1.2 + auction_ratio * 100 * 0.8 + wts_factor * 1.5 + obi * 2.0
            if is_s1 and cur_ratio >= 1.098:
                score += 15.0
            if not is_s1 and cur_ratio >= 1.08:
                score += 12.0
            if not is_s1 and cur_ratio < 0.97:
                score += 5.0

            # 涨幅因子
            bonus = self.calc_tracked_bonus(code)
            score += bonus

            qualified.append(AuctionResult(
                code=code, name=name, setup_type=stype,
                matched_condition=matched_display, score=round(score, 2),
                open_gap_pct=round((cur_ratio - 1) * 100, 2),
                vol_ratio=round(auction_ratio * 100, 2),
                obi=round(obi, 2), tracked_bonus=bonus,
            ))

        qualified.sort(key=lambda x: x.score, reverse=True)
        return qualified

    def calc_tracked_bonus(self, code):
        """自选涨幅因子"""
        t = self.tracked.get(code)
        if t is None:
            return 0.0
        gain = t.max_pct
        if gain >= 40: return 10.0
        elif gain >= 30: return 15.0
        elif gain >= 20: return 20.0
        elif gain >= 10: return 10.0
        elif gain >= 5: return 5.0
        return 0.0

    def register_candidates(self, codes, names, setup_type, date_str, yst_close_map):
        """注册候选到追踪池"""
        for code in codes:
            pure = code.split('.')[0]
            name = names.get(pure, code)
            base = yst_close_map.get(code, 0)
            if code not in self.tracked:
                self.tracked[code] = TrackedStock(
                    code=code, name=name,
                    entry_date=date_str, base_price=base,
                    max_price=base, max_pct=0.0, setup_type=setup_type,
                )
            # 同时持久化到 SQLite 数据库中
            self.repo.upsert_tracking(code, name, date_str, base, setup_type)

    def update_tracked_max(self, codes_with_high):
        """更新追踪池最高价"""
        updates = []
        for code, high_price in codes_with_high:
            if code in self.tracked and high_price > self.tracked[code].max_price:
                self.tracked[code].max_price = high_price
                base = self.tracked[code].base_price
                if base > 0:
                    self.tracked[code].max_pct = (high_price - base) / base * 100
                    updates.append((code, high_price, self.tracked[code].max_pct))
        if updates:
            self.repo.update_tracking_max(updates)

    def get_leaderboard(self, top_n=30):
        """获取涨幅排行榜"""
        board = sorted(self.tracked.values(), key=lambda x: x.max_pct, reverse=True)
        return board[:top_n]

    def update_tracked_highest_prices(self):
        """从腾讯 API 获取 active 股票最新最高价，并更新数据库"""
        active_codes = list(self.tracked.keys())
        if not active_codes:
            return
        rt = self.get_realtime(active_codes)
        updates = []
        for code in active_codes:
            q = rt.get(code)
            if q and q.get('high', 0) > 0:
                high_price = q['high']
                t = self.tracked[code]
                if high_price > t.max_price:
                    t.max_price = high_price
                    base = t.base_price
                    if base > 0:
                        t.max_pct = (high_price - base) / base * 100
                        updates.append((code, high_price, t.max_pct))
        if updates:
            self.repo.update_tracking_max(updates)

    def check_holding_signals(self, date_str, holdings):
        """
        验证持仓股卖出信号。
        holdings: 包含 {'code': ..., 'name': ..., 'avg_cost': ...} 的列表
        Returns:
            list: 触发的买卖信号列表
        """
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        signals = []
        if not holdings:
            return signals

        # 获取各持仓近 15 日的 K 线以计算 MA5
        def fetch_stock_kline(code):
            pure = code.split('.')[0]
            prefix = 'sh' if pure.startswith('6') else 'sz'
            sym = f"{prefix}{pure}"
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,15,qfq"
            try:
                resp = _tc.get(url, verify=False, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    qfqday = data.get("data", {}).get(sym, {}).get("qfqday", [])
                    if qfqday:
                        valid_bars = [bar for bar in qfqday if bar[0] <= date_str]
                        if len(valid_bars) >= 5:
                            return code, valid_bars[-5:]
            except Exception as e:
                logger.error(f"Error fetching kline for {code}: {e}")
            return code, None

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_stock_kline, h['code']): h for h in holdings}
            holding_bars = {}
            for f in as_completed(futures):
                code, bars = f.result()
                if bars:
                    holding_bars[code] = bars

        for h in holdings:
            code = h['code']
            name = h['name']
            avg_cost = h['avg_cost']
            
            bars = holding_bars.get(code)
            if not bars or len(bars) < 5:
                continue
            
            # bar format: [date, open, close, high, low, volume]
            last_price = float(bars[-1][2])
            yst_close = float(bars[-2][2])
            
            # 计算 MA5
            closes = [float(b[2]) for b in bars]
            MA5 = sum(closes) / 5.0
            
            is_gem = code.split('.')[0].startswith('30') or code.split('.')[0].startswith('68')
            limit_ratio = 1.198 if is_gem else 1.098
            is_limit_up = (last_price >= yst_close * limit_ratio - 0.02)
            
            if is_limit_up:
                # 涨停持有
                signals.append({
                    'code': code,
                    'name': name,
                    'signal_type': 'hold_limit',
                    'price': last_price,
                    'reason': f"涨停持有 (当前价: {last_price:.2f})"
                })
                continue
                
            # TP (Take Profit)
            if last_price > avg_cost:
                signals.append({
                    'code': code,
                    'name': name,
                    'signal_type': 'sell_tp',
                    'price': last_price,
                    'reason': f"未涨停止盈 (当前价: {last_price:.2f} > 成本价: {avg_cost:.2f})"
                })
            # MA5 Stop Loss
            elif last_price < MA5 * 0.98:
                signals.append({
                    'code': code,
                    'name': name,
                    'signal_type': 'sell_ma5',
                    'price': last_price,
                    'reason': f"跌破5日线止损 (当前价: {last_price:.2f} < MA5*0.98: {MA5*0.98:.2f})"
                })
            # Daily Drop
            elif (yst_close - last_price) / yst_close >= 0.05:
                drop_pct = (yst_close - last_price) / yst_close * 100
                signals.append({
                    'code': code,
                    'name': name,
                    'signal_type': 'sell_drop',
                    'price': last_price,
                    'reason': f"跌幅过大止损 (今日跌幅: {drop_pct:.2f}% >= 5%)"
                })
                
        return signals
