from __future__ import annotations

from src.models.candle import Candle


class DailyBarRepository:
    def __init__(self, database):
        self.database = database

    def replace_for_stock(self, stock_code: str, bars: list[Candle]) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM daily_bars WHERE stock_code = ?", (stock_code,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bars
                (stock_code, trade_date, open, high, low, close, volume, amount, pct_chg)
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
                        bar.pct_chg,
                    )
                    for bar in bars
                ],
            )

    def get_recent(self, stock_code: str, limit: int) -> list[Candle]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT stock_code, trade_date, open, high, low, close, volume, amount, pct_chg
                FROM daily_bars
                WHERE stock_code = ?
                ORDER BY trade_date DESC
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
                pct_chg=row[8],
            )
            for row in reversed(rows)
        ]
