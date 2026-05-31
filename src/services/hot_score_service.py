from __future__ import annotations

from typing import Optional, Set
from src.data_providers.ths_hot_provider import THSHotProvider
from src.utils.logger import get_logger


class HotScoreService:
    """热度评分服务，集成同花顺热门榜单数据"""

    def __init__(self, ths_provider: Optional[THSHotProvider] = None, enable_ths: bool = True,
                 ths_hourly_hot_bonus: int = 15, ths_value_hot_bonus: int = 10):
        """
        初始化热度评分服务

        Args:
            ths_provider: 同花顺热门榜单数据提供者
            enable_ths: 是否启用同花顺数据
            ths_hourly_hot_bonus: 24小时热榜加分
            ths_value_hot_bonus: 价值投资热榜加分
        """
        self.logger = get_logger()
        self.ths_provider = ths_provider
        self.enable_ths = enable_ths
        self.ths_hourly_hot_bonus = ths_hourly_hot_bonus
        self.ths_value_hot_bonus = ths_value_hot_bonus

        # 缓存同花顺热门股票代码
        self._hourly_hot_codes: Set[str] = set()  # 24小时热榜
        self._value_hot_codes: Set[str] = set()   # 价值投资热榜

        # 初始化时加载同花顺数据
        if self.enable_ths and self.ths_provider:
            self._refresh_ths_cache()

    def _refresh_ths_cache(self) -> None:
        """刷新同花顺热门榜单缓存"""
        try:
            # 获取24小时热榜（日榜）
            hourly_stocks = self.ths_provider.get_24h_hot(limit=100)
            self._hourly_hot_codes = {s.code for s in hourly_stocks}
            self.logger.info(f"[THS] 24小时热榜已更新，共 {len(self._hourly_hot_codes)} 只股票")

            # 获取价值投资热榜（日榜-价值面）
            value_stocks = self.ths_provider.get_hot_stocks(
                time_type="day",
                list_type="value",
                limit=100
            )
            self._value_hot_codes = {s.code for s in value_stocks}
            self.logger.info(f"[THS] 价值投资热榜已更新，共 {len(self._value_hot_codes)} 只股票")

        except Exception as e:
            self.logger.warning(f"[THS] 获取同花顺热门榜单失败: {e}")

    def pick_top(self, monitor_pool: list, top_n: int) -> list:
        """选取热度最高的股票"""
        ranked = sorted(monitor_pool, key=self.score, reverse=True)
        return ranked[:top_n]

    def score(self, snapshot) -> int:
        """
        计算股票热度评分

        评分规则：
        - 基础分（成交额、涨跌幅、换手率、振幅）：最高 75 分
        - 同花顺24小时热榜加分：+15 分
        - 同花顺价值投资热榜加分：+10 分
        - 总分最高 100 分
        """
        score = 0

        # 基础评分（原有逻辑）
        # 成交额评分
        if snapshot.amount >= 5_000_000_000:
            score += 40
        elif snapshot.amount >= 3_000_000_000:
            score += 30
        elif snapshot.amount >= 1_000_000_000:
            score += 20
        else:
            score += 10

        # 涨跌幅评分
        if snapshot.pct_chg >= 3:
            score += 15
        elif snapshot.pct_chg >= 1:
            score += 8

        # 换手率评分
        if snapshot.turnover_rate >= 3:
            score += 10
        elif snapshot.turnover_rate >= 1:
            score += 5

        # 振幅评分
        amplitude = 0.0
        if snapshot.last_price:
            amplitude = max(snapshot.high - snapshot.low, 0.0) / snapshot.last_price

        if amplitude >= 0.05:
            score += 10
        elif amplitude >= 0.03:
            score += 5

        # 同花顺热门榜单加分
        if self.enable_ths:
            stock_code = snapshot.code

            # 24小时热榜加分
            if stock_code in self._hourly_hot_codes:
                score += self.ths_hourly_hot_bonus

            # 价值投资热榜加分
            if stock_code in self._value_hot_codes:
                score += self.ths_value_hot_bonus

        return score

    def get_ths_hot_info(self, stock_code: str) -> dict:
        """
        获取股票在同花顺热门榜单的信息

        Args:
            stock_code: 股票代码

        Returns:
            包含榜单信息的字典
        """
        info = {
            'in_hourly_hot': stock_code in self._hourly_hot_codes,
            'in_value_hot': stock_code in self._value_hot_codes,
        }
        return info

    def refresh_ths_data(self) -> None:
        """手动刷新同花顺数据"""
        if self.enable_ths and self.ths_provider:
            self._refresh_ths_cache()
