from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = ROOT / "聚宽策略" / "原始策略_早盘买尾盘出_首板竞价融合.py"


def read_strategy_text():
    if not STRATEGY_PATH.exists():
        raise AssertionError(f"策略文件不存在: {STRATEGY_PATH}")
    return STRATEGY_PATH.read_text(encoding="utf-8")


def load_strategy_module():
    if not STRATEGY_PATH.exists():
        raise AssertionError(f"策略文件不存在: {STRATEGY_PATH}")
    jqdata_stub = types.ModuleType("jqdata")
    previous = sys.modules.get("jqdata")
    sys.modules["jqdata"] = jqdata_stub
    try:
        spec = importlib.util.spec_from_file_location("jq_strategy_auction_fusion", STRATEGY_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("jqdata", None)
        else:
            sys.modules["jqdata"] = previous


class JoinQuantAuctionFusionFileTests(unittest.TestCase):
    def test_strategy_file_registers_auction_confirmation_without_midday_sell(self):
        text = read_strategy_text()

        self.assertIn("run_daily(my_auction_confirm, time='09:26')", text)
        self.assertIn("g.pre_target_list", text)
        self.assertIn("g.final_buy_list", text)
        self.assertIn("buy_list = g.final_buy_list", text)
        self.assertNotIn("run_daily(get_close_sell", text)
        self.assertNotIn("g.ml_weights", text)

    def test_strategy_file_keeps_first_board_overheat_filters_as_hard_filters(self):
        text = read_strategy_text()

        self.assertIn("if extreme_limit_ups >= 3:", text)
        self.assertIn("if volatility_5 > 40.0:", text)
        self.assertIn("if limit_ups_5 >= 4:", text)

    def test_match_auction_condition_uses_money_buckets(self):
        module = load_strategy_module()

        self.assertEqual(
            module.CONDITION_RULES[0][0],
            module.match_auction_condition(cur_ratio=1.08, auction_ratio=0.15, yesterday_money=300_000_000),
        )
        self.assertEqual(
            module.CONDITION_RULES[1][0],
            module.match_auction_condition(cur_ratio=1.08, auction_ratio=0.15, yesterday_money=800_000_000),
        )
        self.assertEqual(
            module.CONDITION_RULES[2][0],
            module.match_auction_condition(cur_ratio=1.05, auction_ratio=0.05, yesterday_money=800_000_000),
        )
        self.assertIsNone(
            module.match_auction_condition(cur_ratio=1.08, auction_ratio=0.15, yesterday_money=2_000_000_000)
        )

    def test_select_confirmed_buys_preserves_pre_market_score_order_and_caps_count(self):
        module = load_strategy_module()

        pre_targets = ["000001.XSHE", "000002.XSHE", "000003.XSHE", "000004.XSHE", "000005.XSHE"]
        confirmed = ["000005.XSHE", "000003.XSHE", "000001.XSHE", "000004.XSHE", "000002.XSHE"]

        self.assertEqual(
            ["000001.XSHE", "000002.XSHE", "000003.XSHE", "000004.XSHE"],
            module.select_confirmed_buys(pre_targets, confirmed, max_stocks=4),
        )

    def test_select_confirmed_buys_falls_back_to_original_top_stocks_when_no_strict_match(self):
        module = load_strategy_module()

        pre_targets = ["000001.XSHE", "000002.XSHE", "000003.XSHE", "000004.XSHE", "000005.XSHE"]

        self.assertEqual(
            ["000001.XSHE", "000002.XSHE", "000003.XSHE", "000004.XSHE"],
            module.select_confirmed_buys(pre_targets, [], max_stocks=4),
        )

    def test_auction_score_rewards_first_board_core_conditions(self):
        module = load_strategy_module()

        matched = module.calc_auction_score(
            cur_ratio=1.08,
            auction_ratio=0.15,
            yesterday_money=800_000_000,
            left_volume_ok=True,
            matched_condition=module.CONDITION_RULES[1][0],
        )
        neutral = module.calc_auction_score(
            cur_ratio=0.995,
            auction_ratio=0.01,
            yesterday_money=2_000_000_000,
            left_volume_ok=False,
            matched_condition=None,
        )

        self.assertGreaterEqual(matched, 60)
        self.assertLessEqual(neutral, 0)

    def test_final_ranking_uses_base_score_plus_auction_score(self):
        module = load_strategy_module()

        pre_targets = ["000001.XSHE", "000002.XSHE", "000003.XSHE", "000004.XSHE", "000005.XSHE"]
        base_scores = {
            "000001.XSHE": 90,
            "000002.XSHE": 88,
            "000003.XSHE": 86,
            "000004.XSHE": 84,
            "000005.XSHE": 70,
        }
        auction_scores = {
            "000001.XSHE": 0,
            "000002.XSHE": 0,
            "000003.XSHE": 0,
            "000004.XSHE": 0,
            "000005.XSHE": 45,
        }

        self.assertEqual(
            ["000005.XSHE", "000001.XSHE", "000002.XSHE", "000003.XSHE"],
            module.select_final_buys_by_score(pre_targets, base_scores, auction_scores, max_stocks=4),
        )

    def test_should_sell_after_two_trading_days(self):
        module = load_strategy_module()
        trade_days = ["2026-06-01", "2026-06-02", "2026-06-03"]

        self.assertFalse(
            module.should_sell_after_hold_days("2026-06-01", "2026-06-02", trade_days, hold_days=2)
        )
        self.assertTrue(
            module.should_sell_after_hold_days("2026-06-01", "2026-06-03", trade_days, hold_days=2)
        )


if __name__ == "__main__":
    unittest.main()
