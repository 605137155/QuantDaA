from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockSnapshot:
    code: str
    name: str
    last_price: float
    pct_chg: float
    amount: float
    volume: float
    turnover_rate: float
    high: float
    low: float
    open: float
    market: str
    security_type: str
    updated_at: str
