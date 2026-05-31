from __future__ import annotations

import unittest
import tkinter as tk
from datetime import datetime
from unittest.mock import patch

from src import bootstrap
from src.core.app_runner import AppRunner
from src.data_providers.akshare_provider import AkshareHistoricalMinuteProvider
from src.models.candle import Candle
from src.models.stock_snapshot import StockSnapshot
from src.ui.main_window import QuantDaAMainWindow, SimpleLineChart


class FakeLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


class FakeProvider:
    def __init__(self):
        self.daily_calls: list[tuple[str, int]] = []

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


class FakeDailyRepo:
    def __init__(self, bars=None):
        self.bars = list(bars or [])
        self.replace_calls: list[tuple[str, int]] = []

    def get_recent(self, _stock_code: str, limit: int):
        return self.bars[:limit]

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
