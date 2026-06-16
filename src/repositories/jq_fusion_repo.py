"""首板与断板反包竞价融合策略 - SQLite候选数据库"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta


class JQFusionRepo:
    """候选数据库：管理盘前候选、竞价结果、追踪池、漏选分析"""

    def __init__(self, db_path: str = "data/jq_fusion.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jq_fusion_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    base_price REAL,
                    yesterday_close REAL,
                    yesterday_amount REAL,
                    market_cap REAL,
                    circ_cap REAL,
                    UNIQUE(date, code)
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_auction_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    matched_condition TEXT,
                    open_gap_pct REAL,
                    vol_ratio REAL,
                    turnover_rate REAL,
                    score REAL,
                    tracked_bonus REAL DEFAULT 0,
                    rank INTEGER,
                    bought INTEGER DEFAULT 0,
                    UNIQUE(date, code)
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_tracking (
                    code TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    entry_date TEXT NOT NULL,
                    base_price REAL NOT NULL,
                    max_price REAL NOT NULL,
                    max_pct REAL DEFAULT 0,
                    setup_type TEXT NOT NULL,
                    status TEXT DEFAULT 'tracking',
                    last_update TEXT
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_missed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    open_pct REAL,
                    high_pct REAL,
                    close_pct REAL,
                    is_limit_up INTEGER DEFAULT 0,
                    UNIQUE(date, code)
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_raw_bidding (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    bid_volume REAL NOT NULL,
                    obi REAL NOT NULL,
                    UNIQUE(date, code)
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_holdings (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    buy_date TEXT NOT NULL,
                    buy_price REAL NOT NULL,
                    qty INTEGER NOT NULL,
                    avg_cost REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jq_fusion_daily_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    price REAL,
                    reason TEXT,
                    UNIQUE(date, code, signal_type)
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_date ON jq_fusion_candidates(date);
                CREATE INDEX IF NOT EXISTS idx_auction_date ON jq_fusion_auction_results(date);
                CREATE INDEX IF NOT EXISTS idx_tracking_status ON jq_fusion_tracking(status);
                CREATE INDEX IF NOT EXISTS idx_missed_date ON jq_fusion_missed(date);
                CREATE INDEX IF NOT EXISTS idx_raw_bidding_date ON jq_fusion_raw_bidding(date);
                CREATE INDEX IF NOT EXISTS idx_daily_signals_date ON jq_fusion_daily_signals(date);
            """)
            try:
                conn.execute("ALTER TABLE jq_fusion_tracking ADD COLUMN name TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

    # ========== 盘前候选 ==========

    def save_candidates(self, date: str, candidates: list):
        """批量写入盘前候选"""
        if not candidates:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO jq_fusion_candidates
                   (date, code, name, setup_type, base_price, yesterday_close,
                    yesterday_amount, market_cap, circ_cap)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(date, c['code'], c['name'], c['setup_type'], c.get('base_price'),
                  c.get('yesterday_close'), c.get('yesterday_amount'),
                  c.get('market_cap'), c.get('circ_cap')) for c in candidates]
            )

    def get_candidates(self, date: str) -> list:
        """获取某日盘前候选"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_candidates WHERE date=? ORDER BY setup_type, code",
                (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ========== 竞价结果 ==========

    def save_auction_results(self, date: str, results: list):
        """批量写入竞价匹配结果"""
        if not results:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO jq_fusion_auction_results
                   (date, code, name, setup_type, matched_condition, open_gap_pct,
                    vol_ratio, turnover_rate, score, tracked_bonus, rank, bought)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(date, r['code'], r['name'], r['setup_type'], r.get('matched_condition'),
                  r.get('open_gap_pct'), r.get('vol_ratio'), r.get('turnover_rate'),
                  r.get('score'), r.get('tracked_bonus', 0), r.get('rank'), r.get('bought', 0))
                 for r in results]
            )

    def get_auction_results(self, date: str, bought_only: bool = False) -> list:
        """获取某日竞价结果"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM jq_fusion_auction_results WHERE date=?"
            if bought_only:
                sql += " AND bought=1"
            sql += " ORDER BY rank"
            rows = conn.execute(sql, (date,)).fetchall()
            return [dict(r) for r in rows]

    def get_saved_auction_display(self, date: str) -> list:
        """获取已保存的竞价显示结果（关联原始竞价数据获取 OBI）"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT r.rank, r.code, r.name, r.setup_type, r.matched_condition, 
                       r.open_gap_pct, b.obi, r.score
                FROM jq_fusion_auction_results r
                LEFT JOIN jq_fusion_raw_bidding b ON r.date = b.date AND r.code = b.code
                WHERE r.date = ?
                ORDER BY r.rank
            """, (date,)).fetchall()
            return [dict(r) for r in rows]


    def mark_bought(self, date: str, codes: list):
        """标记某日买入的股票"""
        if not codes:
            return
        with self._conn() as conn:
            conn.executemany(
                "UPDATE jq_fusion_auction_results SET bought=1 WHERE date=? AND code=?",
                [(date, c) for c in codes]
            )

    # ========== 追踪池 ==========

    def upsert_tracking(self, code: str, name: str, entry_date: str, base_price: float,
                        setup_type: str):
        """写入或跳过（不覆盖已存在的）"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO jq_fusion_tracking
                   (code, name, entry_date, base_price, max_price, max_pct, setup_type, status, last_update)
                   VALUES (?, ?, ?, ?, ?, 0, ?, 'tracking', ?)""",
                (code, name, entry_date, base_price, base_price, setup_type, entry_date)
            )

    def update_tracking_max(self, updates: list):
        """批量更新追踪池最高价 [(code, max_price, max_pct), ...]"""
        if not updates:
            return
        now = datetime.now().strftime('%Y-%m-%d')
        with self._conn() as conn:
            conn.executemany(
                """UPDATE jq_fusion_tracking
                   SET max_price=?, max_pct=?, last_update=?
                   WHERE code=? AND max_price < ?""",
                [(u[1], u[2], now, u[0], u[1]) for u in updates]
            )

    def get_active_tracking(self) -> list:
        """获取所有tracking状态的记录"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_tracking WHERE status='tracking' ORDER BY max_pct DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def expire_old_tracking(self, max_days: int = 10):
        """清除超过max_days天的追踪记录"""
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d')
        with self._conn() as conn:
            conn.execute(
                "UPDATE jq_fusion_tracking SET status='expired' WHERE entry_date < ? AND status='tracking'",
                (cutoff,)
            )

    def cleanup_expired(self):
        """删除已过期的记录"""
        with self._conn() as conn:
            conn.execute("DELETE FROM jq_fusion_tracking WHERE status='expired'")

    # ========== 漏选分析 ==========

    def save_missed_analysis(self, date: str, missed: list):
        """写入漏选分析结果"""
        if not missed:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO jq_fusion_missed
                   (date, code, name, setup_type, open_pct, high_pct, close_pct, is_limit_up)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(date, m['code'], m['name'], m['setup_type'],
                  m.get('open_pct'), m.get('high_pct'), m.get('close_pct'),
                  1 if m.get('is_limit_up') else 0) for m in missed]
            )

    def get_missed_analysis(self, date: str) -> list:
        """获取某日漏选分析"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_missed WHERE date=? ORDER BY close_pct DESC",
                (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_missed_limit_up(self, date: str) -> list:
        """获取某日漏选中涨停的"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_missed WHERE date=? AND is_limit_up=1 ORDER BY close_pct DESC",
                (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ========== 竞价原始数据缓存 ==========

    def save_raw_bidding(self, date: str, bidding_list: list):
        """批量写入竞价原始数据"""
        if not bidding_list:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO jq_fusion_raw_bidding
                   (date, code, open_price, bid_volume, obi)
                   VALUES (?, ?, ?, ?, ?)""",
                [(date, b['code'], b['open_price'], b['bid_volume'], b['obi']) for b in bidding_list]
            )

    def get_raw_bidding(self, date: str) -> dict:
        """获取某日竞价原始数据"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_raw_bidding WHERE date=?", (date,)
            ).fetchall()
            return {r['code']: {'open_price': r['open_price'], 'bid_volume': r['bid_volume'], 'obi': r['obi']} for r in rows}

    # ========== 持仓管理 ==========

    def get_holdings(self) -> list:
        """获取所有当前持仓"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM jq_fusion_holdings ORDER BY code").fetchall()
            return [dict(r) for r in rows]

    def add_holding(self, code: str, name: str, buy_date: str, buy_price: float, qty: int, avg_cost: float):
        """添加/更新持仓"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jq_fusion_holdings
                   (code, name, buy_date, buy_price, qty, avg_cost)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code, name, buy_date, buy_price, qty, avg_cost)
            )

    def remove_holding(self, code: str):
        """移除持仓"""
        with self._conn() as conn:
            conn.execute("DELETE FROM jq_fusion_holdings WHERE code=?", (code,))

    # ========== 每日买卖信号持久化 ==========

    def save_daily_signals(self, date: str, signals: list):
        """批量写入每日信号"""
        if not signals:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO jq_fusion_daily_signals
                   (date, code, name, signal_type, price, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(date, s['code'], s['name'], s['signal_type'], s.get('price'), s.get('reason')) for s in signals]
            )

    def get_daily_signals(self, date: str) -> list:
        """获取某日信号"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jq_fusion_daily_signals WHERE date=?", (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ========== 清理 ==========

    def cleanup_old_data(self, days: int = 10):
        """清除超过N天的所有旧数据"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        with self._conn() as conn:
            conn.execute("DELETE FROM jq_fusion_candidates WHERE date < ?", (cutoff,))
            conn.execute("DELETE FROM jq_fusion_auction_results WHERE date < ?", (cutoff,))
            conn.execute("DELETE FROM jq_fusion_missed WHERE date < ?", (cutoff,))
            self.expire_old_tracking(days)

    def get_available_dates(self) -> list:
        """获取有数据的所有日期"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM jq_fusion_candidates ORDER BY date DESC"
            ).fetchall()
            return [r[0] for r in rows]
