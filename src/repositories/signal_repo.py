from __future__ import annotations

from datetime import datetime
from typing import Optional


class SignalRepository:
    def __init__(self, database):
        self.database = database

    def add(self, signal, snapshot) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO signals
                (stock_code, stock_name, strategy_name, signal_level, score, title, message,
                 reason_text, trigger_time, price, pct_chg, cooldown_minutes, dedupe_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.stock_code,
                    signal.stock_name,
                    signal.strategy_name,
                    signal.signal_level,
                    signal.score,
                    signal.title,
                    signal.message,
                    "; ".join(signal.reasons),
                    signal.timestamp,
                    snapshot.last_price,
                    snapshot.pct_chg,
                    signal.cooldown_minutes,
                    signal.dedupe_key,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )

    def get_last_trigger(self, dedupe_key: str) -> Optional[str]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT trigger_time
                FROM signals
                WHERE dedupe_key = ?
                ORDER BY trigger_time DESC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return row[0] if row else None
