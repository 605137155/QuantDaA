"""
首板1进2竞价策略模块

该模块实现了首板1进2策略的核心逻辑，用于在竞价阶段筛选符合条件的股票。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config_loader import load_toml


@dataclass
class AuctionRule:
    """竞价条件规则"""
    name: str
    open_lo: float
    open_hi: float
    auc_lo: float
    auc_hi: float


@dataclass
class JQStrategyParams:
    """首板1进2策略参数"""
    # 选股条件
    min_yesterday_pct: float = 0.07
    min_money: float = 100000000
    max_money: float = 1500000000
    min_market_cap: float = 10
    max_circ_cap: float = 520
    min_price: float = 3.0
    min_avg_chg: float = 0.07

    # 过滤条件
    max_limit_days_5: int = 4
    max_extreme_limit_10: int = 3
    max_volatility_5: float = 0.4
    high_point_ratio: float = 0.9

    # 竞价条件矩阵
    a_rules: list[AuctionRule] = field(default_factory=list)
    b_rules: list[AuctionRule] = field(default_factory=list)
    money_split: float = 500000000

    # 风控参数
    drop_stop_loss: float = 0.05
    drawdown_threshold: float = 0.08
    consecutive_loss_pause: int = 2
    ml_skip_threshold: float = 0.7
    ml_reduce_threshold: float = 0.5
    ml_sample_window: int = 120
    ml_min_samples: int = 60
    ma5_stop_loss_buffer: float = 0.05

    # 交易参数
    slippage: float = 0.005
    open_commission: float = 0.0002
    close_commission: float = 0.0002
    close_tax: float = 0.0005
    min_commission: float = 5


@dataclass
class AuctionMatchResult:
    """竞价匹配结果"""
    code: str
    name: str
    matched_condition: str
    auction_pct: float  # 竞价涨幅
    auction_vol_ratio: float  # 竞昨比
    yesterday_money: float  # 昨日成交额
    yesterday_close: float  # 昨日收盘价
    yesterday_volume: float  # 昨日成交量
    yesterday_amplitude: float  # 昨日振幅
    circ_cap: float  # 流通市值
    five_dim_score: float = 0.0  # 五维评分（可选）
    status: str = "待买入"  # 状态：待买入/已买入/已卖出/涨停持有


class JQAuction1to2Strategy:
    """首板1进2竞价策略"""

    def __init__(self, params: Optional[JQStrategyParams] = None, config_path: Optional[Path] = None):
        if params is not None:
            self.params = params
        elif config_path is not None:
            self.params = self._load_params_from_config(config_path)
        else:
            self.params = JQStrategyParams()
            self.params.a_rules = self._default_a_rules()
            self.params.b_rules = self._default_b_rules()

    @staticmethod
    def _default_a_rules() -> list[AuctionRule]:
        return [
            AuctionRule("A: 涨幅7~9% | 竞昨比10~20%", 1.07, 1.09, 0.10, 0.20),
        ]

    @staticmethod
    def _default_b_rules() -> list[AuctionRule]:
        return [
            AuctionRule("B: 涨幅7~9% | 竞昨比10~20%", 1.07, 1.09, 0.10, 0.20),
            AuctionRule("B: 涨幅4~7% | 竞昨比3~7%", 1.04, 1.07, 0.03, 0.07),
            AuctionRule("B: 涨幅4~7% | 竞昨比10~20%", 1.04, 1.07, 0.10, 0.20),
            AuctionRule("B: 涨幅0~4% | 竞昨比3~7%", 1.00, 1.04, 0.03, 0.07),
            AuctionRule("B: 涨幅0~4% | 竞昨比7~10%", 1.00, 1.04, 0.07, 0.10),
        ]

    def _load_params_from_config(self, config_path: Path) -> JQStrategyParams:
        """从配置文件加载参数"""
        params = JQStrategyParams()

        if not config_path.exists():
            params.a_rules = self._default_a_rules()
            params.b_rules = self._default_b_rules()
            return params

        raw = load_toml(config_path)

        # 选股条件
        sel = raw.get("selection", {})
        params.min_yesterday_pct = float(sel.get("min_yesterday_pct", 0.07))
        params.min_money = float(sel.get("min_money", 100000000))
        params.max_money = float(sel.get("max_money", 1500000000))
        params.min_market_cap = float(sel.get("min_market_cap", 10))
        params.max_circ_cap = float(sel.get("max_circ_cap", 520))
        params.min_price = float(sel.get("min_price", 3.0))
        params.min_avg_chg = float(sel.get("min_avg_chg", 0.07))

        # 过滤条件
        filt = raw.get("filter", {})
        params.max_limit_days_5 = int(filt.get("max_limit_days_5", 4))
        params.max_extreme_limit_10 = int(filt.get("max_extreme_limit_10", 3))
        params.max_volatility_5 = float(filt.get("max_volatility_5", 0.4))
        params.high_point_ratio = float(filt.get("high_point_ratio", 0.9))

        # 竞价条件矩阵
        auction = raw.get("auction_rules", {})
        params.money_split = float(auction.get("money_split", 500000000))

        params.a_rules = []
        for rule in auction.get("a_rules", []):
            params.a_rules.append(AuctionRule(
                name=rule.get("name", ""),
                open_lo=float(rule.get("open_lo", 1.0)),
                open_hi=float(rule.get("open_hi", 1.0)),
                auc_lo=float(rule.get("auc_lo", 0.0)),
                auc_hi=float(rule.get("auc_hi", 0.0)),
            ))

        params.b_rules = []
        for rule in auction.get("b_rules", []):
            params.b_rules.append(AuctionRule(
                name=rule.get("name", ""),
                open_lo=float(rule.get("open_lo", 1.0)),
                open_hi=float(rule.get("open_hi", 1.0)),
                auc_lo=float(rule.get("auc_lo", 0.0)),
                auc_hi=float(rule.get("auc_hi", 0.0)),
            ))

        if not params.a_rules:
            params.a_rules = self._default_a_rules()
        if not params.b_rules:
            params.b_rules = self._default_b_rules()

        # 风控参数
        risk = raw.get("risk", {})
        params.drop_stop_loss = float(risk.get("drop_stop_loss", 0.05))
        params.drawdown_threshold = float(risk.get("drawdown_threshold", 0.08))
        params.consecutive_loss_pause = int(risk.get("consecutive_loss_pause", 2))
        params.ml_skip_threshold = float(risk.get("ml_skip_threshold", 0.7))
        params.ml_reduce_threshold = float(risk.get("ml_reduce_threshold", 0.5))
        params.ml_sample_window = int(risk.get("ml_sample_window", 120))
        params.ml_min_samples = int(risk.get("ml_min_samples", 60))
        params.ma5_stop_loss_buffer = float(risk.get("ma5_stop_loss_buffer", 0.05))

        # 交易参数
        trading = raw.get("trading", {})
        params.slippage = float(trading.get("slippage", 0.005))
        params.open_commission = float(trading.get("open_commission", 0.0002))
        params.close_commission = float(trading.get("close_commission", 0.0002))
        params.close_tax = float(trading.get("close_tax", 0.0005))
        params.min_commission = float(trading.get("min_commission", 5))

        return params

    def check_auction_match(
        self,
        code: str,
        name: str,
        yesterday_close: float,
        yesterday_volume: float,
        yesterday_money: float,
        yesterday_amplitude: float,
        circ_cap: float,
        auction_price: float,
        auction_volume: float,
    ) -> Optional[AuctionMatchResult]:
        """
        检查股票是否满足竞价条件

        Args:
            code: 股票代码
            name: 股票名称
            yesterday_close: 昨日收盘价
            yesterday_volume: 昨日成交量
            yesterday_money: 昨日成交额（元）
            yesterday_amplitude: 昨日振幅（%）
            circ_cap: 流通市值（亿）
            auction_price: 竞价价格
            auction_volume: 竞价成交量

        Returns:
            AuctionMatchResult 如果匹配，否则 None
        """
        p = self.params

        # 基础过滤
        if yesterday_money < p.min_money or yesterday_money > p.max_money:
            return None
        if circ_cap < p.min_market_cap or circ_cap > p.max_circ_cap:
            return None

        # 计算竞价指标
        cur_ratio = auction_price / yesterday_close if yesterday_close > 0 else 0
        auction_ratio = auction_volume / yesterday_volume if yesterday_volume > 0 else 0

        # 判断成交额区间
        is_small = yesterday_money < p.money_split
        is_large = not is_small

        # 匹配条件矩阵
        matched_condition = None
        rules = p.a_rules if is_small else p.b_rules

        for rule in rules:
            if rule.open_lo < cur_ratio <= rule.open_hi and rule.auc_lo <= auction_ratio <= rule.auc_hi:
                matched_condition = rule.name
                break

        if matched_condition is None:
            return None

        return AuctionMatchResult(
            code=code,
            name=name,
            matched_condition=matched_condition,
            auction_pct=(cur_ratio - 1) * 100,
            auction_vol_ratio=auction_ratio * 100,
            yesterday_money=yesterday_money,
            yesterday_close=yesterday_close,
            yesterday_volume=yesterday_volume,
            yesterday_amplitude=yesterday_amplitude,
            circ_cap=circ_cap,
        )

    def get_condition_rules_display(self) -> list[dict]:
        """获取条件矩阵的显示格式"""
        rules = []
        for rule in self.params.a_rules:
            rules.append({
                "type": "A",
                "name": rule.name,
                "pct_range": f"{(rule.open_lo-1)*100:.0f}%~{(rule.open_hi-1)*100:.0f}%",
                "vol_range": f"{rule.auc_lo*100:.0f}%~{rule.auc_hi*100:.0f}%",
            })
        for rule in self.params.b_rules:
            rules.append({
                "type": "B",
                "name": rule.name,
                "pct_range": f"{(rule.open_lo-1)*100:.0f}%~{(rule.open_hi-1)*100:.0f}%",
                "vol_range": f"{rule.auc_lo*100:.0f}%~{rule.auc_hi*100:.0f}%",
            })
        return rules

    def save_params_to_config(self, config_path: Path) -> None:
        """保存参数到配置文件"""
        p = self.params

        lines = [
            "# 首板1进2策略参数配置（自动保存）",
            "",
            "[meta]",
            'enabled = true',
            'display_name = "竞价1进2"',
            "",
            "[selection]",
            f"min_yesterday_pct = {p.min_yesterday_pct}",
            f"min_money = {int(p.min_money)}",
            f"max_money = {int(p.max_money)}",
            f"min_market_cap = {int(p.min_market_cap)}",
            f"max_circ_cap = {int(p.max_circ_cap)}",
            f"min_price = {p.min_price}",
            f"min_avg_chg = {p.min_avg_chg}",
            "",
            "[filter]",
            f"max_limit_days_5 = {p.max_limit_days_5}",
            f"max_extreme_limit_10 = {p.max_extreme_limit_10}",
            f"max_volatility_5 = {p.max_volatility_5}",
            f"high_point_ratio = {p.high_point_ratio}",
            "",
            "[auction_rules]",
            f"money_split = {int(p.money_split)}",
            "",
        ]

        # A类规则
        lines.append("a_rules = [")
        for rule in p.a_rules:
            lines.append(f'    {{ name = "{rule.name}", open_lo = {rule.open_lo}, open_hi = {rule.open_hi}, auc_lo = {rule.auc_lo}, auc_hi = {rule.auc_hi} }},')
        lines.append("]")
        lines.append("")

        # B类规则
        lines.append("b_rules = [")
        for rule in p.b_rules:
            lines.append(f'    {{ name = "{rule.name}", open_lo = {rule.open_lo}, open_hi = {rule.open_hi}, auc_lo = {rule.auc_lo}, auc_hi = {rule.auc_hi} }},')
        lines.append("]")
        lines.append("")

        # 风控参数
        lines.extend([
            "[risk]",
            f"drop_stop_loss = {p.drop_stop_loss}",
            f"drawdown_threshold = {p.drawdown_threshold}",
            f"consecutive_loss_pause = {p.consecutive_loss_pause}",
            f"ml_skip_threshold = {p.ml_skip_threshold}",
            f"ml_reduce_threshold = {p.ml_reduce_threshold}",
            f"ml_sample_window = {p.ml_sample_window}",
            f"ml_min_samples = {p.ml_min_samples}",
            f"ma5_stop_loss_buffer = {p.ma5_stop_loss_buffer}",
            "",
            "[trading]",
            f"slippage = {p.slippage}",
            f"open_commission = {p.open_commission}",
            f"close_commission = {p.close_commission}",
            f"close_tax = {p.close_tax}",
            f"min_commission = {int(p.min_commission)}",
        ])

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
