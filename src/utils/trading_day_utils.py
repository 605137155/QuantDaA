from __future__ import annotations

from datetime import datetime
from typing import Optional


def is_trading_session(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now()
    if current.weekday() >= 5:
        return False

    hm = current.hour * 100 + current.minute
    in_morning = 930 <= hm < 1130
    in_afternoon = 1300 <= hm < 1500
    return in_morning or in_afternoon
