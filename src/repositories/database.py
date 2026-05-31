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
                """
            )
