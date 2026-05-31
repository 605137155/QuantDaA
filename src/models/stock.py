from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    market: str = ""
    industry: str = ""
    is_active: int = 1
