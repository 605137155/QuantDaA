from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.models.stock_snapshot import StockSnapshot
from src.utils.trading_day_utils import is_trading_session


@dataclass
class RuntimeState:
    monitor_pool: list = field(default_factory=list)
    focus_pool: list = field(default_factory=list)
    snapshot_map: dict = field(default_factory=dict)


class AppRunner:
    def __init__(self, logger, provider, pool_manager, strategy_runner, daily_repo, settings: dict):
        self.logger = logger
        self.provider = provider
        self.pool_manager = pool_manager
        self.strategy_runner = strategy_runner
        self.daily_repo = daily_repo
        self.settings = settings
        self.state = RuntimeState()
        self._warmed_up = False

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "source_name", self.provider.__class__.__name__)

    @property
    def provider_error(self) -> str:
        return getattr(self.provider, "last_error", "")

    def warmup(self) -> None:
        if self._warmed_up:
            return
        self.logger.info("warming up stock universe")
        self.pool_manager.seed_universe()
        self._warmed_up = True

    def refresh_pools(self):
        self.warmup()
        snapshot = self.pool_manager.refresh_pools()
        self.state.monitor_pool = snapshot.monitor_pool
        self.state.focus_pool = snapshot.focus_pool
        self.state.snapshot_map = snapshot.snapshot_map
        return snapshot

    def scan_once(self) -> list:
        if not self.state.focus_pool:
            self.refresh_pools()
        return self.strategy_runner.scan(self.state.focus_pool, self.state.snapshot_map)

    def get_stock_detail(self, stock_code: str, selected_date: str = "") -> dict:
        snapshot = self.state.snapshot_map.get(stock_code)
        if snapshot is None:
            snapshot = StockSnapshot(
                code=stock_code,
                name=stock_code,
                last_price=0.0,
                pct_chg=0.0,
                amount=0.0,
                volume=0.0,
                turnover_rate=0.0,
                high=0.0,
                low=0.0,
                open=0.0,
                market="",
                security_type="stock",
                updated_at="",
            )
        daily_bars = self.daily_repo.get_recent(stock_code, limit=60)
        if len(daily_bars) < 60:
            daily_bars = self.provider.get_daily_bars(stock_code, limit=60)
            if daily_bars:
                self.daily_repo.replace_for_stock(stock_code, daily_bars)
        minute_bars = self.strategy_runner.get_cached_or_fetch_minute_bars(stock_code, limit=800)
        available_minute_dates = sorted({bar.ts[:10] for bar in minute_bars})
        target_date = selected_date or (available_minute_dates[-1] if available_minute_dates else "")
        minute_bars_for_date = [bar for bar in minute_bars if bar.ts[:10] == target_date] if target_date else []
        return {
            "snapshot": snapshot,
            "daily_bars": daily_bars,
            "all_minute_bars": minute_bars,
            "minute_bars": minute_bars_for_date,
            "available_minute_dates": available_minute_dates,
            "selected_date": target_date,
        }

    def run_once(self) -> None:
        self.refresh_pools()
        self.scan_once()
        self.logger.info("one-shot run complete")

    def run_forever(self) -> None:
        self.warmup()
        pool_every = self.settings["scan"]["pool_refresh_seconds"]
        scan_every = self.settings["scan"]["signal_scan_seconds"]
        last_pool_refresh = 0.0
        last_scan = 0.0

        while True:
            now = time.time()
            if not is_trading_session():
                time.sleep(5)
                continue

            if now - last_pool_refresh >= pool_every:
                snapshot = self.pool_manager.refresh_pools()
                self.state.monitor_pool = snapshot.monitor_pool
                self.state.focus_pool = snapshot.focus_pool
                self.state.snapshot_map = snapshot.snapshot_map
                last_pool_refresh = now

            if now - last_scan >= scan_every and self.state.focus_pool:
                self.strategy_runner.scan(self.state.focus_pool, self.state.snapshot_map)
                last_scan = now

            time.sleep(1)
