from __future__ import annotations

from src.models.signal import Signal
from src.strategies.base import BaseStrategy


class LimitUpAuctionStrategy(BaseStrategy):
    name = "limit_up_auction"

    def __init__(self, params: dict):
        self.params = params

    def evaluate(self, stock, daily_bars, minute_bars, snapshot) -> Signal:
        min_required = self.params.get("min_required_bars", 100)
        if len(daily_bars) < min_required:
            return self.no_signal(stock)

        # 1. 历史日线引用
        yesterday = daily_bars[-1]    # T-1
        t2 = daily_bars[-2]           # T-2
        t3 = daily_bars[-3]           # T-3
        t5 = daily_bars[-5]           # T-5

        # 2. 涨停限价计算
        def calc_high_limit(code: str, prev_close: float) -> float:
            pct = 1.20 if code.startswith(('300', '301', '688')) else 1.10
            return round(prev_close * pct, 2)

        high_limit_y = calc_high_limit(stock.code, t2.close)
        high_limit_t2 = calc_high_limit(stock.code, t3.close)

        # 3. 基础基因特征判定
        # 昨日是否是首板 (昨日收在涨停价附近，但前天未封板)
        is_first_limit_up = (yesterday.close >= high_limit_y - 0.015) and (t2.close < high_limit_t2 - 0.015)
        # 昨日是否是炸板 (昨日最高触及涨停，但收盘未能封死)
        is_blown_limit_up = (yesterday.high >= high_limit_y - 0.015) and (yesterday.close < high_limit_y - 0.015)

        if not (is_first_limit_up or is_blown_limit_up):
            return self.no_signal(stock)

        # 4. 特殊形态过滤
        # A. 异常波动排除 (排除昨日跌幅 > 5% 的个股)
        yesterday_pct_chg = (yesterday.close - t2.close) / t2.close
        if yesterday_pct_chg < -0.05:
            return self.no_signal(stock)

        # B. 过度涨幅过滤 (剔除近 4 日累计涨幅 > 28% 的个股)
        gain_4d = (yesterday.close - t5.close) / t5.close
        if gain_4d > 0.28:
            return self.no_signal(stock)

        # 5. 量价共振验证 —— 左压测试
        # 提取过去 100 日的最大收盘价与最大成交量 (排除昨日/最后一根Bar)
        close_100 = [bar.close for bar in daily_bars[:-1]]
        vol_100 = [bar.volume for bar in daily_bars[:-1]]
        max_close_100 = max(close_100) if close_100 else 0.0
        max_vol_100 = max(vol_100) if vol_100 else 0.0

        if max_close_100 > 0 and yesterday.close >= 0.98 * max_close_100:
            if yesterday.volume < 0.90 * max_vol_100:
                # 逼近或突破百日高点，但成交量未能放大至前期最大量90%，未通过左压测试，淘汰
                return self.no_signal(stock)

        # 6. 市值分层 (估算流通市值，过滤掉不在 70亿 - 520亿 之间的个股)
        # 估算公式：amount * 100 / turnover_rate
        if snapshot.turnover_rate > 0:
            market_cap_est = (snapshot.amount * 100 / snapshot.turnover_rate) / 100_000_000  # 单位：亿
            min_cap = self.params.get("min_market_cap", 70.0)
            max_cap = self.params.get("max_market_cap", 520.0)
            if not (min_cap <= market_cap_est <= max_cap):
                return self.no_signal(stock)

        # 7. 量价共振验证 —— 集合竞价成交量验证 (竞量需达昨日成交量 3% 以上)
        # 在真实开盘后，第一分钟成交量包含竞价成交量；若未开盘则直接取 snapshot.volume
        open_volume = minute_bars[0].volume if minute_bars else snapshot.volume
        yesterday_volume = yesterday.volume

        if yesterday_volume > 0:
            open_vol_ratio = open_volume / yesterday_volume
            if open_vol_ratio < self.params.get("min_open_vol_ratio", 0.03):
                return self.no_signal(stock)
        else:
            open_vol_ratio = 0.0

        # 8. 获取集合竞价产生的今日开盘价与开盘涨幅
        open_price = snapshot.open
        if not open_price or open_price == 0:
            open_price = minute_bars[0].open if minute_bars else snapshot.last_price

        if not open_price or yesterday.close == 0:
            return self.no_signal(stock)

        open_pct = (open_price - yesterday.close) / yesterday.close

        # 9. 形态匹配与竞价开盘涨幅验证
        triggered = False
        morphology = ""
        reasons = [
            f"昨日是否首板: {is_first_limit_up}",
            f"昨日是否炸板: {is_blown_limit_up}",
            f"竞开涨幅: {open_pct:.2%}",
            f"竞量占比: {open_vol_ratio:.2%}",
            f"昨日成交额: {yesterday.amount / 100_000_000:.2f}亿"
        ]

        # A. 首板低吸：昨日首板 + 昨日成交额 > 1亿 + 60日价格前50%低位 + 今日低开 3% 到 4.5%
        if is_first_limit_up and yesterday.amount >= 100_000_000:
            closes_60 = [bar.close for bar in daily_bars[-60:]]
            min_close_60 = min(closes_60)
            max_close_60 = max(closes_60)
            price_range_60 = max_close_60 - min_close_60
            position_60 = (yesterday.close - min_close_60) / (price_range_60 if price_range_60 > 0 else 0.000001)

            if position_60 <= 0.50:
                if -0.045 <= open_pct <= -0.03:
                    triggered = True
                    morphology = "首板低吸"
                    reasons.append(f"60日收盘价相对位置: {position_60:.2%}")

        # B. 首板高开：昨日首板 + 昨日成交额在 5.5亿 - 20亿 区间 + 今日高开 0% 到 6%
        if is_first_limit_up and (550_000_000 <= yesterday.amount <= 2_000_000_000):
            if 0.0 <= open_pct <= 0.06:
                triggered = True
                morphology = "首板高开/一进二"

        # C. 弱转强：昨日炸板 + 昨日成交额在 5.5亿 - 20亿 区间 + 今日高开 2% 到 9%
        if is_blown_limit_up and (550_000_000 <= yesterday.amount <= 2_000_000_000):
            if 0.02 <= open_pct <= 0.09:
                triggered = True
                morphology = "炸板弱转强"

        if triggered:
            # 避坑检查：如果开盘直接封死涨停，则跳过
            limit_up_today = calc_high_limit(stock.code, yesterday.close)
            if open_price >= limit_up_today - 0.015:
                return self.no_signal(stock)

            return Signal(
                triggered=True,
                strategy_name=self.name,
                stock_code=stock.code,
                stock_name=stock.name,
                signal_level="buy_watch",
                score=90,
                title=f"竞价多形态捕捉 - {morphology}",
                message=f"{stock.name} 匹配【{morphology}】形态，集合竞价达标，开盘可关注竞价买入",
                reasons=reasons,
                cooldown_minutes=self.params.get("cooldown_minutes", 30),
                timestamp=snapshot.updated_at,
            )

        return self.no_signal(stock)
