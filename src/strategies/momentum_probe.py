from __future__ import annotations

from src.models.signal import Signal
from src.strategies.base import BaseStrategy


class MomentumProbeStrategy(BaseStrategy):
    name = "momentum_probe"

    def __init__(self, params: dict):
        self.params = params

    def evaluate(self, stock, daily_bars, minute_bars, snapshot) -> Signal:
        if snapshot.pct_chg < self.params["min_pct_chg"]:
            return self.no_signal(stock)

        return Signal(
            triggered=True,
            strategy_name=self.name,
            stock_code=stock.code,
            stock_name=stock.name,
            signal_level="watch",
            score=55,
            title="涨幅测试信号",
            message=f"{stock.name} 当前涨幅 {snapshot.pct_chg:.2f}%，进入观察列表",
            reasons=[f"当前涨幅 {snapshot.pct_chg:.2f}% 高于阈值 {self.params['min_pct_chg']:.2f}%"],
            cooldown_minutes=self.params["cooldown_minutes"],
            timestamp=snapshot.updated_at,
        )
