from __future__ import annotations

import unittest

from src.models.candle import Candle
from src.models.stock import Stock
from src.models.stock_snapshot import StockSnapshot
from src.strategies.limit_up_auction import LimitUpAuctionStrategy


class LimitUpAuctionStrategyTests(unittest.TestCase):
    def setUp(self):
        self.params = {
            "min_required_bars": 100,
            "min_market_cap": 70.0,
            "max_market_cap": 520.0,
            "min_open_vol_ratio": 0.03,
            "cooldown_minutes": 30
        }
        self.strategy = LimitUpAuctionStrategy(self.params)
        self.stock = Stock(code="000001", name="平安银行")

    def _generate_mock_daily_bars(self, count=100, yesterday_close=10.0, yesterday_vol=100000.0, yesterday_amount=200000000.0,
                                   t2_close=9.09, t3_close=9.09, t5_close=9.09, yesterday_high=10.0, has_high_peak=False):
        bars = []
        # Base daily bars
        for idx in range(count - 5):
            close_val = 12.0 if (has_high_peak and idx == 50) else 9.09
            bars.append(
                Candle(
                    stock_code="000001",
                    ts=f"2026-01-{idx+1:02d}",
                    open=9.0,
                    high=close_val + 0.5,
                    low=8.8,
                    close=close_val,
                    volume=50000.0,
                    amount=50000.0 * close_val,
                    pct_chg=0.0
                )
            )
        # T-5 to T-3
        for idx, close in enumerate([t5_close, t5_close, t3_close]):
            bars.append(
                Candle(
                    stock_code="000001",
                    ts=f"2026-05-{20+idx}",
                    open=t5_close,
                    high=t5_close + 0.1,
                    low=t5_close - 0.1,
                    close=close,
                    volume=50000.0,
                    amount=50000.0 * close,
                    pct_chg=0.0
                )
            )
        # T-2 (Day before yesterday)
        bars.append(
            Candle(
                stock_code="000001",
                ts="2026-06-03",
                open=t2_close,
                high=t2_close + 0.1,
                low=t2_close - 0.1,
                close=t2_close,
                volume=80000.0,
                amount=80000.0 * t2_close,
                pct_chg=0.0
            )
        )
        # T-1 (Yesterday)
        bars.append(
            Candle(
                stock_code="000001",
                ts="2026-06-04",
                open=t2_close,
                high=yesterday_high,
                low=t2_close - 0.2,
                close=yesterday_close,
                volume=yesterday_vol,
                amount=yesterday_amount,
                pct_chg=round((yesterday_close - t2_close) / t2_close * 100, 2)
            )
        )
        return bars

    def test_triggers_low_buy_successfully(self):
        # 首板低吸：昨日首板 (t2_close=9.09, yesterday_close=10.0 -> 涨停)
        # 60日收盘价在低位，昨日成交额 >= 1亿 (1.2亿)
        # 今日低开 -3% 到 -4.5% (昨收 10.0，开盘 9.65 -> -3.5%)
        # 竞量验证：昨成交量 100万，今日竞量 4万 -> 4% > 3%
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=10.0,
            yesterday_vol=1_000_000.0,
            yesterday_amount=120_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=9.09,
            has_high_peak=True
        )
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=386_000.0,       # 竞价金额
            volume=40_000.0,        # 竞价量
            turnover_rate=0.01,     # 竞价换手率 (估算市值: amount*100/turnover_rate = 38.6M*100 = 38.6亿)
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )
        # Mock turnover_rate to estimate cap in 70-520亿 range
        # Let's say turnover_rate = 0.0003, cap = 386000 * 100 / 0.0003 = 128亿
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=386_000.0,
            volume=40_000.0,
            turnover_rate=0.0003, # Estimated cap: 3.86M * 100 / 0.03 = 12.87亿?
            # Wait: amount = 386000, 386000 * 100 / 0.0003 = 128,666,666,666? (1286亿, too big)
            # Let's adjust to get ~100亿: amount=300000, turnover_rate=0.0003 -> cap = 300000 * 100 / 0.0003 = 100,000,000,000 (1000亿)
            # Wait, amount=30000, turnover_rate=0.03 -> cap = 30000 * 100 / 0.03 = 100,000,000 (1亿)
            # Let's use amount=30_000_000, turnover_rate=0.3 -> cap = 30M * 100 / 0.3 = 10,000,000,000 (100亿)
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )
        # Wait, snapshot.amount is in RMB. Let's make sure it scales to ~100亿:
        # Amount = 30,000,000 (3000万), Turnover_rate = 0.3%
        # Estimated cap = 30,000,000 * 100 / 0.3 = 10,000,000,000 (100亿)
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=30_000_000.0,
            volume=40_000.0,
            turnover_rate=0.3,
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.signal_level, "buy_watch")
        self.assertIn("首板低吸", signal.title)

    def test_triggers_high_open_successfully(self):
        # 首板高开：昨日首板 (t2_close=9.09, yesterday_close=10.0 -> 涨停)
        # 昨日成交额在 5.5亿-20亿 区间 (昨日 8亿)
        # 今日高开 0% 到 6% (昨收 10.0, 开盘 10.3 -> +3%)
        # 竞量验证: 昨成交量 800万, 今日竞量 30万 -> 3.75% > 3%
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=10.0,
            yesterday_vol=8_000_000.0,
            yesterday_amount=800_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=9.09
        )
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=10.3,
            pct_chg=3.0,
            amount=30_000_000.0,
            volume=300_000.0,
            turnover_rate=0.3,
            high=10.3,
            low=10.3,
            open=10.3,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.signal_level, "buy_watch")
        self.assertIn("首板高开/一进二", signal.title)

    def test_triggers_weak_strong_successfully(self):
        # 弱转强：昨日炸板 (yesterday_high=10.0 -> 涨停, yesterday_close=9.5 -> 未封死)
        # 昨日成交额在 5.5亿-20亿 区间 (昨日 8亿)
        # 今日高开 2% 到 9% (昨收 9.5, 开盘 10.0 -> +5.26%)
        # 竞量验证: 昨成交量 840万, 今日竞量 30万 -> 3.57% > 3%
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=9.5,
            yesterday_vol=8_400_000.0,
            yesterday_amount=800_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=9.09,
            yesterday_high=10.0
        )
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=10.0,
            pct_chg=5.26,
            amount=30_000_000.0,
            volume=300_000.0,
            turnover_rate=0.3,
            high=10.0,
            low=10.0,
            open=10.0,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.signal_level, "buy_watch")
        self.assertIn("炸板弱转强", signal.title)

    def test_fails_left_pressure_test_when_volume_is_low(self):
        # 突破百日高点但成交量没有放大 (昨日收10.0 >= 100日最高收盘价的98%)
        # 前期100日最高收盘价是10.0，最大成交量是500万。
        # 昨日成交量只有10万 (10万 < 500万 * 90%) -> 左压测试失败，不触发。
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=10.0,
            yesterday_vol=100_000.0,
            yesterday_amount=120_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=9.09
        )
        # Modify one historical bar to have 10.0 close and 5,000,000 volume (max_vol_100)
        daily_bars[50] = Candle(
            stock_code="000001",
            ts="2026-03-01",
            open=9.0,
            high=10.0,
            low=8.8,
            close=10.0,
            volume=5_000_000.0,
            amount=50_000_000.0,
            pct_chg=0.0
        )

        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=30_000_000.0,
            volume=4_000.0,
            turnover_rate=0.3,
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertFalse(signal.triggered)

    def test_fails_when_overheated(self):
        # 过度涨幅过滤：近4日涨幅 > 28%
        # 昨收 10.0，T-5收 7.5 -> (10.0 - 7.5) / 7.5 = 33.3% > 28% -> 过热，不触发。
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=10.0,
            yesterday_vol=1_000_000.0,
            yesterday_amount=120_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=7.5
        )
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=30_000_000.0,
            volume=40_000.0,
            turnover_rate=0.3,
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertFalse(signal.triggered)

    def test_fails_when_auction_volume_insufficient(self):
        # 竞量不足：竞开量 1万 < 昨成交量 100万 * 3% -> 竞量不足，不触发。
        daily_bars = self._generate_mock_daily_bars(
            yesterday_close=10.0,
            yesterday_vol=1_000_000.0,
            yesterday_amount=120_000_000.0,
            t2_close=9.09,
            t3_close=9.09,
            t5_close=9.09
        )
        snapshot = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=9.65,
            pct_chg=-3.5,
            amount=30_000_000.0,
            volume=10_000.0,       # 1万 < 3万 (3%)
            turnover_rate=0.3,
            high=9.65,
            low=9.65,
            open=9.65,
            market="SZ",
            security_type="stock",
            updated_at="2026-06-05 09:25:00",
        )

        signal = self.strategy.evaluate(self.stock, daily_bars, [], snapshot)
        self.assertFalse(signal.triggered)


if __name__ == "__main__":
    unittest.main()
