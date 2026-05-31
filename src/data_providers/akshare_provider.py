from __future__ import annotations

from datetime import datetime
import requests
from requests.adapters import HTTPAdapter

try:
    import akshare as ak
except ImportError as exc:
    raise RuntimeError("akshare is not installed") from exc

from src.models.candle import Candle
from src.models.stock import Stock
from src.models.stock_snapshot import StockSnapshot


class AkshareMarketProvider:
    def __init__(self):
        self._patch_akshare_requests()

    def get_universe(self) -> list[Stock]:
        df = ak.stock_info_a_code_name()
        return [Stock(code=row["code"], name=row["name"]) for _, row in df.iterrows()]

    def get_market_snapshot(self) -> list[StockSnapshot]:
        df = ak.stock_zh_a_spot_em()
        snapshots = []
        for _, row in df.iterrows():
            snapshots.append(
                StockSnapshot(
                    code=str(row["代码"]),
                    name=str(row["名称"]),
                    last_price=float(row["最新价"]),
                    pct_chg=float(row["涨跌幅"]),
                    amount=float(row["成交额"]),
                    volume=float(row["成交量"]),
                    turnover_rate=_safe_float(row.get("换手率")),
                    high=_safe_float(row.get("最高")),
                    low=_safe_float(row.get("最低")),
                    open=_safe_float(row.get("今开")),
                    market=str(row.get("所属市场", "")),
                    security_type=str(row.get("证券类型", "")),
                    updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
        return snapshots

    def get_daily_bars(self, stock_code: str, limit: int = 15) -> list[Candle]:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="")
        df = df.tail(limit)
        return [
            Candle(
                stock_code=stock_code,
                ts=str(row["日期"]),
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                volume=float(row["成交量"]),
                amount=float(row["成交额"]),
                pct_chg=_safe_float(row.get("涨跌幅")),
            )
            for _, row in df.iterrows()
        ]

    def get_minute_bars(self, stock_code: str) -> list[Candle]:
        df = ak.stock_zh_a_hist_min_em(symbol=stock_code, period="1", adjust="")
        return [
            Candle(
                stock_code=stock_code,
                ts=str(row["时间"]),
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                volume=float(row["成交量"]),
                amount=float(row["成交额"]),
            )
            for _, row in df.iterrows()
        ]

    @staticmethod
    def _patch_akshare_requests() -> None:
        try:
            import akshare.utils.request as ak_request
            import akshare.utils.func as ak_func
        except Exception:
            return

        def request_with_retry_no_proxy(
            url: str,
            params=None,
            timeout: int = 15,
            max_retries: int = 3,
            base_delay: float = 1.0,
            random_delay_range=(0.5, 1.5),
        ):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    with requests.Session() as session:
                        session.trust_env = False
                        session.proxies.clear()
                        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1)
                        session.mount("http://", adapter)
                        session.mount("https://", adapter)
                        response = session.get(url, params=params, timeout=timeout)
                        response.raise_for_status()
                        return response
                except (requests.RequestException, ValueError) as exc:
                    last_exception = exc
                    if attempt < max_retries - 1:
                        import random
                        import time

                        delay = base_delay * (2 ** attempt) + random.uniform(*random_delay_range)
                        time.sleep(delay)
            raise last_exception

        ak_request.request_with_retry = request_with_retry_no_proxy
        ak_func.request_with_retry = request_with_retry_no_proxy


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
