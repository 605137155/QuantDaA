from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    stock_code TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL,
                    market TEXT,
                    industry TEXT,
                    is_active INTEGER DEFAULT 1,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_bars (
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL,
                    amount REAL,
                    pct_chg REAL,
                    PRIMARY KEY (stock_code, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date
                ON daily_bars (stock_code, trade_date);

                CREATE TABLE IF NOT EXISTS minute_bars (
                    stock_code TEXT NOT NULL,
                    minute_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL,
                    amount REAL,
                    trade_date TEXT NOT NULL,
                    PRIMARY KEY (stock_code, minute_time)
                );
                CREATE INDEX IF NOT EXISTS idx_minute_bars_code_date_time
                ON minute_bars (stock_code, trade_date, minute_time);

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    signal_level TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reason_text TEXT,
                    trigger_time TEXT NOT NULL,
                    price REAL,
                    pct_chg REAL,
                    cooldown_minutes INTEGER DEFAULT 20,
                    dedupe_key TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signals_code_time
                ON signals (stock_code, trigger_time);
                CREATE INDEX IF NOT EXISTS idx_signals_strategy_time
                ON signals (strategy_name, trigger_time);
                CREATE INDEX IF NOT EXISTS idx_signals_dedupe_key
                ON signals (dedupe_key);

                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    signal_level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reason_text TEXT,
                    trigger_time TEXT NOT NULL,
                    price REAL,
                    pct_chg REAL,
                    is_read INTEGER DEFAULT 0,
                    is_archived INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_watchlist_items_time
                ON watchlist_items (trigger_time);
                CREATE INDEX IF NOT EXISTS idx_watchlist_items_read_archived
                ON watchlist_items (is_read, is_archived);

                CREATE TABLE IF NOT EXISTS rank_snapshots (
                    trade_date TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    rank_no INTEGER NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    pct_chg REAL DEFAULT 0,
                    amount REAL DEFAULT 0,
                    extra_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, snapshot_type, rank_no, stock_code)
                );
                CREATE INDEX IF NOT EXISTS idx_rank_snapshots_type_date
                ON rank_snapshots (snapshot_type, trade_date);
                CREATE INDEX IF NOT EXISTS idx_rank_snapshots_code
                ON rank_snapshots (stock_code, trade_date);

                CREATE TABLE IF NOT EXISTS candidate_score_snapshots (
                    trade_date TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    heat_score INTEGER DEFAULT 0,
                    market_cap_score INTEGER DEFAULT 0,
                    volume_price_score INTEGER DEFAULT 0,
                    position_score INTEGER DEFAULT 0,
                    risk_penalty INTEGER DEFAULT 0,
                    flags_json TEXT,
                    risks_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, session_type, stock_code)
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_scores_session_date
                ON candidate_score_snapshots (session_type, trade_date);
                CREATE INDEX IF NOT EXISTS idx_candidate_scores_score
                ON candidate_score_snapshots (trade_date, session_type, total_score DESC);

                CREATE TABLE IF NOT EXISTS candidate_score_history (
                    snapshot_time TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    heat_score INTEGER DEFAULT 0,
                    market_cap_score INTEGER DEFAULT 0,
                    volume_price_score INTEGER DEFAULT 0,
                    position_score INTEGER DEFAULT 0,
                    risk_penalty INTEGER DEFAULT 0,
                    flags_json TEXT,
                    risks_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (snapshot_time, session_type, stock_code)
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_score_history_trade_session
                ON candidate_score_history (trade_date, session_type, snapshot_time);
                """
            )
            self._ensure_column(conn, "candidate_score_snapshots", "market_cap_score", "INTEGER DEFAULT 0")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        column_names = {row[1] for row in rows}
        if column_name in column_names:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
