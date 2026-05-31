from __future__ import annotations

import json
from datetime import datetime


class RankSnapshotRepository:
    def __init__(self, database):
        self.database = database

    def replace_snapshot(self, trade_date: str, snapshot_type: str, rows: list[dict]) -> None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM rank_snapshots WHERE trade_date = ? AND snapshot_type = ?",
                (trade_date, snapshot_type),
            )
            conn.executemany(
                """
                INSERT INTO rank_snapshots
                (trade_date, snapshot_type, rank_no, stock_code, stock_name, pct_chg, amount, extra_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        trade_date,
                        snapshot_type,
                        int(row["rank_no"]),
                        row["stock_code"],
                        row["stock_name"],
                        float(row.get("pct_chg", 0.0) or 0.0),
                        float(row.get("amount", 0.0) or 0.0),
                        json.dumps(row.get("extra", {}), ensure_ascii=False),
                        created_at,
                    )
                    for row in rows
                ],
            )

    def get_snapshot(self, trade_date: str, snapshot_type: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT trade_date, snapshot_type, rank_no, stock_code, stock_name, pct_chg, amount, extra_json
                FROM rank_snapshots
                WHERE trade_date = ? AND snapshot_type = ?
                ORDER BY rank_no ASC, stock_code ASC
                """,
                (trade_date, snapshot_type),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_latest_snapshot(self, snapshot_type: str) -> list[dict]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT trade_date
                FROM rank_snapshots
                WHERE snapshot_type = ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (snapshot_type,),
            ).fetchone()
        if row is None:
            return []
        return self.get_snapshot(row[0], snapshot_type)

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "trade_date": row[0],
            "snapshot_type": row[1],
            "rank_no": row[2],
            "stock_code": row[3],
            "stock_name": row[4],
            "pct_chg": row[5],
            "amount": row[6],
            "extra": json.loads(row[7] or "{}"),
        }
