from __future__ import annotations

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

    def scan(self, focus_pool: list, snapshot_map: dict) -> list:
        triggered = []
        for stock in focus_pool:
            snapshot = snapshot_map.get(stock.code, stock)
            daily_bars = self.daily_repo.get_recent(stock.code, limit=15)
            if len(daily_bars) < 15:
                daily_bars = self.provider.get_daily_bars(stock.code, limit=15)
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
        minute_bars = self.provider.get_minute_bars(stock_code)
        if minute_bars:
            self.minute_repo.replace_for_stock(stock_code, minute_bars)
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
