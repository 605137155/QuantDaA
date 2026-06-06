from __future__ import annotations

import json
import urllib3
from math import ceil
from typing import Optional

import requests

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import akshare as ak
except ImportError as exc:
    raise RuntimeError("akshare is not installed") from exc

from src.models.candle import Candle
from src.models.stock import Stock
from src.models.stock_snapshot import StockSnapshot


class SinaTencentMarketProvider:
    def __init__(self):
        self.source_name = "sina-tencent"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies.clear()
        self.session.verify = False  # 禁用SSL证书验证
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        self._universe_cache: Optional[list[Stock]] = None

    def get_universe(self) -> list[Stock]:
        if self._universe_cache is not None:
            return self._universe_cache

        df = ak.stock_info_a_code_name()
        stocks = [Stock(code=str(row["code"]), name=str(row["name"])) for _, row in df.iterrows()]
        self._universe_cache = stocks
        return stocks

    def get_market_snapshot(self) -> list[StockSnapshot]:
        stocks = self.get_universe()
        snapshots = []
        chunk_size = 200
        for chunk_index in range(ceil(len(stocks) / chunk_size)):
            chunk = stocks[chunk_index * chunk_size : (chunk_index + 1) * chunk_size]
            symbols = ",".join(_to_symbol(stock.code) for stock in chunk)
            response = self.session.get("https://qt.gtimg.cn/q=" + symbols, timeout=20)
            response.raise_for_status()
            text = response.text.strip()
            for line in text.split(";"):
                line = line.strip()
                if not line or "~" not in line:
                    continue
                snapshot = _parse_tencent_quote(line)
                if snapshot is not None:
                    snapshots.append(snapshot)
        return snapshots

    def get_daily_bars(self, stock_code: str, limit: int = 60) -> list[Candle]:
        symbol = _to_symbol(stock_code)
        response = self.session.get(
            "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"_var": "kline_dayqfq", "param": f"{symbol},day,,,{limit},qfq", "r": "0.123456"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.text.split("=", 1)[-1]
        data = json.loads(payload)
        rows = data.get("data", {}).get(symbol, {})
        k_rows = rows.get("qfqday") or rows.get("day") or []
        bars = []
        for item in k_rows[-limit:]:
            bars.append(
                Candle(
                    stock_code=stock_code,
                    ts=str(item[0]),
                    open=float(item[1]),
                    close=float(item[2]),
                    high=float(item[3]),
                    low=float(item[4]),
                    volume=float(item[5]) * 100,
                    amount=0.0,
                )
            )
        return bars

    def get_minute_bars(self, stock_code: str) -> list[Candle]:
        symbol = _to_symbol(stock_code)
        response = self.session.get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            params={"param": f"{symbol},m1,,800", "_var": "m1_today", "r": "0.123456"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.text.split("=", 1)[-1]
        data = json.loads(payload)
        rows = data.get("data", {}).get(symbol, {}).get("m1", [])
        bars = []
        for item in rows:
            ts = _format_tencent_minute(item[0])
            volume = float(item[5]) * 100
            amount = volume * float(item[2])
            bars.append(
                Candle(
                    stock_code=stock_code,
                    ts=ts,
                    open=float(item[1]),
                    close=float(item[2]),
                    high=float(item[3]),
                    low=float(item[4]),
                    volume=volume,
                    amount=amount,
                )
            )
        return bars


def _to_symbol(stock_code: str) -> str:
    if stock_code.startswith(("5", "6", "9")) or stock_code.startswith("688"):
        return f"sh{stock_code}"
    return f"sz{stock_code}"


def _parse_tencent_quote(line: str) -> StockSnapshot | None:
    left, right = line.split("=", 1)
    symbol = left.replace("v_", "").strip()
    payload = right.strip().strip('"')
    if not payload:
        return None

    parts = payload.split("~")
    if len(parts) < 38:
        return None

    amount = 0.0
    try:
        amount = float(parts[37]) * 10000
    except (TypeError, ValueError, IndexError):
        if len(parts) > 35 and "/" in parts[35]:
            try:
                amount = float(parts[35].split("/")[-1])
            except (TypeError, ValueError):
                amount = 0.0

    updated_at = _format_tencent_timestamp(parts[30] if len(parts) > 30 else "")
    return StockSnapshot(
        code=parts[2],
        name=parts[1],
        last_price=_safe_float(parts[3]),
        pct_chg=_safe_float(parts[32]),
        amount=amount,
        volume=_safe_float(parts[6]) * 100,
        turnover_rate=_safe_float(parts[38] if len(parts) > 38 else 0.0),
        high=_safe_float(parts[33] if len(parts) > 33 else 0.0),
        low=_safe_float(parts[34] if len(parts) > 34 else 0.0),
        open=_safe_float(parts[5]),
        market=symbol[:2],
        security_type="stock",
        updated_at=updated_at,
    )


def _format_tencent_timestamp(raw: str) -> str:
    if len(raw) < 14:
        return ""
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"


def _format_tencent_minute(raw: str) -> str:
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:00"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
