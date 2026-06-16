# JQ Hold Two Trading Days Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the fusion JoinQuant strategy so stocks bought in the morning are sold at 14:50 after two full trading days, e.g. Monday buy and Wednesday afternoon sell.

**Architecture:** Keep the strategy file standalone and preserve the existing buy selection flow. Add a small pure helper for trading-day hold checks, record buy dates after morning orders, and make the afternoon sell function skip positions until the helper says they are mature enough to sell.

**Tech Stack:** Python, JoinQuant strategy APIs, local `unittest`, `py_compile`.

---

### Task 1: Add Hold-Day Tests

**Files:**
- Modify: `tests/test_jq_strategy_auction_fusion_file.py`

- [ ] **Step 1: Write failing tests for trading-day hold logic**

Add tests that load the strategy module and assert:

```python
trade_days = ["2026-06-01", "2026-06-02", "2026-06-03"]
self.assertFalse(module.should_sell_after_hold_days("2026-06-01", "2026-06-02", trade_days, hold_days=2))
self.assertTrue(module.should_sell_after_hold_days("2026-06-01", "2026-06-03", trade_days, hold_days=2))
```

- [ ] **Step 2: Run focused test**

Run: `python -m unittest tests.test_jq_strategy_auction_fusion_file.JoinQuantAuctionFusionFileTests.test_should_sell_after_two_trading_days -v`

Expected: FAIL because `should_sell_after_hold_days` does not exist.

### Task 2: Implement Hold-Day Sell

**Files:**
- Modify: `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py`

- [ ] **Step 1: Add state**

Add `g.hold_days = 2` and `g.buy_date_dict = {}` in `initialize`.

- [ ] **Step 2: Add pure helper**

Add `should_sell_after_hold_days(buy_date, current_date, trade_days, hold_days=2)`.

- [ ] **Step 3: Record buy date**

After `order_value(stock, cash_per_stock)`, record `g.buy_date_dict[stock] = context.current_dt.date()`.

- [ ] **Step 4: Gate afternoon sells**

In `my_afternoon_trade`, only sell a stock when `position.closeable_amount > 0` and `should_sell_after_hold_days(...)` returns `True`.

### Task 3: Verify

**Files:**
- Test: `tests/test_jq_strategy_auction_fusion_file.py`
- Test: `聚宽策略/原始策略_早盘买尾盘出_首板竞价融合.py`

- [ ] **Step 1: Run focused strategy tests**

Run: `python -m unittest tests.test_jq_strategy_auction_fusion_file -v`

Expected: PASS.

- [ ] **Step 2: Compile strategy**

Run: `python -m py_compile "聚宽策略\原始策略_早盘买尾盘出_首板竞价融合.py"`

Expected: exit code 0.

- [ ] **Step 3: Run all tests**

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

