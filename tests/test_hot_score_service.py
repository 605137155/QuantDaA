from __future__ import annotations

import unittest
from unittest.mock import patch

from src.models.stock_snapshot import StockSnapshot
from src.data_providers.ths_hot_provider import THSHotProvider
from src.services.hot_score_service import HotScoreService


class HotScoreServiceTests(unittest.TestCase):
    def test_prefers_higher_turnover_and_activity(self):
        service = HotScoreService()
        slow = StockSnapshot("1", "A", 10, 0.5, 800_000_000, 1, 0.2, 10.2, 9.9, 10.0, "", "stock", "2026-05-30 10:00:00")
        hot = StockSnapshot("2", "B", 10, 4.0, 6_000_000_000, 1, 3.5, 10.6, 9.8, 10.0, "", "stock", "2026-05-30 10:00:00")

        self.assertGreater(service.score(hot), service.score(slow))

    def test_ths_provider_tolerates_none_fields_in_stock_payload(self):
        provider = THSHotProvider()
        payload = {
            "stock_list": [
                {
                    "code": "300308",
                    "name": "中际旭创",
                    "order": 1,
                    "rate": None,
                    "rise_and_fall": None,
                    "hot_rank_chg": None,
                    "market": None,
                    "tag": None,
                    "analyse": None,
                    "analyse_title": None,
                }
            ]
        }

        with patch.object(provider, "_get", return_value=payload):
            rows = provider.get_24h_hot(limit=10)

        self.assertEqual(1, len(rows))
        self.assertEqual("300308", rows[0].code)
        self.assertEqual(0.0, rows[0].rate)
        self.assertEqual(0.0, rows[0].rise_and_fall)
        self.assertEqual(0, rows[0].hot_rank_chg)
        self.assertEqual(0, rows[0].market)
        self.assertEqual([], rows[0].concept_tags)
        self.assertEqual("", rows[0].popularity_tag)


if __name__ == "__main__":
    unittest.main()
