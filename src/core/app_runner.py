from __future__ import annotations

import csv
import random
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

from src.models.candle import Candle
from src.models.stock_snapshot import StockSnapshot
from src.utils.trading_day_utils import is_trading_day, is_trading_session


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
        self._latest_market_snapshot_trade_date = ""
        self._latest_market_snapshot_map: dict[str, StockSnapshot] = {}
        scan_settings = settings.get("scan", {})
        self._pool_refresh_base_seconds = int(scan_settings.get("pool_refresh_seconds", 60))
        self._pool_refresh_jitter_seconds = int(scan_settings.get("pool_refresh_jitter_seconds", 5))
        self._pool_refresh_backoff_multiplier = float(scan_settings.get("pool_refresh_backoff_multiplier", 2.0))
        self._pool_refresh_max_seconds = int(scan_settings.get("pool_refresh_max_seconds", 300))
        self._pool_refresh_failures = 0
        self._pool_refresh_rng = random.Random()

    def get_candidate_profiles(self) -> list[str]:
        if self.candidate_scoring_service is None:
            return []
        return self.candidate_scoring_service.get_available_profiles()

    def get_active_candidate_profile(self) -> str:
        if self.candidate_scoring_service is None:
            return ""
        return self.candidate_scoring_service.active_profile

    def set_candidate_profile(self, profile_name: str) -> bool:
        if self.candidate_scoring_service is None:
            return False
        return self.candidate_scoring_service.set_active_profile(profile_name)

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

    def _get_market_snapshot_by_code(self, stock_code: str, trade_date: str) -> StockSnapshot | None:
        snapshot = self.state.snapshot_map.get(stock_code)
        if snapshot is not None:
            snapshot_trade_date = snapshot.updated_at[:10] if snapshot.updated_at else ""
            if not trade_date or snapshot_trade_date >= trade_date:
                return snapshot

        if self._latest_market_snapshot_trade_date != trade_date:
            try:
                market_rows = self.provider.get_market_snapshot()
            except Exception:
                return None
            self._latest_market_snapshot_map = {row.code: row for row in market_rows}
            self._latest_market_snapshot_trade_date = trade_date

        return self._latest_market_snapshot_map.get(stock_code)

    @staticmethod
    def _group_minute_bars(minute_bars: list) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for bar in minute_bars:
            grouped.setdefault(bar.ts[:10], []).append(bar)
        return grouped

    @staticmethod
    def _merge_live_daily_bar(stock_code: str, daily_bars: list, minute_bars: list, snapshot: StockSnapshot) -> list:
        bars = list(daily_bars)
        live_date = snapshot.updated_at[:10] if snapshot.updated_at else ""
        if not live_date and minute_bars:
            live_date = max(bar.ts[:10] for bar in minute_bars)
        if not live_date:
            return bars

        last_daily_date = bars[-1].ts[:10] if bars else ""
        if last_daily_date and live_date < last_daily_date:
            return bars

        live_minute_bars = [bar for bar in minute_bars if bar.ts[:10] == live_date]
        first_minute = live_minute_bars[0] if live_minute_bars else None
        last_minute = live_minute_bars[-1] if live_minute_bars else None
        open_price = snapshot.open or (first_minute.open if first_minute else 0.0)
        close_price = snapshot.last_price or (last_minute.close if last_minute else 0.0)
        high_price = snapshot.high or max((bar.high for bar in live_minute_bars), default=0.0)
        low_candidates = [value for value in (snapshot.low,) if value > 0]
        if live_minute_bars:
            low_candidates.append(min(bar.low for bar in live_minute_bars))
        low_price = min(low_candidates) if low_candidates else 0.0
        volume = snapshot.volume or sum(bar.volume for bar in live_minute_bars)
        amount = snapshot.amount or sum(bar.amount for bar in live_minute_bars)

        if not any(value > 0 for value in (open_price, close_price, high_price, low_price)):
            return bars

        live_bar = Candle(
            stock_code=stock_code,
            ts=live_date,
            open=open_price,
            high=max(high_price, open_price, close_price),
            low=min(value for value in (low_price, open_price, close_price) if value > 0),
            close=close_price,
            volume=volume,
            amount=amount,
            pct_chg=snapshot.pct_chg,
        )
        if last_daily_date == live_date:
            bars[-1] = live_bar
        else:
            bars.append(live_bar)
        return bars

    def _build_stock_detail(self, stock_code: str, daily_bars: list, minute_bars: list, selected_date: str = "") -> dict:
        snapshot = self._get_snapshot(stock_code)
        daily_bars = self._merge_live_daily_bar(stock_code, daily_bars, minute_bars, snapshot)
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
        # 先尝试从数据库获取
        daily_bars = self.daily_repo.get_recent(stock_code, limit=limit)
        if len(daily_bars) >= min(limit, 20):
            return daily_bars

        # 检查缓存时间，避免重复请求
        now = time.time()
        last_fetch = getattr(self, '_daily_fetch_timestamps', {}).get(stock_code, 0)
        if not hasattr(self, '_daily_fetch_timestamps'):
            self._daily_fetch_timestamps = {}
        if now - last_fetch < 60:  # 60秒内不重复请求
            return daily_bars

        # 缓存过期，发起网络请求
        self._daily_fetch_timestamps[stock_code] = now
        fetched = self.provider.get_daily_bars(stock_code, limit=limit)
        if fetched:
            self.daily_repo.replace_for_stock(stock_code, fetched)
            daily_bars = fetched
        return daily_bars

    def _get_scoring_daily_bars_until(self, stock_code: str, trade_date: str, limit: int = 60) -> list:
        fetch_limit = max(limit, 240)
        if hasattr(self.daily_repo, "get_recent_until"):
            daily_bars = self.daily_repo.get_recent_until(stock_code, trade_date, limit=fetch_limit)
        else:
            daily_bars = [bar for bar in self.daily_repo.get_recent(stock_code, limit=fetch_limit) if bar.ts[:10] <= trade_date]

        if len(daily_bars) >= min(limit, 20):
            return daily_bars[-limit:]

        # 检查缓存时间，避免重复请求
        now = time.time()
        last_fetch = getattr(self, '_daily_fetch_timestamps', {}).get(stock_code, 0)
        if not hasattr(self, '_daily_fetch_timestamps'):
            self._daily_fetch_timestamps = {}
        if now - last_fetch < 60:  # 60秒内不重复请求
            return daily_bars[-limit:]

        # 缓存过期，发起网络请求
        self._daily_fetch_timestamps[stock_code] = now
        fetched = self.provider.get_daily_bars(stock_code, limit=fetch_limit)
        if fetched:
            self.daily_repo.replace_for_stock(stock_code, fetched)
            if hasattr(self.daily_repo, "get_recent_until"):
                daily_bars = self.daily_repo.get_recent_until(stock_code, trade_date, limit=fetch_limit)
            else:
                daily_bars = [bar for bar in fetched if bar.ts[:10] <= trade_date]
        return daily_bars[-limit:]

    def _get_recent_daily_bars_with_forward_window(self, stock_code: str, trade_date: str, limit: int = 240) -> list:
        daily_bars = self.daily_repo.get_recent(stock_code, limit=limit)

        # 检查是否需要获取新数据
        need_fetch = not any(bar.ts[:10] >= trade_date for bar in daily_bars)

        # 如果需要获取，检查缓存时间
        if need_fetch:
            now = time.time()
            if not hasattr(self, '_forward_fetch_timestamps'):
                self._forward_fetch_timestamps = {}
            last_fetch = self._forward_fetch_timestamps.get(stock_code, 0)

            # 60秒内不重复请求
            if now - last_fetch < 60:
                return daily_bars

            # 缓存过期，发起网络请求
            self._forward_fetch_timestamps[stock_code] = now
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

    def should_save_daily_snapshots(self, now: datetime | None = None) -> bool:
        if self.rank_snapshot_repo is None:
            return False
        current = now or datetime.now()
        if not is_trading_day(current):
            return False
        if current.hour < 15:
            return False
        trade_date = current.strftime("%Y-%m-%d")
        return self._last_snapshot_save_date != trade_date

    def save_daily_snapshots_if_needed(
        self,
        ths_hourly_rows: list | None = None,
        ths_value_rows: list | None = None,
        kpl_rows: list | None = None,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now()
        if not self.should_save_daily_snapshots(current):
            return
        trade_date = current.strftime("%Y-%m-%d")

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
        ths_value_rows = (
            self.rank_snapshot_repo.get_snapshot(trade_date, "ths_value_close")
            if trade_date
            else self.rank_snapshot_repo.get_latest_snapshot("ths_value_close")
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
            (ths_value_rows, "ths_value_rank_yesterday"),
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
            scoring_trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
            daily_bars = self._get_scoring_daily_bars_until(stock_code, scoring_trade_date)
            score = self.candidate_scoring_service.score_replay_candidate(
                stock_code,
                rank_context["stock_name"],
                daily_bars,
                rank_context,
            )
            if score is not None:
                snapshot = self.state.snapshot_map.get(stock_code)
                if snapshot is not None:
                    score["metrics"] = {
                        **score.get("metrics", {}),
                        "live_pct_chg": float(snapshot.pct_chg or 0.0),
                    }
                results.append(score)
        results = self.candidate_scoring_service.rerank_replay_rows(results)
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
            if rows and self.candidate_scoring_service is not None:
                rows = self.candidate_scoring_service.rerank_replay_rows(rows)
            if not rows:
                rows = self.build_replay_candidate_ranking(trade_date)
            if not rows:
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

    def export_candidate_review_csv(self, session_type: str, trade_date: str, output_path: str | Path) -> Path:
        rows = self.get_candidate_review_rows(session_type, trade_date)
        if not rows:
            raise ValueError(f"{trade_date} 没有可导出的 {session_type} 候选数据")

        rows = self._attach_live_labels(rows, trade_date)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        metric_keys = sorted({key for row in rows for key in row.get("metrics", {}).keys()})
        fieldnames = [
            "trade_date",
            "session_type",
            "rank_no",
            "stock_code",
            "stock_name",
            "total_score",
            "grade",
            "heat_score",
            "market_cap_score",
            "volume_price_score",
            "position_score",
            "risk_penalty",
            "next_day_pct",
            "next_day_mode",
            "next_trade_date",
            "label_live_pct",
            "label_live_up",
            "label_live_strong",
            "label_live_rank_pct",
            "flags",
            "risks",
        ] + [f"metric_{key}" for key in metric_keys]

        with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for index, row in enumerate(rows, start=1):
                exported_row = {
                    "trade_date": trade_date,
                    "session_type": session_type,
                    "rank_no": index,
                    "stock_code": row["stock_code"],
                    "stock_name": row["stock_name"],
                    "total_score": row["total_score"],
                    "grade": row["grade"],
                    "heat_score": row.get("heat_score", 0),
                    "market_cap_score": row.get("market_cap_score", 0),
                    "volume_price_score": row.get("volume_price_score", 0),
                    "position_score": row.get("position_score", 0),
                    "risk_penalty": row.get("risk_penalty", 0),
                    "next_day_pct": row.get("next_day_pct"),
                    "next_day_mode": row.get("next_day_mode", ""),
                    "next_trade_date": row.get("next_trade_date", ""),
                    "label_live_pct": row.get("label_live_pct"),
                    "label_live_up": row.get("label_live_up"),
                    "label_live_strong": row.get("label_live_strong"),
                    "label_live_rank_pct": row.get("label_live_rank_pct"),
                    "flags": "|".join(row.get("flags", [])),
                    "risks": "|".join(row.get("risks", [])),
                }
                for key in metric_keys:
                    exported_row[f"metric_{key}"] = row.get("metrics", {}).get(key)
                writer.writerow(exported_row)

        return output

    def _attach_live_labels(self, rows: list[dict], trade_date: str) -> list[dict]:
        labeled_rows = [dict(row) for row in rows]
        live_pct_rows: list[tuple[int, float]] = []
        current_trade_date = datetime.now().strftime("%Y-%m-%d")

        for index, row in enumerate(labeled_rows):
            live_pct = self._resolve_live_label_pct(row, trade_date, current_trade_date)
            row["label_live_pct"] = live_pct
            row["label_live_up"] = 1 if live_pct is not None and live_pct > 0 else (0 if live_pct is not None else None)
            row["label_live_strong"] = 1 if live_pct is not None and live_pct >= 2.0 else (0 if live_pct is not None else None)
            row["label_live_rank_pct"] = None
            if live_pct is not None:
                live_pct_rows.append((index, live_pct))

        if live_pct_rows:
            sorted_rows = sorted(live_pct_rows, key=lambda item: item[1])
            denominator = max(len(sorted_rows) - 1, 1)
            for rank_index, (row_index, _live_pct) in enumerate(sorted_rows):
                labeled_rows[row_index]["label_live_rank_pct"] = round(rank_index / denominator, 4) if len(sorted_rows) > 1 else 1.0

        return labeled_rows

    def _resolve_live_label_pct(self, row: dict, trade_date: str, current_trade_date: str) -> float | None:
        # 优先从日线库取trade_date当天的实际收盘价作为基准
        reference_price = 0.0
        try:
            daily_bars = self._get_recent_daily_bars_with_forward_window(row["stock_code"], trade_date, limit=240)
            trade_bar = next((bar for bar in daily_bars if bar.ts[:10] == trade_date[:10]), None)
            if trade_bar is not None and trade_bar.close > 0:
                reference_price = trade_bar.close
        except Exception:
            pass

        # fallback: 用评分时存的reference_price
        if reference_price <= 0:
            reference_price = float(row.get("metrics", {}).get("reference_price", 0.0) or 0.0)
        if reference_price <= 0:
            return None

        snapshot = self.state.snapshot_map.get(row["stock_code"])
        if snapshot is None or snapshot.last_price <= 0:
            return None

        snapshot_trade_date = snapshot.updated_at[:10] if snapshot.updated_at else current_trade_date
        if snapshot_trade_date < trade_date:
            return None

        return round((snapshot.last_price - reference_price) / reference_price * 100, 2)

    def _enrich_forward_performance(self, row: dict, trade_date: str) -> dict:
        row["next_day_pct"] = None
        row["next_day_mode"] = ""
        row["next_trade_date"] = ""
        row["today_pct"] = None

        daily_bars = self._get_recent_daily_bars_with_forward_window(row["stock_code"], trade_date, limit=240)

        # 从日线库取当天的实际收盘价作为基准
        trade_bar = next((bar for bar in daily_bars if bar.ts[:10] == trade_date[:10]), None)
        if trade_bar is None or trade_bar.close <= 0:
            return row
        reference_price = trade_bar.close

        # 计算当日涨幅
        today_pct = None
        try:
            idx = daily_bars.index(trade_bar)
            if idx > 0:
                prev_bar = daily_bars[idx - 1]
                if prev_bar.close > 0:
                    today_pct = round((trade_bar.close - prev_bar.close) / prev_bar.close * 100, 2)
        except Exception:
            pass
        if today_pct is None:
            today_pct = getattr(trade_bar, "pct_chg", None)
        row["today_pct"] = today_pct

        next_bar = next((bar for bar in daily_bars if bar.ts[:10] > trade_date[:10]), None)
        current_trade_date = datetime.now().strftime("%Y-%m-%d")
        use_current_snapshot = current_trade_date > trade_date and self._is_yesterday_trade_date(trade_date, current_trade_date)

        # 库里没有次日数据，判断是否需要获取
        if next_bar is None:
            # 检查次日是否是工作日且已经开盘
            should_fetch = self._should_fetch_next_day_data(trade_date)
            if should_fetch:
                try:
                    fetched = self.provider.get_daily_bars(row["stock_code"], limit=10)
                    if fetched:
                        self.daily_repo.replace_for_stock(row["stock_code"], fetched)
                        daily_bars = fetched
                        next_bar = next((bar for bar in daily_bars if bar.ts[:10] > trade_date[:10]), None)
                except Exception:
                    pass

        if (
            use_current_snapshot
            and is_trading_session()
            and next_bar is not None
            and next_bar.ts[:10] == current_trade_date
        ):
            snapshot = self._get_market_snapshot_by_code(row["stock_code"], current_trade_date)
            if snapshot is not None and snapshot.last_price > 0:
                row["next_trade_date"] = current_trade_date
                row["next_day_pct"] = round((snapshot.last_price - reference_price) / reference_price * 100, 2)
                row["next_day_mode"] = "current"
                return row

        if next_bar is not None and next_bar.close:
            # 日线库里有次日收盘价
            row["next_trade_date"] = next_bar.ts
            row["next_day_pct"] = round((next_bar.close - reference_price) / reference_price * 100, 2)
            row["next_day_mode"] = "close"
            return row

        # 次日就是今天 → 用实时快照价（盘中=实时价，收盘后=收盘价）
        if use_current_snapshot and is_trading_session():
            snapshot = self._get_market_snapshot_by_code(row["stock_code"], current_trade_date)
            if snapshot is not None and snapshot.last_price > 0:
                row["next_trade_date"] = current_trade_date
                row["next_day_pct"] = round((snapshot.last_price - reference_price) / reference_price * 100, 2)
                row["next_day_mode"] = "current"
        return row

    def _should_fetch_next_day_data(self, trade_date: str) -> bool:
        """判断是否应该获取次日数据
        条件：
        1. 次日是工作日（周一到周五）
        2. 当前时间已经过了次日9:15（集合竞价开始时间）
        3. 或者次日已经收盘（15:00之后）
        """
        try:
            from datetime import datetime, timedelta

            # 解析交易日期
            trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            current_dt = datetime.now()

            # 计算次日日期
            next_dt = trade_dt + timedelta(days=1)

            # 检查次日是否是工作日（周一到周五）
            if next_dt.weekday() >= 5:  # 5=周六, 6=周日
                return False

            # 如果次日是今天
            if next_dt.date() == current_dt.date():
                # 检查当前时间是否已经过了9:15
                current_time = current_dt.hour * 60 + current_dt.minute
                if current_time < 555:  # 9:15 = 555分钟
                    return False
                return True

            # 如果次日已经过去（昨天或更早），应该获取
            if next_dt.date() < current_dt.date():
                return True

            # 如果次日是未来日期，不获取
            return False

        except Exception:
            # 如果解析失败，默认获取（保持原有行为）
            return True

    @staticmethod
    def _is_yesterday_trade_date(trade_date: str, current_trade_date: str) -> bool:
        try:
            trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            current_dt = datetime.strptime(current_trade_date, "%Y-%m-%d")
        except ValueError:
            return False
        return (current_dt - trade_dt).days == 1

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
