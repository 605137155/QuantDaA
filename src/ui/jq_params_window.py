"""
首板1进2策略参数调整窗口

提供一个GUI窗口，用于微调策略参数。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Callable, Optional

from src.strategies.jq_auction_1to2 import JQStrategyParams, JQAuction1to2Strategy


class JQParamsWindow:
    """策略参数调整窗口"""

    def __init__(
        self,
        parent: tk.Tk,
        params: JQStrategyParams,
        config_path: Path,
        on_save: Optional[Callable[[JQStrategyParams], None]] = None,
    ):
        self.parent = parent
        self.params = params
        self.config_path = config_path
        self.on_save = on_save
        self.window: Optional[tk.Toplevel] = None
        self.entries: dict[str, tk.Entry] = {}
        self.rule_frames: list[dict] = []

    def show(self) -> None:
        """显示参数调整窗口"""
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return

        self.window = tk.Toplevel(self.parent)
        self.window.title("竞价1进2 - 参数调整")
        self.window.geometry("800x900")
        self.window.resizable(True, True)

        # 创建滚动区域
        canvas = tk.Canvas(self.window)
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # 构建参数界面
        self._build_selection_section(scrollable_frame)
        self._build_filter_section(scrollable_frame)
        self._build_auction_rules_section(scrollable_frame)
        self._build_risk_section(scrollable_frame)
        self._build_buttons(scrollable_frame)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 绑定鼠标滚轮（使用 bind 而非 bind_all，避免窗口关闭后报错）
        def _on_mousewheel(event):
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            try:
                canvas.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass

        self.window.bind("<Enter>", _bind_mousewheel)
        self.window.bind("<Leave>", _unbind_mousewheel)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self._on_close())

    def _add_label_entry(self, parent: ttk.Frame, row: int, label: str, key: str, value: str, tooltip: str = "") -> None:
        """添加一个标签+输入框"""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(10, 5), pady=3)
        entry = ttk.Entry(parent, width=20)
        entry.insert(0, str(value))
        entry.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        self.entries[key] = entry

        if tooltip:
            ttk.Label(parent, text=tooltip, foreground="gray").grid(row=row, column=2, sticky="w", padx=5, pady=3)

    def _build_selection_section(self, parent: ttk.Frame) -> None:
        """选股条件区域"""
        frame = ttk.LabelFrame(parent, text="选股条件", padding=10)
        frame.pack(fill="x", padx=10, pady=5)

        p = self.params
        self._add_label_entry(frame, 0, "昨日涨幅阈值", "min_yesterday_pct", p.min_yesterday_pct, "0.07=7%，越小越宽松")
        self._add_label_entry(frame, 1, "最小成交额(亿)", "min_money", p.min_money / 1e8, "原版1亿")
        self._add_label_entry(frame, 2, "最大成交额(亿)", "max_money", p.max_money / 1e8, "原版15亿")
        self._add_label_entry(frame, 3, "最小总市值(亿)", "min_market_cap", p.min_market_cap, "原版10亿")
        self._add_label_entry(frame, 4, "最大流通市值(亿)", "max_circ_cap", p.max_circ_cap, "原版520亿")
        self._add_label_entry(frame, 5, "最低股价(元)", "min_price", p.min_price, "原版3元")
        self._add_label_entry(frame, 6, "avg_chg阈值", "min_avg_chg", p.min_avg_chg, "原版0.07，越小越宽松")

    def _build_filter_section(self, parent: ttk.Frame) -> None:
        """过滤条件区域"""
        frame = ttk.LabelFrame(parent, text="过滤条件", padding=10)
        frame.pack(fill="x", padx=10, pady=5)

        p = self.params
        self._add_label_entry(frame, 0, "近5日涨停上限", "max_limit_days_5", p.max_limit_days_5, "超过则排除")
        self._add_label_entry(frame, 1, "近10日一字/T字上限", "max_extreme_limit_10", p.max_extreme_limit_10, "超过则排除")
        self._add_label_entry(frame, 2, "近5日波动上限", "max_volatility_5", p.max_volatility_5, "0.4=40%")
        self._add_label_entry(frame, 3, "100日高点比例", "high_point_ratio", p.high_point_ratio, "0.9=90%")

    def _build_auction_rules_section(self, parent: ttk.Frame) -> None:
        """竞价条件矩阵区域"""
        frame = ttk.LabelFrame(parent, text="竞价条件矩阵", padding=10)
        frame.pack(fill="x", padx=10, pady=5)

        # 说明
        ttk.Label(frame, text="格式：条件名称 | 竞价涨幅下限~上限 | 竞昨比下限~上限", foreground="gray").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 10)
        )

        # A类规则
        ttk.Label(frame, text="A类规则（小市值）", font=("", 10, "bold")).grid(row=1, column=0, columnspan=6, sticky="w")

        self.a_rule_entries = []
        for i, rule in enumerate(self.params.a_rules):
            row = i + 2
            entries = self._add_rule_row(frame, row, rule, f"a_{i}")
            self.a_rule_entries.append(entries)

        # 添加A类规则按钮
        a_add_row = len(self.params.a_rules) + 2
        ttk.Button(frame, text="+ 添加A类规则", command=lambda: self._add_rule(frame, "a")).grid(
            row=a_add_row, column=0, columnspan=6, sticky="w", pady=5
        )

        # B类规则
        b_start_row = a_add_row + 1
        ttk.Label(frame, text="B类规则（中市值）", font=("", 10, "bold")).grid(
            row=b_start_row, column=0, columnspan=6, sticky="w", pady=(10, 0)
        )

        self.b_rule_entries = []
        for i, rule in enumerate(self.params.b_rules):
            row = b_start_row + i + 1
            entries = self._add_rule_row(frame, row, rule, f"b_{i}")
            self.b_rule_entries.append(entries)

        # 添加B类规则按钮
        b_add_row = b_start_row + len(self.params.b_rules) + 1
        ttk.Button(frame, text="+ 添加B类规则", command=lambda: self._add_rule(frame, "b")).grid(
            row=b_add_row, column=0, columnspan=6, sticky="w", pady=5
        )

        # 成交额分界线
        ttk.Label(frame, text="A/B类成交额分界线(亿)").grid(row=b_add_row + 1, column=0, sticky="w", padx=(10, 5), pady=3)
        money_split_entry = ttk.Entry(frame, width=20)
        money_split_entry.insert(0, str(self.params.money_split / 1e8))
        money_split_entry.grid(row=b_add_row + 1, column=1, sticky="w", padx=5, pady=3)
        self.entries["money_split"] = money_split_entry

    def _add_rule_row(self, parent: ttk.Frame, row: int, rule, prefix: str) -> dict:
        """添加一行规则输入"""
        entries = {}

        # 条件名称
        ttk.Label(parent, text="名称").grid(row=row, column=0, sticky="w", padx=(20, 5))
        name_entry = ttk.Entry(parent, width=30)
        name_entry.insert(0, rule.name)
        name_entry.grid(row=row, column=1, sticky="w", padx=5)
        entries["name"] = name_entry

        # 竞价涨幅
        ttk.Label(parent, text="涨幅").grid(row=row, column=2, sticky="w", padx=5)
        open_lo_entry = ttk.Entry(parent, width=8)
        open_lo_entry.insert(0, str(rule.open_lo))
        open_lo_entry.grid(row=row, column=3, sticky="w", padx=2)
        entries["open_lo"] = open_lo_entry

        ttk.Label(parent, text="~").grid(row=row, column=3, sticky="e", padx=(0, 20))

        open_hi_entry = ttk.Entry(parent, width=8)
        open_hi_entry.insert(0, str(rule.open_hi))
        open_hi_entry.grid(row=row, column=4, sticky="w", padx=2)
        entries["open_hi"] = open_hi_entry

        # 竞昨比
        ttk.Label(parent, text="竞昨比").grid(row=row, column=5, sticky="w", padx=5)
        auc_lo_entry = ttk.Entry(parent, width=8)
        auc_lo_entry.insert(0, str(rule.auc_lo))
        auc_lo_entry.grid(row=row, column=6, sticky="w", padx=2)
        entries["auc_lo"] = auc_lo_entry

        ttk.Label(parent, text="~").grid(row=row, column=6, sticky="e", padx=(0, 20))

        auc_hi_entry = ttk.Entry(parent, width=8)
        auc_hi_entry.insert(0, str(rule.auc_hi))
        auc_hi_entry.grid(row=row, column=7, sticky="w", padx=2)
        entries["auc_hi"] = auc_hi_entry

        # 删除按钮
        del_btn = ttk.Button(parent, text="×", width=3, command=lambda: self._delete_rule(parent, row, prefix))
        del_btn.grid(row=row, column=8, padx=5)

        return entries

    def _add_rule(self, parent: ttk.Frame, rule_type: str) -> None:
        """添加新规则"""
        if rule_type == "a":
            idx = len(self.a_rule_entries)
            row = idx + 2
            rule = type('Rule', (), {'name': f'A: 新规则{idx+1}', 'open_lo': 1.03, 'open_hi': 1.07, 'auc_lo': 0.05, 'auc_hi': 0.15})()
            entries = self._add_rule_row(parent, row, rule, f"a_{idx}")
            self.a_rule_entries.append(entries)
        else:
            idx = len(self.b_rule_entries)
            row = idx + len(self.a_rule_entries) + 4
            rule = type('Rule', (), {'name': f'B: 新规则{idx+1}', 'open_lo': 1.03, 'open_hi': 1.07, 'auc_lo': 0.05, 'auc_hi': 0.15})()
            entries = self._add_rule_row(parent, row, rule, f"b_{idx}")
            self.b_rule_entries.append(entries)

    def _delete_rule(self, parent: ttk.Frame, row: int, prefix: str) -> None:
        """删除规则"""
        # 简化处理：重新构建整个窗口
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.show()

    def _build_risk_section(self, parent: ttk.Frame) -> None:
        """风控参数区域"""
        frame = ttk.LabelFrame(parent, text="风控参数", padding=10)
        frame.pack(fill="x", padx=10, pady=5)

        p = self.params
        self._add_label_entry(frame, 0, "跌幅止损", "drop_stop_loss", p.drop_stop_loss, "0.05=5%")
        self._add_label_entry(frame, 1, "净值回撤减仓", "drawdown_threshold", p.drawdown_threshold, "0.08=8%")
        self._add_label_entry(frame, 2, "连亏暂停天数", "consecutive_loss_pause", p.consecutive_loss_pause, "连亏N天暂停")
        self._add_label_entry(frame, 3, "ML跳过阈值", "ml_skip_threshold", p.ml_skip_threshold, "预测亏损概率>此值跳过")
        self._add_label_entry(frame, 4, "ML减半阈值", "ml_reduce_threshold", p.ml_reduce_threshold, "预测亏损概率>此值减半")
        self._add_label_entry(frame, 5, "5日线止损加成", "ma5_stop_loss_buffer", p.ma5_stop_loss_buffer, "价格<5日线*(1+此值)止损")

    def _build_buttons(self, parent: ttk.Frame) -> None:
        """按钮区域"""
        frame = ttk.Frame(parent, padding=10)
        frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(frame, text="保存配置", command=self._save_params).pack(side="left", padx=5)
        ttk.Button(frame, text="恢复默认", command=self._reset_defaults).pack(side="left", padx=5)
        ttk.Button(frame, text="取消", command=self._cancel).pack(side="right", padx=5)

    def _save_params(self) -> None:
        """保存参数"""
        try:
            p = self.params

            # 选股条件
            p.min_yesterday_pct = float(self.entries["min_yesterday_pct"].get())
            p.min_money = float(self.entries["min_money"].get()) * 1e8
            p.max_money = float(self.entries["max_money"].get()) * 1e8
            p.min_market_cap = float(self.entries["min_market_cap"].get())
            p.max_circ_cap = float(self.entries["max_circ_cap"].get())
            p.min_price = float(self.entries["min_price"].get())
            p.min_avg_chg = float(self.entries["min_avg_chg"].get())

            # 过滤条件
            p.max_limit_days_5 = int(self.entries["max_limit_days_5"].get())
            p.max_extreme_limit_10 = int(self.entries["max_extreme_limit_10"].get())
            p.max_volatility_5 = float(self.entries["max_volatility_5"].get())
            p.high_point_ratio = float(self.entries["high_point_ratio"].get())

            # 成交额分界线
            p.money_split = float(self.entries["money_split"].get()) * 1e8

            # A类规则
            p.a_rules = []
            for entry_group in self.a_rule_entries:
                p.a_rules.append(type('AuctionRule', (), {
                    'name': entry_group["name"].get(),
                    'open_lo': float(entry_group["open_lo"].get()),
                    'open_hi': float(entry_group["open_hi"].get()),
                    'auc_lo': float(entry_group["auc_lo"].get()),
                    'auc_hi': float(entry_group["auc_hi"].get()),
                })())

            # B类规则
            p.b_rules = []
            for entry_group in self.b_rule_entries:
                p.b_rules.append(type('AuctionRule', (), {
                    'name': entry_group["name"].get(),
                    'open_lo': float(entry_group["open_lo"].get()),
                    'open_hi': float(entry_group["open_hi"].get()),
                    'auc_lo': float(entry_group["auc_lo"].get()),
                    'auc_hi': float(entry_group["auc_hi"].get()),
                })())

            # 风控参数
            p.drop_stop_loss = float(self.entries["drop_stop_loss"].get())
            p.drawdown_threshold = float(self.entries["drawdown_threshold"].get())
            p.consecutive_loss_pause = int(self.entries["consecutive_loss_pause"].get())
            p.ml_skip_threshold = float(self.entries["ml_skip_threshold"].get())
            p.ml_reduce_threshold = float(self.entries["ml_reduce_threshold"].get())
            p.ma5_stop_loss_buffer = float(self.entries["ma5_stop_loss_buffer"].get())

            # 保存到文件
            strategy = JQAuction1to2Strategy(params=p)
            strategy.save_params_to_config(self.config_path)

            # 回调
            if self.on_save:
                self.on_save(p)

            messagebox.showinfo("保存成功", "参数已保存到配置文件")

        except Exception as e:
            messagebox.showerror("保存失败", f"参数格式错误：{e}")

    def _reset_defaults(self) -> None:
        """恢复默认参数"""
        if messagebox.askyesno("确认", "确定要恢复默认参数吗？"):
            self.params = JQStrategyParams()
            self.params.a_rules = JQAuction1to2Strategy._default_a_rules()
            self.params.b_rules = JQAuction1to2Strategy._default_b_rules()

            # 刷新窗口
            if self.window is not None:
                self.window.destroy()
                self.window = None
            self.show()

    def _cancel(self) -> None:
        """取消"""
        self._on_close()

    def _on_close(self) -> None:
        """关闭窗口"""
        if self.window is not None:
            try:
                self.window.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass
            self.window.destroy()
            self.window = None
