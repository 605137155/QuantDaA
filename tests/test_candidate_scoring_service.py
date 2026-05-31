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
            "kpl_rank_yesterday": 18,
        }

        result = service.score_replay_candidate("300308", "中际旭创", bars, rank_context)

        self.assertEqual("300308", result["stock_code"])
        self.assertGreaterEqual(result["total_score"], 60)
        self.assertIn(result["grade"], {"A", "B", "C"})
        self.assertTrue(result["flags"])
        self.assertIn("vol_ratio_5", result["metrics"])
        self.assertIn("float_market_cap_est", result["metrics"])

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


if __name__ == "__main__":
    unittest.main()
