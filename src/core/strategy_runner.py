from __future__ import annotations

import time
from src.models.watch_item import WatchItem


class StrategyRunner:
    def __init__(
        self,
        provider,
        daily_repo,
        minute_repo,
        signal_repo,
        watchlist_repo,
        dedupe_service,
        alert_manager,
        strategies,
        logger,
    ):
        self.provider = provider
        self.daily_repo = daily_repo
        self.minute_repo = minute_repo
        self.signal_repo = signal_repo
        self.watchlist_repo = watchlist_repo
        self.dedupe_service = dedupe_service
        self.alert_manager = alert_manager
        self.strategies = strategies
        self.logger = logger
        # 分钟线缓存时间戳 {stock_code: last_fetch_time}
        self._minute_fetch_timestamps: dict[str, float] = {}
        self._minute_fetch_interval = 30  # 30秒内不重复请求

    def scan(self, focus_pool: list, snapshot_map: dict) -> list:
        triggered = []
        for stock in focus_pool:
            snapshot = snapshot_map.get(stock.code, stock)
            daily_bars = self.daily_repo.get_recent(stock.code, limit=100)
            if len(daily_bars) < 100:
                daily_bars = self.provider.get_daily_bars(stock.code, limit=100)
                if daily_bars:
                    self.daily_repo.replace_for_stock(stock.code, daily_bars)
            minute_bars = self.get_cached_or_fetch_minute_bars(stock.code)

            for strategy in self.strategies:
                signal = strategy.evaluate(stock, daily_bars, minute_bars, snapshot)
                if not signal.triggered:
                    continue

                if not self.dedupe_service.should_alert(signal):
                    continue

                self.signal_repo.add(signal, snapshot)
                self.watchlist_repo.add(self._to_watch_item(signal, snapshot))
                self.alert_manager.send(signal)
                triggered.append(signal)
                self.logger.info("triggered %s on %s", signal.strategy_name, signal.stock_code)

        return triggered

    def get_cached_or_fetch_minute_bars(self, stock_code: str, limit: int = 240) -> list:
        # 先尝试从缓存获取
        cached_bars = self.minute_repo.get_recent(stock_code, limit=limit)
        if cached_bars:
            return cached_bars

        # 检查是否在缓存时间内
        now = time.time()
        last_fetch = self._minute_fetch_timestamps.get(stock_code, 0)
        if now - last_fetch < self._minute_fetch_interval:
            # 在缓存时间内，返回空列表（避免重复请求）
            return []

        # 缓存为空且超过缓存时间，才发起网络请求
        self._minute_fetch_timestamps[stock_code] = now
        minute_bars = self.provider.get_minute_bars(stock_code)
        if minute_bars:
            self.minute_repo.replace_for_stock(stock_code, minute_bars)
            return self.minute_repo.get_recent(stock_code, limit=limit)
        return []

    def get_cached_minute_bars(self, stock_code: str, limit: int = 240) -> list:
        return self.minute_repo.get_recent(stock_code, limit=limit)

    @staticmethod
    def _to_watch_item(signal, snapshot) -> WatchItem:
        return WatchItem(
            stock_code=signal.stock_code,
            stock_name=signal.stock_name,
            strategy_name=signal.strategy_name,
            signal_level=signal.signal_level,
            trigger_time=signal.timestamp,
            price=snapshot.last_price,
            pct_chg=snapshot.pct_chg,
            title=signal.title,
            message=signal.message,
            reason_text="; ".join(signal.reasons),
        )
