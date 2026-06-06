from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.models.candle import Candle
from src.repositories.database import Database


class RankSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.db.initialize()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_database_initializes_snapshot_tables(self):
        conn = self.db.connect()
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('rank_snapshots', 'candidate_score_snapshots', 'candidate_score_history')"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertEqual({"rank_snapshots", "candidate_score_snapshots", "candidate_score_history"}, tables)

    def test_database_initialize_adds_missing_market_cap_column(self):
        legacy_db = Database(Path(self._tmpdir.name) / "legacy.db")
        with legacy_db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE candidate_score_snapshots (
                    trade_date TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    heat_score INTEGER DEFAULT 0,
                    volume_price_score INTEGER DEFAULT 0,
                    position_score INTEGER DEFAULT 0,
                    risk_penalty INTEGER DEFAULT 0,
                    flags_json TEXT,
                    risks_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, session_type, stock_code)
                )
                """
            )

        legacy_db.initialize()

        with legacy_db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(candidate_score_snapshots)").fetchall()}

        self.assertIn("market_cap_score", columns)

    def test_rank_snapshot_repository_saves_and_reads_by_date_and_type(self):
        from src.repositories.rank_snapshot_repo import RankSnapshotRepository

        repo = RankSnapshotRepository(self.db)
        rows = [
            {
                "rank_no": 1,
                "stock_code": "300308",
                "stock_name": "中际旭创",
                "pct_chg": 5.21,
                "amount": 33892000000.0,
                "extra": {"source": "monitor"},
            },
            {
                "rank_no": 2,
                "stock_code": "002475",
                "stock_name": "立讯精密",
                "pct_chg": 3.18,
                "amount": 19879000000.0,
                "extra": {},
            },
        ]

        repo.replace_snapshot("2026-05-30", "monitor_close", rows)

        saved = repo.get_snapshot("2026-05-30", "monitor_close")

        self.assertEqual(2, len(saved))
        self.assertEqual("300308", saved[0]["stock_code"])
        self.assertEqual(1, saved[0]["rank_no"])
        self.assertEqual({"source": "monitor"}, saved[0]["extra"])
        self.assertEqual("002475", saved[1]["stock_code"])

    def test_rank_snapshot_repository_returns_latest_snapshot(self):
        from src.repositories.rank_snapshot_repo import RankSnapshotRepository

        repo = RankSnapshotRepository(self.db)
        repo.replace_snapshot(
            "2026-05-29",
            "ths_hourly_close",
            [{"rank_no": 1, "stock_code": "300308", "stock_name": "中际旭创", "pct_chg": 0.0, "amount": 0.0, "extra": {}}],
        )
        repo.replace_snapshot(
            "2026-05-30",
            "ths_hourly_close",
            [{"rank_no": 1, "stock_code": "002475", "stock_name": "立讯精密", "pct_chg": 0.0, "amount": 0.0, "extra": {}}],
        )

        latest = repo.get_latest_snapshot("ths_hourly_close")

        self.assertEqual("2026-05-30", latest[0]["trade_date"])
        self.assertEqual("002475", latest[0]["stock_code"])


class CandidateScoreRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.db.initialize()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_candidate_score_repository_saves_and_reads_scores(self):
        from src.repositories.candidate_score_repo import CandidateScoreRepository

        repo = CandidateScoreRepository(self.db)
        rows = [
            {
                "stock_code": "300308",
                "stock_name": "中际旭创",
                "total_score": 86,
                "grade": "A",
                "heat_score": 24,
                "market_cap_score": 8,
                "volume_price_score": 27,
                "position_score": 21,
                "risk_penalty": -4,
                "flags": ["成交额榜前10", "放量"],
                "risks": ["偏离MA5较大"],
                "metrics": {"vol_ratio_5": 1.82},
            }
        ]

        repo.replace_scores("2026-05-30", "replay", rows)

        saved = repo.get_scores("2026-05-30", "replay")

        self.assertEqual(1, len(saved))
        self.assertEqual("300308", saved[0]["stock_code"])
        self.assertEqual(["成交额榜前10", "放量"], saved[0]["flags"])
        self.assertEqual({"vol_ratio_5": 1.82}, saved[0]["metrics"])
        self.assertEqual(8, saved[0]["market_cap_score"])

    def test_candidate_score_repository_returns_latest_scores(self):
        from src.repositories.candidate_score_repo import CandidateScoreRepository

        repo = CandidateScoreRepository(self.db)
        repo.replace_scores(
            "2026-05-29",
            "replay",
            [{"stock_code": "300308", "stock_name": "中际旭创", "total_score": 80, "grade": "B", "heat_score": 20, "market_cap_score": 6, "volume_price_score": 24, "position_score": 20, "risk_penalty": -4, "flags": [], "risks": [], "metrics": {}}],
        )
        repo.replace_scores(
            "2026-05-30",
            "replay",
            [{"stock_code": "002475", "stock_name": "立讯精密", "total_score": 82, "grade": "B", "heat_score": 22, "market_cap_score": 8, "volume_price_score": 24, "position_score": 20, "risk_penalty": -4, "flags": [], "risks": [], "metrics": {}}],
        )

        latest = repo.get_latest_scores("replay")

        self.assertEqual("2026-05-30", latest[0]["trade_date"])
        self.assertEqual("002475", latest[0]["stock_code"])

    def test_candidate_score_repository_appends_history_snapshots(self):
        from src.repositories.candidate_score_repo import CandidateScoreRepository

        repo = CandidateScoreRepository(self.db)
        rows = [
            {
                "stock_code": "300308",
                "stock_name": "中际旭创",
                "total_score": 86,
                "grade": "A",
                "heat_score": 24,
                "market_cap_score": 8,
                "volume_price_score": 27,
                "position_score": 21,
                "risk_penalty": -4,
                "flags": ["成交额榜前10"],
                "risks": [],
                "metrics": {"vol_ratio_5": 1.82},
            }
        ]

        repo.append_history_snapshot("2026-05-30 10:05:00", "2026-05-30", "intraday", rows)
        repo.append_history_snapshot("2026-05-30 10:10:00", "2026-05-30", "intraday", rows)

        history = repo.get_history("2026-05-30", "intraday")

        self.assertEqual(2, len(history))
        self.assertEqual("2026-05-30 10:05:00", history[0]["snapshot_time"])
        self.assertEqual("2026-05-30 10:10:00", history[1]["snapshot_time"])


class CandidateScoringServiceTests(unittest.TestCase):
    def test_score_replay_candidate_returns_grade_and_flags(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService()
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
        rank_context = {
            "monitor_rank_yesterday": 6,
            "ths_rank_yesterday": 12,
            "ths_value_rank_yesterday": 15,
            "kpl_rank_yesterday": 18,
        }

        result = service.score_replay_candidate("300308", "中际旭创", bars, rank_context)

        self.assertEqual("300308", result["stock_code"])
        self.assertGreaterEqual(result["total_score"], 60)
        self.assertIn(result["grade"], {"A", "B", "C"})
        self.assertTrue(result["flags"])
        self.assertIn("同花顺价值榜#15", result["flags"])
        self.assertIn("vol_ratio_5", result["metrics"])
        self.assertIn("float_market_cap_est", result["metrics"])
        self.assertEqual(15, result["metrics"]["ths_value_rank_yesterday"])
        self.assertEqual(6, result["metrics"]["monitor_rank_yesterday"])
        self.assertEqual(12, result["metrics"]["ths_rank_yesterday"])
        self.assertEqual(18, result["metrics"]["kpl_rank_yesterday"])
        self.assertIn("metric_monitor_rank", result["metrics"]["factor_features"])
        self.assertIn("metric_ths_rank", result["metrics"]["factor_features"])
        self.assertIn("metric_ths_value_rank", result["metrics"]["factor_features"])
        self.assertIn("metric_kpl_rank", result["metrics"]["factor_features"])
        self.assertEqual("default", result["metrics"]["weight_profile"])

    def test_build_intraday_ranking_uses_union_pool_and_sorts_by_score(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService()
        bars_map = {
            "300308": [
                Candle("300308", "2026-05-26", 96, 99, 95, 98, 13500, 1323000, 2.08),
                Candle("300308", "2026-05-27", 98, 102, 97, 101, 16000, 1616000, 3.06),
                Candle("300308", "2026-05-28", 101, 106, 100, 105, 18500, 1942500, 3.96),
                Candle("300308", "2026-05-29", 105, 109, 104, 108, 21000, 2268000, 2.86),
                Candle("300308", "2026-05-30", 108, 113, 107, 112, 32000, 3584000, 3.70),
            ],
            "002475": [
                Candle("002475", "2026-05-26", 35, 35.6, 34.8, 35.3, 12000, 423600, 0.0),
                Candle("002475", "2026-05-27", 35.3, 35.7, 35.0, 35.4, 11800, 417720, 0.28),
                Candle("002475", "2026-05-28", 35.4, 35.6, 35.0, 35.2, 11000, 387200, -0.56),
                Candle("002475", "2026-05-29", 35.2, 35.5, 34.9, 35.0, 10800, 378000, -0.57),
                Candle("002475", "2026-05-30", 35.0, 35.2, 34.5, 34.7, 12500, 433750, -0.86),
            ],
        }
        intraday_context = {
            "today_monitor_rows": [{"code": "300308", "name": "中际旭创", "rank_no": 4}, {"code": "002475", "name": "立讯精密", "rank_no": 18}],
            "yesterday_monitor_rows": [{"code": "300308", "name": "中际旭创", "rank_no": 7}],
            "ths_rows": [{"code": "300308", "name": "中际旭创", "rank_no": 10}],
            "kpl_rows": [],
            "daily_bars_map": bars_map,
            "snapshot_map": {
                "300308": {"pct_chg": 3.1, "amount": 33892000000.0, "turnover_rate": 4.2},
                "002475": {"pct_chg": -0.4, "amount": 19879000000.0, "turnover_rate": 0.6},
            },
        }

        rows = service.build_intraday_ranking(intraday_context)

        self.assertEqual(2, len(rows))
        self.assertEqual("300308", rows[0]["stock_code"])
        self.assertGreater(rows[0]["total_score"], rows[1]["total_score"])

    def test_intraday_candidate_prefers_more_upside_room(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService()
        bars = [
            Candle("000001", "2026-05-26", 9.7, 10.1, 9.6, 9.9, 12000, 118800, 0.0),
            Candle("000001", "2026-05-27", 9.9, 10.2, 9.8, 10.0, 12500, 125000, 1.01),
            Candle("000001", "2026-05-28", 10.0, 10.3, 9.9, 10.1, 13000, 131300, 1.0),
            Candle("000001", "2026-05-29", 10.1, 10.4, 10.0, 10.2, 13500, 137700, 0.99),
            Candle("000001", "2026-05-30", 10.2, 10.5, 10.1, 10.3, 14000, 144200, 0.98),
        ]
        rank_context = {
            "monitor_rank_today": 8,
            "ths_rank_today": 10,
            "ths_value_rank_today": 12,
            "kpl_rank_today": 15,
            "monitor_rank_yesterday": 9,
        }

        more_room = service.score_intraday_candidate(
            "000001",
            "平安银行",
            bars,
            rank_context,
            {
                "pct_chg": 2.0,
                "amount": 3_000_000_000,
                "turnover_rate": 2.0,
                "last_price": 10.51,
                "low": 10.20,
            },
        )
        less_room = service.score_intraday_candidate(
            "000001",
            "平安银行",
            bars,
            rank_context,
            {
                "pct_chg": 9.0,
                "amount": 3_000_000_000,
                "turnover_rate": 2.0,
                "last_price": 11.23,
                "low": 10.80,
            },
        )

        self.assertGreater(more_room["total_score"], less_room["total_score"])
        self.assertGreater(more_room["metrics"]["upside_room_pct"], less_room["metrics"]["upside_room_pct"])
        self.assertIn("上行空间偏低", less_room["risks"])

    def test_market_cap_score_prefers_mid_float_market_cap(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService()
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

        preferred = service.score_replay_candidate(
            "300308",
            "中际旭创",
            bars,
            {"monitor_rank_yesterday": 6, "float_market_cap_est": 18_000_000_000},
        )
        too_large = service.score_replay_candidate(
            "300308",
            "中际旭创",
            bars,
            {"monitor_rank_yesterday": 6, "float_market_cap_est": 260_000_000_000},
        )

        self.assertGreater(preferred["market_cap_score"], too_large["market_cap_score"])

    def test_candidate_scoring_service_applies_configured_weight_profile(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService(
            weight_profiles={
                "optimized": {
                    "heat_weight": 0.5,
                    "market_cap_weight": 0.5,
                    "volume_price_weight": 2.0,
                    "position_weight": 1.5,
                    "risk_weight": 1.0,
                }
            },
            active_profile="optimized",
        )
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

        result = service.score_replay_candidate(
            "300308",
            "中际旭创",
            bars,
            {"monitor_rank_yesterday": 6, "ths_rank_yesterday": 12, "kpl_rank_yesterday": 18},
        )

        self.assertEqual("optimized", result["metrics"]["weight_profile"])
        self.assertNotEqual(
            result["total_score"],
            result["heat_score"] + result["market_cap_score"] + result["volume_price_score"] + result["position_score"] + result["risk_penalty"],
        )

    def test_candidate_scoring_service_applies_raw_metric_weights(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService(
            weight_profiles={
                "metric_boosted": {
                    "heat_weight": 1.0,
                    "market_cap_weight": 1.0,
                    "volume_price_weight": 1.0,
                    "position_weight": 1.0,
                    "risk_weight": 1.0,
                    "metric_vol_ratio_5_weight": 2.0,
                    "metric_red_green_ratio_5_weight": 0.0,
                    "metric_close_strength_weight": 0.0,
                    "metric_day_pct_weight": 0.0,
                    "metric_breakout_20_weight": 0.0,
                    "metric_bias_ma5_weight": 0.0,
                    "metric_pos60_weight": 0.0,
                    "metric_upper_shadow_ratio_weight": 0.0,
                    "metric_pct3_weight": 0.0,
                    "metric_float_market_cap_weight": 0.0,
                }
            },
            active_profile="metric_boosted",
        )
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

        result = service.score_replay_candidate("300308", "中际旭创", bars, {"monitor_rank_yesterday": 6})

        self.assertGreater(result["metrics"]["weighted_total_raw"], 0)
        self.assertIn("factor_features", result["metrics"])
        self.assertNotEqual(0.0, result["metrics"]["factor_features"]["metric_vol_ratio_5"])
        self.assertIn("day_amplitude", result["metrics"])
        self.assertIn("body_ratio", result["metrics"])
        self.assertIn("signed_body_pct", result["metrics"])
        self.assertIn("breakout_gap_20", result["metrics"])
        self.assertIn("amount_continuity_2d", result["metrics"])

    def test_rerank_replay_rows_uses_cross_section_normalized_factor_features(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        service = CandidateScoringService(
            weight_profiles={
                "metric_boosted": {
                    "heat_weight": 0.0,
                    "market_cap_weight": 0.0,
                    "volume_price_weight": 0.0,
                    "position_weight": 0.0,
                    "risk_weight": 0.0,
                    "metric_vol_ratio_5_weight": 2.0,
                    "metric_red_green_ratio_5_weight": 0.0,
                    "metric_close_strength_weight": 0.0,
                    "metric_day_pct_weight": 0.0,
                    "metric_day_amplitude_weight": 0.0,
                    "metric_body_ratio_weight": 0.0,
                    "metric_signed_body_pct_weight": 0.0,
                    "metric_breakout_20_weight": 0.0,
                    "metric_breakout_gap_20_weight": 0.0,
                    "metric_bias_ma5_weight": 0.0,
                    "metric_pos60_weight": 0.0,
                    "metric_upper_shadow_ratio_weight": 0.0,
                    "metric_pct3_weight": 0.0,
                    "metric_amount_continuity_2d_weight": 0.0,
                    "metric_float_market_cap_weight": 0.0,
                }
            },
            active_profile="metric_boosted",
        )
        rows = [
            {"stock_code": "000001", "total_score": 0, "grade": "D", "heat_score": 0, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {"metric_vol_ratio_5": -2.0}}},
            {"stock_code": "000002", "total_score": 0, "grade": "D", "heat_score": 0, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {"metric_vol_ratio_5": 0.0}}},
            {"stock_code": "000003", "total_score": 0, "grade": "D", "heat_score": 0, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {"metric_vol_ratio_5": 4.0}}},
        ]

        reranked = service.rerank_replay_rows(rows)

        by_code = {row["stock_code"]: row for row in reranked}
        self.assertEqual("000003", reranked[0]["stock_code"])
        self.assertEqual(100, by_code["000003"]["total_score"])
        self.assertEqual(50, by_code["000002"]["total_score"])
        self.assertEqual(0, by_code["000001"]["total_score"])
        self.assertEqual(1.0, by_code["000003"]["metrics"]["normalized_factor_features"]["metric_vol_ratio_5"])
        self.assertEqual(0.0, by_code["000002"]["metrics"]["normalized_factor_features"]["metric_vol_ratio_5"])
        self.assertEqual(-1.0, by_code["000001"]["metrics"]["normalized_factor_features"]["metric_vol_ratio_5"])

    def test_rerank_replay_rows_batches_model_predictions(self):
        from src.services.candidate_scoring_service import CandidateScoringService

        class FakeModel:
            def __init__(self):
                self.batch_sizes: list[int] = []

            def predict(self, rows):
                self.batch_sizes.append(len(rows))
                return [row[0] for row in rows]

        service = CandidateScoringService()
        service._model = FakeModel()
        rows = [
            {"stock_code": "000001", "total_score": 0, "grade": "D", "heat_score": 20, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {}}},
            {"stock_code": "000002", "total_score": 0, "grade": "D", "heat_score": 40, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {}}},
            {"stock_code": "000003", "total_score": 0, "grade": "D", "heat_score": 10, "market_cap_score": 0, "volume_price_score": 0, "position_score": 0, "risk_penalty": 0, "metrics": {"factor_features": {}}},
        ]

        reranked = service.rerank_replay_rows(rows)

        self.assertEqual([3], service._model.batch_sizes)
        self.assertEqual(["000002", "000001", "000003"], [row["stock_code"] for row in reranked])


class CandidateWeightConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_load_candidate_weight_config_returns_default_when_missing(self):
        from src.services.candidate_weight_config import load_candidate_weight_config

        config = load_candidate_weight_config(Path(self._tmpdir.name) / "missing.toml")

        self.assertEqual("default", config["active_profile"])
        self.assertIn("default", config["profiles"])

    def test_save_and_load_candidate_weight_config_round_trip(self):
        from src.services.candidate_weight_config import load_candidate_weight_config, save_candidate_weight_config

        path = Path(self._tmpdir.name) / "candidate_weights.toml"
        original = {
            "active_profile": "optimized_latest",
            "model_paths": {
                "optimized_latest": "config/candidate_model_optimized_latest.pkl",
            },
            "profiles": {
                "default": {
                    "heat_weight": 1.0,
                    "market_cap_weight": 1.0,
                    "volume_price_weight": 1.0,
                    "position_weight": 1.0,
                    "risk_weight": 1.0,
                },
                "optimized_latest": {
                    "heat_weight": 0.8,
                    "market_cap_weight": 0.6,
                    "volume_price_weight": 1.9,
                    "position_weight": 1.4,
                    "risk_weight": 1.1,
                },
            },
        }

        save_candidate_weight_config(path, original)
        loaded = load_candidate_weight_config(path)

        self.assertEqual("optimized_latest", loaded["active_profile"])
        self.assertAlmostEqual(1.9, loaded["profiles"]["optimized_latest"]["volume_price_weight"])
        self.assertEqual(
            "config/candidate_model_optimized_latest.pkl",
            loaded["model_paths"]["optimized_latest"],
        )


class CandidateWeightOptimizerTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_optimize_replay_weights_writes_optimized_profile(self):
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer
        from src.services.candidate_weight_config import load_candidate_weight_config

        csv_path = Path(self._tmpdir.name) / "replay_training.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "trade_date,session_type,rank_no,stock_code,stock_name,total_score,grade,heat_score,market_cap_score,volume_price_score,position_score,risk_penalty,label_live_pct",
                    "2026-06-01,replay,1,000001,平安银行,80,B,20,10,12,8,-2,5.0",
                    "2026-06-01,replay,2,000002,万科A,70,B,18,8,10,7,-3,3.0",
                    "2026-06-01,replay,3,000003,国农科技,60,C,16,6,8,6,-4,2.0",
                    "2026-06-01,replay,4,000004,国华网安,50,D,12,2,5,3,-6,-1.0",
                    "2026-06-01,replay,5,000005,世纪星源,40,D,8,0,4,2,-8,-3.0",
                    "2026-06-01,replay,6,000006,深振业A,75,B,19,9,11,7,-2,4.0",
                    "2026-06-01,replay,7,000007,全新好,55,C,14,4,7,5,-5,1.0",
                    "2026-06-01,replay,8,000008,神州高铁,65,C,17,7,9,6,-3,2.5",
                    "2026-06-01,replay,9,000009,中国宝安,45,D,10,1,4,2,-7,-2.0",
                    "2026-06-01,replay,10,000010,美丽生态,35,D,6,0,3,1,-9,-4.0",
                ]
            ),
            encoding="utf-8-sig",
        )
        weight_path = Path(self._tmpdir.name) / "candidate_weights.toml"

        result = CandidateWeightOptimizer().optimize_replay_weights(
            csv_path=csv_path,
            weight_config_path=weight_path,
            profile_name="optimized_test",
            activate_profile=True,
        )
        saved = load_candidate_weight_config(weight_path)

        self.assertEqual("optimized_test", result.profile_name)
        self.assertEqual(10, result.sample_count)
        self.assertEqual("optimized_test", saved["active_profile"])
        self.assertIn("default", saved["profiles"])
        self.assertIn("optimized_test", saved["profiles"])
        self.assertEqual(
            "config/candidate_model_optimized_test.pkl",
            saved["model_paths"]["optimized_test"],
        )
        self.assertTrue((Path(self._tmpdir.name) / "config" / "candidate_model_optimized_test.pkl").exists())

    def test_optimize_replay_weights_prefers_next_day_pct_as_label(self):
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer

        csv_path = Path(self._tmpdir.name) / "replay_training_next_day.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "trade_date,session_type,rank_no,stock_code,stock_name,total_score,grade,heat_score,market_cap_score,volume_price_score,position_score,risk_penalty,next_day_pct,label_live_pct",
                    "2026-06-02,replay,1,000001,平安银行,80,B,20,10,12,8,-2,6.0,-8.0",
                    "2026-06-02,replay,2,000002,万科A,70,B,18,8,10,7,-3,3.0,-6.0",
                    "2026-06-02,replay,3,000003,国农科技,60,C,16,6,8,6,-4,1.0,-4.0",
                    "2026-06-02,replay,4,000004,国华网安,50,D,12,2,5,3,-6,-2.0,7.0",
                    "2026-06-02,replay,5,000005,世纪星源,40,D,8,0,4,2,-8,-5.0,9.0",
                ]
            ),
            encoding="utf-8-sig",
        )
        weight_path = Path(self._tmpdir.name) / "candidate_weights.toml"

        result = CandidateWeightOptimizer().optimize_replay_weights(
            csv_path=csv_path,
            weight_config_path=weight_path,
            profile_name="optimized_next_day_test",
            activate_profile=False,
        )

        self.assertGreater(result.optimized_top10_avg_pct, 0.0)

    def test_load_samples_normalizes_labels_by_board_limit(self):
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer

        csv_path = Path(self._tmpdir.name) / "replay_training_normalized.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "trade_date,session_type,rank_no,stock_code,stock_name,total_score,grade,heat_score,market_cap_score,volume_price_score,position_score,risk_penalty,next_day_pct",
                    "2026-06-01,replay,1,000001,平安银行,80,B,20,10,12,8,-2,10.0",
                    "2026-06-01,replay,2,300001,特锐德,70,B,18,8,10,7,-3,20.0",
                    "2026-06-01,replay,3,688001,华兴源创,60,C,16,6,8,6,-4,20.0",
                    "2026-06-01,replay,4,000002,万科A,50,D,12,2,5,3,-6,-10.0",
                ]
            ),
            encoding="utf-8-sig",
        )

        samples = CandidateWeightOptimizer()._load_samples(csv_path)

        self.assertEqual(10.0, samples[0]["label_raw_pct"])
        self.assertEqual(10.0, samples[0]["label_pct"])
        self.assertEqual(20.0, samples[1]["label_raw_pct"])
        self.assertEqual(10.0, samples[1]["label_pct"])
        self.assertEqual(20.0, samples[2]["label_raw_pct"])
        self.assertEqual(10.0, samples[2]["label_pct"])
        self.assertEqual(-10.0, samples[3]["label_pct"])

    def test_normalize_label_pct_treats_limit_up_as_same_strength_across_boards(self):
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer

        optimizer = CandidateWeightOptimizer()

        self.assertEqual(10.0, optimizer._normalize_label_pct(10.0, "000001", "平安银行"))
        self.assertEqual(10.0, optimizer._normalize_label_pct(20.0, "300001", "特锐德"))
        self.assertEqual(10.0, optimizer._normalize_label_pct(20.0, "688001", "华兴源创"))
        self.assertEqual(10.0, optimizer._normalize_label_pct(5.0, "600001", "*ST测试"))
        self.assertEqual(10.0, optimizer._normalize_label_pct(30.0, "830001", "北交测试"))

    def test_topn_objective_rewards_higher_spearman_when_top10_equal(self):
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer

        optimizer = CandidateWeightOptimizer()
        labels = [10.0, 8.0, 6.0, 4.0, 2.0]
        perfectly_ranked_scores = [5.0, 4.0, 3.0, 2.0, 1.0]
        reversed_scores = [1.0, 2.0, 3.0, 4.0, 5.0]

        good_objective = optimizer._topn_objective(perfectly_ranked_scores, labels, topn=5)
        bad_objective = optimizer._topn_objective(reversed_scores, labels, topn=5)

        self.assertGreater(good_objective, bad_objective)


if __name__ == "__main__":
    unittest.main()
