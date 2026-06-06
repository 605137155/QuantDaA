from __future__ import annotations

import csv
import tempfile
import unittest
import tkinter as tk
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src import bootstrap
from src.core.app_runner import AppRunner
from src.data_providers.akshare_provider import AkshareHistoricalMinuteProvider
from src.models.candle import Candle
from src.models.stock_snapshot import StockSnapshot
from src.ui.main_window import QuantDaAMainWindow, SimpleLineChart, SimpleMinuteChart


class FakeLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


class FakeProvider:
    def __init__(self, market_snapshots=None):
        self.daily_calls: list[tuple[str, int]] = []
        self.market_snapshots = list(market_snapshots or [])

    def get_daily_bars(self, stock_code: str, limit: int = 60):
        self.daily_calls.append((stock_code, limit))
        return [
            Candle(
                stock_code=stock_code,
                ts="2026-05-30",
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=1000,
                amount=10000,
            )
        ]

    def get_market_snapshot(self):
        return list(self.market_snapshots)


class FakeDailyRepo:
    def __init__(self, bars=None):
        self.bars = list(bars or [])
        self.replace_calls: list[tuple[str, int]] = []

    def get_recent(self, _stock_code: str, limit: int):
        return self.bars[:limit]

    def get_recent_until(self, _stock_code: str, trade_date: str, limit: int):
        return [bar for bar in self.bars if bar.ts[:10] <= trade_date][:limit]

    def replace_for_stock(self, stock_code: str, bars: list[Candle]) -> None:
        self.replace_calls.append((stock_code, len(bars)))


class FakeStrategyRunner:
    def __init__(self, cached_bars=None, fetched_bars=None):
        self.cached_bars = list(cached_bars or [])
        self.fetched_bars = list(fetched_bars or [])
        self.cached_calls: list[tuple[str, int]] = []
        self.fetch_calls: list[tuple[str, int]] = []

    def get_cached_minute_bars(self, stock_code: str, limit: int = 800):
        self.cached_calls.append((stock_code, limit))
        return self.cached_bars[:limit]

    def get_cached_or_fetch_minute_bars(self, stock_code: str, limit: int = 800):
        self.fetch_calls.append((stock_code, limit))
        return self.fetched_bars[:limit]


class FakeHistoricalMinuteProvider:
    def __init__(self, bars=None):
        self.bars = list(bars or [])
        self.calls: list[tuple[str, str, str, str]] = []

    def get_history_minute_bars(self, stock_code: str, start_date: str, end_date: str, period: str = "5"):
        self.calls.append((stock_code, start_date, end_date, period))
        return list(self.bars)


class FakeRankSnapshotRepo:
    def __init__(self):
        self.rows_by_key: dict[tuple[str, str], list[dict]] = {}
        self.latest_by_type: dict[str, list[dict]] = {}

    def replace_snapshot(self, trade_date: str, snapshot_type: str, rows: list[dict]) -> None:
        copied = [dict(row) for row in rows]
        self.rows_by_key[(trade_date, snapshot_type)] = copied
        self.latest_by_type[snapshot_type] = copied

    def get_snapshot(self, trade_date: str, snapshot_type: str) -> list[dict]:
        return [dict(row) for row in self.rows_by_key.get((trade_date, snapshot_type), [])]

    def get_latest_snapshot(self, snapshot_type: str) -> list[dict]:
        return [dict(row) for row in self.latest_by_type.get(snapshot_type, [])]


class FakeCandidateScoreRepo:
    def __init__(self):
        self.rows_by_key: dict[tuple[str, str], list[dict]] = {}
        self.history_rows: list[dict] = []

    def replace_scores(self, trade_date: str, session_type: str, rows: list[dict]) -> None:
        self.rows_by_key[(trade_date, session_type)] = [dict(row) for row in rows]

    def get_scores(self, trade_date: str, session_type: str) -> list[dict]:
        return [dict(row) for row in self.rows_by_key.get((trade_date, session_type), [])]

    def get_latest_scores(self, session_type: str) -> list[dict]:
        candidates = [(trade_date, rows) for (trade_date, current_session), rows in self.rows_by_key.items() if current_session == session_type]
        if not candidates:
            return []
        latest_date, rows = max(candidates, key=lambda item: item[0])
        return [dict(row) | {"trade_date": latest_date, "session_type": session_type} for row in rows]

    def append_history_snapshot(self, snapshot_time: str, trade_date: str, session_type: str, rows: list[dict]) -> None:
        for row in rows:
            self.history_rows.append(
                {
                    "snapshot_time": snapshot_time,
                    "trade_date": trade_date,
                    "session_type": session_type,
                    "stock_code": row["stock_code"],
                }
            )

    def get_history(self, trade_date: str, session_type: str) -> list[dict]:
        return [
            dict(row)
            for row in self.history_rows
            if row["trade_date"] == trade_date and row["session_type"] == session_type
        ]


class FakePoolManager:
    def __init__(self, snapshots=None, error=None):
        self.snapshots = list(snapshots or [])
        self.error = error
        self.calls = 0
        self.seed_calls = 0

    def seed_universe(self):
        self.seed_calls += 1

    def refresh_pools(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshots.pop(0)


class AppRunnerDetailTests(unittest.TestCase):
    def test_get_cached_stock_detail_uses_local_cache_only(self):
        cached_daily = [
            Candle("000001", "2026-05-29", 10.0, 10.2, 9.9, 10.1, 1000, 10000),
            Candle("000001", "2026-05-30", 10.1, 10.4, 10.0, 10.3, 1200, 12000),
        ]
        cached_minute = [
            Candle("000001", "2026-05-29 09:31:00", 10.0, 10.1, 9.9, 10.0, 100, 1000),
            Candle("000001", "2026-05-30 09:31:00", 10.1, 10.3, 10.0, 10.2, 100, 1000),
        ]
        strategy_runner = FakeStrategyRunner(cached_bars=cached_minute, fetched_bars=[])
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=None,
            strategy_runner=strategy_runner,
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
        )
        app.state.snapshot_map["000001"] = StockSnapshot(
            code="000001",
            name="Ping An",
            last_price=10.2,
            pct_chg=1.2,
            amount=1000000,
            volume=100000,
            turnover_rate=2.0,
            high=10.3,
            low=9.9,
            open=10.0,
            market="sz",
            security_type="stock",
            updated_at="2026-05-30 10:00:00",
        )

        detail = app.get_cached_stock_detail("000001")

        self.assertEqual(["2026-05-29", "2026-05-30"], detail["available_minute_dates"])
        self.assertEqual("2026-05-30", detail["selected_date"])
        self.assertEqual(2, len(detail["daily_bars"]))
        self.assertEqual(1, len(detail["minute_bars"]))
        self.assertEqual([("000001", 800)], strategy_runner.cached_calls)
        self.assertEqual([], strategy_runner.fetch_calls)
        self.assertEqual([], app.provider.daily_calls)

    def test_pool_refresh_backoff_grows_after_failure(self):
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(error=RuntimeError("limit")),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={
                "scan": {
                    "pool_refresh_seconds": 60,
                    "pool_refresh_jitter_seconds": 0,
                    "pool_refresh_backoff_multiplier": 2.0,
                    "pool_refresh_max_seconds": 300,
                }
            },
        )

        with self.assertRaisesRegex(RuntimeError, "limit"):
            app.refresh_pools()

        self.assertEqual(120000, app.get_next_pool_refresh_delay_ms())
        self.assertEqual(1, app.pool_refresh_failures)

    def test_pool_refresh_backoff_resets_after_success(self):
        snapshot = type(
            "Snapshot",
            (),
            {"monitor_pool": ["m"], "focus_pool": ["f"], "snapshot_map": {"000001": "s"}},
        )()
        failing_manager = FakePoolManager(error=RuntimeError("limit"))
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=failing_manager,
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={
                "scan": {
                    "pool_refresh_seconds": 60,
                    "pool_refresh_jitter_seconds": 0,
                    "pool_refresh_backoff_multiplier": 2.0,
                    "pool_refresh_max_seconds": 300,
                }
            },
        )

        with self.assertRaises(RuntimeError):
            app.refresh_pools()

        app.pool_manager = FakePoolManager(snapshots=[snapshot])
        result = app.refresh_pools()

        self.assertIs(result, snapshot)
        self.assertEqual(60000, app.get_next_pool_refresh_delay_ms())
        self.assertEqual(0, app.pool_refresh_failures)
        self.assertEqual(["m"], app.state.monitor_pool)

    def test_get_stock_detail_appends_live_daily_bar_for_today_snapshot(self):
        cached_daily = [
            Candle("000001", f"2026-04-{idx + 1:02d}", 10.0, 10.4, 9.8, 10.1, 1000 + idx, 10000 + idx)
            for idx in range(30)
        ] + [
            Candle("000001", f"2026-05-{idx + 1:02d}", 11.0, 11.4, 10.8, 11.1, 2000 + idx, 20000 + idx)
            for idx in range(29)
        ]
        cached_daily.append(Candle("000001", "2026-05-29", 12.0, 12.4, 11.8, 12.1, 3000, 30000))
        minute_bars = [
            Candle("000001", "2026-06-01 09:31:00", 12.2, 12.5, 12.1, 12.4, 100, 1200),
            Candle("000001", "2026-06-01 09:32:00", 12.4, 12.7, 12.3, 12.6, 120, 1500),
        ]
        strategy_runner = FakeStrategyRunner(cached_bars=[], fetched_bars=minute_bars)
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=strategy_runner,
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
        )
        app.state.snapshot_map["000001"] = StockSnapshot(
            code="000001",
            name="Ping An",
            last_price=12.6,
            pct_chg=4.13,
            amount=5200000,
            volume=220,
            turnover_rate=2.0,
            high=12.7,
            low=12.1,
            open=12.2,
            market="sz",
            security_type="stock",
            updated_at="2026-06-01 10:00:00",
        )

        detail = app.get_stock_detail("000001")

        self.assertEqual("2026-06-01", detail["daily_bars"][-1].ts)
        self.assertEqual(12.6, detail["daily_bars"][-1].close)
        self.assertEqual(12.2, detail["daily_bars"][-1].open)

    def test_get_stock_detail_preserves_recent_tencent_days_and_extends_older_history(self):
        primary_minute = [
            Candle("000001", "2026-05-26 09:31:00", 10.0, 10.1, 9.9, 10.0, 100, 1000),
            Candle("000001", "2026-05-27 09:31:00", 10.1, 10.2, 10.0, 10.1, 100, 1000),
            Candle("000001", "2026-05-28 09:31:00", 10.2, 10.3, 10.1, 10.2, 100, 1000),
            Candle("000001", "2026-05-29 09:31:00", 10.3, 10.4, 10.2, 10.3, 100, 1000),
        ]
        historical_minute = [
            Candle("000001", "2026-05-20 09:35:00", 9.5, 9.6, 9.4, 9.5, 80, 800),
            Candle("000001", "2026-05-21 09:35:00", 9.6, 9.7, 9.5, 9.6, 80, 800),
            Candle("000001", "2026-05-22 09:35:00", 9.7, 9.8, 9.6, 9.7, 80, 800),
            Candle("000001", "2026-05-23 09:35:00", 9.8, 9.9, 9.7, 9.8, 80, 800),
            Candle("000001", "2026-05-26 09:35:00", 99.0, 99.0, 99.0, 99.0, 1, 1),
        ]
        strategy_runner = FakeStrategyRunner(cached_bars=[], fetched_bars=primary_minute)
        historical_provider = FakeHistoricalMinuteProvider(historical_minute)
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=strategy_runner,
            daily_repo=FakeDailyRepo(),
            settings={"scan": {"enable_historical_minute_extension": True}},
            historical_minute_provider=historical_provider,
        )
        app.state.snapshot_map["000001"] = StockSnapshot(
            code="000001",
            name="Ping An",
            last_price=10.3,
            pct_chg=1.2,
            amount=1000000,
            volume=100000,
            turnover_rate=2.0,
            high=10.4,
            low=9.9,
            open=10.0,
            market="sz",
            security_type="stock",
            updated_at="2026-05-29 10:00:00",
        )

        detail = app.get_stock_detail("000001")

        self.assertEqual(
            ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"],
            detail["available_minute_dates"],
        )
        self.assertEqual(8, len(detail["available_minute_dates"]))
        self.assertEqual(10.0, detail["minute_bars_by_date"]["2026-05-26"][0].close)
        self.assertEqual(1, len(historical_provider.calls))

    def test_get_stock_detail_skips_historical_extension_when_disabled(self):
        primary_minute = [
            Candle("000001", "2026-05-26 09:31:00", 10.0, 10.1, 9.9, 10.0, 100, 1000),
            Candle("000001", "2026-05-27 09:31:00", 10.1, 10.2, 10.0, 10.1, 100, 1000),
            Candle("000001", "2026-05-28 09:31:00", 10.2, 10.3, 10.1, 10.2, 100, 1000),
            Candle("000001", "2026-05-29 09:31:00", 10.3, 10.4, 10.2, 10.3, 100, 1000),
        ]
        strategy_runner = FakeStrategyRunner(cached_bars=[], fetched_bars=primary_minute)
        historical_provider = FakeHistoricalMinuteProvider(
            [Candle("000001", "2026-05-20 09:35:00", 9.5, 9.6, 9.4, 9.5, 80, 800)]
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=strategy_runner,
            daily_repo=FakeDailyRepo(),
            settings={"scan": {"enable_historical_minute_extension": False}},
            historical_minute_provider=historical_provider,
        )

        detail = app.get_stock_detail("000001")

        self.assertEqual(["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"], detail["available_minute_dates"])
        self.assertEqual([], historical_provider.calls)

    def test_build_replay_candidate_ranking_uses_saved_snapshots(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        cached_daily = [
            Candle("300308", f"2026-05-{idx + 1:02d}", 90 + idx, 92 + idx, 89 + idx, 91 + idx, 10000 + idx * 400, (91 + idx) * (10000 + idx * 400), 0.0)
            for idx in range(20)
        ]
        cached_daily[-1] = Candle("300308", "2026-05-20", 108, 113, 107, 112, 32000, 3584000, 3.70)
        rank_repo = FakeRankSnapshotRepo()
        rank_repo.replace_snapshot(
            "2026-05-30",
            "monitor_close",
            [{"rank_no": 6, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 3.7, "amount": 3584000, "extra": {}}],
        )
        rank_repo.replace_snapshot(
            "2026-05-30",
            "ths_hourly_close",
            [{"rank_no": 12, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 3.7, "amount": 98, "extra": {}}],
        )
        rank_repo.replace_snapshot(
            "2026-05-30",
            "ths_value_close",
            [{"rank_no": 15, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 0.0, "amount": 0.0, "extra": {}}],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
            rank_snapshot_repo=rank_repo,
            candidate_score_repo=FakeCandidateScoreRepo(),
            candidate_scoring_service=CandidateScoringService(),
        )

        rows = app.build_replay_candidate_ranking("2026-05-30")

        self.assertEqual(1, len(rows))
        self.assertEqual("300308", rows[0]["stock_code"])
        self.assertGreater(rows[0]["total_score"], 0)
        self.assertEqual(15, rows[0]["metrics"]["ths_value_rank_yesterday"])

    def test_build_replay_candidate_ranking_uses_trade_date_daily_cutoff(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        cached_daily = [
            Candle("300308", "2026-05-24", 95, 98, 94, 97, 9000, 873000, 2.1),
            Candle("300308", "2026-05-25", 97, 100, 96, 99, 9200, 910800, 2.06),
            Candle("300308", "2026-05-26", 99, 101, 98, 100, 9500, 950000, 1.01),
            Candle("300308", "2026-05-27", 100, 102, 99, 101, 9800, 989800, 1.0),
            Candle("300308", "2026-05-28", 101, 103, 100, 102, 10000, 1020000, 0.99),
            Candle("300308", "2026-05-29", 102, 106, 101, 105, 14000, 1470000, 2.94),
            Candle("300308", "2026-05-30", 105, 109, 104, 108, 18000, 1944000, 2.86),
            Candle("300308", "2026-06-02", 108, 120, 107, 118, 60000, 7080000, 9.26),
        ]
        rank_repo = FakeRankSnapshotRepo()
        rank_repo.replace_snapshot(
            "2026-05-30",
            "monitor_close",
            [{"rank_no": 6, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 2.86, "amount": 1944000, "extra": {}}],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
            rank_snapshot_repo=rank_repo,
            candidate_score_repo=FakeCandidateScoreRepo(),
            candidate_scoring_service=CandidateScoringService(),
        )

        rows = app.build_replay_candidate_ranking("2026-05-30")

        self.assertEqual(1, len(rows))
        self.assertEqual(108, rows[0]["metrics"]["reference_price"])
        self.assertAlmostEqual(2.86, rows[0]["metrics"]["day_pct"], places=2)

    def test_build_intraday_candidate_ranking_combines_today_and_yesterday_context(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        cached_daily = [
            Candle("300308", f"2026-05-{idx + 1:02d}", 90 + idx, 92 + idx, 89 + idx, 91 + idx, 10000 + idx * 400, (91 + idx) * (10000 + idx * 400), 0.0)
            for idx in range(20)
        ]
        cached_daily[-1] = Candle("300308", "2026-05-20", 108, 113, 107, 112, 32000, 3584000, 3.70)
        rank_repo = FakeRankSnapshotRepo()
        rank_repo.latest_by_type["monitor_close"] = [
            {"rank_no": 9, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 3.7, "amount": 3584000, "extra": {}}
        ]
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
            rank_snapshot_repo=rank_repo,
            candidate_score_repo=FakeCandidateScoreRepo(),
            candidate_scoring_service=CandidateScoringService(),
        )
        app.state.monitor_pool = [
            type("MonitorRow", (), {"code": "300308", "name": "中际旭创", "amount": 33892000000.0, "pct_chg": 3.1})()
        ]
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=112.0,
            pct_chg=3.1,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=2.0,
            high=113.0,
            low=107.0,
            open=108.0,
            market="sz",
            security_type="stock",
            updated_at="2026-05-31 10:00:00",
        )

        rows = app.build_intraday_candidate_ranking(
            ths_rows=[type("THSRow", (), {"code": "300308", "name": "中际旭创", "order": 10})()],
            kpl_rows=[],
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("300308", rows[0]["stock_code"])

    def test_save_intraday_candidate_snapshots_appends_history_once_per_minute(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        cached_daily = [
            Candle("300308", f"2026-05-{idx + 1:02d}", 90 + idx, 92 + idx, 89 + idx, 91 + idx, 10000 + idx * 400, (91 + idx) * (10000 + idx * 400), 0.0)
            for idx in range(20)
        ]
        cached_daily[-1] = Candle("300308", "2026-05-20", 108, 113, 107, 112, 32000, 3584000, 3.70)
        rank_repo = FakeRankSnapshotRepo()
        rank_repo.latest_by_type["monitor_close"] = [
            {"rank_no": 9, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 3.7, "amount": 3584000, "extra": {}}
        ]
        candidate_repo = FakeCandidateScoreRepo()
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(cached_daily),
            settings={"scan": {}},
            rank_snapshot_repo=rank_repo,
            candidate_score_repo=candidate_repo,
            candidate_scoring_service=CandidateScoringService(),
        )
        app.state.monitor_pool = [
            type("MonitorRow", (), {"code": "300308", "name": "中际旭创", "amount": 33892000000.0, "pct_chg": 3.1, "turnover_rate": 4.2})()
        ]
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=112.0,
            pct_chg=3.1,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=4.2,
            high=113.0,
            low=107.0,
            open=108.0,
            market="sz",
            security_type="stock",
            updated_at="2026-05-31 10:00:00",
        )

        with patch("src.core.app_runner.is_trading_session", return_value=True):
            app.save_intraday_candidate_snapshots_if_needed(now=datetime(2026, 5, 31, 10, 5, 0), ths_rows=[], kpl_rows=[])
            app.save_intraday_candidate_snapshots_if_needed(now=datetime(2026, 5, 31, 10, 5, 30), ths_rows=[], kpl_rows=[])
            app.save_intraday_candidate_snapshots_if_needed(now=datetime(2026, 5, 31, 10, 6, 0), ths_rows=[], kpl_rows=[])

        history = candidate_repo.get_history("2026-05-31", "intraday")

        self.assertEqual(2, len(history))
        self.assertEqual("2026-05-31 10:05:00", history[0]["snapshot_time"])
        self.assertEqual("2026-05-31 10:06:00", history[1]["snapshot_time"])

    def test_save_daily_snapshots_skips_non_trading_day(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        rank_repo = FakeRankSnapshotRepo()
        candidate_repo = FakeCandidateScoreRepo()
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={"scan": {}},
            rank_snapshot_repo=rank_repo,
            candidate_score_repo=candidate_repo,
            candidate_scoring_service=CandidateScoringService(),
        )
        app.state.monitor_pool = [
            type("MonitorRow", (), {"code": "300308", "name": "中际旭创", "amount": 33892000000.0, "pct_chg": 3.1, "turnover_rate": 4.2})()
        ]

        app.save_daily_snapshots_if_needed(
            ths_hourly_rows=[],
            ths_value_rows=[],
            now=datetime(2026, 6, 6, 15, 5, 0),
        )

        self.assertEqual({}, rank_repo.rows_by_key)
        self.assertEqual("", app._last_snapshot_save_date)

    def test_export_candidate_review_csv_writes_scores_metrics_and_forward_perf(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-01",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": ["breakout", "strong_close"],
                    "risks": ["overheat"],
                    "metrics": {"reference_price": 112.0, "vol_ratio_5": 1.82, "close_strength": 0.76},
                }
            ],
        )
        future_bars = [
            Candle("300308", "2026-06-01", 108, 113, 107, 112, 32000, 3584000, 3.70),
            Candle("300308", "2026-06-02", 112, 118, 111, 116, 36000, 4176000, 3.57),
        ]
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(future_bars),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )
        app.provider.get_daily_bars = lambda *_args, **_kwargs: []
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=117.6,
            pct_chg=1.38,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=4.2,
            high=118.0,
            low=111.0,
            open=112.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-03 10:00:00",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "replay_2026-06-01.csv"
            exported = app.export_candidate_review_csv("replay", "2026-06-01", output_path)

            self.assertEqual(output_path, exported)
            with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(1, len(rows))
        self.assertEqual("300308", rows[0]["stock_code"])
        self.assertEqual("1", rows[0]["rank_no"])
        self.assertEqual("3.57", rows[0]["next_day_pct"])
        self.assertEqual("close", rows[0]["next_day_mode"])
        self.assertEqual("5.0", rows[0]["label_live_pct"])
        self.assertEqual("1", rows[0]["label_live_up"])
        self.assertEqual("1", rows[0]["label_live_strong"])
        self.assertEqual("1.0", rows[0]["label_live_rank_pct"])
        self.assertEqual("breakout|strong_close", rows[0]["flags"])
        self.assertEqual("1.82", rows[0]["metric_vol_ratio_5"])
        self.assertEqual("0.76", rows[0]["metric_close_strength"])

    def test_export_candidate_review_csv_assigns_live_rank_percentiles(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-01",
            "replay",
            [
                {
                    "stock_code": "000001",
                    "stock_name": "平安银行",
                    "total_score": 90,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 30,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 10.0},
                },
                {
                    "stock_code": "000002",
                    "stock_name": "万科A",
                    "total_score": 70,
                    "grade": "B",
                    "heat_score": 18,
                    "market_cap_score": 6,
                    "volume_price_score": 22,
                    "position_score": 20,
                    "risk_penalty": 4,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 20.0},
                },
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )
        app.provider.get_daily_bars = lambda *_args, **_kwargs: []
        app.state.snapshot_map["000001"] = StockSnapshot(
            code="000001",
            name="平安银行",
            last_price=10.5,
            pct_chg=0.0,
            amount=1.0,
            volume=1.0,
            turnover_rate=1.0,
            high=10.5,
            low=10.0,
            open=10.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-03 10:00:00",
        )
        app.state.snapshot_map["000002"] = StockSnapshot(
            code="000002",
            name="万科A",
            last_price=19.0,
            pct_chg=0.0,
            amount=1.0,
            volume=1.0,
            turnover_rate=1.0,
            high=20.0,
            low=19.0,
            open=20.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-03 10:00:00",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "replay_training.csv"
            app.export_candidate_review_csv("replay", "2026-06-01", output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        by_code = {row["stock_code"]: row for row in rows}
        self.assertEqual("1.0", by_code["000001"]["label_live_rank_pct"])
        self.assertEqual("0.0", by_code["000002"]["label_live_rank_pct"])

    def test_export_candidate_review_csv_includes_new_metric_columns_from_scored_rows(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        bars = [
            Candle("300308", "2026-05-20", 90, 93, 89, 92, 10000, 920000, 0.0),
            Candle("300308", "2026-05-21", 92, 94, 91, 93, 11000, 1023000, 1.09),
            Candle("300308", "2026-05-22", 93, 95, 92, 94, 12000, 1128000, 1.08),
            Candle("300308", "2026-05-23", 94, 97, 93, 96, 13000, 1248000, 2.13),
            Candle("300308", "2026-05-26", 96, 99, 95, 98, 13500, 1323000, 2.08),
            Candle("300308", "2026-05-27", 98, 102, 97, 101, 16000, 1616000, 3.06),
            Candle("300308", "2026-05-28", 101, 106, 100, 105, 18500, 1942500, 3.96),
            Candle("300308", "2026-05-29", 105, 109, 104, 108, 21000, 2268000, 2.86),
            Candle("300308", "2026-05-30", 108, 113, 107, 112, 32000, 3584000, 3.70),
        ]
        scored_row = CandidateScoringService().score_replay_candidate(
            "300308",
            "中际旭创",
            bars,
            {"monitor_rank_yesterday": 6, "ths_rank_yesterday": 12, "kpl_rank_yesterday": 18},
        )
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores("2026-06-01", "replay", [scored_row])
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(bars),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )
        app.provider.get_daily_bars = lambda *_args, **_kwargs: []
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=117.6,
            pct_chg=4.09,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=4.2,
            high=118.0,
            low=111.0,
            open=112.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-01 10:41:35",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "replay_new_metrics.csv"
            app.export_candidate_review_csv("replay", "2026-06-01", output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertIn("metric_day_amplitude", rows[0])
        self.assertIn("metric_body_ratio", rows[0])
        self.assertIn("metric_signed_body_pct", rows[0])
        self.assertIn("metric_breakout_gap_20", rows[0])
        self.assertIn("metric_amount_continuity_2d", rows[0])

    def test_export_candidate_review_csv_uses_same_day_live_snapshot_for_labels(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-01",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": ["breakout"],
                    "risks": [],
                    "metrics": {"reference_price": 112.0},
                }
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=117.6,
            pct_chg=4.09,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=4.2,
            high=118.0,
            low=111.0,
            open=112.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-01 10:41:35",
        )

        with patch("src.core.app_runner.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 1, 10, 42, 0)
            mock_datetime.strptime = datetime.strptime
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "replay_same_day.csv"
                app.export_candidate_review_csv("replay", "2026-06-01", output_path)
                with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                    rows = list(csv.DictReader(csv_file))

        self.assertEqual("5.0", rows[0]["label_live_pct"])
        self.assertEqual("1", rows[0]["label_live_up"])
        self.assertEqual("1", rows[0]["label_live_strong"])

    def test_export_candidate_review_csv_leaves_binary_labels_blank_without_live_snapshot(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-01",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 112.0},
                }
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "replay_empty_labels.csv"
            app.export_candidate_review_csv("replay", "2026-06-01", output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual("", rows[0]["label_live_pct"])
        self.assertEqual("", rows[0]["label_live_up"])
        self.assertEqual("", rows[0]["label_live_strong"])
        self.assertEqual("", rows[0]["label_live_rank_pct"])

    def test_get_candidate_review_rows_reuses_saved_replay_rows_and_reranks(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-03",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"factor_features": {}},
                }
            ],
        )

        class FakeScoringService:
            def __init__(self):
                self.calls = 0

            def rerank_replay_rows(self, rows):
                self.calls += 1
                reranked = [dict(row) for row in rows]
                reranked[0]["total_score"] = 99
                return reranked

        scoring_service = FakeScoringService()
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo([Candle("300308", "2026-06-03", 110, 113, 109, 112, 1000, 10000)]),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
            candidate_scoring_service=scoring_service,
        )

        rows = app.get_candidate_review_rows("replay", "2026-06-03")

        self.assertEqual(1, scoring_service.calls)
        self.assertEqual(99, rows[0]["total_score"])

    def test_get_candidate_review_rows_uses_current_snapshot_only_for_yesterdays_replay(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-03",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 112.0},
                }
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(
                market_snapshots=[
                    StockSnapshot(
                        code="300308",
                        name="中际旭创",
                        last_price=117.6,
                        pct_chg=4.09,
                        amount=33892000000.0,
                        volume=100000,
                        turnover_rate=4.2,
                        high=118.0,
                        low=111.0,
                        open=112.0,
                        market="sz",
                        security_type="stock",
                        updated_at="2026-06-04 10:41:35",
                    )
                ]
            ),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(
                [
                    Candle("300308", "2026-06-03", 110, 113, 109, 112, 1000, 10000),
                ]
            ),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )

        with patch("src.core.app_runner.datetime") as mock_datetime, patch("src.core.app_runner.is_trading_session") as mock_session:
            mock_datetime.now.return_value = datetime(2026, 6, 4, 10, 42, 0)
            mock_datetime.strptime = datetime.strptime
            mock_session.return_value = True
            rows = app.get_candidate_review_rows("replay", "2026-06-03")

        self.assertEqual(1, len(rows))
        self.assertEqual(5.0, rows[0]["next_day_pct"])
        self.assertEqual("current", rows[0]["next_day_mode"])
        self.assertEqual("2026-06-04", rows[0]["next_trade_date"])

    def test_get_candidate_review_rows_prefers_current_snapshot_over_intraday_daily_bar_for_yesterdays_replay(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-03",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "涓檯鏃垱",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 112.0},
                }
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(
                market_snapshots=[
                    StockSnapshot(
                        code="300308",
                        name="涓檯鏃垱",
                        last_price=117.6,
                        pct_chg=4.09,
                        amount=33892000000.0,
                        volume=100000,
                        turnover_rate=4.2,
                        high=118.0,
                        low=111.0,
                        open=112.0,
                        market="sz",
                        security_type="stock",
                        updated_at="2026-06-04 11:21:53",
                    )
                ]
            ),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(
                [
                    Candle("300308", "2026-06-03", 110, 113, 109, 112, 1000, 10000),
                    Candle("300308", "2026-06-04", 112, 114, 111, 113.5, 1000, 10000),
                ]
            ),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )

        with patch("src.core.app_runner.datetime") as mock_datetime, patch("src.core.app_runner.is_trading_session") as mock_session:
            mock_datetime.now.return_value = datetime(2026, 6, 4, 11, 22, 0)
            mock_datetime.strptime = datetime.strptime
            mock_session.return_value = True
            rows = app.get_candidate_review_rows("replay", "2026-06-03")

        self.assertEqual(1, len(rows))
        self.assertEqual(5.0, rows[0]["next_day_pct"])
        self.assertEqual("current", rows[0]["next_day_mode"])
        self.assertEqual("2026-06-04", rows[0]["next_trade_date"])

    def test_get_candidate_review_rows_uses_close_for_older_replay_even_with_current_snapshot(self):
        candidate_repo = FakeCandidateScoreRepo()
        candidate_repo.replace_scores(
            "2026-06-02",
            "replay",
            [
                {
                    "stock_code": "300308",
                    "stock_name": "中际旭创",
                    "total_score": 88,
                    "grade": "A",
                    "heat_score": 20,
                    "market_cap_score": 10,
                    "volume_price_score": 28,
                    "position_score": 24,
                    "risk_penalty": 6,
                    "flags": [],
                    "risks": [],
                    "metrics": {"reference_price": 112.0},
                }
            ],
        )
        app = AppRunner(
            logger=FakeLogger(),
            provider=FakeProvider(),
            pool_manager=FakePoolManager(),
            strategy_runner=FakeStrategyRunner(),
            daily_repo=FakeDailyRepo(
                [
                    Candle("300308", "2026-06-02", 108, 110, 107, 108, 1000, 10000),
                    Candle("300308", "2026-06-03", 110, 113, 109, 112, 1000, 10000),
                ]
            ),
            settings={"scan": {}},
            candidate_score_repo=candidate_repo,
        )
        app.state.snapshot_map["300308"] = StockSnapshot(
            code="300308",
            name="中际旭创",
            last_price=117.6,
            pct_chg=4.09,
            amount=33892000000.0,
            volume=100000,
            turnover_rate=4.2,
            high=118.0,
            low=111.0,
            open=112.0,
            market="sz",
            security_type="stock",
            updated_at="2026-06-04 10:41:35",
        )

        with patch("src.core.app_runner.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 4, 10, 42, 0)
            mock_datetime.strptime = datetime.strptime
            rows = app.get_candidate_review_rows("replay", "2026-06-02")

        self.assertEqual(1, len(rows))
        self.assertEqual(3.7, rows[0]["next_day_pct"])
        self.assertEqual("close", rows[0]["next_day_mode"])
        self.assertEqual("2026-06-03", rows[0]["next_trade_date"])


class SimpleLineChartTests(unittest.TestCase):
    def test_chart_defaults_to_latest_window_on_first_render(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = SimpleLineChart(root, title="test")
            chart.frame.pack()
            root.update_idletasks()

            bars = [
                Candle("000001", f"2026-05-{idx + 1:02d}", 10.0, 10.5, 9.8, 10.1 + idx * 0.01, 1000 + idx, 10000 + idx)
                for idx in range(40)
            ]

            chart.render(bars, selected_date="2026-05-40")

            visible_dates = [point[2].ts for point in chart._points]
            self.assertEqual("2026-05-11", visible_dates[0])
            self.assertEqual("2026-05-40", visible_dates[-1])
        finally:
            root.destroy()

    def test_daily_tooltip_uses_derived_amount_and_pct_when_missing(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = SimpleLineChart(root, title="test")
            bars = [
                Candle("000001", "2026-05-29", 9.8, 10.2, 9.7, 10.0, 10000, 0.0, 0.0),
                Candle("000001", "2026-05-30", 10.0, 10.4, 9.9, 10.3, 12000, 0.0, 0.0),
            ]
            chart.render(bars, selected_date="2026-05-30")

            lines = chart._build_tooltip_lines(bars[1])

            self.assertIn("额 12.36 万", lines)
            self.assertIn("涨跌 +3.00%", lines)
        finally:
            root.destroy()

    def test_render_respects_zoom_state_with_same_bars(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = SimpleLineChart(root, title="test")
            chart.frame.pack()
            root.update_idletasks()

            bars = [
                Candle("000001", f"2026-05-{idx + 1:02d}", 10.0, 10.5, 9.8, 10.1 + idx * 0.01, 1000 + idx, 10000 + idx)
                for idx in range(40)
            ]

            chart.render(bars, selected_date="2026-05-40")
            self.assertEqual(30, len(chart._points))

            chart._view_size = 20
            chart.render(bars, selected_date="2026-05-40")

            self.assertEqual(20, len(chart._points))
        finally:
            root.destroy()

    def test_chart_rerenders_when_canvas_size_changes(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = SimpleLineChart(root, title="test")
            chart.frame.pack(fill="both", expand=True)
            root.update_idletasks()

            bars = [
                Candle("000001", f"2026-05-{idx + 1:02d}", 10.0, 10.5, 9.8, 10.1 + idx * 0.01, 1000 + idx, 10000 + idx)
                for idx in range(20)
            ]

            original_winfo_width = chart.canvas.winfo_width
            original_winfo_height = chart.canvas.winfo_height
            chart.canvas.winfo_width = lambda: 340
            chart.canvas.winfo_height = lambda: 240
            chart.render(bars, selected_date="2026-05-20")
            first_max_x = max(point[0] for point in chart._points)

            chart.canvas.winfo_width = lambda: 520
            chart.canvas.winfo_height = lambda: 240
            chart._on_canvas_configure(None)
            second_max_x = max(point[0] for point in chart._points)

            self.assertGreater(second_max_x, first_max_x)
            chart.canvas.winfo_width = original_winfo_width
            chart.canvas.winfo_height = original_winfo_height
        finally:
            root.destroy()


class SimpleMinuteChartTests(unittest.TestCase):
    def test_render_handles_single_bar_without_tclerror(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = SimpleMinuteChart(root, title="test")
            chart.frame.pack(fill="both", expand=True)
            root.update_idletasks()

            bars = [
                Candle("000001", "2026-06-03 09:30:00", 10.0, 10.2, 9.9, 10.1, 1000, 10000),
            ]

            chart.render(bars, selected_date="2026-06-03")

            self.assertEqual(1, len(chart._points))
            self.assertIn("2026-06-03", chart.info_var.get())
        finally:
            root.destroy()


class ReviewDateUiTests(unittest.TestCase):
    def test_on_review_date_selected_loads_review_and_selects_first_row(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.review_date_var = tk.StringVar(master=root, value="2026-05-30")
            window.review_trade_date = ""
            window.monitor_rows = [{"stock_code": "300308"}]

            refresh_calls = []
            selected_codes = []
            status_messages = []
            window._refresh_hot_tree = lambda: refresh_calls.append("refresh")
            window._select_stock = lambda code: selected_codes.append(code)
            window._update_status = lambda message: status_messages.append(message)

            window._on_review_date_selected()

            self.assertEqual("2026-05-30", window.review_trade_date)
            self.assertEqual(["refresh"], refresh_calls)
            self.assertEqual(["300308"], selected_codes)
            self.assertEqual([], status_messages)
        finally:
            root.destroy()

    def test_format_candidate_amount_value_includes_intraday_pct_and_tags(self):
        row = {
            "grade": "B",
            "flags": ["成交额榜前10", "温和放量"],
            "metrics": {"intraday_pct_chg": 3.21},
        }

        text = QuantDaAMainWindow._format_candidate_amount_value("intraday_candidate", row, review_trade_date="")

        self.assertEqual("B | +3.21% | 成交额榜前10、温和放量", text)

    def test_format_candidate_amount_value_includes_live_pct_for_replay_candidate(self):
        row = {
            "grade": "A",
            "flags": ["breakout", "strong_close"],
            "metrics": {"live_pct_chg": 1.86},
        }

        text = QuantDaAMainWindow._format_candidate_amount_value("replay_candidate", row, review_trade_date="")

        self.assertEqual("A | +1.86% | breakout、strong_close", text)

    def test_save_daily_snapshots_refreshes_ths_before_first_close_save(self):
        class FakeSnapshotRunner:
            def __init__(self):
                self.should_calls = []
                self.save_calls = []

            def should_save_daily_snapshots(self, now):
                self.should_calls.append(now)
                return True

            def save_daily_snapshots_if_needed(self, **kwargs):
                self.save_calls.append(kwargs)

        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.app_runner = FakeSnapshotRunner()
            window.ths_hourly_hot = []
            window.ths_value_hot = []
            window._ths_last_update = ""

            def load_hourly():
                window.ths_hourly_hot = ["hourly"]

            def load_value():
                window.ths_value_hot = ["value"]

            window._load_ths_hourly_hot = load_hourly
            window._load_ths_value_hot = load_value

            current = datetime(2026, 6, 1, 15, 2, 0)
            window._save_daily_snapshots(now=current)

            self.assertEqual(["hourly"], window.ths_hourly_hot)
            self.assertEqual(["value"], window.ths_value_hot)
            self.assertEqual("15:02:00", window._ths_last_update)
            self.assertEqual(current, window.app_runner.should_calls[0])
            self.assertEqual(["hourly"], window.app_runner.save_calls[0]["ths_hourly_rows"])
            self.assertEqual(["value"], window.app_runner.save_calls[0]["ths_value_rows"])
            self.assertEqual(current, window.app_runner.save_calls[0]["now"])
        finally:
            root.destroy()

    def test_refresh_intraday_candidate_fast_refreshes_pool_and_tree(self):
        class FakeIntradayRunner:
            def __init__(self):
                self.refresh_calls = 0
                self.save_calls = []

            def refresh_pools(self):
                self.refresh_calls += 1

            def save_intraday_candidate_snapshots_if_needed(self, **kwargs):
                self.save_calls.append(kwargs)

        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.root = root
            window.app_runner = FakeIntradayRunner()
            window.ths_hourly_hot = ["hourly"]
            window.selected_stock_code = None
            window.monitor_rows = []
            refresh_calls = []
            status_messages = []
            window._refresh_hot_tree = lambda: refresh_calls.append("refresh")
            window._render_stock_detail = lambda *_args, **_kwargs: None
            window._update_status = lambda message: status_messages.append(message)

            window._refresh_intraday_candidate_fast()

            self.assertEqual(1, window.app_runner.refresh_calls)
            self.assertEqual(["refresh"], refresh_calls)
            self.assertEqual(["hourly"], window.app_runner.save_calls[0]["ths_rows"])
            self.assertEqual([], window.app_runner.save_calls[0]["kpl_rows"])
            self.assertEqual([], status_messages)
        finally:
            root.destroy()

    def test_intraday_candidate_cycle_only_refreshes_when_mode_active(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.root = type(
                "FakeRoot",
                (),
                {"after": lambda _self, delay, callback: ("job", delay, callback.__name__)},
            )()
            window.rank_mode = tk.StringVar(master=root, value="monitor")
            window.review_trade_date = ""
            window._intraday_candidate_refresh_ms = 15000
            refresh_calls = []
            window._refresh_intraday_candidate_fast = lambda: refresh_calls.append("refresh")

            window._intraday_candidate_cycle()
            self.assertEqual([], refresh_calls)

            window.rank_mode.set("intraday_candidate")
            window._intraday_candidate_cycle()
            self.assertEqual(["refresh"], refresh_calls)
            self.assertEqual(("job", 15000, "_intraday_candidate_cycle"), window._intraday_candidate_job)
        finally:
            root.destroy()

    def test_on_candidate_profile_selected_switches_profile_and_refreshes(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.candidate_profile_var = tk.StringVar(master=root, value="replay_2026_06_01_strict_norm")
            from unittest.mock import MagicMock
            window.status_light = MagicMock()
            window._light_id = 1
            switched = []
            refresh_calls = []
            status_messages = []
            window.app_runner = type(
                "FakeRunner",
                (),
                {
                    "set_candidate_profile": lambda _self, profile: switched.append(profile),
                },
            )()
            window._refresh_hot_tree = lambda: refresh_calls.append("refresh")
            window._update_status = lambda message: status_messages.append(message)

            window._on_candidate_profile_selected()

            self.assertEqual(["replay_2026_06_01_strict_norm"], switched)
            self.assertEqual(["refresh"], refresh_calls)
            self.assertTrue(status_messages)
        finally:
            root.destroy()

    def test_load_review_date_keeps_selected_candidate_profile(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = QuantDaAMainWindow.__new__(QuantDaAMainWindow)
            window.review_date_var = tk.StringVar(master=root, value="2026-06-01")
            window.review_trade_date = ""
            window.rank_mode = tk.StringVar(master=root, value="replay_candidate")
            window.candidate_profile_var = tk.StringVar(master=root, value="replay_2026_06_02_strict_norm")
            window.monitor_rows = [{"stock_code": "300308"}]
            refresh_calls = []
            selected_codes = []
            status_messages = []
            switched = []
            window.app_runner = type(
                "FakeRunner",
                (),
                {
                    "set_candidate_profile": lambda _self, profile: switched.append(profile),
                },
            )()
            window._refresh_hot_tree = lambda: refresh_calls.append("refresh")
            window._select_stock = lambda code: selected_codes.append(code)
            window._update_status = lambda message: status_messages.append(message)

            window._load_review_date()

            self.assertEqual("2026-06-01", window.review_trade_date)
            self.assertEqual("replay_2026_06_02_strict_norm", window.candidate_profile_var.get())
            self.assertEqual([], switched)
            self.assertEqual(["refresh"], refresh_calls)
            self.assertEqual(["300308"], selected_codes)
            self.assertEqual([], status_messages)
        finally:
            root.destroy()


class AkshareHistoricalMinuteProviderTests(unittest.TestCase):
    def test_build_proxy_session_reads_environment_proxies_per_request(self):
        provider = AkshareHistoricalMinuteProvider()

        with patch("src.data_providers.akshare_provider.get_environ_proxies", return_value={"https": "http://127.0.0.1:7897"}):
            proxy_session = provider._build_proxy_session("https://push2his.eastmoney.com/api/test")

        self.assertIsNotNone(proxy_session)
        self.assertEqual("http://127.0.0.1:7897", proxy_session.proxies["https"])
        proxy_session.close()

    def test_build_proxy_session_returns_none_without_environment_proxy(self):
        provider = AkshareHistoricalMinuteProvider()

        with patch("src.data_providers.akshare_provider.get_environ_proxies", return_value={}):
            proxy_session = provider._build_proxy_session("https://push2his.eastmoney.com/api/test")

        self.assertIsNone(proxy_session)


class BootstrapTests(unittest.TestCase):
    def test_build_historical_minute_provider_disabled_by_default(self):
        with patch("src.bootstrap.AkshareHistoricalMinuteProvider") as provider_cls:
            provider = bootstrap._build_historical_minute_provider({"scan": {}}, FakeLogger())

        self.assertIsNone(provider)
        provider_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
