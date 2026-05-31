from __future__ import annotations

from typing import Optional, Tuple

from src.models.signal import Signal
from src.strategies.base import BaseStrategy


class DoubleBottomStrategy(BaseStrategy):
    name = "double_bottom"

    def __init__(self, params: dict):
        self.params = params

    def evaluate(self, stock, daily_bars, minute_bars, snapshot) -> Signal:
        if len(minute_bars) < self.params["min_required_bars"]:
            return self.no_signal(stock)

        lookback = self.params["lookback_minutes"]
        bars = minute_bars[-lookback:]
        local_lows = self._find_local_lows(bars)
        if len(local_lows) < 2:
            return self.no_signal(stock)

        left_idx, right_idx = self._pick_pair(bars, local_lows)
        if left_idx is None:
            return self.no_signal(stock)

        left_bar = bars[left_idx]
        right_bar = bars[right_idx]
        gap = right_idx - left_idx
        if gap < self.params["min_gap_minutes"]:
            return self.no_signal(stock)

        diff_ratio = abs(right_bar.low - left_bar.low) / max(left_bar.low, 0.01)
        if diff_ratio > self.params["max_low_diff_ratio"]:
            return self.no_signal(stock)

        neckline = max(bar.high for bar in bars[left_idx:right_idx + 1])
        rebound_ratio = (neckline - min(left_bar.low, right_bar.low)) / max(min(left_bar.low, right_bar.low), 0.01)
        if rebound_ratio < self.params["min_rebound_ratio"]:
            return self.no_signal(stock)

        current_price = snapshot.last_price
        near_breakout_ratio = (neckline - current_price) / max(neckline, 0.01)

        reasons = [
            f"两个低点价差 {diff_ratio:.2%}",
            f"底部间隔 {gap} 分钟",
            f"反弹高度 {rebound_ratio:.2%}",
            f"颈线价格 {neckline:.2f}",
        ]

        if current_price >= neckline * (1 + self.params["breakout_ratio"]):
            return Signal(
                triggered=True,
                strategy_name=self.name,
                stock_code=stock.code,
                stock_name=stock.name,
                signal_level="buy_watch",
                score=85,
                title="双底突破",
                message=f"{stock.name} 出现双底突破，可重点关注",
                reasons=reasons + [f"当前价 {current_price:.2f} 已突破颈线"],
                cooldown_minutes=self.params["cooldown_minutes"],
                timestamp=snapshot.updated_at,
            )

        if 0 <= near_breakout_ratio <= self.params["near_breakout_ratio"]:
            return Signal(
                triggered=True,
                strategy_name=self.name,
                stock_code=stock.code,
                stock_name=stock.name,
                signal_level="watch",
                score=70,
                title="双底接近突破",
                message=f"{stock.name} 双底接近颈线，可留意",
                reasons=reasons + [f"当前价 {current_price:.2f} 接近颈线"],
                cooldown_minutes=self.params["cooldown_minutes"],
                timestamp=snapshot.updated_at,
            )

        return self.no_signal(stock)

    @staticmethod
    def _find_local_lows(bars: list) -> list[int]:
        lows = []
        for idx in range(1, len(bars) - 1):
            if bars[idx].low <= bars[idx - 1].low and bars[idx].low <= bars[idx + 1].low:
                lows.append(idx)
        return lows

    def _pick_pair(self, bars: list, local_lows: list[int]) -> Tuple[Optional[int], Optional[int]]:
        best_pair = (None, None)
        best_diff = None

        for left_pos in range(len(local_lows) - 1):
            for right_pos in range(left_pos + 1, len(local_lows)):
                left_idx = local_lows[left_pos]
                right_idx = local_lows[right_pos]
                if right_idx - left_idx < self.params["min_gap_minutes"]:
                    continue

                left_low = bars[left_idx].low
                right_low = bars[right_idx].low
                diff_ratio = abs(right_low - left_low) / max(left_low, 0.01)
                if best_diff is None or diff_ratio < best_diff:
                    best_pair = (left_idx, right_idx)
                    best_diff = diff_ratio

        return best_pair
