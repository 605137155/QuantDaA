from __future__ import annotations


class ResilientMarketProvider:
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback
        self.source_name = getattr(primary, "source_name", primary.__class__.__name__)
        self.last_error = ""

    def get_universe(self):
        return self._call("get_universe")

    def get_market_snapshot(self):
        return self._call("get_market_snapshot")

    def get_daily_bars(self, stock_code: str, limit: int = 15):
        return self._call("get_daily_bars", stock_code, limit=limit)

    def get_minute_bars(self, stock_code: str):
        return self._call("get_minute_bars", stock_code)

    def _call(self, method_name: str, *args, **kwargs):
        try:
            result = getattr(self.primary, method_name)(*args, **kwargs)
            self.source_name = getattr(self.primary, "source_name", self.primary.__class__.__name__)
            self.last_error = ""
            return result
        except Exception as exc:
            self.last_error = str(exc)
            self.source_name = f"{getattr(self.fallback, 'source_name', self.fallback.__class__.__name__)}"
            return getattr(self.fallback, method_name)(*args, **kwargs)
