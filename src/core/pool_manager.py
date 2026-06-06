from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PoolSnapshot:
    monitor_pool: list
    focus_pool: list
    snapshot_map: dict


class PoolManager:
    def __init__(self, provider, stock_filter, hot_score_service, stock_repo, scan_settings: dict):
        self.provider = provider
        self.stock_filter = stock_filter
        self.hot_score_service = hot_score_service
        self.stock_repo = stock_repo
        self.scan_settings = scan_settings
        self._seed_stocks = []
        # 缓存上次刷新结果
        self._last_snapshot: PoolSnapshot | None = None
        self._last_snapshot_time: float = 0
        self._snapshot_cache_interval = 30  # 30秒内返回缓存

    def seed_universe(self) -> None:
        self._seed_stocks = self.provider.get_universe()
        if self._seed_stocks:
            self.stock_repo.upsert_many(self._seed_stocks)

    def get_seed_stocks(self) -> list:
        return self._seed_stocks

    def refresh_pools(self) -> PoolSnapshot:
        # 检查缓存是否有效
        now = time.time()
        if self._last_snapshot and (now - self._last_snapshot_time) < self._snapshot_cache_interval:
            return self._last_snapshot

        # 缓存过期，重新获取数据
        rows = self.provider.get_market_snapshot()
        filtered = self.stock_filter.apply(rows)
        monitor_size = self.scan_settings["monitor_pool_size"]
        focus_size = self.scan_settings["focus_pool_size"]

        monitor_pool = sorted(filtered, key=lambda item: item.amount, reverse=True)[:monitor_size]
        focus_pool = self.hot_score_service.pick_top(monitor_pool, top_n=focus_size)
        snapshot_map = {item.code: item for item in filtered}

        # 更新缓存
        result = PoolSnapshot(monitor_pool=monitor_pool, focus_pool=focus_pool, snapshot_map=snapshot_map)
        self._last_snapshot = result
        self._last_snapshot_time = now
        return result
