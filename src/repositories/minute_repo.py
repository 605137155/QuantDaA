from __future__ import annotations

from src.models.candle import Candle


class MinuteBarRepository:
    def __init__(self, database):
        self.database = database

    def replace_for_stock(self, stock_code: str, bars: list[Candle]) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM minute_bars WHERE stock_code = ?", (stock_code,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO minute_bars
                (stock_code, minute_time, open, high, low, close, volume, amount, trade_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        bar.stock_code,
                        bar.ts,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.amount,
                        bar.ts[:10],
                    )
                    for bar in bars
                ],
            )

    def get_recent(self, stock_code: str, limit: int) -> list[Candle]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT stock_code, minute_time, open, high, low, close, volume, amount
                FROM minute_bars
                WHERE stock_code = ?
                ORDER BY minute_time DESC
                LIMIT ?
                """,
                (stock_code, limit),
            ).fetchall()
        return [
            Candle(
                stock_code=row[0],
                ts=row[1],
                open=row[2],
                high=row[3],
                low=row[4],
                close=row[5],
                volume=row[6],
                amount=row[7],
            )
            for row in reversed(rows)
        ]
