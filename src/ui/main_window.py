from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from src.data_providers.ths_hot_provider import THSHotProvider


def launch_desktop_app(app_runner) -> None:
    root = tk.Tk()
    QuantDaAMainWindow(root, app_runner)
    root.mainloop()


class QuantDaAMainWindow:
    def __init__(self, root: tk.Tk, app_runner):
        self.root = root
        self.app_runner = app_runner
        self.selected_stock_code: Optional[str] = None
        self.selected_daily_date = ""
        self.monitor_rows: list = []
        self.signal_rows: list = []
        self.rank_mode = tk.StringVar(value="monitor")
        self.review_date_var = tk.StringVar(value="")
        self.review_trade_date = ""
        self.review_date_combo = None
        self.current_detail = None
        self.current_candidate_map: dict[str, dict] = {}
        self.hover_stock_code = ""
        self.hover_item_id = ""
        self._pending_hover_code = ""
        self._hover_preview_job = None
        self._hover_preview_delay_ms = 180
        self._refresh_job = None
        self._scan_job = None
        self.popup_notifier = None

        # 同花顺热门榜单数据
        self.ths_provider = THSHotProvider()
        self.ths_hourly_hot: list = []  # 24小时热榜
        self.ths_value_hot: list = []   # 价值投资热榜
        self._ths_refresh_job = None  # 同花顺定时刷新任务
        # 从配置文件读取刷新间隔，默认60秒
        ths_refresh_seconds = self.app_runner.settings.get("hot_score", {}).get("ths_refresh_seconds", 60)
        self._ths_base_interval = int(ths_refresh_seconds) * 1000  # 基础间隔（毫秒）
        self._ths_random_range = 10000  # 随机范围±10秒（毫秒）
        self._ths_last_update = ""  # 上次更新时间

        self.root.title("QuantDaA 热门股监控")
        self._configure_window_size()

        self._build_layout()
        self.popup_notifier = PopupNotifier(self.root, on_click=self._on_popup_click)
        self.root.after(80, self._ensure_window_visible)
        self._initial_load()
        self._schedule_jobs()

    def _configure_window_size(self) -> None:
        self.root.minsize(1260, 800)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(max(int(screen_width * 0.74), 1400), max(screen_width - 40, 1400))
        height = min(max(int(screen_height * 0.82), 880), max(screen_height - 60, 880))
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _ensure_window_visible(self) -> None:
        self.root.update_idletasks()

    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        title = tk.Label(
            self.root,
            text="QuantDaA 热门股监控",
            font=("Microsoft YaHei UI", 18, "bold"),
            anchor="w",
            padx=16,
            pady=10,
            bg="#eef3f8",
        )
        title.grid(row=0, column=0, columnspan=2, sticky="ew")

        content = tk.Frame(self.root, bg="#eef3f8")
        content.grid(row=1, column=0, columnspan=2, sticky="nsew")
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=7)
        content.grid_rowconfigure(1, weight=3)
        content.grid_rowconfigure(2, weight=2)
        self.content = content

        self.status_var = tk.StringVar(value="准备中")
        tk.Label(content, textvariable=self.status_var, anchor="w", fg="#475467", bg="#eef3f8", padx=10, pady=6).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )

        rank_frame = tk.Frame(content, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        signal_frame = tk.Frame(content, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        top_right_frame = tk.Frame(content, bg="#eef3f8")
        minute_frame = tk.Frame(content, bg="#eef3f8")
        self.top_right_frame = top_right_frame
        self.minute_frame = minute_frame
        self.signal_frame = signal_frame

        rank_frame.grid(row=1, column=0, sticky="nsew", padx=(8, 6), pady=(4, 6))
        top_right_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 8), pady=(4, 6))
        signal_frame.grid(row=2, column=0, sticky="nsew", padx=(8, 6), pady=(6, 8))
        minute_frame.grid(row=2, column=1, sticky="nsew", padx=(6, 8), pady=(6, 8))

        rank_frame.grid_columnconfigure(0, weight=1)
        rank_frame.grid_rowconfigure(1, weight=1)
        signal_frame.grid_columnconfigure(0, weight=1)
        signal_frame.grid_rowconfigure(0, weight=1)
        top_right_frame.grid_columnconfigure(0, weight=5)
        top_right_frame.grid_columnconfigure(1, weight=3)
        top_right_frame.grid_rowconfigure(0, weight=1)
        top_right_frame.grid_rowconfigure(1, weight=5)
        minute_frame.grid_columnconfigure(0, weight=3)
        minute_frame.grid_columnconfigure(1, weight=2)
        minute_frame.grid_rowconfigure(0, weight=1)

        overview_frame = tk.Frame(top_right_frame, bg="#dce7f3", padx=12, pady=10, bd=1, relief="solid")
        daily_frame = tk.Frame(top_right_frame, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        daily_signal_frame = tk.Frame(top_right_frame, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        self.overview_frame = overview_frame
        self.daily_frame = daily_frame
        self.daily_signal_frame = daily_signal_frame
        overview_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        overview_frame.grid_configure(columnspan=2)
        daily_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0), padx=(0, 6))
        daily_signal_frame.grid(row=1, column=1, sticky="nsew", pady=(6, 0), padx=(6, 0))

        overview_frame.grid_columnconfigure(0, weight=1)
        daily_frame.grid_columnconfigure(0, weight=1)
        daily_frame.grid_rowconfigure(1, weight=1)
        daily_signal_frame.grid_columnconfigure(0, weight=1)
        daily_signal_frame.grid_rowconfigure(1, weight=1)

        rank_bar = tk.Frame(rank_frame, bg="#ffffff")
        rank_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        rank_bar.grid_columnconfigure(0, weight=1)
        tk.Label(rank_bar, text="排行视图", bg="#ffffff", fg="#344054").grid(row=0, column=0, sticky="w")

        rank_row1 = tk.Frame(rank_bar, bg="#ffffff")
        rank_row1.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Radiobutton(rank_row1, text="成交额前100", value="monitor", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=(0, 4)
        )
        ttk.Radiobutton(rank_row1, text="重点池", value="focus", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )
        ttk.Radiobutton(rank_row1, text="同花顺24小时热榜", value="ths_hourly", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )

        rank_row2 = tk.Frame(rank_bar, bg="#ffffff")
        rank_row2.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
        ttk.Radiobutton(rank_row2, text="同花顺价值投资", value="ths_value", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=(0, 4)
        )
        ttk.Radiobutton(rank_row2, text="复盘候选", value="replay_candidate", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )
        ttk.Radiobutton(rank_row2, text="盘中候选", value="intraday_candidate", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )

        review_row = tk.Frame(rank_bar, bg="#ffffff")
        review_row.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
        tk.Label(review_row, text="回看日期", bg="#ffffff", fg="#344054").pack(side="left", padx=(0, 6))
        self.review_date_combo = ttk.Combobox(review_row, textvariable=self.review_date_var, width=16, state="disabled")
        self.review_date_combo.pack(side="left", padx=(0, 6))
        self.review_date_combo.bind("<<ComboboxSelected>>", self._on_review_date_selected)
        ttk.Button(review_row, text="清除", command=self._clear_review_date, width=5).pack(side="left")

        self.hot_tree = ttk.Treeview(
            rank_frame,
            columns=("rank", "code", "name", "pct", "amount"),
            show="headings",
            height=14,
        )
        for col, text, width in (
            ("rank", "排名", 50),
            ("code", "代码", 90),
            ("name", "名称", 120),
            ("pct", "涨幅%", 70),
            ("amount", "成交额", 120),
        ):
            self.hot_tree.heading(col, text=text)
            self.hot_tree.column(col, width=width, anchor="center")
        self.hot_tree.grid(row=1, column=0, sticky="nsew")
        hot_scroll = ttk.Scrollbar(rank_frame, orient="vertical", command=self.hot_tree.yview)
        hot_scroll.grid(row=1, column=1, sticky="ns")
        self.hot_tree.configure(yscrollcommand=hot_scroll.set)
        self.hot_tree.tag_configure("hover", background="#344054", foreground="#ffffff")
        self.hot_tree.bind("<<TreeviewSelect>>", self._on_hot_tree_select)
        self.hot_tree.bind("<Motion>", self._on_hot_tree_hover)
        self.hot_tree.bind("<Leave>", self._on_hot_tree_leave)

        tk.Label(overview_frame, text="股票概览", anchor="w", font=("Microsoft YaHei UI", 11, "bold"), bg="#dce7f3").grid(
            row=0, column=0, sticky="ew"
        )
        self.detail_var = tk.StringVar(value="选择左侧热门股或候选信号后查看详情")
        self.detail_label = tk.Label(
            overview_frame,
            textvariable=self.detail_var,
            anchor="w",
            justify="left",
            bg="#dce7f3",
            fg="#243b53",
            font=("Microsoft YaHei UI", 9),
        )
        self.detail_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        tk.Label(daily_frame, text="60日线", anchor="w", font=("Microsoft YaHei UI", 12, "bold"), bg="#ffffff").grid(
            row=0, column=0, sticky="ew"
        )
        self.daily_chart = SimpleLineChart(
            daily_frame,
            title="近60日日线",
            on_select=self._on_daily_bar_selected,
            on_hover=self._on_daily_bar_hover,
        )
        self.daily_chart.frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        daily_frame.grid_rowconfigure(1, weight=1)

        signal_frame.grid_rowconfigure(1, weight=3)
        signal_frame.grid_rowconfigure(3, weight=2)
        tk.Label(signal_frame, text="分时候选信号", anchor="w", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff").grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(8, 8), padx=8
        )

        self.signal_tree = ttk.Treeview(
            signal_frame,
            columns=("time", "stock", "strategy", "level"),
            show="headings",
            height=8,
        )
        for col, text, width in (
            ("time", "时间", 130),
            ("stock", "股票", 120),
            ("strategy", "策略", 110),
            ("level", "级别", 80),
        ):
            self.signal_tree.heading(col, text=text)
            self.signal_tree.column(col, width=width, anchor="center")
        self.signal_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0))
        signal_scroll = ttk.Scrollbar(signal_frame, orient="vertical", command=self.signal_tree.yview)
        signal_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8))
        self.signal_tree.configure(yscrollcommand=signal_scroll.set)
        self.signal_tree.bind("<<TreeviewSelect>>", self._on_signal_tree_select)
        tk.Label(signal_frame, text="信号原因", anchor="w", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(12, 6), padx=8
        )
        self.reason_text = tk.Text(signal_frame, height=8, wrap="word")
        self.reason_text.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=8, pady=(0, 8))

        tk.Label(daily_signal_frame, text="日线候选信号", anchor="w", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff").grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        tk.Label(
            daily_signal_frame,
            text="这里预留给后续日线候选信号列表和图形。",
            bg="#ffffff",
            fg="#667085",
            anchor="center",
            justify="center",
        ).grid(row=1, column=0, sticky="nsew")

        live_frame = tk.Frame(minute_frame, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        replay_frame = tk.Frame(minute_frame, bg="#ffffff", padx=10, pady=10, bd=1, relief="solid")
        self.live_frame = live_frame
        self.replay_frame = replay_frame
        live_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        replay_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.live_chart = SimpleMinuteChart(live_frame, title="实时/当日分时")
        live_frame.grid_columnconfigure(0, weight=1)
        live_frame.grid_rowconfigure(0, weight=1)
        self.live_chart.frame.grid(row=0, column=0, sticky="nsew")

        self.replay_chart = SimpleMinuteChart(replay_frame, title="复盘分时")
        replay_frame.grid_columnconfigure(0, weight=1)
        replay_frame.grid_rowconfigure(0, weight=1)
        self.replay_chart.frame.grid(row=0, column=0, sticky="nsew")
        self.live_hint_var = tk.StringVar(value="")
        self.replay_hint_var = tk.StringVar(value="")
        self.live_hint_label = tk.Label(
            live_frame,
            textvariable=self.live_hint_var,
            anchor="w",
            justify="left",
            bg="#ffffff",
            fg="#175cd3",
            font=("Microsoft YaHei UI", 8),
            wraplength=360,
        )
        self.live_hint_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.replay_hint_label = tk.Label(
            replay_frame,
            textvariable=self.replay_hint_var,
            anchor="w",
            justify="left",
            bg="#ffffff",
            fg="#b42318",
            font=("Microsoft YaHei UI", 8),
            wraplength=260,
        )
        self.replay_hint_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.root.bind("<Configure>", self._on_window_resize)

    def _on_window_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        total_width = max(self.root.winfo_width() - 24, 1260)
        total_height = max(self.root.winfo_height() - 112, 780)
        left_width = max(340, int(total_width * 0.31))
        right_width = max(700, total_width - left_width)
        top_height = max(360, int(total_height * 0.57))
        bottom_height = max(300, total_height - top_height)

        self.content.grid_columnconfigure(0, minsize=left_width)
        self.content.grid_columnconfigure(1, minsize=right_width)
        self.content.grid_rowconfigure(1, minsize=top_height)
        self.content.grid_rowconfigure(2, minsize=bottom_height)
        self.top_right_frame.grid_rowconfigure(0, minsize=108)
        self.top_right_frame.grid_rowconfigure(1, minsize=max(top_height - 120, 240))
        self.minute_frame.grid_columnconfigure(0, minsize=max(int(right_width * 0.56), 360))
        self.minute_frame.grid_columnconfigure(1, minsize=max(int(right_width * 0.34), 250))
        self.signal_frame.grid_rowconfigure(1, minsize=max(int(bottom_height * 0.58), 170))
        self.signal_frame.grid_rowconfigure(3, minsize=max(int(bottom_height * 0.32), 110))
        if hasattr(self, "live_hint_label"):
            self.live_hint_label.configure(wraplength=max(int(right_width * 0.54) - 48, 220))
        if hasattr(self, "replay_hint_label"):
            self.replay_hint_label.configure(wraplength=max(int(right_width * 0.36) - 48, 180))
        if hasattr(self, "detail_label"):
            self.detail_label.configure(wraplength=max(right_width - 72, 360))

    def _initial_load(self) -> None:
        self.app_runner.refresh_pools()
        self.signal_rows = self.app_runner.scan_once()
        self._refresh_hot_tree()
        self._refresh_signal_tree()
        self.app_runner.save_daily_snapshots_if_needed(
            ths_hourly_rows=self.ths_hourly_hot,
            ths_value_rows=self.ths_value_hot,
        )
        if self.monitor_rows:
            self._select_stock(self._row_code(self.monitor_rows[0]))
        self._update_status("初始化完成")

    def _schedule_jobs(self) -> None:
        pool_ms = self.app_runner.get_next_pool_refresh_delay_ms()
        scan_ms = int(self.app_runner.settings["scan"]["signal_scan_seconds"] * 1000)
        self._refresh_job = self.root.after(pool_ms, self._refresh_cycle)
        self._scan_job = self.root.after(scan_ms, self._scan_cycle)

        # 启动同花顺数据定时刷新（首次延迟8-12秒后执行，避免启动时卡顿）
        import random
        first_delay = random.randint(8000, 12000)
        self._ths_refresh_job = self.root.after(first_delay, self._refresh_ths_cycle)

    def _refresh_cycle(self) -> None:
        try:
            self.app_runner.refresh_pools()
            self.app_runner.save_daily_snapshots_if_needed(
                ths_hourly_rows=self.ths_hourly_hot,
                ths_value_rows=self.ths_value_hot,
            )
            self.app_runner.save_intraday_candidate_snapshots_if_needed(
                ths_rows=self.ths_hourly_hot,
                kpl_rows=[],
            )
            self._refresh_hot_tree()
            self._update_status("排行榜已刷新")
        except Exception as exc:
            delay_seconds = self.app_runner.get_next_pool_refresh_delay_ms() // 1000
            self._update_status(f"排行榜刷新失败: {exc} | {delay_seconds} 秒后重试")
        self._refresh_job = self.root.after(self.app_runner.get_next_pool_refresh_delay_ms(), self._refresh_cycle)

    def _scan_cycle(self) -> None:
        new_signals = self.app_runner.scan_once()
        if new_signals:
            self.signal_rows = new_signals + self.signal_rows
            self._refresh_signal_tree()
            self._update_status(f"新增 {len(new_signals)} 条信号")
            for signal in new_signals[:3]:
                self.popup_notifier.show(signal)
        if self.selected_stock_code:
            self._render_stock_detail(self.selected_stock_code)
        self._scan_job = self.root.after(int(self.app_runner.settings["scan"]["signal_scan_seconds"] * 1000), self._scan_cycle)

    def _load_ths_hourly_hot(self) -> None:
        """加载同花顺24小时热榜"""
        try:
            self.ths_hourly_hot = self.ths_provider.get_24h_hot(limit=100)
        except Exception as e:
            print(f"[THS] 加载24小时热榜失败: {e}")
            self.ths_hourly_hot = []

    def _load_ths_value_hot(self) -> None:
        """加载同花顺价值投资热榜"""
        try:
            self.ths_value_hot = self.ths_provider.get_hot_stocks(
                time_type="day",
                list_type="value",
                limit=100
            )
        except Exception as e:
            print(f"[THS] 加载价值投资热榜失败: {e}")
            self.ths_value_hot = []

    def _get_ths_refresh_interval(self) -> int:
        """获取带随机延时的刷新间隔（毫秒）"""
        import random
        # 基础间隔 ± 随机范围
        random_offset = random.randint(-self._ths_random_range, self._ths_random_range)
        interval = max(30000, self._ths_base_interval + random_offset)  # 最少30秒
        return interval

    def _refresh_ths_cycle(self) -> None:
        """定时刷新同花顺热门榜单数据"""
        try:
            from datetime import datetime
            current_time = datetime.now().strftime("%H:%M:%S")

            # 刷新数据
            self._load_ths_hourly_hot()
            self._load_ths_value_hot()
            self._ths_last_update = current_time

            self.app_runner.save_daily_snapshots_if_needed(
                ths_hourly_rows=self.ths_hourly_hot,
                ths_value_rows=self.ths_value_hot,
            )
            # 如果当前正在查看同花顺或候选排行榜，刷新UI显示
            if self.rank_mode.get() in ("ths_hourly", "ths_value", "replay_candidate", "intraday_candidate"):
                self._refresh_hot_tree()

            print(f"[THS] 同花顺热榜数据已更新: {current_time} (24h:{len(self.ths_hourly_hot)}只, 价值:{len(self.ths_value_hot)}只)")

        except Exception as e:
            print(f"[THS] 定时刷新失败: {e}")

        # 设置下次刷新任务（带随机延时）
        next_interval = self._get_ths_refresh_interval()
        self._ths_refresh_job = self.root.after(next_interval, self._refresh_ths_cycle)

    def _refresh_hot_tree(self) -> None:
        current = self.selected_stock_code
        mode = self.rank_mode.get()
        self.current_candidate_map = {}
        review_enabled = bool(self.review_trade_date and mode in ("replay_candidate", "intraday_candidate"))
        self._update_hot_tree_columns(mode, review_enabled)
        self._refresh_review_date_options(mode)

        if mode == "ths_hourly":
            # 同花顺24小时热榜（使用缓存数据，不重复请求）
            self.monitor_rows = self.ths_hourly_hot
        elif mode == "ths_value":
            # 同花顺价值投资热榜（使用缓存数据，不重复请求）
            self.monitor_rows = self.ths_value_hot
        elif mode == "replay_candidate":
            if self.review_trade_date:
                self.monitor_rows = self.app_runner.get_candidate_review_rows("replay", self.review_trade_date)
            else:
                self.monitor_rows = self.app_runner.build_replay_candidate_ranking()
            self.current_candidate_map = {row["stock_code"]: row for row in self.monitor_rows}
        elif mode == "intraday_candidate":
            if self.review_trade_date:
                self.monitor_rows = self.app_runner.get_candidate_review_rows("intraday", self.review_trade_date)
            else:
                self.monitor_rows = self.app_runner.build_intraday_candidate_ranking(ths_rows=self.ths_hourly_hot, kpl_rows=[])
            self.current_candidate_map = {row["stock_code"]: row for row in self.monitor_rows}
        else:
            self.monitor_rows = list(self.app_runner.state.focus_pool if mode == "focus" else self.app_runner.state.monitor_pool)

        self.hot_tree.delete(*self.hot_tree.get_children())
        for idx, row in enumerate(self.monitor_rows, start=1):
            if mode in ("replay_candidate", "intraday_candidate"):
                tags_text = "、".join(row["flags"][:2]) if row["flags"] else "-"
                if self.review_trade_date:
                    perf_text = self._format_candidate_forward_perf(row)
                    amount_value = f"{row['grade']} | {perf_text}"
                else:
                    amount_value = f"{row['grade']} | {tags_text}"
                item_id = self.hot_tree.insert(
                    "",
                    "end",
                    values=(idx, row["stock_code"], row["stock_name"], f"{row['total_score']}", amount_value),
                    tags=(),
                )
            elif mode in ("ths_hourly", "ths_value"):
                # 同花顺数据格式
                amount_text = f"热度:{row.rate:.0f}"
                pct_text = f"{row.rise_and_fall:.2f}"
                item_id = self.hot_tree.insert("", "end", values=(idx, row.code, row.name, pct_text, amount_text), tags=())
            else:
                # 原有数据格式
                amount_text = f"{row.amount / 100000000:.2f}亿"
                item_id = self.hot_tree.insert("", "end", values=(idx, row.code, row.name, f"{row.pct_chg:.2f}", amount_text), tags=())

            if self._row_code(row) == current:
                self.hot_tree.selection_set(item_id)

    def _update_hot_tree_columns(self, mode: str, review_enabled: bool = False) -> None:
        if mode in ("replay_candidate", "intraday_candidate"):
            self.hot_tree.heading("pct", text="候选评分")
            self.hot_tree.heading("amount", text="等级/次日涨幅" if review_enabled else "等级/标签")
            self.hot_tree.column("pct", width=80, anchor="center")
            self.hot_tree.column("amount", width=170 if review_enabled else 150, anchor="center")
            return

        self.hot_tree.heading("pct", text="涨幅%")
        self.hot_tree.heading("amount", text="成交额")
        self.hot_tree.column("pct", width=70, anchor="center")
        self.hot_tree.column("amount", width=120, anchor="center")

    @staticmethod
    def _row_code(row) -> str:
        if isinstance(row, dict):
            return row.get("stock_code") or row.get("code", "")
        return getattr(row, "code", "")

    def _load_review_date(self) -> None:
        self.review_trade_date = self.review_date_var.get().strip()
        self._refresh_hot_tree()
        if self.monitor_rows:
            self._select_stock(self._row_code(self.monitor_rows[0]))
        else:
            self._update_status(f"未找到 {self.review_trade_date} 的候选历史")

    def _on_review_date_selected(self, _event=None) -> None:
        self._load_review_date()

    def _clear_review_date(self) -> None:
        self.review_trade_date = ""
        self.review_date_var.set("")
        self._refresh_hot_tree()
        if self.monitor_rows:
            self._select_stock(self._row_code(self.monitor_rows[0]))

    def _refresh_review_date_options(self, mode: str) -> None:
        if self.review_date_combo is None:
            return
        if mode == "replay_candidate":
            dates = self.app_runner.get_candidate_review_dates("replay")
            self.review_date_combo.configure(state="readonly" if dates else "normal")
            self.review_date_combo["values"] = dates
            return
        if mode == "intraday_candidate":
            dates = self.app_runner.get_candidate_review_dates("intraday")
            self.review_date_combo.configure(state="readonly" if dates else "normal")
            self.review_date_combo["values"] = dates
            return

        self.review_date_combo["values"] = []
        self.review_date_combo.configure(state="disabled")

    @staticmethod
    def _format_candidate_forward_perf(row: dict) -> str:
        next_day_pct = row.get("next_day_pct")
        if next_day_pct is None:
            return "待观察"
        prefix = "当前" if row.get("next_day_mode") == "current" else "次日"
        return f"{prefix}{next_day_pct:+.2f}%"

    def _refresh_signal_tree(self) -> None:
        self.signal_tree.delete(*self.signal_tree.get_children())
        for signal in self.signal_rows[:100]:
            stock_text = f"{signal.stock_name} {signal.stock_code}"
            self.signal_tree.insert("", "end", values=(signal.timestamp, stock_text, signal.strategy_name, signal.signal_level))

    def _on_hot_tree_select(self, _event) -> None:
        selected = self.hot_tree.selection()
        if selected:
            code = self.hot_tree.item(selected[0], "values")[1]
            self._select_stock(code)

    def _on_hot_tree_hover(self, event) -> None:
        item_id = self.hot_tree.identify_row(event.y)
        if not item_id:
            return
        self._set_hover_item(item_id)
        values = self.hot_tree.item(item_id, "values")
        if not values:
            return
        code = values[1]
        if code == self._pending_hover_code:
            return
        if code == self.hover_stock_code and self._hover_preview_job is None:
            return
        self._cancel_hover_preview()
        self._pending_hover_code = code
        self._hover_preview_job = self.root.after(
            self._hover_preview_delay_ms,
            lambda stock_code=code: self._apply_hover_preview(stock_code),
        )

    def _on_hot_tree_leave(self, _event) -> None:
        had_preview = bool(self.hover_stock_code)
        self._cancel_hover_preview()
        self.hover_stock_code = ""
        self._set_hover_item("")
        if had_preview and self.selected_stock_code:
            self._render_stock_detail(self.selected_stock_code, preview_only=False)

    def _on_signal_tree_select(self, _event) -> None:
        selected = self.signal_tree.selection()
        if not selected:
            return
        row = self.signal_tree.item(selected[0], "values")
        code = row[1].split()[-1]
        self._select_stock(code)
        signal = next((item for item in self.signal_rows if item.stock_code == code and item.timestamp == row[0]), None)
        if signal:
            self.reason_text.delete("1.0", "end")
            self.reason_text.insert("1.0", "\n".join(signal.reasons))

    def _select_stock(self, code: str) -> None:
        self._cancel_hover_preview()
        self.hover_stock_code = ""
        self.selected_stock_code = code
        self.selected_daily_date = ""
        self._render_stock_detail(code, preview_only=False)

    def _on_daily_bar_selected(self, bar) -> None:
        if self.selected_stock_code is None:
            return
        self.selected_daily_date = bar.ts[:10]
        self._render_stock_detail(self.selected_stock_code, preview_only=False)

    def _on_popup_click(self, signal) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._select_stock(signal.stock_code)
        self.reason_text.delete("1.0", "end")
        self.reason_text.insert("1.0", "\n".join(signal.reasons))

    def _render_stock_detail(self, code: str, preview_only: bool = False) -> None:
        target_date = "" if preview_only and code != self.selected_stock_code else self.selected_daily_date
        if preview_only:
            detail = self.app_runner.get_cached_stock_detail(code, target_date)
        else:
            detail = self.app_runner.get_stock_detail(code, target_date)
        self.current_detail = detail
        snapshot = detail["snapshot"]
        if not preview_only:
            self.selected_daily_date = detail["selected_date"]
        shown_date = detail["selected_date"]
        available_dates = detail["available_minute_dates"]
        minute_bars_by_date = detail["minute_bars_by_date"]
        live_date = available_dates[-1] if available_dates else ""
        live_bars = minute_bars_by_date.get(live_date, []) if live_date else []
        candidate = self.current_candidate_map.get(code)
        candidate_text = ""
        if candidate:
            perf_text = ""
            if self.review_trade_date:
                next_trade_suffix = f" ({candidate.get('next_trade_date', '')})" if candidate.get("next_trade_date") else ""
                perf_text = (
                    f"    表现: {self._format_candidate_forward_perf(candidate)}"
                    f"{next_trade_suffix}"
                )
            candidate_text = (
                f"\n候选评分: {candidate['total_score']} ({candidate['grade']})"
                f"    加分: {'、'.join(candidate['flags'][:3]) if candidate['flags'] else '-'}"
                f"    风险: {'、'.join(candidate['risks'][:2]) if candidate['risks'] else '-'}"
                f"{perf_text}"
            )
        self.detail_var.set(
            f"{snapshot.name} {snapshot.code}\n"
            f"最新价: {snapshot.last_price:.2f}    涨幅: {snapshot.pct_chg:.2f}%    成交额: {snapshot.amount / 100000000:.2f}亿    换手率: {snapshot.turnover_rate:.2f}%\n"
            f"最高: {snapshot.high:.2f}    最低: {snapshot.low:.2f}    实时窗口: {live_date or '无'}    复盘窗口: {shown_date or '无'}    分时日期数: {len(available_dates)}"
            f"{candidate_text}"
        )
        selected_marker = self.selected_daily_date if not preview_only else shown_date
        self.daily_chart.render(detail["daily_bars"], selected_date=selected_marker)
        self.live_chart.render(live_bars, selected_date=live_date)
        self.replay_chart.render(detail["minute_bars"], selected_date=shown_date)

        if live_bars:
            earliest_live = live_bars[0].ts[-8:]
            self.live_hint_var.set(f"实时窗口: {live_date}  起始时间: {earliest_live}")
        else:
            self.live_hint_var.set("实时窗口暂无分钟数据，免费源可能未提供集合竞价或当日分时。")

        if shown_date and shown_date not in available_dates:
            self.replay_hint_var.set(f"{shown_date} 暂无免费分钟数据，仅支持最近 {len(available_dates)} 个交易日左右。")
        elif not detail["minute_bars"] and available_dates:
            self.replay_hint_var.set(f"当前日期无分钟数据，可改看：{', '.join(available_dates)}")
        elif preview_only:
            self.replay_hint_var.set(f"正在预览 {snapshot.name} {snapshot.code}，单击该行可锁定。")
        else:
            self.replay_hint_var.set("")

    def _on_daily_bar_hover(self, bar) -> None:
        if not self.current_detail:
            return
        hover_date = bar.ts[:10]
        minute_bars = self.current_detail["minute_bars_by_date"].get(hover_date, [])
        self.replay_chart.render(minute_bars, selected_date=hover_date)
        available_dates = self.current_detail["available_minute_dates"]
        if minute_bars:
            self.replay_hint_var.set(f"正在预览 {hover_date} 的分时，点击可锁定该日期。")
        else:
            self.replay_hint_var.set(f"{hover_date} 暂无免费分钟数据，当前仅支持最近 {len(available_dates)} 个交易日左右的分时查看。")

    def _set_hover_item(self, item_id: str) -> None:
        if item_id == self.hover_item_id:
            return
        if self.hover_item_id and self.hot_tree.exists(self.hover_item_id):
            self.hot_tree.item(self.hover_item_id, tags=())
        self.hover_item_id = item_id
        if item_id and self.hot_tree.exists(item_id):
            self.hot_tree.item(item_id, tags=("hover",))

    def _cancel_hover_preview(self) -> None:
        if self._hover_preview_job is not None:
            self.root.after_cancel(self._hover_preview_job)
            self._hover_preview_job = None
        self._pending_hover_code = ""

    def _apply_hover_preview(self, stock_code: str) -> None:
        self._hover_preview_job = None
        self._pending_hover_code = ""
        if stock_code == self.selected_stock_code:
            self.hover_stock_code = ""
            return
        if stock_code == self.hover_stock_code:
            return
        self.hover_stock_code = stock_code
        self._render_stock_detail(stock_code, preview_only=True)

    def _update_status(self, prefix: str) -> None:
        mode = "回退数据" if "mock" in self.app_runner.provider_name else "真实数据"
        rank_mode_text = {
            "monitor": "成交额前100",
            "focus": "重点池",
            "ths_hourly": "同花顺24h热榜",
            "ths_value": "同花顺价值投资",
            "replay_candidate": "复盘候选",
            "intraday_candidate": "盘中候选",
        }.get(self.rank_mode.get(), self.rank_mode.get())

        current_count = len(self.monitor_rows)
        suffix = f" | 数据源: {self.app_runner.provider_name} ({mode}) | 当前排行: {rank_mode_text} ({current_count}只)"
        if self.review_trade_date and self.rank_mode.get() in ("replay_candidate", "intraday_candidate"):
            suffix += f" | 回看日期: {self.review_trade_date}"

        # 显示同花顺数据更新时间
        if self._ths_last_update:
            suffix += f" | 同花顺更新: {self._ths_last_update}"

        if self.app_runner.provider_error:
            suffix += " | 实时接口异常，已回退"
        self.status_var.set(prefix + suffix)


class SimpleLineChart:
    def __init__(self, parent, title: str, on_select=None, on_hover=None):
        self.frame = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid")
        self.on_select = on_select
        self.on_hover = on_hover
        self._bars = []
        self._points = []
        self._selected_date = ""
        self._view_start = 0
        self._view_size = 30
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_view = 0
        self._render_signature = None

        tk.Label(self.frame, text=title, anchor="w", bg="#ffffff", font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=8, pady=(8, 0))
        self.canvas = tk.Canvas(self.frame, bg="#ffffff", height=240, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.info_var = tk.StringVar(value="鼠标悬停看日线，点击某天切换右侧分时")
        tk.Label(self.frame, textvariable=self.info_var, anchor="w", bg="#ffffff", fg="#475467").pack(fill="x", padx=8, pady=(0, 4))
        self.scroll = ttk.Scrollbar(self.frame, orient="horizontal", command=self._on_scroll)
        self.scroll.pack(fill="x", padx=8, pady=(0, 8))
        self.legend = tk.Label(
            self.frame,
            text="绿线: 收盘价  红绿K线: 日线实体  红绿柱: 成交量  红点: 当前选中日期",
            anchor="w",
            bg="#ffffff",
            fg="#475467",
        )
        self.legend.pack(fill="x", padx=8, pady=(0, 8))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def render(self, bars: list, selected_date: str = "") -> None:
        bars_list = list(bars)
        incoming_bar_keys = tuple(bar.ts for bar in bars_list)
        current_bar_keys = tuple(bar.ts for bar in self._bars)
        if incoming_bar_keys != current_bar_keys and bars_list:
            next_view_size = max(10, min(self._view_size, len(bars_list)))
            self._view_start = max(0, len(bars_list) - next_view_size)
        render_signature = (
            selected_date,
            max(self.canvas.winfo_width(), 340),
            max(self.canvas.winfo_height(), 240),
            self._view_start,
            self._view_size,
            tuple((bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume) for bar in bars_list),
        )
        if render_signature == self._render_signature:
            return
        self._render_signature = render_signature
        self.canvas.delete("all")
        self._bars = bars_list
        self._points = []
        self._selected_date = selected_date

        width = max(self.canvas.winfo_width(), 340)
        height = max(self.canvas.winfo_height(), 240)
        self.canvas.create_rectangle(0, 0, width, height, outline="#e4e7ec")

        if not bars:
            self.canvas.create_text(width / 2, height / 2, text="暂无日线数据", fill="#98a2b3")
            self.info_var.set("暂无日线数据")
            return

        self._view_size = max(10, min(self._view_size, len(self._bars)))
        self._view_start = max(0, min(self._view_start, max(0, len(self._bars) - self._view_size)))
        visible = self._bars[self._view_start : self._view_start + self._view_size]

        closes = [bar.close for bar in visible]
        volumes = [bar.volume for bar in visible]
        max_price = max(closes)
        min_price = min(closes)
        max_volume = max(volumes) or 1.0
        span = max(max_price - min_price, 0.01)
        left, top, right = 28, 18, width - 18
        price_bottom = height - 90
        volume_top = price_bottom + 12
        volume_bottom = height - 24
        step = max((right - left) / max(len(visible) - 1, 1), 1)

        points = []
        for idx, bar in enumerate(visible):
            x = left + idx * step
            y = price_bottom - ((bar.close - min_price) / span) * (price_bottom - top)
            points.extend([x, y])
            self._points.append((x, y, bar))
        self.canvas.create_line(*points, fill="#0b6e4f", width=2, smooth=True)

        candle_width = max(min(step * 0.55, 14), 4)
        for idx, bar in enumerate(visible):
            x = left + idx * step
            open_y = price_bottom - ((bar.open - min_price) / span) * (price_bottom - top)
            close_y = price_bottom - ((bar.close - min_price) / span) * (price_bottom - top)
            high_y = price_bottom - ((bar.high - min_price) / span) * (price_bottom - top)
            low_y = price_bottom - ((bar.low - min_price) / span) * (price_bottom - top)
            color = "#d92d20" if bar.close >= bar.open else "#039855"
            body_top = min(open_y, close_y)
            body_bottom = max(open_y, close_y)
            if abs(body_bottom - body_top) < 1:
                body_bottom = body_top + 1
            self.canvas.create_line(x, high_y, x, low_y, fill=color, width=1)
            self.canvas.create_rectangle(
                x - candle_width / 2,
                body_top,
                x + candle_width / 2,
                body_bottom,
                fill=color,
                outline=color,
            )

        bar_width = max(step * 0.55, 3)
        for idx, bar in enumerate(visible):
            x = left + idx * step
            volume_height = (bar.volume / max_volume) * (volume_bottom - volume_top)
            y0 = volume_bottom - volume_height
            color = "#d92d20" if bar.close >= bar.open else "#039855"
            self.canvas.create_rectangle(x - bar_width / 2, y0, x + bar_width / 2, volume_bottom, fill=color, outline=color)

        for x, y, bar in self._points:
            radius = 3 if bar.ts[:10] == self._selected_date else 2
            color = "#b42318" if bar.ts[:10] == self._selected_date else "#0b6e4f"
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline=color)

        self.canvas.create_text(left, top, text=f"{max_price:.2f}", anchor="w", fill="#475467")
        self.canvas.create_text(left, price_bottom, text=f"{min_price:.2f}", anchor="sw", fill="#475467")
        self.canvas.create_text(left, volume_top, text=f"{max_volume:.0f}", anchor="w", fill="#98a2b3")
        self._update_scroll()
        self.info_var.set("鼠标悬停看日线，点击某天切换右侧分时，滚轮缩放")

    def _on_motion(self, event) -> None:
        if not self._points:
            return
        idx = min(range(len(self._points)), key=lambda i: abs(self._points[i][0] - event.x))
        x, y, bar = self._points[idx]
        amount, _pct_chg = self._resolve_display_metrics(bar)
        self._draw_crosshair(x, y)
        self._draw_tooltip(x, y, bar)
        if self.on_hover is not None:
            self.on_hover(bar)
        self.info_var.set(
            f"{bar.ts}  开:{bar.open:.2f} 高:{bar.high:.2f} 低:{bar.low:.2f} 收:{bar.close:.2f} 量:{bar.volume:.0f} 额:{amount / 10000:.2f}万"
        )

    def _on_leave(self, _event) -> None:
        self.canvas.delete("crosshair")
        self.canvas.delete("tooltip")
        if self._bars:
            self.info_var.set("鼠标悬停看日线，点击某天切换右侧分时，滚轮缩放")

    def _draw_tooltip(self, x: float, y: float, bar) -> None:
        """绘制浮动提示框，显示日线详细信息"""
        self.canvas.delete("tooltip")
        lines = self._build_tooltip_lines(bar)
        box_width = 175
        box_height = 110
        canvas_width = max(self.canvas.winfo_width(), 340)
        canvas_height = max(self.canvas.winfo_height(), 240)
        box_x = x + 14
        box_y = y - box_height - 10
        if box_x + box_width > canvas_width - 8:
            box_x = x - box_width - 14
        if box_y < 8:
            box_y = y + 14
        if box_y + box_height > canvas_height - 8:
            box_y = canvas_height - box_height - 8

        self.canvas.create_rectangle(
            box_x,
            box_y,
            box_x + box_width,
            box_y + box_height,
            fill="#101828",
            outline="#344054",
            width=1,
            tags="tooltip",
        )
        for idx, line in enumerate(lines):
            self.canvas.create_text(
                box_x + 10,
                box_y + 12 + idx * 17,
                text=line,
                anchor="w",
                fill="#f8fafc" if idx == 0 else "#d0d5dd",
                font=("Microsoft YaHei UI", 9, "bold" if idx == 0 else "normal"),
                tags="tooltip",
            )

    def _build_tooltip_lines(self, bar) -> list[str]:
        amount, pct_chg = self._resolve_display_metrics(bar)
        return [
            bar.ts[:10],
            f"开 {bar.open:.2f}  高 {bar.high:.2f}",
            f"低 {bar.low:.2f}  收 {bar.close:.2f}",
            f"量 {bar.volume:.0f}",
            f"额 {amount / 10000:.2f} 万",
            f"涨跌 {pct_chg:+.2f}%",
        ]

    def _resolve_display_metrics(self, bar) -> tuple[float, float]:
        amount = bar.amount
        if amount <= 0 and bar.volume > 0 and bar.close > 0:
            amount = bar.volume * bar.close

        pct_chg = bar.pct_chg
        if abs(pct_chg) < 1e-9:
            bar_index = next((idx for idx, current in enumerate(self._bars) if current.ts == bar.ts), -1)
            if bar_index > 0:
                prev_close = self._bars[bar_index - 1].close
                if prev_close:
                    pct_chg = (bar.close - prev_close) / prev_close * 100
        return amount, pct_chg

    def _on_press(self, event) -> None:
        self._dragging = True
        self._drag_start_x = event.x
        self._drag_start_view = self._view_start

    def _on_drag(self, event) -> None:
        if not self._dragging or not self._bars:
            return
        max_start = max(0, len(self._bars) - self._view_size)
        usable_width = max(self.canvas.winfo_width() - 46, 1)
        delta_ratio = (event.x - self._drag_start_x) / usable_width
        delta_points = int(delta_ratio * self._view_size)
        self._view_start = max(0, min(max_start, self._drag_start_view - delta_points))
        self.render(self._bars, self._selected_date)

    def _on_release(self, event) -> None:
        was_drag = abs(event.x - self._drag_start_x) > 6
        self._dragging = False
        if was_drag or not self._points or self.on_select is None:
            return
        idx = min(range(len(self._points)), key=lambda i: abs(self._points[i][0] - event.x))
        _, _, bar = self._points[idx]
        self._selected_date = bar.ts[:10]
        self.on_select(bar)

    def _on_mousewheel(self, event) -> None:
        if not self._bars:
            return
        delta = -1 if event.delta > 0 else 1
        new_size = max(10, min(len(self._bars), self._view_size + delta * 5))
        if new_size != self._view_size:
            old_end = self._view_start + self._view_size
            self._view_size = new_size
            self._view_start = max(0, min(len(self._bars) - self._view_size, old_end - self._view_size))
            self.render(self._bars, self._selected_date)

    def _on_scroll(self, *args) -> None:
        if not self._bars:
            return
        max_start = max(0, len(self._bars) - self._view_size)
        if args[0] == "moveto":
            self._view_start = int(float(args[1]) * max_start)
        elif args[0] == "scroll":
            self._view_start = max(0, min(max_start, self._view_start + int(args[1]) * 3))
        self.render(self._bars, self._selected_date)

    def _on_canvas_configure(self, _event) -> None:
        if self._bars:
            self.render(self._bars, self._selected_date)

    def _update_scroll(self) -> None:
        total = max(len(self._bars), 1)
        first = self._view_start / total
        last = min((self._view_start + self._view_size) / total, 1.0)
        self.scroll.set(first, last)

    def _draw_crosshair(self, x: float, y: float) -> None:
        width = max(self.canvas.winfo_width(), 340)
        height = max(self.canvas.winfo_height(), 240)
        self.canvas.delete("crosshair")
        self.canvas.create_line(x, 0, x, height, fill="#98a2b3", dash=(3, 3), tags="crosshair")
        self.canvas.create_line(0, y, width, y, fill="#98a2b3", dash=(3, 3), tags="crosshair")
        self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#b42318", outline="#b42318", tags="crosshair")


class SimpleMinuteChart:
    def __init__(self, parent, title: str):
        self.frame = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid")
        self._bars = []
        self._points = []
        self._render_signature = None
        self._selected_date = ""  # 添加缺失的属性

        tk.Label(self.frame, text=title, anchor="w", bg="#ffffff", font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=8, pady=(8, 0))
        self.canvas = tk.Canvas(self.frame, bg="#ffffff", height=220, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.info_var = tk.StringVar(value="鼠标悬停查看分钟数据")
        tk.Label(self.frame, textvariable=self.info_var, anchor="w", bg="#ffffff", fg="#475467").pack(fill="x", padx=8, pady=(0, 8))
        self.legend = tk.Label(
            self.frame,
            text="蓝线: 收盘  橙线: MA5  紫线: MA10  红绿柱: 成交量",
            anchor="w",
            bg="#ffffff",
            fg="#475467",
        )
        self.legend.pack(fill="x", padx=8, pady=(0, 8))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.last_selected_bar = None

    def render(self, bars: list, selected_date: str = "") -> None:
        bars_list = list(bars)
        render_signature = (
            selected_date,
            max(self.canvas.winfo_width(), 340),
            max(self.canvas.winfo_height(), 240),
            tuple((bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.amount) for bar in bars_list),
        )
        if render_signature == self._render_signature:
            return
        self._render_signature = render_signature
        self.canvas.delete("all")
        self._bars = bars_list
        self._points = []
        self.last_selected_bar = None

        width = max(self.canvas.winfo_width(), 340)
        height = max(self.canvas.winfo_height(), 240)
        self.canvas.create_rectangle(0, 0, width, height, outline="#e4e7ec")
        if not bars:
            self.canvas.create_text(width / 2, height / 2, text="该日期暂无分钟数据", fill="#98a2b3")
            self.info_var.set("当前选中日期没有可用的分钟数据")
            return

        closes = [bar.close for bar in bars]
        max_price = max(closes)
        min_price = min(closes)
        span = max(max_price - min_price, 0.01)
        left, top, right = 28, 18, width - 18
        price_bottom = height - 88
        step = max((right - left) / max(len(closes) - 1, 1), 1)

        price_points = []
        ma5_points = []
        ma10_points = []
        for idx, bar in enumerate(bars):
            x = left + idx * step
            y = price_bottom - ((bar.close - min_price) / span) * (price_bottom - top)
            price_points.extend([x, y])
            self._points.append((x, y, bar))

            ma5 = sum(item.close for item in bars[max(0, idx - 4) : idx + 1]) / len(bars[max(0, idx - 4) : idx + 1])
            ma10 = sum(item.close for item in bars[max(0, idx - 9) : idx + 1]) / len(bars[max(0, idx - 9) : idx + 1])
            ma5_y = price_bottom - ((ma5 - min_price) / span) * (price_bottom - top)
            ma10_y = price_bottom - ((ma10 - min_price) / span) * (price_bottom - top)
            ma5_points.extend([x, ma5_y])
            ma10_points.extend([x, ma10_y])

        self.canvas.create_line(*price_points, fill="#175cd3", width=2, smooth=True)
        self.canvas.create_line(*ma5_points, fill="#f79009", width=1.5, smooth=True)
        self.canvas.create_line(*ma10_points, fill="#7a5af8", width=1.5, smooth=True)

        volumes = [bar.volume for bar in bars]
        max_volume = max(volumes) or 1.0
        volume_top = price_bottom + 12
        volume_bottom = height - 20
        bar_width = max(step * 0.6, 2)
        for idx, bar in enumerate(bars):
            x = left + idx * step
            volume_height = (bar.volume / max_volume) * (volume_bottom - volume_top)
            y0 = volume_bottom - volume_height
            color = "#d92d20" if bar.close >= bar.open else "#039855"
            self.canvas.create_rectangle(x - bar_width / 2, y0, x + bar_width / 2, volume_bottom, fill=color, outline=color)

        self.canvas.create_text(left, top, text=f"{max_price:.2f}", anchor="w", fill="#475467")
        self.canvas.create_text(left, price_bottom, text=f"{min_price:.2f}", anchor="sw", fill="#475467")
        self.info_var.set(f"{selected_date} 分时，蓝线收盘，橙线MA5，紫线MA10")

    def _on_motion(self, event) -> None:
        if not self._points:
            return
        idx = min(range(len(self._points)), key=lambda i: abs(self._points[i][0] - event.x))
        x, y, bar = self._points[idx]
        self.last_selected_bar = bar
        self._draw_crosshair(x, y)
        self._draw_tooltip(x, y, bar)
        self.info_var.set(
            f"{bar.ts[-8:]}  开:{bar.open:.2f} 高:{bar.high:.2f} 低:{bar.low:.2f} 收:{bar.close:.2f} 量:{bar.volume:.0f} 额:{bar.amount / 10000:.2f}万"
        )

    def _on_leave(self, _event) -> None:
        self.canvas.delete("crosshair")
        self.canvas.delete("tooltip")
        if self.last_selected_bar is not None:
            bar = self.last_selected_bar
            self.info_var.set(
                f"{bar.ts[-8:]}  开:{bar.open:.2f} 高:{bar.high:.2f} 低:{bar.low:.2f} 收:{bar.close:.2f} 量:{bar.volume:.0f} 额:{bar.amount / 10000:.2f}万"
            )
        elif self._bars:
            self.info_var.set("鼠标悬停查看分钟数据")

    def _draw_crosshair(self, x: float, y: float) -> None:
        width = max(self.canvas.winfo_width(), 340)
        height = max(self.canvas.winfo_height(), 240)
        self.canvas.delete("crosshair")
        self.canvas.create_line(x, 0, x, height, fill="#98a2b3", dash=(3, 3), tags="crosshair")
        self.canvas.create_line(0, y, width, y, fill="#98a2b3", dash=(3, 3), tags="crosshair")
        self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#175cd3", outline="#175cd3", tags="crosshair")

    def _draw_tooltip(self, x: float, y: float, bar) -> None:
        self.canvas.delete("tooltip")
        lines = [
            bar.ts[-8:],
            f"开 {bar.open:.2f}  高 {bar.high:.2f}",
            f"低 {bar.low:.2f}  收 {bar.close:.2f}",
            f"量 {bar.volume:.0f}",
            f"额 {bar.amount / 10000:.2f} 万",
        ]
        box_width = 168
        box_height = 94
        canvas_width = max(self.canvas.winfo_width(), 340)
        canvas_height = max(self.canvas.winfo_height(), 240)
        box_x = x + 14
        box_y = y - box_height - 10
        if box_x + box_width > canvas_width - 8:
            box_x = x - box_width - 14
        if box_y < 8:
            box_y = y + 14
        if box_y + box_height > canvas_height - 8:
            box_y = canvas_height - box_height - 8

        self.canvas.create_rectangle(
            box_x,
            box_y,
            box_x + box_width,
            box_y + box_height,
            fill="#101828",
            outline="#344054",
            width=1,
            tags="tooltip",
        )
        for idx, line in enumerate(lines):
            self.canvas.create_text(
                box_x + 10,
                box_y + 12 + idx * 17,
                text=line,
                anchor="w",
                fill="#f8fafc" if idx == 0 else "#d0d5dd",
                font=("Microsoft YaHei UI", 9, "bold" if idx == 0 else "normal"),
                tags="tooltip",
            )

    def _on_canvas_configure(self, _event) -> None:
        if self._bars:
            self.render(self._bars, self._selected_date)


class PopupNotifier:
    def __init__(self, root: tk.Tk, on_click=None):
        self.root = root
        self.on_click = on_click
        self.active_popups = []

    def show(self, signal) -> None:
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#101828")

        card = tk.Frame(popup, bg="#101828", padx=14, pady=12)
        card.pack(fill="both", expand=True)

        level_color = "#f79009" if signal.signal_level == "watch" else "#12b76a"
        tk.Label(
            card,
            text=signal.title or signal.strategy_name,
            bg="#101828",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            card,
            text=f"{signal.stock_name} {signal.stock_code}",
            bg="#101828",
            fg=level_color,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(6, 0))
        tk.Label(
            card,
            text=signal.message,
            bg="#101828",
            fg="#d0d5dd",
            justify="left",
            wraplength=280,
            anchor="w",
        ).pack(fill="x", pady=(6, 0))
        if signal.reasons:
            tk.Label(
                card,
                text=signal.reasons[0],
                bg="#101828",
                fg="#98a2b3",
                justify="left",
                wraplength=280,
                anchor="w",
            ).pack(fill="x", pady=(6, 0))

        for widget in (popup, card):
            widget.bind("<Button-1>", lambda _event, s=signal, p=popup: self._handle_click(s, p))

        self.active_popups.append(popup)
        self._reposition()
        popup.after(7000, lambda p=popup: self._destroy_popup(p))

    def _handle_click(self, signal, popup) -> None:
        self._destroy_popup(popup)
        if self.on_click is not None:
            self.on_click(signal)

    def _destroy_popup(self, popup) -> None:
        if popup in self.active_popups:
            self.active_popups.remove(popup)
        if popup.winfo_exists():
            popup.destroy()
        self._reposition()

    def _reposition(self) -> None:
        self.root.update_idletasks()
        screen_width = self.root.winfo_vrootwidth() or self.root.winfo_screenwidth()
        screen_height = self.root.winfo_vrootheight() or self.root.winfo_screenheight()
        width = 320
        height = 120
        margin = 18
        for index, popup in enumerate(reversed(self.active_popups)):
            y = screen_height - ((index + 1) * (height + 12)) - margin
            x = screen_width - width - margin
            popup.geometry(f"{width}x{height}+{x}+{y}")
