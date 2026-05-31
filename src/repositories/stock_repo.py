from __future__ import annotations

from datetime import datetime


class StockRepository:
    def __init__(self, database):
        self.database = database

    def upsert_many(self, stocks: list) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.database.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO stocks
                (stock_code, stock_name, market, industry, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(stock.code, stock.name, stock.market, stock.industry, stock.is_active, now) for stock in stocks],
            )
