from __future__ import annotations

import unittest

from src.models.candle import Candle
from src.models.stock import Stock
from src.models.stock_snapshot import StockSnapshot
from src.strategies.double_bottom import DoubleBottomStrategy


class DoubleBottomStrategyTests(unittest.TestCase):
    def test_triggers_when_pattern_near_breakout(self):
        strategy = DoubleBottomStrategy(
            {
                "lookback_minutes": 120,
                "min_required_bars": 10,
                "min_gap_minutes": 3,
                "max_low_diff_ratio": 0.02,
                "min_rebound_ratio": 0.02,
                "near_breakout_ratio": 0.003,
                "breakout_ratio": 0.0,
                "cooldown_minutes": 20,
            }
        )

        closes = [10.2, 10.0, 9.8, 9.7, 9.85, 10.0, 9.9, 9.72, 9.9, 10.18]
        bars = []
        prev = closes[0]
        for idx, close in enumerate(closes):
            bars.append(
                Candle(
                    stock_code="000001",
                    ts=f"2026-05-30 09:{30 + idx:02d}:00",
                    open=prev,
                    high=max(prev, close) + 0.03,
                    low=min(prev, close) - 0.03,
                    close=close,
                    volume=100000,
                    amount=100000 * close,
                )
            )
            prev = close

        signal = strategy.evaluate(
            stock=Stock(code="000001", name="平安银行"),
            daily_bars=[],
            minute_bars=bars,
            snapshot=StockSnapshot(
                code="000001",
                name="平安银行",
                last_price=10.19,
                pct_chg=2.3,
                amount=1000000000,
                volume=10000000,
                turnover_rate=2.1,
                high=10.21,
                low=9.68,
                open=10.20,
                market="",
                security_type="stock",
                updated_at="2026-05-30 10:00:00",
            ),
        )

        self.assertTrue(signal.triggered)
        self.assertEqual(signal.strategy_name, "double_bottom")


if __name__ == "__main__":
    unittest.main()
