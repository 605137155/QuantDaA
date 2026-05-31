from __future__ import annotations

import json
from datetime import datetime


class CandidateScoreRepository:
    def __init__(self, database):
        self.database = database

    def replace_scores(self, trade_date: str, session_type: str, rows: list[dict]) -> None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM candidate_score_snapshots WHERE trade_date = ? AND session_type = ?",
                (trade_date, session_type),
            )
            conn.executemany(
                """
                INSERT INTO candidate_score_snapshots
                (trade_date, session_type, stock_code, stock_name, total_score, grade, heat_score,
                 market_cap_score, volume_price_score, position_score, risk_penalty, flags_json, risks_json, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        trade_date,
                        session_type,
                        row["stock_code"],
                        row["stock_name"],
                        int(row["total_score"]),
                        row["grade"],
                        int(row.get("heat_score", 0)),
                        int(row.get("market_cap_score", 0)),
                        int(row.get("volume_price_score", 0)),
                        int(row.get("position_score", 0)),
                        int(row.get("risk_penalty", 0)),
                        json.dumps(row.get("flags", []), ensure_ascii=False),
                        json.dumps(row.get("risks", []), ensure_ascii=False),
                        json.dumps(row.get("metrics", {}), ensure_ascii=False),
                        created_at,
                    )
                    for row in rows
                ],
            )

    def get_scores(self, trade_date: str, session_type: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT trade_date, session_type, stock_code, stock_name, total_score, grade, heat_score,
                       market_cap_score, volume_price_score, position_score, risk_penalty, flags_json, risks_json, metrics_json
                FROM candidate_score_snapshots
                WHERE trade_date = ? AND session_type = ?
                ORDER BY total_score DESC, stock_code ASC
                """,
                (trade_date, session_type),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_latest_scores(self, session_type: str) -> list[dict]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT trade_date
                FROM candidate_score_snapshots
                WHERE session_type = ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (session_type,),
            ).fetchone()
        if row is None:
            return []
        return self.get_scores(row[0], session_type)

    def get_available_trade_dates(self, session_type: str) -> list[str]:
        table_name = "candidate_score_history" if session_type == "intraday" else "candidate_score_snapshots"
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT trade_date
                FROM {table_name}
                WHERE session_type = ?
                ORDER BY trade_date DESC
                """,
                (session_type,),
            ).fetchall()
        return [row[0] for row in rows]

    def append_history_snapshot(self, snapshot_time: str, trade_date: str, session_type: str, rows: list[dict]) -> None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.database.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO candidate_score_history
                (snapshot_time, trade_date, session_type, stock_code, stock_name, total_score, grade, heat_score,
                 market_cap_score, volume_price_score, position_score, risk_penalty, flags_json, risks_json, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_time,
                        trade_date,
                        session_type,
                        row["stock_code"],
                        row["stock_name"],
                        int(row["total_score"]),
                        row["grade"],
                        int(row.get("heat_score", 0)),
                        int(row.get("market_cap_score", 0)),
                        int(row.get("volume_price_score", 0)),
                        int(row.get("position_score", 0)),
                        int(row.get("risk_penalty", 0)),
                        json.dumps(row.get("flags", []), ensure_ascii=False),
                        json.dumps(row.get("risks", []), ensure_ascii=False),
                        json.dumps(row.get("metrics", {}), ensure_ascii=False),
                        created_at,
                    )
                    for row in rows
                ],
            )

    def get_history(self, trade_date: str, session_type: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_time, trade_date, session_type, stock_code, stock_name, total_score, grade,
                       heat_score, market_cap_score, volume_price_score, position_score, risk_penalty,
                       flags_json, risks_json, metrics_json
                FROM candidate_score_history
                WHERE trade_date = ? AND session_type = ?
                ORDER BY snapshot_time ASC, total_score DESC, stock_code ASC
                """,
                (trade_date, session_type),
            ).fetchall()
        return [self._history_row_to_dict(row) for row in rows]

    def get_history_snapshot_times(self, trade_date: str, session_type: str) -> list[str]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT snapshot_time
                FROM candidate_score_history
                WHERE trade_date = ? AND session_type = ?
                ORDER BY snapshot_time ASC
                """,
                (trade_date, session_type),
            ).fetchall()
        return [row[0] for row in rows]

    def get_history_snapshot(self, snapshot_time: str, session_type: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_time, trade_date, session_type, stock_code, stock_name, total_score, grade,
                       heat_score, market_cap_score, volume_price_score, position_score, risk_penalty,
                       flags_json, risks_json, metrics_json
                FROM candidate_score_history
                WHERE snapshot_time = ? AND session_type = ?
                ORDER BY total_score DESC, stock_code ASC
                """,
                (snapshot_time, session_type),
            ).fetchall()
        return [self._history_row_to_dict(row) for row in rows]

    def get_latest_history_snapshot(self, trade_date: str, session_type: str) -> list[dict]:
        snapshot_times = self.get_history_snapshot_times(trade_date, session_type)
        if not snapshot_times:
            return []
        return self.get_history_snapshot(snapshot_times[-1], session_type)

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "trade_date": row[0],
            "session_type": row[1],
            "stock_code": row[2],
            "stock_name": row[3],
            "total_score": row[4],
            "grade": row[5],
            "heat_score": row[6],
            "market_cap_score": row[7],
            "volume_price_score": row[8],
            "position_score": row[9],
            "risk_penalty": row[10],
            "flags": json.loads(row[11] or "[]"),
            "risks": json.loads(row[12] or "[]"),
            "metrics": json.loads(row[13] or "{}"),
        }

    @staticmethod
    def _history_row_to_dict(row) -> dict:
        return {
            "snapshot_time": row[0],
            "trade_date": row[1],
            "session_type": row[2],
            "stock_code": row[3],
            "stock_name": row[4],
            "total_score": row[5],
            "grade": row[6],
            "heat_score": row[7],
            "market_cap_score": row[8],
            "volume_price_score": row[9],
            "position_score": row[10],
            "risk_penalty": row[11],
            "flags": json.loads(row[12] or "[]"),
            "risks": json.loads(row[13] or "[]"),
            "metrics": json.loads(row[14] or "{}"),
        }
