from __future__ import annotations

import tkinter as tk
import threading
from datetime import datetime
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
        self.candidate_profile_var = tk.StringVar(value="")
        self.review_trade_date = ""
        self.review_date_combo = None
        self.candidate_profile_combo = None
        self.current_detail = None
        self.current_candidate_map: dict[str, dict] = {}
        self.hover_stock_code = ""
        self.hover_item_id = ""
        self._pending_hover_code = ""
        self._hover_preview_job = None
        self._hover_preview_delay_ms = 180
        self._refresh_job = None
        self._scan_job = None
        self._intraday_candidate_job = None
        self._background_tasks: set[str] = set()
        self._background_dispatcher = self._start_background_task
        self.popup_notifier = None
        self._last_auto_export_date = ""  # 自动导出日期追踪

        # 首板1进2策略
        from src.strategies.jq_auction_1to2 import JQAuction1to2Strategy
        from pathlib import Path
        jq_config_path = Path("config/jq_strategy_params.toml")
        self.jq_strategy = JQAuction1to2Strategy(config_path=jq_config_path)
        self.jq_auction_results: list = []  # 竞价匹配结果
        self.jq_params_window = None  # 参数调整窗口
        self.jq_fusion_window = None  # 首板断板融合策略窗口

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
        intraday_candidate_seconds = self.app_runner.settings.get("scan", {}).get("intraday_candidate_refresh_seconds", 15)
        self._intraday_candidate_refresh_ms = max(int(intraday_candidate_seconds) * 1000, 5000)

        self.root.title("QuantDaA 热门股监控")
        self._configure_window_size()

        self._build_layout()
        self._init_candidate_profile()
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

        # 状态栏（包含文本和指示灯）
        status_bar = tk.Frame(content, bg="#eef3f8")
        status_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        status_bar.grid_columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="准备中")
        tk.Label(status_bar, textvariable=self.status_var, anchor="w", fg="#475467", bg="#eef3f8", padx=10, pady=6).grid(
            row=0, column=0, sticky="ew"
        )

        # 状态指示灯（在状态栏右边）
        light_frame = tk.Frame(status_bar, bg="#eef3f8", padx=10, pady=6)
        light_frame.grid(row=0, column=1, sticky="e")
        self.status_light = tk.Canvas(light_frame, width=14, height=14, bg="#eef3f8", highlightthickness=0)
        self.status_light.pack(side="right")
        self._light_id = self.status_light.create_oval(1, 1, 13, 13, fill="#98a2b3", outline="#667085")  # 默认灰色
        self._status_light_state = "idle"  # idle, loading, done

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
        ttk.Radiobutton(rank_row2, text="竞价1进2", value="jq_auction_1to2", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )
        ttk.Button(rank_row2, text="参数调整", command=self._open_jq_params_window).pack(side="left", padx=(8, 0))
        ttk.Button(rank_row2, text="首板断板融合", command=self._open_jq_fusion_window).pack(side="left", padx=(4, 0))

        review_row = tk.Frame(rank_bar, bg="#ffffff")
        review_row.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
        tk.Label(review_row, text="回看日期", bg="#ffffff", fg="#344054").pack(side="left", padx=(0, 6))
        self.review_date_combo = ttk.Combobox(review_row, textvariable=self.review_date_var, width=16, state="disabled")
        self.review_date_combo.pack(side="left", padx=(0, 6))
        self.review_date_combo.bind("<<ComboboxSelected>>", self._on_review_date_selected)
        ttk.Button(review_row, text="清除", command=self._clear_review_date, width=5).pack(side="left")
        tk.Label(review_row, text="评分模型", bg="#ffffff", fg="#344054").pack(side="left", padx=(12, 6))
        self.candidate_profile_combo = ttk.Combobox(review_row, textvariable=self.candidate_profile_var, width=50, state="readonly")
        self.candidate_profile_combo.pack(side="left", padx=(0, 6))
        self.candidate_profile_combo.bind("<<ComboboxSelected>>", self._on_candidate_profile_selected)

        self.hot_tree = ttk.Treeview(
            rank_frame,
            columns=("rank", "code", "name", "pct", "amount"),
            show="headings",
            height=14,
        )
        for col, text, width in (
            ("rank", "排", 45),
            ("code", "代码", 85),
            ("name", "名称", 100),
            ("pct", "涨幅%", 75),
            ("amount", "成交额", 225),
        ):
            self.hot_tree.heading(col, text=text)
            self.hot_tree.column(col, width=width, anchor="center")
        self.hot_tree.grid(row=1, column=0, sticky="nsew")
        hot_scroll = ttk.Scrollbar(rank_frame, orient="vertical", command=self.hot_tree.yview)
        hot_scroll.grid(row=1, column=1, sticky="ns")
        hot_x_scroll = ttk.Scrollbar(rank_frame, orient="horizontal", command=self.hot_tree.xview)
        hot_x_scroll.grid(row=2, column=0, sticky="ew")
        self.hot_tree.configure(yscrollcommand=hot_scroll.set, xscrollcommand=hot_x_scroll.set)
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
        left_width = max(550, int(total_width * 0.38))
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
        self._save_daily_snapshots()
        if self.monitor_rows:
            self._select_stock(self._row_code(self.monitor_rows[0]))
        self._update_status("初始化完成")

    def _schedule_jobs(self) -> None:
        pool_ms = self.app_runner.get_next_pool_refresh_delay_ms()
        self._refresh_job = self.root.after(pool_ms, self._refresh_cycle)

        # 根据配置决定是否启动信号扫描
        enable_signal_scan = self.app_runner.settings.get("scan", {}).get("enable_signal_scan", True)
        if enable_signal_scan:
            scan_ms = int(self.app_runner.settings["scan"]["signal_scan_seconds"] * 1000)
            self._scan_job = self.root.after(scan_ms, self._scan_cycle)
            print("[Schedule] 信号扫描已启用")
        else:
            self._scan_job = None
            print("[Schedule] 信号扫描已禁用（配置 enable_signal_scan = false）")

        # 启动同花顺数据定时刷新（首次延迟8-12秒后执行，避免启动时卡顿）
        import random
        first_delay = random.randint(8000, 12000)
        self._ths_refresh_job = self.root.after(first_delay, self._refresh_ths_cycle)

        # 根据配置决定是否启动盘中候选自动刷新
        enable_intraday_candidate = self.app_runner.settings.get("scan", {}).get("enable_intraday_candidate_auto_refresh", True)
        if enable_intraday_candidate:
            self._intraday_candidate_job = self.root.after(self._intraday_candidate_refresh_ms, self._intraday_candidate_cycle)
            print("[Schedule] 盘中候选自动刷新已启用")
        else:
            self._intraday_candidate_job = None
            print("[Schedule] 盘中候选自动刷新已禁用（配置 enable_intraday_candidate_auto_refresh = false）")

        # 启动时自动打开首板断板融合窗口
        enable_auto_open_fusion = self.app_runner.settings.get("app", {}).get("enable_auto_open_fusion_window", True)
        if enable_auto_open_fusion:
            self.root.after(1000, self._open_jq_fusion_window)

    def _dispatch_background(self, func, on_success=None, on_error=None, task_key: str = "") -> bool:
        dispatcher = getattr(self, "_background_dispatcher", None)
        if dispatcher is None:
            try:
                result = func()
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)
                return False
            if on_success is not None:
                on_success(result)
            return True
        return dispatcher(func, on_success=on_success, on_error=on_error, task_key=task_key)

    def _start_background_task(self, func, on_success=None, on_error=None, task_key: str = "") -> bool:
        if task_key and task_key in self._background_tasks:
            return False
        if task_key:
            self._background_tasks.add(task_key)

        def finish_success(result):
            if task_key:
                self._background_tasks.discard(task_key)
            if on_success is not None:
                on_success(result)

        def finish_error(exc):
            if task_key:
                self._background_tasks.discard(task_key)
            if on_error is not None:
                on_error(exc)

        def worker() -> None:
            try:
                result = func()
            except Exception as exc:
                try:
                    self.root.after(0, lambda exc=exc: finish_error(exc))
                except tk.TclError:
                    pass
                return
            # 检查任务是否被取消
            if task_key and task_key not in self._background_tasks:
                return  # 任务已被取消，不执行回调
            try:
                self.root.after(0, lambda result=result: finish_success(result))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _refresh_cycle(self) -> None:
        ths_rows = list(self.ths_hourly_hot)
        enable_intraday_candidate = self.app_runner.settings.get("scan", {}).get("enable_intraday_candidate_auto_refresh", True)

        def work() -> None:
            self.app_runner.refresh_pools()
            # 只有启用盘中候选自动刷新时才调用
            if enable_intraday_candidate:
                self.app_runner.save_intraday_candidate_snapshots_if_needed(
                    ths_rows=ths_rows,
                    kpl_rows=[],
                )

        def on_success(_result=None) -> None:
            self._save_daily_snapshots()
            self._refresh_hot_tree()
            self._update_status("排行榜已刷新")

        def on_error(exc: Exception) -> None:
            delay_seconds = self.app_runner.get_next_pool_refresh_delay_ms() // 1000
            self._update_status(f"排行榜刷新失败: {exc} | {delay_seconds} 秒后重试")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key="refresh_cycle")
        self._refresh_job = self.root.after(self.app_runner.get_next_pool_refresh_delay_ms(), self._refresh_cycle)

    def _scan_cycle(self) -> None:
        def work() -> list:
            return self.app_runner.scan_once()

        def on_success(new_signals: list) -> None:
            if new_signals:
                self.signal_rows = new_signals + self.signal_rows
                self._refresh_signal_tree()
                self._update_status(f"新增 {len(new_signals)} 条信号")
                for signal in new_signals[:3]:
                    self.popup_notifier.show(signal)
            if self.selected_stock_code:
                self._render_stock_detail(self.selected_stock_code, preview_only=True)

        def on_error(exc: Exception) -> None:
            self._update_status(f"信号扫描失败: {exc}")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key="scan_cycle")
        self._scan_job = self.root.after(int(self.app_runner.settings["scan"]["signal_scan_seconds"] * 1000), self._scan_cycle)

    def _intraday_candidate_cycle(self) -> None:
        try:
            if self.rank_mode.get() == "intraday_candidate" and not self.review_trade_date:
                self._refresh_intraday_candidate_fast()
        finally:
            self._intraday_candidate_job = self.root.after(self._intraday_candidate_refresh_ms, self._intraday_candidate_cycle)

    def _refresh_intraday_candidate_fast(self) -> None:
        ths_rows = list(self.ths_hourly_hot)

        def work() -> None:
            self.app_runner.refresh_pools()
            self.app_runner.save_intraday_candidate_snapshots_if_needed(
                ths_rows=ths_rows,
                kpl_rows=[],
            )

        def on_success(_result=None) -> None:
            self._refresh_hot_tree()
            if self.selected_stock_code:
                self._render_stock_detail(self.selected_stock_code, preview_only=True)

        def on_error(exc: Exception) -> None:
            self._update_status(f"盘中候选刷新失败: {exc}")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key="intraday_candidate_cycle")

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
        def work():
            current_time = datetime.now().strftime("%H:%M:%S")
            hourly_hot = self.ths_provider.get_24h_hot(limit=100)
            value_hot = self.ths_provider.get_hot_stocks(time_type="day", list_type="value", limit=100)
            return current_time, hourly_hot, value_hot

        def on_success(result) -> None:
            current_time, hourly_hot, value_hot = result
            self.ths_hourly_hot = hourly_hot
            self.ths_value_hot = value_hot
            self._ths_last_update = current_time
            self._save_daily_snapshots()
            if self.rank_mode.get() in ("ths_hourly", "ths_value", "replay_candidate", "intraday_candidate"):
                self._refresh_hot_tree()
            print(f"[THS] 同花顺热榜数据已更新: {current_time} (24h:{len(self.ths_hourly_hot)}只, 价值:{len(self.ths_value_hot)}只)")

        def on_error(exc: Exception) -> None:
            print(f"[THS] 定时刷新失败: {exc}")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key="ths_refresh_cycle")

        # 设置下次刷新任务（带随机延时）
        next_interval = self._get_ths_refresh_interval()
        self._ths_refresh_job = self.root.after(next_interval, self._refresh_ths_cycle)

    def _refresh_hot_tree(self) -> None:
        current = self.selected_stock_code
        mode = self.rank_mode.get()
        self.current_candidate_map = {}
        review_enabled = bool(self.review_trade_date and mode in ("replay_candidate", "intraday_candidate"))
        self._update_hot_tree_columns(mode, review_enabled)
        if mode == "intraday_candidate" and not review_enabled:
            self.hot_tree.heading("amount", text="等级/当前涨幅/标签")
            self.hot_tree.column("amount", width=260, anchor="center")
        self._refresh_review_date_options(mode)

        # 对于复盘候选和盘中候选，使用异步方式执行
        if mode in ("replay_candidate", "intraday_candidate"):
            self._refresh_candidate_tree_async(mode, current, review_enabled)
            return

        # 竞价1进2模式
        if mode == "jq_auction_1to2":
            self._refresh_jq_auction_tree(current)
            return

        # 其他模式直接在主线程执行（数据已经在内存中）
        if mode == "ths_hourly":
            self.monitor_rows = self.ths_hourly_hot
        elif mode == "ths_value":
            self.monitor_rows = self.ths_value_hot
        else:
            self.monitor_rows = list(self.app_runner.state.focus_pool if mode == "focus" else self.app_runner.state.monitor_pool)

        self._update_hot_tree_display(current)

    def _refresh_candidate_tree_async(self, mode: str, current: str, review_enabled: bool) -> None:
        """异步刷新候选排行榜"""
        # 取消之前的任务（如果还在运行）
        if "candidate_refresh" in self._background_tasks:
            self._background_tasks.discard("candidate_refresh")

        # 设置指示灯为红色（加载中）
        self._set_status_light("loading")

        ths_rows = list(self.ths_hourly_hot)

        def work() -> list:
            if mode == "replay_candidate":
                if self.review_trade_date:
                    return self.app_runner.get_candidate_review_rows("replay", self.review_trade_date)
                else:
                    replay_dates = self.app_runner.get_candidate_review_dates("replay")
                    if replay_dates:
                        return self.app_runner.get_candidate_review_rows("replay", replay_dates[0])
                    else:
                        return self.app_runner.build_replay_candidate_ranking()
            else:  # intraday_candidate
                if self.review_trade_date:
                    return self.app_runner.get_candidate_review_rows("intraday", self.review_trade_date)
                else:
                    return self.app_runner.build_intraday_candidate_ranking(ths_rows=ths_rows, kpl_rows=[])

        def on_success(rows: list) -> None:
            self.monitor_rows = rows
            self.current_candidate_map = {row["stock_code"]: row for row in rows}
            self._update_hot_tree_display(current)
            # 设置指示灯为绿色（完成）
            self._set_status_light("done")

        def on_error(exc: Exception) -> None:
            print(f"[Candidate] 异步刷新失败: {exc}")
            # 设置指示灯为灰色（空闲）
            self._set_status_light("idle")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key="candidate_refresh")

    def _refresh_jq_auction_tree(self, current: str) -> None:
        """刷新竞价1进2排行榜"""
        # 更新列定义
        self.hot_tree.heading("rank", text="排名")
        self.hot_tree.heading("code", text="代码")
        self.hot_tree.heading("name", text="名称")
        self.hot_tree.heading("pct", text="竞价涨幅")
        self.hot_tree.heading("amount", text="竞昨比/成交额/命中条件")
        self.hot_tree.column("amount", width=300, anchor="center")

        # 清空现有数据
        self.hot_tree.delete(*self.hot_tree.get_children())

        # 显示竞价匹配结果
        for idx, result in enumerate(self.jq_auction_results, start=1):
            amount_text = f"{result.auction_vol_ratio:.2f}% / {result.yesterday_money/1e8:.1f}亿 / {result.matched_condition}"
            pct_text = f"{result.auction_pct:.2f}%"
            item_id = self.hot_tree.insert(
                "",
                "end",
                values=(idx, result.code, result.name, pct_text, amount_text),
                tags=(),
            )
            if result.code == current:
                self.hot_tree.selection_set(item_id)
                self.hot_tree.see(item_id)

        # 更新状态
        self._update_status(f"竞价1进2: {len(self.jq_auction_results)}只股票命中条件")

    def _open_jq_params_window(self) -> None:
        """打开竞价1进2参数调整窗口"""
        from src.ui.jq_params_window import JQParamsWindow
        from pathlib import Path

        if self.jq_params_window is not None and self.jq_params_window.window is not None and self.jq_params_window.window.winfo_exists():
            self.jq_params_window.window.lift()
            return

        config_path = Path("config/jq_strategy_params.toml")

        def on_save(new_params):
            self.jq_strategy.params = new_params
            print("[JQ策略] 参数已更新")

        self.jq_params_window = JQParamsWindow(
            parent=self.root,
            params=self.jq_strategy.params,
            config_path=config_path,
            on_save=on_save,
        )
        self.jq_params_window.show()

    def _open_jq_fusion_window(self) -> None:
        """打开首板断板融合竞价策略选股窗口"""
        from src.ui.jq_fusion_window import JQFusionWindow

        if self.jq_fusion_window is not None and self.jq_fusion_window.window is not None and self.jq_fusion_window.window.winfo_exists():
            self.jq_fusion_window.window.lift()
            return

        self.jq_fusion_window = JQFusionWindow(parent=self.root)
        review_date = getattr(self, 'review_trade_date', '')
        self.jq_fusion_window.show(date_str=review_date if review_date else None)

    def _update_hot_tree_display(self, current: str) -> None:
        """更新排行榜显示（在主线程执行）"""
        mode = self.rank_mode.get()
        self.hot_tree.delete(*self.hot_tree.get_children())
        for idx, row in enumerate(self.monitor_rows, start=1):
            if mode in ("replay_candidate", "intraday_candidate"):
                amount_value = self._format_candidate_amount_value(mode, row, self.review_trade_date)
                item_id = self.hot_tree.insert(
                    "",
                    "end",
                    values=(idx, row["stock_code"], row["stock_name"], f"{row['total_score']}", amount_value),
                    tags=(),
                )
            elif mode in ("ths_hourly", "ths_value"):
                amount_text = f"热度:{row.rate:.0f}"
                pct_text = f"{row.rise_and_fall:.2f}"
                item_id = self.hot_tree.insert("", "end", values=(idx, row.code, row.name, pct_text, amount_text), tags=())
            elif mode == "jq_auction_1to2":
                # 竞价1进2模式不使用monitor_rows，直接返回
                return
            else:
                amount_text = f"{row.amount / 100000000:.2f}亿"
                item_id = self.hot_tree.insert("", "end", values=(idx, row.code, row.name, f"{row.pct_chg:.2f}", amount_text), tags=())

            if self._row_code(row) == current:
                self.hot_tree.selection_set(item_id)

    def _save_daily_snapshots(self, now=None) -> None:
        current = now or datetime.now()
        if self.app_runner.should_save_daily_snapshots(current):
            self._load_ths_hourly_hot()
            self._load_ths_value_hot()
            self._ths_last_update = current.strftime("%H:%M:%S")
        self.app_runner.save_daily_snapshots_if_needed(
            ths_hourly_rows=self.ths_hourly_hot,
            ths_value_rows=self.ths_value_hot,
            now=current,
        )
        # 自动导出训练数据CSV
        self._auto_export_training_csv(current)

    def _auto_export_training_csv(self, now: datetime) -> None:
        """自动导出训练数据CSV：昨日候选 + 今日实际表现"""
        from src.utils.trading_day_utils import is_trading_day
        from pathlib import Path

        # 只在15:00后且是交易日才导出
        if now.hour < 15:
            return
        if not is_trading_day(now):
            return

        today_str = now.strftime("%Y-%m-%d")

        # 检查今天是否已导出过
        if getattr(self, "_last_auto_export_date", "") == today_str:
            return
        if not hasattr(self.app_runner, "get_candidate_review_dates"):
            return

        # 获取可用的复盘候选日期（昨天）
        available_dates = self.app_runner.get_candidate_review_dates("replay")
        if not available_dates:
            return

        # 取最新的可用日期（应该是昨天）
        yesterday_str = available_dates[-1]

        # 确保是昨天的数据（避免导出更早的数据）
        from datetime import timedelta
        yesterday_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if yesterday_str != yesterday_date:
            # 如果最新日期不是昨天，可能是周末或节假日，跳过
            return

        # 构建导出路径
        project_root = Path(self.app_runner.settings.get("database", {}).get("path", "data/app.db")).parent.parent
        export_dir = project_root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"train_{yesterday_str}.csv"

        # 如果文件已存在，跳过
        if output_path.exists():
            self._last_auto_export_date = today_str
            return

        try:
            # 获取昨日候选数据并附加今日实时表现
            rows = self.app_runner.get_candidate_review_rows("replay", yesterday_str)
            if not rows:
                return

            # 附加实时标签（今日实际涨幅）
            rows = self.app_runner._attach_live_labels(rows, yesterday_str)

            # 导出CSV
            import csv
            metric_keys = sorted({key for row in rows for key in row.get("metrics", {}).keys()})
            fieldnames = [
                "trade_date", "session_type", "rank_no", "stock_code", "stock_name",
                "total_score", "grade", "heat_score", "market_cap_score",
                "volume_price_score", "position_score", "risk_penalty",
                "next_day_pct", "next_day_mode", "next_trade_date",
                "label_live_pct", "label_live_up", "label_live_strong", "label_live_rank_pct",
                "flags", "risks",
            ] + [f"metric_{key}" for key in metric_keys]

            with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for index, row in enumerate(rows, start=1):
                    exported_row = {
                        "trade_date": yesterday_str,
                        "session_type": "replay",
                        "rank_no": index,
                        "stock_code": row["stock_code"],
                        "stock_name": row["stock_name"],
                        "total_score": row["total_score"],
                        "grade": row["grade"],
                        "heat_score": row.get("heat_score", 0),
                        "market_cap_score": row.get("market_cap_score", 0),
                        "volume_price_score": row.get("volume_price_score", 0),
                        "position_score": row.get("position_score", 0),
                        "risk_penalty": row.get("risk_penalty", 0),
                        "next_day_pct": row.get("next_day_pct"),
                        "next_day_mode": row.get("next_day_mode", ""),
                        "next_trade_date": row.get("next_trade_date", ""),
                        "label_live_pct": row.get("label_live_pct"),
                        "label_live_up": row.get("label_live_up"),
                        "label_live_strong": row.get("label_live_strong"),
                        "label_live_rank_pct": row.get("label_live_rank_pct"),
                        "flags": "|".join(row.get("flags", [])),
                        "risks": "|".join(row.get("risks", [])),
                    }
                    for key in metric_keys:
                        exported_row[f"metric_{key}"] = row.get("metrics", {}).get(key)
                    writer.writerow(exported_row)

            # 标记今天已导出
            self._last_auto_export_date = today_str

            # 更新状态栏
            self._update_status(f"✅ 已自动导出训练数据: {output_path.name}")

            # 控制台输出
            print(f"[AutoExport] 训练数据已导出: {output_path}")
            print(f"[AutoExport] 包含 {len(rows)} 只昨日候选股票及今日实际表现")

        except Exception as e:
            print(f"[AutoExport] 导出失败: {e}")
            self._update_status(f"❌ 自动导出失败: {e}")

    def _update_hot_tree_columns(self, mode: str, review_enabled: bool = False) -> None:
        if mode in ("replay_candidate", "intraday_candidate"):
            self.hot_tree.heading("pct", text="候选评分")
            self.hot_tree.heading("amount", text="等级/当日/次日" if review_enabled else "等级/标签")
            self.hot_tree.column("rank", width=45, anchor="center")
            self.hot_tree.column("code", width=85, anchor="center")
            self.hot_tree.column("name", width=100, anchor="center")
            self.hot_tree.column("pct", width=75, anchor="center")
            self.hot_tree.column("amount", width=225 if review_enabled else 180, anchor="center")
            return

        if mode == "jq_auction_1to2":
            self.hot_tree.heading("pct", text="竞价涨幅")
            self.hot_tree.heading("amount", text="竞昨比/成交额/命中条件")
            self.hot_tree.column("rank", width=45, anchor="center")
            self.hot_tree.column("code", width=85, anchor="center")
            self.hot_tree.column("name", width=100, anchor="center")
            self.hot_tree.column("pct", width=85, anchor="center")
            self.hot_tree.column("amount", width=300, anchor="center")
            return

        self.hot_tree.heading("pct", text="涨幅%")
        self.hot_tree.heading("amount", text="成交额")
        self.hot_tree.column("rank", width=45, anchor="center")
        self.hot_tree.column("code", width=85, anchor="center")
        self.hot_tree.column("name", width=100, anchor="center")
        self.hot_tree.column("pct", width=75, anchor="center")
        self.hot_tree.column("amount", width=225, anchor="center")

    @staticmethod
    def _row_code(row) -> str:
        if isinstance(row, dict):
            return row.get("stock_code") or row.get("code", "")
        return getattr(row, "code", "")

    def _init_candidate_profile(self) -> None:
        profiles = self.app_runner.get_candidate_profiles() if hasattr(self.app_runner, "get_candidate_profiles") else []
        active_profile = self.app_runner.get_active_candidate_profile() if hasattr(self.app_runner, "get_active_candidate_profile") else ""

        # 构建更友好的显示名称
        profile_display_map = {
            'default': '默认配置 (无线训练)',
            'optimized_0603': 'optimized_0603 [06-03→06-05] 归一化训练',
            'optimized_2026_06_02': 'optimized_2026_06_02 [06-02训练]',
            'optimized_continuous_hot_2026_06_01': 'optimized_continuous_hot_2026_06_01 [06-01连续热门]',
            'optimized_continuous_hot_2026_06_01_strict': 'optimized_continuous_hot_2026_06_01_strict [06-01严格]',
            'optimized_continuous_hot_2026_06_02': 'optimized_continuous_hot_2026_06_02 [06-02连续热门]',
            'optimized_continuous_hot_2026_06_02_after_close': 'optimized_continuous_hot_2026_06_02_after_close [06-02盘后]',
            'optimized_continuous_hot_2026_06_02_strict': 'optimized_continuous_hot_2026_06_02_strict [06-02严格]',
            'optimized_latest': 'optimized_latest [最新优化]',
            'optimized_norm_raw5_2026_06_02': 'optimized_norm_raw5_2026_06_02 [06-02归一化原始5日]',
            'optimized_raw5_2026_06_02': 'optimized_raw5_2026_06_02 [06-02原始5日]',
            'optimized_raw_2026_06_02': 'optimized_raw_2026_06_02 [06-02原始]',
            'replay_2026_06_01_strict_norm': 'replay_2026_06_01_strict_norm [06-01复盘严格归一化]',
            'replay_2026_06_02_strict_norm': 'replay_2026_06_02_strict_norm [06-02复盘严格归一化]',
        }

        # 创建显示名称列表和反向映射
        display_names = []
        self._profile_name_map = {}  # 显示名称 -> 实际名称
        for profile in profiles:
            display_name = profile_display_map.get(profile, profile)
            display_names.append(display_name)
            self._profile_name_map[display_name] = profile

        if self.candidate_profile_combo is not None:
            self.candidate_profile_combo["values"] = display_names
            self.candidate_profile_combo.configure(state="readonly" if profiles else "disabled")

        # 设置当前选中的值
        active_display = profile_display_map.get(active_profile, active_profile)
        self.candidate_profile_var.set(active_display or (display_names[0] if display_names else ""))

    def _set_status_light(self, state: str) -> None:
        """设置状态指示灯颜色
        state: 'idle' (灰色), 'loading' (红色), 'done' (绿色)
        """
        self._status_light_state = state
        if state == "loading":
            self.status_light.itemconfig(self._light_id, fill="#ef4444", outline="#dc2626")  # 红色
        elif state == "done":
            self.status_light.itemconfig(self._light_id, fill="#22c55e", outline="#16a34a")  # 绿色
        else:  # idle
            self.status_light.itemconfig(self._light_id, fill="#98a2b3", outline="#667085")  # 灰色

    def _on_candidate_profile_selected(self, _event=None) -> None:
        display_name = self.candidate_profile_var.get().strip()
        if not display_name:
            return

        # 将显示名称转换为实际名称
        profile_name = getattr(self, '_profile_name_map', {}).get(display_name, display_name)

        # 设置指示灯为红色（加载中）
        self._set_status_light("loading")

        changed = self.app_runner.set_candidate_profile(profile_name)
        if changed is not False:
            self._refresh_hot_tree()
            self._update_status(f"已切换评分模型: {profile_name}")
            # 设置指示灯为绿色（完成）
            self._set_status_light("done")
        else:
            # 设置指示灯为灰色（空闲）
            self._set_status_light("idle")

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
        if mode == "jq_auction_1to2":
            self.review_date_combo["values"] = []
            self.review_date_combo.configure(state="disabled")
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

    @staticmethod
    def _format_candidate_amount_value(mode: str, row: dict, review_trade_date: str) -> str:
        tags_text = "、".join(row.get("flags", [])[:2]) if row.get("flags") else "-"
        if review_trade_date:
            today_pct = row.get("today_pct")
            today_str = f"当日{today_pct:+.2f}%" if today_pct is not None else "当日--"
            perf_text = QuantDaAMainWindow._format_candidate_forward_perf(row)
            return f"{row['grade']} | {today_str} | {perf_text}"
        if mode == "intraday_candidate":
            intraday_pct = (row.get("metrics") or {}).get("intraday_pct_chg")
            pct_text = f"{intraday_pct:+.2f}%" if intraday_pct is not None else "--"
            return f"{row['grade']} | {pct_text} | {tags_text}"
        if mode == "replay_candidate":
            live_pct = (row.get("metrics") or {}).get("live_pct_chg")
            pct_text = f"{live_pct:+.2f}%" if live_pct is not None else "--"
            return f"{row['grade']} | {pct_text} | {tags_text}"
        return f"{row['grade']} | {tags_text}"

    def _refresh_signal_tree(self) -> None:
        self.signal_tree.delete(*self.signal_tree.get_children())
        for signal in self.signal_rows[:100]:
            stock_text = f"{signal.stock_name} {signal.stock_code}"
            self.signal_tree.insert("", "end", values=(signal.timestamp, stock_text, signal.strategy_name, signal.signal_level))

    def _on_hot_tree_select(self, _event) -> None:
        selected = self.hot_tree.selection()
        if selected:
            code = self.hot_tree.item(selected[0], "values")[1]
            # 竞价1进2模式下，更新候选映射
            if self.rank_mode.get() == "jq_auction_1to2":
                result = next((r for r in self.jq_auction_results if r.code == code), None)
                if result:
                    self.current_candidate_map[code] = {
                        "stock_code": result.code,
                        "stock_name": result.name,
                        "total_score": 0,
                        "grade": result.matched_condition,
                        "flags": [f"竞价涨幅{result.auction_pct:.2f}%", f"竞昨比{result.auction_vol_ratio:.2f}%"],
                        "risks": [],
                    }
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
        # 先显示缓存数据，避免卡顿
        self._render_stock_detail(code, preview_only=True)
        # 后台获取完整数据
        self._render_stock_detail_async(code, preview_only=False)

    def _on_daily_bar_selected(self, bar) -> None:
        if self.selected_stock_code is None:
            return
        self.selected_daily_date = bar.ts[:10]
        self._render_stock_detail_async(self.selected_stock_code, preview_only=False)

    def _on_popup_click(self, signal) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._select_stock(signal.stock_code)
        self.reason_text.delete("1.0", "end")
        self.reason_text.insert("1.0", "\n".join(signal.reasons))

    def _render_stock_detail_async(self, code: str, preview_only: bool = False) -> None:
        """异步渲染股票详情，避免阻塞UI"""
        target_date = "" if preview_only and code != self.selected_stock_code else self.selected_daily_date

        def work() -> dict:
            if preview_only:
                return self.app_runner.get_cached_stock_detail(code, target_date)
            else:
                return self.app_runner.get_stock_detail(code, target_date)

        def on_success(detail: dict) -> None:
            # 确保当前选中的股票没变
            if code != self.selected_stock_code:
                return
            self._update_detail_display(detail, code, preview_only)

        def on_error(exc: Exception) -> None:
            print(f"[Async] 获取股票详情失败: {exc}")

        self._dispatch_background(work, on_success=on_success, on_error=on_error, task_key=f"render_{code}")

    def _update_detail_display(self, detail: dict, code: str, preview_only: bool) -> None:
        """更新详情显示（在主线程执行）"""
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
            "jq_auction_1to2": "竞价1进2",
        }.get(self.rank_mode.get(), self.rank_mode.get())

        current_count = len(self.monitor_rows) if self.rank_mode.get() != "jq_auction_1to2" else len(self.jq_auction_results)
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

        if len(price_points) >= 4:
            self.canvas.create_line(*price_points, fill="#175cd3", width=2, smooth=True)
        elif len(price_points) == 2:
            x, y = price_points
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#175cd3", outline="#175cd3")

        if len(ma5_points) >= 4:
            self.canvas.create_line(*ma5_points, fill="#f79009", width=1.5, smooth=True)
        if len(ma10_points) >= 4:
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
