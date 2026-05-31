from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    triggered: bool
    strategy_name: str
    stock_code: str
    stock_name: str
    signal_level: str
    score: int
    title: str
    message: str
    reasons: list[str] = field(default_factory=list)
    cooldown_minutes: int = 20
    timestamp: str = ""

    @property
    def dedupe_key(self) -> str:
        return f"{self.stock_code}:{self.strategy_name}:{self.signal_level}"
