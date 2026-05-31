from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional


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
        self.current_detail = None
        self.hover_stock_code = ""
        self._refresh_job = None
        self._scan_job = None
        self.popup_notifier = None

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
        tk.Label(rank_bar, text="排行视图", bg="#ffffff", fg="#344054").pack(side="left")
        ttk.Radiobutton(rank_bar, text="成交额前100", value="monitor", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=(12, 4)
        )
        ttk.Radiobutton(rank_bar, text="重点池", value="focus", variable=self.rank_mode, command=self._refresh_hot_tree).pack(
            side="left", padx=4
        )

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
        if self.monitor_rows:
            self._select_stock(self.monitor_rows[0].code)
        self._update_status("初始化完成")

    def _schedule_jobs(self) -> None:
        pool_ms = int(self.app_runner.settings["scan"]["pool_refresh_seconds"] * 1000)
        scan_ms = int(self.app_runner.settings["scan"]["signal_scan_seconds"] * 1000)
        self._refresh_job = self.root.after(pool_ms, self._refresh_cycle)
        self._scan_job = self.root.after(scan_ms, self._scan_cycle)

    def _refresh_cycle(self) -> None:
        self.app_runner.refresh_pools()
        self._refresh_hot_tree()
        self._update_status("排行榜已刷新")
        self._refresh_job = self.root.after(int(self.app_runner.settings["scan"]["pool_refresh_seconds"] * 1000), self._refresh_cycle)

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

    def _refresh_hot_tree(self) -> None:
        current = self.selected_stock_code
        self.monitor_rows = list(self.app_runner.state.focus_pool if self.rank_mode.get() == "focus" else self.app_runner.state.monitor_pool)
        self.hot_tree.delete(*self.hot_tree.get_children())
        for idx, row in enumerate(self.monitor_rows, start=1):
            amount_text = f"{row.amount / 100000000:.2f}亿"
            item_id = self.hot_tree.insert("", "end", values=(idx, row.code, row.name, f"{row.pct_chg:.2f}", amount_text), tags=())
            if row.code == current:
                self.hot_tree.selection_set(item_id)

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
        for existing in self.hot_tree.get_children():
            self.hot_tree.item(existing, tags=())
        self.hot_tree.item(item_id, tags=("hover",))
        values = self.hot_tree.item(item_id, "values")
        if not values:
            return
        code = values[1]
        if code == self.hover_stock_code:
            return
        self.hover_stock_code = code
        self._render_stock_detail(code, preview_only=True)

    def _on_hot_tree_leave(self, _event) -> None:
        self.hover_stock_code = ""
        for existing in self.hot_tree.get_children():
            self.hot_tree.item(existing, tags=())
        if self.selected_stock_code:
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
        detail = self.app_runner.get_stock_detail(code, target_date)
        self.current_detail = detail
        snapshot = detail["snapshot"]
        if not preview_only:
            self.selected_daily_date = detail["selected_date"]
        shown_date = detail["selected_date"]
        available_dates = detail["available_minute_dates"]
        all_minute_bars = detail["all_minute_bars"]
        live_date = available_dates[-1] if available_dates else ""
        live_bars = [item for item in all_minute_bars if item.ts[:10] == live_date] if live_date else []
        self.detail_var.set(
            f"{snapshot.name} {snapshot.code}\n"
            f"最新价: {snapshot.last_price:.2f}    涨幅: {snapshot.pct_chg:.2f}%    成交额: {snapshot.amount / 100000000:.2f}亿    换手率: {snapshot.turnover_rate:.2f}%\n"
            f"最高: {snapshot.high:.2f}    最低: {snapshot.low:.2f}    实时窗口: {live_date or '无'}    复盘窗口: {shown_date or '无'}    分时日期数: {len(available_dates)}"
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
        all_minute_bars = self.current_detail["all_minute_bars"]
        minute_bars = [item for item in all_minute_bars if item.ts[:10] == hover_date]
        self.replay_chart.render(minute_bars, selected_date=hover_date)
        available_dates = self.current_detail["available_minute_dates"]
        if minute_bars:
            self.replay_hint_var.set(f"正在预览 {hover_date} 的分时，点击可锁定该日期。")
        else:
            self.replay_hint_var.set(f"{hover_date} 暂无免费分钟数据，当前仅支持最近 {len(available_dates)} 个交易日左右的分时查看。")

    def _update_status(self, prefix: str) -> None:
        mode = "回退数据" if "mock" in self.app_runner.provider_name else "真实数据"
        suffix = f" | 数据源: {self.app_runner.provider_name} ({mode}) | 当前排行数: {len(self.app_runner.state.monitor_pool)}"
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

    def render(self, bars: list, selected_date: str = "") -> None:
        self.canvas.delete("all")
        self._bars = list(bars)
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
        self._draw_crosshair(x, y)
        if self.on_hover is not None:
            self.on_hover(bar)
        self.info_var.set(f"{bar.ts}  开:{bar.open:.2f} 高:{bar.high:.2f} 低:{bar.low:.2f} 收:{bar.close:.2f} 量:{bar.volume:.0f}")

    def _on_leave(self, _event) -> None:
        self.canvas.delete("crosshair")
        if self._bars:
            self.info_var.set("鼠标悬停看日线，点击某天切换右侧分时，滚轮缩放")

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
        self.last_selected_bar = None

    def render(self, bars: list, selected_date: str = "") -> None:
        self.canvas.delete("all")
        self._bars = list(bars)
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
