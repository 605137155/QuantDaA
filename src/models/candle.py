from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    stock_code: str
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    pct_chg: float = 0.0
