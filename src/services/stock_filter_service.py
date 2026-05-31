from __future__ import annotations


class StockFilterService:
    def __init__(self, market_settings: dict):
        self.market_settings = market_settings

    def apply(self, rows: list) -> list:
        filtered = []
        for row in rows:
            if self.market_settings.get("exclude_st", True) and "ST" in row.name.upper():
                continue
            if self.market_settings.get("exclude_etf", True) and "ETF" in row.name.upper():
                continue
            if self.market_settings.get("exclude_convertible", True) and row.security_type.lower().find("bond") >= 0:
                continue
            if not self.market_settings.get("enable_bj", False) and row.code.startswith("8"):
                continue
            filtered.append(row)
        return filtered
