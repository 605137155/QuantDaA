from __future__ import annotations

import unittest

from src.models.stock_snapshot import StockSnapshot
from src.services.hot_score_service import HotScoreService


class HotScoreServiceTests(unittest.TestCase):
    def test_prefers_higher_turnover_and_activity(self):
        service = HotScoreService()
        slow = StockSnapshot("1", "A", 10, 0.5, 800_000_000, 1, 0.2, 10.2, 9.9, 10.0, "", "stock", "2026-05-30 10:00:00")
        hot = StockSnapshot("2", "B", 10, 4.0, 6_000_000_000, 1, 3.5, 10.6, 9.8, 10.0, "", "stock", "2026-05-30 10:00:00")

        self.assertGreater(service.score(hot), service.score(slow))


if __name__ == "__main__":
    unittest.main()
