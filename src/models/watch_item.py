from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchItem:
    stock_code: str
    stock_name: str
    strategy_name: str
    signal_level: str
    trigger_time: str
    price: float
    pct_chg: float
    title: str
    message: str
    reason_text: str
    is_read: bool = False
