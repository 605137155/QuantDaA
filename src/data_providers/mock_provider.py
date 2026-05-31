from __future__ import annotations

from datetime import datetime, timedelta

from src.models.candle import Candle
from src.models.stock import Stock
from src.models.stock_snapshot import StockSnapshot


class MockMarketProvider:
    def __init__(self):
        self.source_name = "mock-demo"
        self._stocks = self._build_stocks()

    def get_universe(self) -> list[Stock]:
        return list(self._stocks)

    def get_market_snapshot(self) -> list[StockSnapshot]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snapshots = [
            StockSnapshot("000001", "平安银行", 12.48, 1.2, 1_200_000_000, 96_000_000, 2.4, 12.55, 12.18, 12.20, "", "stock", now),
            StockSnapshot("600519", "贵州茅台", 1688.0, 0.8, 5_500_000_000, 3_300_000, 0.9, 1692.0, 1666.0, 1670.0, "", "stock", now),
            StockSnapshot("300750", "宁德时代", 206.0, 3.6, 7_300_000_000, 36_000_000, 3.2, 207.0, 196.0, 197.5, "", "stock", now),
            StockSnapshot("002594", "比亚迪", 248.1, 2.4, 4_100_000_000, 16_000_000, 2.0, 249.4, 240.0, 241.0, "", "stock", now),
        ]

        for idx, stock in enumerate(self._stocks[4:], start=4):
            base_price = 8.0 + idx * 0.7
            pct = ((idx % 11) - 5) * 0.6
            amount = max(120_000_000, 3_800_000_000 - idx * 21_000_000)
            volume = amount / max(base_price, 1)
            high = base_price * (1 + max(pct, 0) / 100 + 0.015)
            low = base_price * (1 + min(pct, 0) / 100 - 0.015)
            snapshots.append(
                StockSnapshot(
                    stock.code,
                    stock.name,
                    round(base_price * (1 + pct / 100), 2),
                    round(pct, 2),
                    float(amount),
                    float(volume),
                    round(0.8 + (idx % 7) * 0.35, 2),
                    round(high, 2),
                    round(low, 2),
                    round(base_price, 2),
                    "",
                    "stock",
                    now,
                )
            )
        return snapshots

    def get_daily_bars(self, stock_code: str, limit: int = 15) -> list[Candle]:
        start = datetime.now() - timedelta(days=limit + 5)
        bars = []
        price = 100.0
        for idx in range(limit):
            trade_day = start + timedelta(days=idx)
            price += 0.8
            bars.append(
                Candle(
                    stock_code=stock_code,
                    ts=trade_day.strftime("%Y-%m-%d"),
                    open=price - 0.5,
                    high=price + 1.0,
                    low=price - 1.2,
                    close=price,
                    volume=1_000_000 + idx * 50_000,
                    amount=(1_000_000 + idx * 50_000) * price,
                    pct_chg=0.8,
                )
            )
        return bars

    def get_minute_bars(self, stock_code: str) -> list[Candle]:
        start = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
        base = (sum(ord(ch) for ch in stock_code) % 20) / 10
        closes = [
            10.00 + base, 9.92 + base, 9.85 + base, 9.78 + base, 9.74 + base,
            9.70 + base, 9.74 + base, 9.81 + base, 9.90 + base, 10.02 + base,
            10.10 + base, 10.04 + base, 9.95 + base, 9.84 + base, 9.76 + base,
            9.71 + base, 9.73 + base, 9.80 + base, 9.92 + base, 10.08 + base,
            10.16 + base, 10.24 + base, 10.20 + base, 10.26 + base, 10.35 + base,
        ]
        bars = []
        prev = closes[0]
        for idx, close in enumerate(closes):
            ts = start + timedelta(minutes=idx)
            open_price = prev
            high = max(open_price, close) + 0.03
            low = min(open_price, close) - 0.03
            volume = 100_000 + idx * 4_000
            bars.append(
                Candle(
                    stock_code=stock_code,
                    ts=ts.strftime("%Y-%m-%d %H:%M:%S"),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    amount=volume * close,
                )
            )
            prev = close
        return bars

    @staticmethod
    def _build_stocks() -> list[Stock]:
        names = [
            ("000001", "平安银行"),
            ("600519", "贵州茅台"),
            ("300750", "宁德时代"),
            ("002594", "比亚迪"),
        ]
        for idx in range(5, 121):
            code = f"{600000 + idx:06d}" if idx % 2 == 0 else f"{300000 + idx:06d}"
            names.append((code, f"示例股票{idx:03d}"))
        return [Stock(code=code, name=name) for code, name in names]
