"""
首板与断板反包竞价融合策略 - 选股窗口UI
数据源: 同花顺涨停板 + 腾讯实时行情
"""
import sys
import logging
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime

logger = logging.getLogger(__name__)


class JQFusionWindow:
    """首板断板融合竞价策略选股窗口"""

    def __init__(self, parent):
        self.parent = parent
        self.window = None
        self._bg_running = False
        self._auto_id = None

        # 策略引擎（延迟初始化）
        self.strategy = None
        self.candidates = None  # 最近一次候选结果

        # UI变量
        self.status_var = None
        self.date_var = None
        self.s1_count_var = None
        self.s2_count_var = None
        self.qualified_var = None

        # Treeview
        self.s1_tree = None
        self.s2_tree = None
        self.s3_tree = None
        self.auction_tree = None
        self.leaderboard_tree = None

        self.holdings_tree = None
        self.buy_sig_tree = None
        self.sell_sig_tree = None
        self._auto_auction_run_today = False

    def show(self, date_str=None):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return

        # 延迟导入避免循环引用
        from src.strategies.jq_fusion_strategy import JQFusionStrategy
        self.strategy = JQFusionStrategy()

        self.window = tk.Toplevel(self.parent)
        self.window.title("首板与断板反包竞价融合策略")
        self.window.geometry("1280x800")
        self.window.minsize(1000, 600)

        self._build_toolbar(date_str)
        self._build_main()
        self._build_bottom()

        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_auto()

        # 初始加载持仓列表与排行榜
        self._refresh_holdings_list()
        self._refresh_signals_list()
        self._refresh_leaderboard_list()

        # 自动触发盘前扫描（延迟1秒，确保UI渲染完全就绪）
        self.window.after(1000, self._run_premarket)

    def _build_toolbar(self, date_str=None):
        bar = ttk.Frame(self.window, padding=5)
        bar.pack(fill='x')

        ttk.Label(bar, text="首板与断板反包竞价融合策略",
                  font=('Microsoft YaHei', 12, 'bold')).pack(side='left', padx=5)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, foreground='gray').pack(side='left', padx=15)

        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        self.date_var = tk.StringVar(value=date_str)
        ttk.Label(bar, text="日期:").pack(side='left', padx=(20, 2))
        ttk.Label(bar, textvariable=self.date_var, font=('Consolas', 10)).pack(side='left')

        self.s1_count_var = tk.StringVar(value="S1: -")
        self.s2_count_var = tk.StringVar(value="S2: -")
        self.qualified_var = tk.StringVar(value="通过: -")
        ttk.Label(bar, textvariable=self.s1_count_var).pack(side='left', padx=15)
        ttk.Label(bar, textvariable=self.s2_count_var).pack(side='left', padx=5)
        ttk.Label(bar, textvariable=self.qualified_var).pack(side='left', padx=5)

        ttk.Button(bar, text="刷新", command=self._manual_refresh).pack(side='right', padx=2)

    def _build_main(self):
        main = ttk.Frame(self.window)
        main.pack(fill='both', expand=True, padx=5, pady=2)

        # 左侧候选池
        left = ttk.Frame(main, width=300)
        left.pack(side='left', fill='y', padx=(0, 5))
        left.pack_propagate(False)
        self._build_sidebar(left)

        # 右侧Notebook
        right = ttk.Frame(main)
        right.pack(side='left', fill='both', expand=True)
        self._build_notebook(right)

    def _build_sidebar(self, parent):
        # Setup 1
        f1 = ttk.LabelFrame(parent, text="Setup 1 (首板1进2)", padding=3)
        f1.pack(fill='both', expand=True, pady=(0, 3))
        self.s1_tree = ttk.Treeview(f1, columns=("code", "name"), show='headings', height=6)
        self.s1_tree.heading("code", text="代码")
        self.s1_tree.heading("name", text="名称")
        self.s1_tree.column("code", width=100)
        self.s1_tree.column("name", width=80)
        sb1 = ttk.Scrollbar(f1, orient='vertical', command=self.s1_tree.yview)
        self.s1_tree.configure(yscrollcommand=sb1.set)
        self.s1_tree.pack(side='left', fill='both', expand=True)
        sb1.pack(side='right', fill='y')

        # Setup 2
        f2 = ttk.LabelFrame(parent, text="Setup 2 (断板反包)", padding=3)
        f2.pack(fill='both', expand=True, pady=(0, 3))
        self.s2_tree = ttk.Treeview(f2, columns=("code", "name"), show='headings', height=6)
        self.s2_tree.heading("code", text="代码")
        self.s2_tree.heading("name", text="名称")
        self.s2_tree.column("code", width=100)
        self.s2_tree.column("name", width=80)
        sb2 = ttk.Scrollbar(f2, orient='vertical', command=self.s2_tree.yview)
        self.s2_tree.configure(yscrollcommand=sb2.set)
        self.s2_tree.pack(side='left', fill='both', expand=True)
        sb2.pack(side='right', fill='y')

        # Setup 3
        f3 = ttk.LabelFrame(parent, text="Setup 3 (三日断板)", padding=3)
        f3.pack(fill='both', expand=True, pady=(0, 3))
        self.s3_tree = ttk.Treeview(f3, columns=("code", "name"), show='headings', height=6)
        self.s3_tree.heading("code", text="代码")
        self.s3_tree.heading("name", text="名称")
        self.s3_tree.column("code", width=100)
        self.s3_tree.column("name", width=80)
        sb3 = ttk.Scrollbar(f3, orient='vertical', command=self.s3_tree.yview)
        self.s3_tree.configure(yscrollcommand=sb3.set)
        self.s3_tree.pack(side='left', fill='both', expand=True)
        sb3.pack(side='right', fill='y')

    def _build_notebook(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill='both', expand=True)

        # Tab1: 竞价重排
        t1 = ttk.Frame(nb)
        nb.add(t1, text="竞价重排")
        self._build_auction_tab(t1)

        # Tab2: 排行榜
        t2 = ttk.Frame(nb)
        nb.add(t2, text="自选涨幅排行榜")
        self._build_leaderboard_tab(t2)

        # Tab3: 持仓与买卖信号
        t3 = ttk.Frame(nb)
        nb.add(t3, text="持仓与买卖信号")
        self._build_holdings_tab(t3)

    def _build_auction_tab(self, parent):
        cols = ("rank", "code", "name", "setup", "condition", "open_gap", "obi", "score", "day_pct")
        self.auction_tree = ttk.Treeview(parent, columns=cols, show='headings')
        headers = {
            "rank": ("排名", 45), "code": ("代码", 90), "name": ("名称", 70),
            "setup": ("类型", 65), "condition": ("命中条件", 180),
            "open_gap": ("开盘%", 60), "obi": ("OBI", 55), "score": ("总分", 55),
            "day_pct": ("当日%", 60),
        }
        for col, (text, w) in headers.items():
            self.auction_tree.heading(col, text=text)
            self.auction_tree.column(col, width=w, anchor='center')
        sb = ttk.Scrollbar(parent, orient='vertical', command=self.auction_tree.yview)
        self.auction_tree.configure(yscrollcommand=sb.set)
        self.auction_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # 绑定右键菜单
        self.auction_menu = tk.Menu(self.window, tearoff=0)
        self.auction_menu.add_command(label="买入此股", command=self._buy_stock_from_menu)
        self.auction_tree.bind("<Button-3>", self._show_auction_menu)

    def _build_leaderboard_tab(self, parent):
        cols = ("rank", "code", "name", "date", "type", "base", "max_price", "pct")
        self.leaderboard_tree = ttk.Treeview(parent, columns=cols, show='headings')
        headers = {
            "rank": ("排名", 45), "code": ("代码", 90), "name": ("名称", 70),
            "date": ("候选日", 80), "type": ("类型", 65),
            "base": ("基准价", 70), "max_price": ("最高价", 70), "pct": ("涨幅%", 65),
        }
        for col, (text, w) in headers.items():
            self.leaderboard_tree.heading(col, text=text)
            self.leaderboard_tree.column(col, width=w, anchor='center')
        sb = ttk.Scrollbar(parent, orient='vertical', command=self.leaderboard_tree.yview)
        self.leaderboard_tree.configure(yscrollcommand=sb.set)
        self.leaderboard_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_bottom(self):
        bar = ttk.Frame(self.window, padding=5)
        bar.pack(fill='x')
        ttk.Button(bar, text="手动盘前扫描", command=self._run_premarket).pack(side='left', padx=3)
        ttk.Button(bar, text="手动竞价匹配", command=self._run_auction).pack(side='left', padx=3)
        ttk.Button(bar, text="更新排行榜", command=self._run_update_board).pack(side='left', padx=3)

    # ========== 持仓与买卖信号 Tab & CRUD ==========

    def _build_holdings_tab(self, parent):
        paned = ttk.PanedWindow(parent, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=5, pady=5)

        # Left: Holdings
        left_f = ttk.LabelFrame(paned, text="当前持仓池", padding=5)
        paned.add(left_f, weight=4)

        self.holdings_tree = ttk.Treeview(left_f, columns=("code", "name", "buy_date", "buy_price", "qty", "avg_cost"), show='headings')
        self.holdings_tree.heading("code", text="代码")
        self.holdings_tree.heading("name", text="名称")
        self.holdings_tree.heading("buy_date", text="买入日期")
        self.holdings_tree.heading("buy_price", text="买入价")
        self.holdings_tree.heading("qty", text="持股数")
        self.holdings_tree.heading("avg_cost", text="成本价")
        
        for c in ("code", "name", "buy_date", "buy_price", "qty", "avg_cost"):
            self.holdings_tree.column(c, width=80, anchor='center')

        sb_h = ttk.Scrollbar(left_f, orient='vertical', command=self.holdings_tree.yview)
        self.holdings_tree.configure(yscrollcommand=sb_h.set)
        self.holdings_tree.pack(side='top', fill='both', expand=True)
        sb_h.pack(side='right', fill='y')

        btn_f = ttk.Frame(left_f, padding=2)
        btn_f.pack(side='bottom', fill='x', pady=5)
        ttk.Button(btn_f, text="手工添加", command=self._add_holding_dialog).pack(side='left', padx=3)
        ttk.Button(btn_f, text="手工移出", command=self._remove_holding_action).pack(side='left', padx=3)
        ttk.Button(btn_f, text="刷新持仓", command=self._refresh_holdings_action).pack(side='left', padx=3)

        # Right: Signals
        right_f = ttk.LabelFrame(paned, text="买卖信号监控", padding=5)
        paned.add(right_f, weight=5)

        nb_sig = ttk.Notebook(right_f)
        nb_sig.pack(fill='both', expand=True)

        # Buy signals subtab
        t_buy = ttk.Frame(nb_sig)
        nb_sig.add(t_buy, text="触发买入池")
        self.buy_sig_tree = ttk.Treeview(t_buy, columns=("pool", "code", "name", "condition", "score"), show='headings')
        self.buy_sig_tree.heading("pool", text="买入池")
        self.buy_sig_tree.heading("code", text="代码")
        self.buy_sig_tree.heading("name", text="名称")
        self.buy_sig_tree.heading("condition", text="触发条件")
        self.buy_sig_tree.heading("score", text="评分")
        for c in ("pool", "code", "name", "condition", "score"):
            self.buy_sig_tree.column(c, width=80, anchor='center')
        sb_b = ttk.Scrollbar(t_buy, orient='vertical', command=self.buy_sig_tree.yview)
        self.buy_sig_tree.configure(yscrollcommand=sb_b.set)
        self.buy_sig_tree.pack(side='left', fill='both', expand=True)
        sb_b.pack(side='right', fill='y')

        # Sell signals subtab
        t_sell = ttk.Frame(nb_sig)
        nb_sig.add(t_sell, text="触发卖出池")
        self.sell_sig_tree = ttk.Treeview(t_sell, columns=("code", "name", "signal", "reason", "price"), show='headings')
        self.sell_sig_tree.heading("code", text="代码")
        self.sell_sig_tree.heading("name", text="名称")
        self.sell_sig_tree.heading("signal", text="卖出信号")
        self.sell_sig_tree.heading("reason", text="触发原因")
        self.sell_sig_tree.heading("price", text="触发价格")
        for c in ("code", "name", "signal", "reason", "price"):
            self.sell_sig_tree.column(c, width=80, anchor='center')
        sb_s = ttk.Scrollbar(t_sell, orient='vertical', command=self.sell_sig_tree.yview)
        self.sell_sig_tree.configure(yscrollcommand=sb_s.set)
        self.sell_sig_tree.pack(side='left', fill='both', expand=True)
        sb_s.pack(side='right', fill='y')

    def _add_holding_dialog(self):
        d = tk.Toplevel(self.window)
        d.title("手工添加持仓")
        d.geometry("300x250")
        d.transient(self.window)
        d.grab_set()

        ttk.Label(d, text="股票代码:").grid(row=0, column=0, padx=10, pady=10)
        code_entry = ttk.Entry(d)
        code_entry.grid(row=0, column=1, padx=10, pady=10)
        code_entry.insert(0, "600367.XSHG")

        ttk.Label(d, text="股票名称:").grid(row=1, column=0, padx=10, pady=10)
        name_entry = ttk.Entry(d)
        name_entry.grid(row=1, column=1, padx=10, pady=10)

        ttk.Label(d, text="买入日期:").grid(row=2, column=0, padx=10, pady=10)
        date_entry = ttk.Entry(d)
        date_entry.grid(row=2, column=1, padx=10, pady=10)
        date_entry.insert(0, self.date_var.get())

        ttk.Label(d, text="买入价格:").grid(row=3, column=0, padx=10, pady=10)
        price_entry = ttk.Entry(d)
        price_entry.grid(row=3, column=1, padx=10, pady=10)

        ttk.Label(d, text="买入股数:").grid(row=4, column=0, padx=10, pady=10)
        qty_entry = ttk.Entry(d)
        qty_entry.grid(row=4, column=1, padx=10, pady=10)
        qty_entry.insert(0, "100")

        def save():
            code = code_entry.get().strip()
            name = name_entry.get().strip()
            bdate = date_entry.get().strip()
            try:
                price = float(price_entry.get().strip())
                qty = int(qty_entry.get().strip())
            except ValueError:
                return
            if not name:
                pure = code.split('.')[0]
                rt = self.strategy.get_names([code])
                name = rt.get(pure, code)
            
            self.strategy.repo.add_holding(code, name, bdate, price, qty, price)
            self._refresh_holdings_list()
            self._refresh_signals_list()
            d.destroy()

        ttk.Button(d, text="保存", command=save).grid(row=5, column=0, columnspan=2, pady=15)

    def _remove_holding_action(self):
        sel = self.holdings_tree.selection()
        if not sel:
            return
        code = self.holdings_tree.item(sel[0], 'values')[0]
        self.strategy.repo.remove_holding(code)
        self._refresh_holdings_list()
        self._refresh_signals_list()

    def _refresh_holdings_action(self):
        self._refresh_holdings_list()
        self._refresh_signals_list()

    def _refresh_holdings_list(self):
        rows = self.strategy.repo.get_holdings()
        self._fill(self.holdings_tree, [
            (r['code'], r['name'], r['buy_date'], f"{r['buy_price']:.2f}", r['qty'], f"{r['avg_cost']:.2f}")
            for r in rows
        ])

    def _refresh_signals_list(self):
        date_str = self.date_var.get()
        signals = self.strategy.repo.get_daily_signals(date_str)
        
        # Fill buy list
        buys = [s for s in signals if s['signal_type'] in ('buy_s1', 'buy_s2', 'buy_s3')]
        self._fill(self.buy_sig_tree, [
            (
                'Setup 1 (1进2)' if s['signal_type'] == 'buy_s1' else ('Setup 2 (断板反包)' if s['signal_type'] == 'buy_s2' else 'Setup 3 (三日断板)'),
                s['code'], s['name'], s['reason'], f"{s['price']:.2f}%" if s['price'] is not None else '-'
            )
            for s in buys
        ])
        
        # Fill sell list
        sells = [s for s in signals if s['signal_type'] in ('sell_tp', 'sell_ma5', 'sell_drop', 'hold_limit')]
        self._fill(self.sell_sig_tree, [
            (
                s['code'], s['name'],
                '持股涨停' if s['signal_type'] == 'hold_limit' else ('止盈卖出' if s['signal_type'] == 'sell_tp' else ('MA5止损' if s['signal_type'] == 'sell_ma5' else '跌幅止损')),
                s['reason'], f"{s['price']:.2f}" if s['price'] is not None else '-'
            )
            for s in sells
        ])

    def _refresh_leaderboard_list(self):
        """加载本地自选股排行榜列表"""
        board = self.strategy.get_leaderboard(30)
        self._fill(self.leaderboard_tree, [
            (i+1, t.code, t.name, t.entry_date, t.setup_type,
             f"{t.base_price:.2f}", f"{t.max_price:.2f}", f"{t.max_pct:.2f}%")
            for i, t in enumerate(board)
        ])

    def _show_auction_menu(self, event):
        item = self.auction_tree.identify_row(event.y)
        if item:
            self.auction_tree.selection_set(item)
            self.auction_menu.post(event.x_root, event.y_root)

    def _buy_stock_from_menu(self):
        sel = self.auction_tree.selection()
        if not sel:
            return
        vals = self.auction_tree.item(sel[0], 'values')
        code = vals[1]
        name = vals[2]
        date_str = self.date_var.get()
        
        d = tk.Toplevel(self.window)
        d.title("买入个股确认")
        d.geometry("300x200")
        d.transient(self.window)
        d.grab_set()

        ttk.Label(d, text=f"买入股票: {code} ({name})").grid(row=0, column=0, columnspan=2, padx=10, pady=10)
        
        ttk.Label(d, text="买入价格:").grid(row=1, column=0, padx=10, pady=5)
        price_entry = ttk.Entry(d)
        price_entry.grid(row=1, column=1, padx=10, pady=5)
        
        # 预估买入价为开盘价
        rt = self.strategy.get_realtime([code])
        q = rt.get(code)
        if q:
            price_entry.insert(0, f"{q.get('open', 0.0):.2f}")

        ttk.Label(d, text="买入股数:").grid(row=2, column=0, padx=10, pady=5)
        qty_entry = ttk.Entry(d)
        qty_entry.grid(row=2, column=1, padx=10, pady=5)
        qty_entry.insert(0, "100")

        def save():
            try:
                price = float(price_entry.get().strip())
                qty = int(qty_entry.get().strip())
            except ValueError:
                return
            self.strategy.repo.add_holding(code, name, date_str, price, qty, price)
            self._refresh_holdings_list()
            self._refresh_signals_list()
            d.destroy()

        ttk.Button(d, text="确认买入", command=save).grid(row=3, column=0, columnspan=2, pady=15)

    def _build_bottom(self):
        bar = ttk.Frame(self.window, padding=5)
        bar.pack(fill='x')
        ttk.Button(bar, text="手动盘前扫描", command=self._run_premarket).pack(side='left', padx=3)
        ttk.Button(bar, text="手动竞价匹配", command=self._run_auction).pack(side='left', padx=3)
        ttk.Button(bar, text="更新排行榜", command=self._run_update_board).pack(side='left', padx=3)

    # ========== 数据操作 ==========

    def _fill(self, tree, rows):
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert('', 'end', values=row)

    def _run_in_bg(self, target, callback=None):
        if self._bg_running:
            return
        self._bg_running = True
        self.status_var.set("执行中...")

        def worker():
            try:
                result = target()
                if callback and self.window and self.window.winfo_exists():
                    self.window.after(0, lambda: callback(result))
            except Exception as e:
                logger.error(f"后台任务异常: {e}")
                if self.window and self.window.winfo_exists():
                    self.window.after(0, lambda: self.status_var.set(f"错误: {e}"))
            finally:
                self._bg_running = False
                if self.window and self.window.winfo_exists():
                    self.window.after(0, lambda: self.status_var.set("就绪"))

        threading.Thread(target=worker, daemon=True).start()

    def _run_premarket(self):
        """盘前扫描"""
        from scripts.ths_candidate_fetcher import fetch_candidates
        import akshare as ak

        # 只要执行扫描就重置自动竞价匹配的状态
        self._auto_auction_run_today = False
        # 清空旧候选，避免扫描期间定时器使用旧候选触发竞价
        self.candidates = None

        # 支持往日回放时保留原日期
        date_str = self.date_var.get()
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
            self.date_var.set(date_str)

        def do_scan():
            # 优先从数据库加载缓存的候选池
            db_cands = self.strategy.repo.get_candidates(date_str)
            if db_cands:
                setup1 = [r['code'] for r in db_cands if r['setup_type'] == '1进2']
                setup2 = [r['code'] for r in db_cands if r['setup_type'] == '断板反包']
                setup3 = [r['code'] for r in db_cands if r['setup_type'] == '三日断板']
                names = {r['code'].split('.')[0]: r['name'] for r in db_cands}
                
                df = ak.tool_trade_date_hist_sina()
                td = [str(d)[:10] for d in df['trade_date']]
                td = [d for d in td if '2026-01-01' <= d <= '2026-12-31']
                y_day = ''
                if date_str in td:
                    idx = td.index(date_str)
                    if idx > 0:
                        y_day = td[idx - 1]
                return {
                    'date': date_str,
                    'yesterday': y_day,
                    'setup1': setup1,
                    'setup2': setup2,
                    'setup3': setup3,
                    'names': names
                }

            # 无缓存则发起 API 扫描
            df = ak.tool_trade_date_hist_sina()
            td = [str(d)[:10] for d in df['trade_date']]
            td = [d for d in td if '2026-01-01' <= d <= '2026-12-31']
            result = fetch_candidates(td, date_str)
            
            # 写入 SQLite 数据库缓存
            if result:
                cands_to_save = []
                names = result.get('names', {})
                for code in result['setup1']:
                    cands_to_save.append({'code': code, 'name': names.get(code.split('.')[0], ''), 'setup_type': '1进2'})
                for code in result['setup2']:
                    cands_to_save.append({'code': code, 'name': names.get(code.split('.')[0], ''), 'setup_type': '断板反包'})
                for code in result.get('setup3', []):
                    cands_to_save.append({'code': code, 'name': names.get(code.split('.')[0], ''), 'setup_type': '三日断板'})
                self.strategy.repo.save_candidates(date_str, cands_to_save)
            return result

        def on_done(result):
            if result is None:
                self.status_var.set("扫描失败或非交易日")
                return
            self.candidates = result
            s1 = result['setup1']
            s2 = result['setup2']
            s3 = result.get('setup3', [])
            names = result['names']

            self._fill(self.s1_tree, [(c, names.get(c.split('.')[0], '')) for c in s1])
            self._fill(self.s2_tree, [(c, names.get(c.split('.')[0], '')) for c in s2])
            self._fill(self.s3_tree, [(c, names.get(c.split('.')[0], '')) for c in s3])

            self.s1_count_var.set(f"S1: {len(s1)}")
            self.s2_count_var.set(f"S2: {len(s2)}")
            self.status_var.set(f"盘前扫描完成: S1={len(s1)} S2={len(s2)} S3={len(s3)}")
            
            self._refresh_signals_list()

            # 自动加载已存的竞价结果
            db_auction = self.strategy.get_saved_auction_display(date_str)
            if db_auction:
                # 先以 '-' 填充当日涨跌幅展示
                self._fill(self.auction_tree, [
                    (r['rank'], r['code'], r['name'], r['setup_type'], r['matched_condition'],
                     f"{r['open_gap_pct']:+.2f}%" if r['open_gap_pct'] is not None else '-',
                     f"{r['obi']:.2f}" if r['obi'] is not None else '-',
                     f"{r['score']:.2f}" if r['score'] is not None else '-',
                     '-')
                    for r in db_auction
                ])
                self.qualified_var.set(f"通过: {len(db_auction)}")
                
                # 如果是今天，异步拉取实时最新涨跌幅并刷新列表
                now = datetime.now()
                today_str = now.strftime('%Y-%m-%d')
                if date_str == today_str:
                    self._auto_auction_run_today = True
                    def update_realtime_pct():
                        try:
                            codes = [r['code'] for r in db_auction]
                            rt = self.strategy.get_realtime(codes)
                            if self.window and self.window.winfo_exists():
                                self.window.after(0, lambda: self._fill(self.auction_tree, [
                                    (r['rank'], r['code'], r['name'], r['setup_type'], r['matched_condition'],
                                     f"{r['open_gap_pct']:+.2f}%" if r['open_gap_pct'] is not None else '-',
                                     f"{r['obi']:.2f}" if r['obi'] is not None else '-',
                                     f"{r['score']:.2f}" if r['score'] is not None else '-',
                                     f"{rt.get(r['code'], {}).get('pct_chg', 0.0):+.2f}%")
                                    for r in db_auction
                                ]))
                        except Exception as e:
                            logger.error(f"异步更新实时涨跌幅失败: {e}")
                    threading.Thread(target=update_realtime_pct, daemon=True).start()
                else:
                    # 如果是历史日期，异步拉取历史当日的涨跌幅并刷新列表
                    def update_historical_pct():
                        try:
                            codes = [r['code'] for r in db_auction]
                            hist_pct = {}
                            from concurrent.futures import ThreadPoolExecutor, as_completed
                            import requests
                            import urllib3
                            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                            
                            def fetch_historical_pct_single(code):
                                pure = code.split('.')[0]
                                prefix = 'sh' if pure.startswith('6') else 'sz'
                                sym = f"{prefix}{pure}"
                                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,60,qfq"
                                try:
                                    resp = requests.get(url, verify=False, timeout=5)
                                    if resp.status_code == 200:
                                        data = resp.json()
                                        qfqday = data.get("data", {}).get(sym, {}).get("qfqday", [])
                                        if qfqday:
                                            for idx, bar in enumerate(qfqday):
                                                if bar[0] == date_str:
                                                    close = float(bar[2])
                                                    if idx > 0:
                                                        prev_close = float(qfqday[idx-1][2])
                                                        pct = (close - prev_close) / prev_close * 100
                                                        return code, pct
                                                    break
                                except:
                                    pass
                                return code, None

                            with ThreadPoolExecutor(max_workers=15) as executor:
                                futures = {executor.submit(fetch_historical_pct_single, c): c for c in codes}
                                for f in as_completed(futures):
                                    c, pct = f.result()
                                    if pct is not None:
                                        hist_pct[c] = pct
                                        
                            if self.window and self.window.winfo_exists():
                                self.window.after(0, lambda: self._fill(self.auction_tree, [
                                    (r['rank'], r['code'], r['name'], r['setup_type'], r['matched_condition'],
                                     f"{r['open_gap_pct']:+.2f}%" if r['open_gap_pct'] is not None else '-',
                                     f"{r['obi']:.2f}" if r['obi'] is not None else '-',
                                     f"{r['score']:.2f}" if r['score'] is not None else '-',
                                     f"{hist_pct[r['code']]:+.2f}%" if r['code'] in hist_pct else '-')
                                    for r in db_auction
                                ]))
                        except Exception as e:
                            logger.error(f"异步更新历史涨跌幅失败: {e}")
                    threading.Thread(target=update_historical_pct, daemon=True).start()
            else:
                # 盘前扫描完成后，如果今天尚未运行竞价，且当前时间已经过了 09:25:30 且匹配的是今天，自动触发竞价打分匹配
                now = datetime.now()
                today_str = now.strftime('%Y-%m-%d')
                if date_str == today_str:
                    if now.hour > 9 or (now.hour == 9 and now.minute >= 26) or (now.hour == 9 and now.minute == 25 and now.second >= 30):
                        if not self._auto_auction_run_today:
                            self._auto_auction_run_today = True
                            self._run_auction()

        self._run_in_bg(do_scan, on_done)

    def _run_auction(self):
        """竞价匹配"""
        if self.candidates is None:
            self.status_var.set("请先执行盘前扫描")
            return

        cands = self.candidates
        strategy = self.strategy
        date_str = self.date_var.get()

        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        if date_str == today_str:
            # 1. 今日 09:25 之前禁止执行竞价匹配
            if now.hour < 9 or (now.hour == 9 and now.minute < 25):
                import tkinter.messagebox as messagebox
                messagebox.showwarning("无法匹配", "今日集合竞价尚未结束（09:25之后才会有有效竞价数据），无法进行匹配！", parent=self.window)
                self.status_var.set("集合竞价尚未结束")
                return

            # 2. 今日 15:30 之后，如果数据库中没有今日的原始竞价缓存，禁止执行匹配
            if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
                db_bidding = strategy.repo.get_raw_bidding(date_str)
                all_codes = cands['setup1'] + cands['setup2'] + cands.get('setup3', [])
                if all_codes and not db_bidding:
                    import tkinter.messagebox as messagebox
                    messagebox.showerror("缺少原始数据", "今日未在交易时间内开启程序，缺少今日竞价原始数据，盘后无法进行追溯计算！", parent=self.window)
                    self.status_var.set("缺少竞价原始数据")
                    return

        def do_match():
            yst_close_map = {}
            yst_vol_map = {}
            yst_turnover_map = {}
            all_codes = cands['setup1'] + cands['setup2'] + cands.get('setup3', [])
            pure_codes = [c.split('.')[0] for c in all_codes]
            names = strategy.get_names(pure_codes)
            
            rt = strategy.get_realtime(all_codes)
            for code in all_codes:
                q = rt.get(code)
                if q:
                    yst_close_map[code] = q.get('open', 0) / (1 + q.get('turnover', 0) / 100) if q.get('turnover', 0) > 0 else q.get('open', 0)
            
            import akshare as ak
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            y_day = cands.get('yesterday', '')

            def fetch_akshare(code):
                pure = code.split('.')[0]
                try:
                    df = ak.stock_zh_a_hist(symbol=pure, period='daily', adjust='',
                                            start_date=y_day.replace('-',''), end_date=y_day.replace('-',''),
                                            timeout=5)
                    if df is not None and not df.empty:
                        return code, float(df.iloc[0]['收盘']), float(df.iloc[0]['成交量']), float(df.iloc[0]['换手率'])
                except:
                    pass
                return code, None, None, None

            def fetch_tencent_kline(code):
                pure = code.split('.')[0]
                prefix = 'sh' if pure.startswith('6') else 'sz'
                sym = f"{prefix}{pure}"
                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,5,qfq"
                try:
                    resp = requests.get(url, verify=False, timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        qfqday = data.get("data", {}).get(sym, {}).get("qfqday", [])
                        target_bar = None
                        for bar in qfqday:
                            if bar[0] == y_day:
                                target_bar = bar
                                break
                        if target_bar is None and len(qfqday) >= 2:
                            target_bar = qfqday[-2]
                        if target_bar:
                            close_val = float(target_bar[2])
                            vol_val = float(target_bar[5])
                            return code, close_val, vol_val
                except:
                    pass
                return code, None, None

            if all_codes:
                with ThreadPoolExecutor(max_workers=15) as executor:
                    futures = {executor.submit(fetch_akshare, code): code for code in all_codes}
                    for future in as_completed(futures):
                        code, close_val, vol_val, turnover_val = future.result()
                        if close_val is not None:
                            yst_close_map[code] = close_val
                            yst_vol_map[code] = vol_val
                            yst_turnover_map[code] = turnover_val

            failed_codes = [c for c in all_codes if c not in yst_vol_map or yst_vol_map[c] is None or yst_vol_map[c] <= 0]
            if failed_codes:
                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {executor.submit(fetch_tencent_kline, code): code for code in failed_codes}
                    for future in as_completed(futures):
                        code, close_val, vol_val = future.result()
                        if close_val is not None:
                            yst_close_map[code] = close_val
                            yst_vol_map[code] = vol_val
                            if code not in yst_turnover_map:
                                yst_turnover_map[code] = 3.0

            for code in all_codes:
                if code not in yst_close_map or yst_close_map[code] <= 0:
                    yst_close_map[code] = 10.0
                if code not in yst_vol_map or yst_vol_map[code] <= 0:
                    yst_vol_map[code] = 100000.0
                if code not in yst_turnover_map:
                    yst_turnover_map[code] = 3.0

            results = strategy.match_auction(date_str, cands['setup1'], cands['setup2'], cands.get('setup3', []), yst_close_map, yst_vol_map, yst_turnover_map, names)

            # 注册新候选到追踪池（持久化）
            strategy.register_candidates(cands['setup1'], names, '1进2', date_str, yst_close_map)
            strategy.register_candidates(cands['setup2'], names, '断板反包', date_str, yst_close_map)
            strategy.register_candidates(cands.get('setup3', []), names, '三日断板', date_str, yst_close_map)

            # 获取持仓列表并检验卖出信号（T-1日及更早持有的股票）
            holdings = strategy.repo.get_holdings()
            sell_signals = strategy.check_holding_signals(date_str, holdings)

            # 自动买入前2只符合条件且未涨停封死的个股
            auto_buys = []
            existing_codes = {h['code'] for h in holdings}
            for r in results:
                if len(auto_buys) >= 2:
                    break
                if r.matched_condition == "未命中":
                    continue
                # 判定是否一字涨停封死
                is_gem = r.code.split('.')[0].startswith('30') or r.code.split('.')[0].startswith('68')
                limit_pct = 19.8 if is_gem else 9.8
                is_open_limit_up = (r.open_gap_pct >= limit_pct - 0.05)
                if is_open_limit_up:
                    continue
                # 排除已持有
                if r.code in existing_codes:
                    continue
                
                yst_close = yst_close_map.get(r.code, 10.0)
                buy_price = yst_close * (1 + r.open_gap_pct / 100)
                auto_buys.append((r.code, r.name, buy_price))

            # 执行自动买入写入持仓
            for code, name, buy_price in auto_buys:
                strategy.repo.add_holding(code, name, date_str, buy_price, 100, buy_price)

            # 写入每日信号表
            daily_sigs_to_save = []
            for r in results:
                if r.matched_condition != "未命中":
                    sig_type = 'buy_s1' if r.setup_type == '1进2' else ('buy_s2' if r.setup_type == '断板反包' else 'buy_s3')
                    is_auto_bought = any(ab[0] == r.code for ab in auto_buys)
                    reason_str = f"自动买入 ({r.matched_condition})" if is_auto_bought else r.matched_condition
                    daily_sigs_to_save.append({
                        'code': r.code,
                        'name': r.name,
                        'signal_type': sig_type,
                        'price': r.open_gap_pct,
                        'reason': reason_str
                    })
            for s in sell_signals:
                daily_sigs_to_save.append({
                    'code': s['code'],
                    'name': s['name'],
                    'signal_type': s['signal_type'],
                    'price': s['price'],
                    'reason': s['reason']
                })
            if daily_sigs_to_save:
                strategy.repo.save_daily_signals(date_str, daily_sigs_to_save)

            day_pct_map = {}
            all_result_codes = [r.code for r in results]
            if all_result_codes:
                rt = strategy.get_realtime(all_result_codes)
                for code, q in rt.items():
                    day_pct_map[code] = q.get('pct_chg', 0)

            # 持久化竞价匹配排名结果
            results_to_save = []
            for idx, r in enumerate(results, 1):
                results_to_save.append({
                    'code': r.code,
                    'name': r.name,
                    'setup_type': r.setup_type,
                    'matched_condition': r.matched_condition,
                    'open_gap_pct': r.open_gap_pct,
                    'vol_ratio': r.vol_ratio,
                    'turnover_rate': yst_turnover_map.get(r.code, 0.0),
                    'score': r.score,
                    'tracked_bonus': r.tracked_bonus,
                    'rank': idx,
                    'bought': 1 if any(ab[0] == r.code for ab in auto_buys) else 0
                })
            strategy.repo.save_auction_results(date_str, results_to_save)

            return results, names, day_pct_map, sell_signals, auto_buys

        def on_done(data):
            if data is None:
                return
            results, names, day_pct_map, sell_signals, auto_buys = data
            self._fill(self.auction_tree, [
                (i+1, r.code, r.name, r.setup_type, r.matched_condition,
                 f"{r.open_gap_pct:+.2f}%", f"{r.obi:.2f}", f"{r.score:.2f}",
                 f"{day_pct_map.get(r.code, 0):+.2f}%")
                for i, r in enumerate(results)
            ])
            self.qualified_var.set(f"通过: {len(results)}")
            
            # 刷新持仓与信号列表
            self._refresh_holdings_list()
            self._refresh_signals_list()

            # 显示自动买入结果提示
            if auto_buys:
                msg_buy = "✅ 自动买入提示：今日已自动买入以下个股并存入持仓池：\n\n"
                for code, name, price in auto_buys:
                    msg_buy += f"• {code} ({name}) | 价格: {price:.2f}\n"
                import tkinter.messagebox as messagebox
                messagebox.showinfo("自动买入成功", msg_buy, parent=self.window)

            # 如有卖出信号，进行弹窗警告
            triggered_sells = [s for s in sell_signals if s['signal_type'] in ('sell_tp', 'sell_ma5', 'sell_drop')]
            if triggered_sells:
                msg = "⚠️ 卖出警报：以下持仓股票今日已触发卖出信号，请及时处理！\n\n"
                for s in triggered_sells:
                    msg += f"• {s['code']} ({s['name']}) -> {s['reason']}\n"
                import tkinter.messagebox as messagebox
                messagebox.showwarning("持仓卖出信号提示", msg, parent=self.window)

        self._run_in_bg(do_match, on_done)

    def _run_update_board(self):
        """更新排行榜"""
        def do_update():
            # 更新自选股今日最高价到数据库中
            self.strategy.update_tracked_highest_prices()
            board = self.strategy.get_leaderboard(30)
            return board

        def on_done(board):
            self._fill(self.leaderboard_tree, [
                (i+1, t.code, t.name, t.entry_date, t.setup_type,
                 f"{t.base_price:.2f}", f"{t.max_price:.2f}", f"{t.max_pct:.2f}%")
                for i, t in enumerate(board)
            ])

        self._run_in_bg(do_update, on_done)

    def _manual_refresh(self):
        self._auto_auction_run_today = False
        self._run_premarket()

    def _schedule_auto(self):
        if self.window is None or not self.window.winfo_exists():
            return
            
        # 自动定时检查：如果是当天并且时间已经过了 09:25:30，自动执行竞价匹配
        now = datetime.now()
        date_str = self.date_var.get()
        today_str = now.strftime('%Y-%m-%d')
        
        if date_str == today_str and self.candidates is not None:
            if now.hour > 9 or (now.hour == 9 and now.minute >= 26) or (now.hour == 9 and now.minute == 25 and now.second >= 30):
                if not self._auto_auction_run_today:
                    self._auto_auction_run_today = True
                    self._run_auction()
                    
        self._auto_id = self.window.after(5000, self._schedule_auto)

    def _on_close(self):
        if self._auto_id:
            self.window.after_cancel(self._auto_id)
        self.window.destroy()
        self.window = None
