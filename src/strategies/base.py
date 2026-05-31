from __future__ import annotations

from src.models.signal import Signal


class BaseStrategy:
    name = "base"

    def evaluate(self, stock, daily_bars, minute_bars, snapshot) -> Signal:
        raise NotImplementedError

    def no_signal(self, stock) -> Signal:
        return Signal(
            triggered=False,
            strategy_name=self.name,
            stock_code=stock.code,
            stock_name=stock.name,
            signal_level="none",
            score=0,
            title="",
            message="",
        )
