from __future__ import annotations

from datetime import datetime


class WatchlistRepository:
    def __init__(self, database):
        self.database = database

    def add(self, item) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist_items
                (stock_code, stock_name, strategy_name, signal_level, title, message,
                 reason_text, trigger_time, price, pct_chg, is_read, is_archived, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.stock_code,
                    item.stock_name,
                    item.strategy_name,
                    item.signal_level,
                    item.title,
                    item.message,
                    item.reason_text,
                    item.trigger_time,
                    item.price,
                    item.pct_chg,
                    int(item.is_read),
                    0,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
