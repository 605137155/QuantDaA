from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from src.models.stock_snapshot import StockSnapshot
from src.utils.trading_day_utils import is_trading_session


@dataclass
class RuntimeState:
    monitor_pool: list = field(default_factory=list)
    focus_pool: list = field(default_factory=list)
    snapshot_map: dict = field(default_factory=dict)


class AppRunner:
    def __init__(
        self,
        logger,
        provider,
        pool_manager,
        strategy_runner,
        daily_repo,
        settings: dict,
        historical_minute_provider=None,
        rank_snapshot_repo=None,
        candidate_score_repo=None,
        candidate_scoring_service=None,
    ):
        self.logger = logger
        self.provider = provider
        self.pool_manager = pool_manager
        self.strategy_runner = strategy_runner
        self.daily_repo = daily_repo
        self.historical_minute_provider = historical_minute_provider
        self.rank_snapshot_repo = rank_snapshot_repo
        self.candidate_score_repo = candidate_score_repo
        self.candidate_scoring_service = candidate_scoring_service
        self.settings = settings
        self.state = RuntimeState()
        self._warmed_up = False
        self._last_snapshot_save_date = ""
        self._last_intraday_candidate_snapshot_key = ""
        scan_settings = settings.get("scan", {})
        self._pool_refresh_base_seconds = int(scan_settings.get("pool_refresh_seconds", 60))
        self._pool_refresh_jitter_seconds = int(scan_settings.get("pool_refresh_jitter_seconds", 5))
        self._pool_refresh_backoff_multiplier = float(scan_settings.get("pool_refresh_backoff_multiplier", 2.0))
        self._pool_refresh_max_seconds = int(scan_settings.get("pool_refresh_max_seconds", 300))
        self._pool_refresh_failures = 0
        self._pool_refresh_rng = random.Random()

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "source_name", self.provider.__class__.__name__)

    @property
    def provider_error(self) -> str:
        return getattr(self.provider, "last_error", "")

    def warmup(self) -> None:
        if self._warmed_up:
            return
        self.logger.info("warming up stock universe")
        self.pool_manager.seed_universe()
        self._warmed_up = True

    def refresh_pools(self):
        self.warmup()
        try:
            snapshot = self.pool_manager.refresh_pools()
        except Exception:
            self._pool_refresh_failures += 1
            raise

        self._pool_refresh_failures = 0
        self.state.monitor_pool = snapshot.monitor_pool
        self.state.focus_pool = snapshot.focus_pool
        self.state.snapshot_map = snapshot.snapshot_map
        return snapshot

    @property
    def pool_refresh_failures(self) -> int:
        return self._pool_refresh_failures

    def get_next_pool_refresh_delay_ms(self) -> int:
        seconds = self._pool_refresh_base_seconds
        if self._pool_refresh_failures:
            seconds = min(
                self._pool_refresh_max_seconds,
                int(self._pool_refresh_base_seconds * (self._pool_refresh_backoff_multiplier ** self._pool_refresh_failures)),
            )
        if self._pool_refresh_jitter_seconds:
            seconds += self._pool_refresh_rng.randint(-self._pool_refresh_jitter_seconds, self._pool_refresh_jitter_seconds)
        seconds = max(5, min(seconds, self._pool_refresh_max_seconds))
        return seconds * 1000

    def scan_once(self) -> list:
        if not self.state.focus_pool:
            self.refresh_pools()
        return self.strategy_runner.scan(self.state.focus_pool, self.state.snapshot_map)

    def _get_snapshot(self, stock_code: str) -> StockSnapshot:
        snapshot = self.state.snapshot_map.get(stock_code)
        if snapshot is None:
            return StockSnapshot(
                code=stock_code,
                name=stock_code,
                last_price=0.0,
                pct_chg=0.0,
                amount=0.0,
                volume=0.0,
                turnover_rate=0.0,
                high=0.0,
                low=0.0,
                open=0.0,
                market="",
                security_type="stock",
                updated_at="",
            )
        return snapshot

    @staticmethod
    def _group_minute_bars(minute_bars: list) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for bar in minute_bars:
            grouped.setdefault(bar.ts[:10], []).append(bar)
        return grouped

    def _build_stock_detail(self, stock_code: str, daily_bars: list, minute_bars: list, selected_date: str = "") -> dict:
        snapshot = self._get_snapshot(stock_code)
        minute_bars_by_date = self._group_minute_bars(minute_bars)
        available_minute_dates = sorted(minute_bars_by_date)
        target_date = selected_date or (available_minute_dates[-1] if available_minute_dates else "")
        minute_bars_for_date = minute_bars_by_date.get(target_date, []) if target_date else []
        return {
            "snapshot": snapshot,
            "daily_bars": daily_bars,
            "all_minute_bars": minute_bars,
            "minute_bars": minute_bars_for_date,
            "minute_bars_by_date": minute_bars_by_date,
            "available_minute_dates": available_minute_dates,
            "selected_date": target_date,
        }

    def _get_scoring_daily_bars(self, stock_code: str, limit: int = 60) -> list:
        daily_bars = self.daily_repo.get_recent(stock_code, limit=limit)
        if len(daily_bars) < min(limit, 20):
            fetched = self.provider.get_daily_bars(stock_code, limit=limit)
            if fetched:
                self.daily_repo.replace_for_stock(stock_code, fetched)
                daily_bars = fetched
        return daily_bars

    @staticmethod
    def _normalize_snapshot_rows(rows: list, snapshot_type: str) -> list[dict]:
        normalized = []
        for idx, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                normalized.append(
                    {
                        "rank_no": int(row.get("rank_no") or row.get("rank") or idx),
                        "stock_code": row.get("stock_code") or row.get("code", ""),
                        "stock_name": row.get("stock_name") or row.get("name", ""),
                        "pct_chg": float(row.get("pct_chg", row.get("rise_and_fall", 0.0)) or 0.0),
                        "amount": float(row.get("amount", row.get("rate", 0.0)) or 0.0),
                        "extra": row.get("extra", {}),
                    }
                )
                continue

            extra = {}
            if snapshot_type.startswith("ths"):
                extra = {
                    "rate": float(getattr(row, "rate", 0.0) or 0.0),
                    "hot_rank_chg": int(getattr(row, "hot_rank_chg", 0) or 0),
                }
                pct_chg = float(getattr(row, "rise_and_fall", 0.0) or 0.0)
                amount = float(getattr(row, "rate", 0.0) or 0.0)
                rank_no = int(getattr(row, "order", idx) or idx)
            else:
                pct_chg = float(getattr(row, "pct_chg", 0.0) or 0.0)
                amount = float(getattr(row, "amount", 0.0) or 0.0)
                rank_no = idx

            normalized.append(
                {
                    "rank_no": rank_no,
                    "stock_code": getattr(row, "code", ""),
                    "stock_name": getattr(row, "name", ""),
                    "pct_chg": pct_chg,
                    "amount": amount,
                    "extra": {
                        **extra,
                        "turnover_rate": float(getattr(row, "turnover_rate", 0.0) or 0.0),
                        "float_market_cap_est": (amount * 100 / float(getattr(row, "turnover_rate", 0.0)))
                        if amount > 0 and float(getattr(row, "turnover_rate", 0.0) or 0.0) > 0
                        else 0.0,
                    },
                }
            )
        return normalized

    def save_daily_snapshots_if_needed(
        self,
        ths_hourly_rows: list | None = None,
        ths_value_rows: list | None = None,
        kpl_rows: list | None = None,
        now: datetime | None = None,
    ) -> None:
        if self.rank_snapshot_repo is None:
            return
        current = now or datetime.now()
        if current.hour < 15:
            return

        trade_date = current.strftime("%Y-%m-%d")
        if self._last_snapshot_save_date == trade_date:
            return

        self.rank_snapshot_repo.replace_snapshot(
            trade_date,
            "monitor_close",
            self._normalize_snapshot_rows(self.state.monitor_pool, "monitor_close"),
        )
        if ths_hourly_rows:
            self.rank_snapshot_repo.replace_snapshot(
                trade_date,
                "ths_hourly_close",
                self._normalize_snapshot_rows(ths_hourly_rows, "ths_hourly_close"),
            )
        if ths_value_rows:
            self.rank_snapshot_repo.replace_snapshot(
                trade_date,
                "ths_value_close",
                self._normalize_snapshot_rows(ths_value_rows, "ths_value_close"),
            )
        if kpl_rows:
            self.rank_snapshot_repo.replace_snapshot(
                trade_date,
                "kpl_close",
                self._normalize_snapshot_rows(kpl_rows, "kpl_close"),
            )
        self._last_snapshot_save_date = trade_date

        replay_rows = self.build_replay_candidate_ranking(trade_date)
        if self.candidate_score_repo is not None and replay_rows:
            self.candidate_score_repo.replace_scores(trade_date, "replay", replay_rows)
            self.candidate_score_repo.append_history_snapshot(f"{trade_date} 15:00:00", trade_date, "replay", replay_rows)

    def save_intraday_candidate_snapshots_if_needed(
        self,
        now: datetime | None = None,
        ths_rows: list | None = None,
        kpl_rows: list | None = None,
    ) -> None:
        if self.candidate_score_repo is None or self.candidate_scoring_service is None:
            return
        current = now or datetime.now()
        if not is_trading_session():
            return

        snapshot_time = current.replace(second=0, microsecond=0)
        snapshot_key = snapshot_time.strftime("%Y-%m-%d %H:%M:%S")
        if snapshot_key == self._last_intraday_candidate_snapshot_key:
            return

        rows = self.build_intraday_candidate_ranking(ths_rows=ths_rows, kpl_rows=kpl_rows)
        if not rows:
            return

        trade_date = snapshot_time.strftime("%Y-%m-%d")
        self.candidate_score_repo.replace_scores(trade_date, "intraday", rows)
        self.candidate_score_repo.append_history_snapshot(snapshot_key, trade_date, "intraday", rows)
        self._last_intraday_candidate_snapshot_key = snapshot_key

    def build_replay_candidate_ranking(self, trade_date: str | None = None) -> list[dict]:
        if self.rank_snapshot_repo is None or self.candidate_scoring_service is None:
            return []
        if trade_date and self.candidate_score_repo is not None:
            saved = self.candidate_score_repo.get_scores(trade_date, "replay")
            if saved:
                return saved

        monitor_rows = (
            self.rank_snapshot_repo.get_snapshot(trade_date, "monitor_close")
            if trade_date
            else self.rank_snapshot_repo.get_latest_snapshot("monitor_close")
        )
        if not monitor_rows:
            monitor_rows = self._normalize_snapshot_rows(self.state.monitor_pool, "monitor_close")
        if not monitor_rows:
            return []

        ths_rows = (
            self.rank_snapshot_repo.get_snapshot(trade_date, "ths_hourly_close")
            if trade_date
            else self.rank_snapshot_repo.get_latest_snapshot("ths_hourly_close")
        )
        kpl_rows = (
            self.rank_snapshot_repo.get_snapshot(trade_date, "kpl_close")
            if trade_date
            else self.rank_snapshot_repo.get_latest_snapshot("kpl_close")
        )

        rank_context_map: dict[str, dict] = {}
        for rows, key in (
            (monitor_rows, "monitor_rank_yesterday"),
            (ths_rows, "ths_rank_yesterday"),
            (kpl_rows, "kpl_rank_yesterday"),
        ):
            for row in rows:
                stock_code = row["stock_code"]
                item = rank_context_map.setdefault(stock_code, {"stock_code": stock_code, "stock_name": row["stock_name"]})
                item[key] = row["rank_no"]
                if key == "monitor_rank_yesterday":
                    item["amount"] = row.get("amount", 0.0)
                    item["turnover_rate"] = row.get("extra", {}).get("turnover_rate", 0.0)
                    item["float_market_cap_est"] = row.get("extra", {}).get("float_market_cap_est", 0.0)

        results = []
        for stock_code, rank_context in rank_context_map.items():
            daily_bars = self._get_scoring_daily_bars(stock_code)
            score = self.candidate_scoring_service.score_replay_candidate(
                stock_code,
                rank_context["stock_name"],
                daily_bars,
                rank_context,
            )
            if score is not None:
                results.append(score)
        results.sort(key=lambda item: (-item["total_score"], item["stock_code"]))
        return results

    def build_intraday_candidate_ranking(self, ths_rows: list | None = None, kpl_rows: list | None = None) -> list[dict]:
        if self.candidate_scoring_service is None:
            return []
        yesterday_rows = self.rank_snapshot_repo.get_latest_snapshot("monitor_close") if self.rank_snapshot_repo else []
        today_monitor_rows = [
            {"code": row.code, "name": row.name, "rank_no": idx}
            for idx, row in enumerate(self.state.monitor_pool, start=1)
        ]
        ths_rank_rows = [
            {"code": row.code, "name": row.name, "rank_no": getattr(row, "order", idx)}
            for idx, row in enumerate(ths_rows or [], start=1)
        ]
        kpl_rank_rows = [
            {"code": row.code, "name": row.name, "rank_no": getattr(row, "order", idx)}
            for idx, row in enumerate(kpl_rows or [], start=1)
        ]
        candidate_codes = {row["code"] for row in today_monitor_rows}
        candidate_codes.update(row["stock_code"] for row in yesterday_rows)
        candidate_codes.update(row["code"] for row in ths_rank_rows)
        candidate_codes.update(row["code"] for row in kpl_rank_rows)

        daily_bars_map = {code: self._get_scoring_daily_bars(code) for code in candidate_codes}
        snapshot_map = {
            code: {
                "pct_chg": snapshot.pct_chg,
                "amount": snapshot.amount,
                "turnover_rate": snapshot.turnover_rate,
                "last_price": snapshot.last_price,
            }
            for code, snapshot in self.state.snapshot_map.items()
            if code in candidate_codes
        }
        return self.candidate_scoring_service.build_intraday_ranking(
            {
                "today_monitor_rows": today_monitor_rows,
                "yesterday_monitor_rows": [
                    {"code": row["stock_code"], "name": row["stock_name"], "rank_no": row["rank_no"]}
                    for row in yesterday_rows
                ],
                "ths_rows": ths_rank_rows,
                "kpl_rows": kpl_rank_rows,
                "daily_bars_map": daily_bars_map,
                "snapshot_map": snapshot_map,
            }
        )

    def get_candidate_review_rows(self, session_type: str, trade_date: str) -> list[dict]:
        if self.candidate_score_repo is None:
            return []

        if session_type == "replay":
            rows = self.candidate_score_repo.get_scores(trade_date, "replay")
        else:
            rows = self.candidate_score_repo.get_latest_history_snapshot(trade_date, "intraday")
        if not rows:
            return []

        return [self._enrich_forward_performance(dict(row), trade_date) for row in rows]

    def get_candidate_review_dates(self, session_type: str) -> list[str]:
        if self.candidate_score_repo is None:
            return []
        return self.candidate_score_repo.get_available_trade_dates(session_type)

    def _enrich_forward_performance(self, row: dict, trade_date: str) -> dict:
        reference_price = float(row.get("metrics", {}).get("reference_price", 0.0) or 0.0)
        row["next_day_pct"] = None
        row["next_day_mode"] = ""
        row["next_trade_date"] = ""
        if reference_price <= 0:
            return row

        daily_bars = self._get_scoring_daily_bars(row["stock_code"], limit=120)
        next_bar = next((bar for bar in daily_bars if bar.ts > trade_date), None)
        if next_bar is not None and next_bar.close:
            row["next_trade_date"] = next_bar.ts
            row["next_day_pct"] = round((next_bar.close - reference_price) / reference_price * 100, 2)
            row["next_day_mode"] = "close"
            return row

        current_trade_date = datetime.now().strftime("%Y-%m-%d")
        snapshot = self.state.snapshot_map.get(row["stock_code"])
        if snapshot is not None and current_trade_date > trade_date and snapshot.last_price > 0:
            row["next_trade_date"] = current_trade_date
            row["next_day_pct"] = round((snapshot.last_price - reference_price) / reference_price * 100, 2)
            row["next_day_mode"] = "current"
        return row

    @staticmethod
    def _select_last_trade_dates(minute_bars_by_date: dict[str, list], max_dates: int) -> list[str]:
        return sorted(minute_bars_by_date)[-max_dates:] if max_dates > 0 else sorted(minute_bars_by_date)

    def _extend_replay_history(self, stock_code: str, minute_bars: list) -> list:
        scan_settings = self.settings.get("scan", {})
        if not scan_settings.get("enable_historical_minute_extension", False):
            return minute_bars
        if self.historical_minute_provider is None or not minute_bars:
            return minute_bars

        replay_history_days = int(scan_settings.get("replay_minute_history_days", 15))
        if replay_history_days <= 0:
            return minute_bars

        primary_by_date = self._group_minute_bars(minute_bars)
        primary_dates = sorted(primary_by_date)
        if len(primary_dates) >= replay_history_days:
            keep_dates = set(primary_dates[-replay_history_days:])
            return [bar for bar in minute_bars if bar.ts[:10] in keep_dates]

        latest_date = primary_dates[-1]
        lookback_days = int(scan_settings.get("replay_minute_lookback_days", 45))
        start_date = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d 09:30:00")
        end_date = f"{latest_date} 15:00:00"
        history_period = str(scan_settings.get("replay_minute_history_period", "5"))
        try:
            historical_bars = self.historical_minute_provider.get_history_minute_bars(
                stock_code,
                start_date=start_date,
                end_date=end_date,
                period=history_period,
            )
        except Exception as exc:
            self.logger.warning("historical minute extension failed for %s: %s", stock_code, exc)
            return minute_bars
        if not historical_bars:
            return minute_bars

        merged_by_date = {
            trade_date: list(bars)
            for trade_date, bars in self._group_minute_bars(historical_bars).items()
            if trade_date not in primary_by_date
        }
        merged_by_date.update(primary_by_date)
        keep_dates = set(self._select_last_trade_dates(merged_by_date, replay_history_days))
        combined = []
        for trade_date in sorted(merged_by_date):
            if trade_date in keep_dates:
                combined.extend(merged_by_date[trade_date])
        return combined

    def get_cached_stock_detail(self, stock_code: str, selected_date: str = "") -> dict:
        daily_bars = self.daily_repo.get_recent(stock_code, limit=60)
        minute_bars = self.strategy_runner.get_cached_minute_bars(stock_code, limit=800)
        return self._build_stock_detail(stock_code, daily_bars, minute_bars, selected_date)

    def get_stock_detail(self, stock_code: str, selected_date: str = "") -> dict:
        daily_bars = self.daily_repo.get_recent(stock_code, limit=60)
        if len(daily_bars) < 60:
            daily_bars = self.provider.get_daily_bars(stock_code, limit=60)
            if daily_bars:
                self.daily_repo.replace_for_stock(stock_code, daily_bars)
        minute_bars = self.strategy_runner.get_cached_or_fetch_minute_bars(stock_code, limit=800)
        minute_bars = self._extend_replay_history(stock_code, minute_bars)
        return self._build_stock_detail(stock_code, daily_bars, minute_bars, selected_date)

    def run_once(self) -> None:
        self.refresh_pools()
        self.scan_once()
        self.logger.info("one-shot run complete")

    def run_forever(self) -> None:
        self.warmup()
        scan_every = self.settings["scan"]["signal_scan_seconds"]
        last_pool_refresh = 0.0
        last_scan = 0.0

        while True:
            now = time.time()
            if not is_trading_session():
                time.sleep(5)
                continue

            pool_every = self.get_next_pool_refresh_delay_ms() / 1000
            if now - last_pool_refresh >= pool_every:
                try:
                    self.refresh_pools()
                except Exception as exc:
                    self.logger.warning("pool refresh failed, backing off: %s", exc)
                last_pool_refresh = now

            if now - last_scan >= scan_every and self.state.focus_pool:
                self.strategy_runner.scan(self.state.focus_pool, self.state.snapshot_map)
                last_scan = now

            time.sleep(1)
