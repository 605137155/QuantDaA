# JQ Strategy Auction Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a new JoinQuant strategy file that preserves the original morning-buy/afternoon-sell rhythm while adding first-board auction confirmation.

**Architecture:** The existing original strategy remains untouched. A new standalone strategy file copies the original structure, expands the pre-market target pool, adds a 09:26 auction confirmation stage, and makes the morning buy stage consume the confirmed list. Lightweight local tests validate the new file contract and pure auction matching helpers without requiring JoinQuant runtime access.

**Tech Stack:** Python, JoinQuant strategy APIs, local `unittest`, `py_compile`.

---

### Task 1: Add Contract Tests First

**Files:**
- Create: `tests/test_jq_strategy_auction_fusion_file.py`
- Create later: `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = ROOT / "聚宽策略" / "原始策略_早盘买尾盘出_首板竞价融合.py"


def load_strategy_module():
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
        text = STRATEGY_PATH.read_text(encoding="utf-8")

        self.assertIn("run_daily(my_auction_confirm, time='09:26')", text)
        self.assertIn("g.pre_target_list", text)
        self.assertIn("g.final_buy_list", text)
        self.assertIn("buy_list = g.final_buy_list", text)
        self.assertNotIn("run_daily(get_close_sell", text)
        self.assertNotIn("g.ml_weights", text)

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_jq_strategy_auction_fusion_file -v`

Expected: FAIL because `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py` does not exist yet.

### Task 2: Create New Strategy File

**Files:**
- Create: `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py`

- [ ] **Step 1: Create the new file from the current original strategy**

Use `Copy-Item` only to duplicate the current strategy into a new file. Do not modify or overwrite `聚宽策略/原始策略_早盘买尾盘出.py`.

- [ ] **Step 2: Add auction constants and state**

Add `CONDITION_RULES`, `g.pre_target_count`, `g.pre_target_list`, `g.final_buy_list`, `g.auction_info`, and register `run_daily(my_auction_confirm, time='09:26')`.

- [ ] **Step 3: Expand pre-market selection**

Change the final assignment from direct Top4 buy targets to Top12 candidates:

```python
g.pre_target_list = [item['code'] for item in scored_candidates][:g.pre_target_count]
g.target_list = g.pre_target_list[:]
g.final_buy_list = []
```

- [ ] **Step 4: Add pure helpers and auction confirmation**

Add:

```python
def match_auction_condition(cur_ratio, auction_ratio, yesterday_money):
    is_1_5 = 1e8 <= yesterday_money < 5e8
    is_5_15 = 5e8 <= yesterday_money <= 15e8
    for cond_name, open_lo, open_hi, auc_lo, auc_hi in CONDITION_RULES:
        if cond_name.startswith('A') and not is_1_5:
            continue
        if not cond_name.startswith('A') and not is_5_15:
            continue
        if open_lo < cur_ratio <= open_hi and auc_lo <= auction_ratio <= auc_hi:
            return cond_name
    return None


def select_confirmed_buys(pre_targets, confirmed_codes, max_stocks):
    confirmed_set = set(confirmed_codes)
    return [code for code in pre_targets if code in confirmed_set][:max_stocks]
```

Add `my_auction_confirm(context)` to fetch previous-day price/fundamental data, call `get_call_auction`, apply `match_auction_condition`, run left-pressure volume validation with `calculate_zyts`, and store `g.final_buy_list`.

- [ ] **Step 5: Make morning buy consume final confirmed list**

Change `my_morning_trade` to use:

```python
buy_list = g.final_buy_list
if not buy_list:
    log.info("【早盘买入跳过】竞价确认通过列表为空，今日不买入")
    return
```

### Task 3: Verify

**Files:**
- Test: `tests/test_jq_strategy_auction_fusion_file.py`
- Test: `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py`

- [ ] **Step 1: Run focused tests**

Run: `python -m unittest tests.test_jq_strategy_auction_fusion_file -v`

Expected: PASS with 3 tests.

- [ ] **Step 2: Compile new strategy**

Run: `python -m py_compile "聚宽策略\原始策略_早盘买尾盘出_首板竞价融合.py"`

Expected: exit code 0.

- [ ] **Step 3: Inspect diff**

Run: `git diff -- "聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py" "tests/test_jq_strategy_auction_fusion_file.py" "docs/superpowers/plans/2026-06-07-jq-strategy-auction-fusion.md"`

Expected: only the new plan, new test, and new strategy file are included.

