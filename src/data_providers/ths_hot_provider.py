"""
同花顺热门榜单数据提供者

提供同花顺热门榜单数据，包括：
- 热门股票（小时榜/日榜）
- 热门板块（概念/行业）

API 来源：https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock
"""

import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class THSStockHot:
    """同花顺热门股票数据"""
    code: str               # 股票代码
    name: str               # 股票名称
    order: int              # 排名
    rate: float             # 热度值
    rise_and_fall: float    # 涨跌幅 (%)
    hot_rank_chg: int       # 排名变化
    market: int             # 市场代码 (17=上海, 33=深圳)
    concept_tags: List[str] # 概念标签
    popularity_tag: str     # 人气标签
    analyse: Optional[str]  # 分析说明
    analyse_title: Optional[str]  # 分析标题


@dataclass
class THSPlateHot:
    """同花顺热门板块数据"""
    code: str               # 板块代码
    name: str               # 板块名称
    order: int              # 排名
    rate: float             # 热度值
    rise_and_fall: float    # 涨跌幅 (%)
    lead_stock: Optional[str]  # 领涨股票


class THSHotProvider:
    """同花顺热门榜单数据提供者"""

    BASE_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1"

    # 列表类型
    LIST_TYPE_NORMAL = "normal"      # 普通热门
    LIST_TYPE_SKYROCKET = "skyrocket"  # 飙升

    # 时间类型
    TYPE_HOUR = "hour"  # 小时榜
    TYPE_DAY = "day"    # 日榜

    # 日榜维度
    DAY_LIST_TECH = "tech"    # 技术面
    DAY_LIST_VALUE = "value"  # 价值面
    DAY_LIST_TREND = "trend"  # 趋势面

    # 板块类型
    PLATE_CONCEPT = "concept"    # 概念板块
    PLATE_INDUSTRY = "industry"  # 行业板块

    def __init__(self, timeout: int = 10):
        """
        初始化同花顺热门榜单数据提供者

        Args:
            timeout: 请求超时时间（秒）
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://eq.10jqka.com.cn/frontend/thsTopRank/index.html',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Origin': 'https://eq.10jqka.com.cn',
        })

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        发送 GET 请求

        Args:
            endpoint: API 端点
            params: 请求参数

        Returns:
            API 响应数据

        Raises:
            Exception: 请求失败时抛出异常
        """
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if data.get('status_code') != 0:
                raise Exception(f"API 返回错误: {data.get('status_msg', '未知错误')}")

            return data.get('data', {})

        except requests.exceptions.RequestException as e:
            raise Exception(f"请求失败: {e}")

    def get_hot_stocks(
        self,
        time_type: str = "hour",
        list_type: str = "normal",
        limit: int = 100
    ) -> List[THSStockHot]:
        """
        获取热门股票榜单

        Args:
            time_type: 时间类型，"hour"（小时榜）或 "day"（日榜）
            list_type: 列表类型
                - 小时榜: "normal"（普通）, "skyrocket"（飙升）
                - 日榜: "tech"（技术面）, "value"（价值面）, "trend"（趋势面）
            limit: 返回数量限制，默认 100

        Returns:
            热门股票列表
        """
        params = {
            'stock_type': 'a',
            'type': time_type,
            'list_type': list_type
        }

        data = self._get('stock', params)
        stock_list = data.get('stock_list', [])

        result = []
        for item in stock_list[:limit]:
            stock = THSStockHot(
                code=item.get('code', ''),
                name=item.get('name', ''),
                order=item.get('order', 0),
                rate=float(item.get('rate', 0)),
                rise_and_fall=float(item.get('rise_and_fall', 0)),
                hot_rank_chg=item.get('hot_rank_chg', 0),
                market=item.get('market', 0),
                concept_tags=item.get('tag', {}).get('concept_tag', []),
                popularity_tag=item.get('tag', {}).get('popularity_tag', ''),
                analyse=item.get('analyse'),
                analyse_title=item.get('analyse_title')
            )
            result.append(stock)

        return result

    def get_hot_plates(
        self,
        plate_type: str = "concept",
        limit: int = 50
    ) -> List[THSPlateHot]:
        """
        获取热门板块榜单

        Args:
            plate_type: 板块类型，"concept"（概念板块）或 "industry"（行业板块）
            limit: 返回数量限制，默认 50

        Returns:
            热门板块列表
        """
        params = {
            'type': plate_type
        }

        data = self._get('plate', params)
        plate_list = data.get('plate_list', [])

        result = []
        for item in plate_list[:limit]:
            plate = THSPlateHot(
                code=item.get('code', ''),
                name=item.get('name', ''),
                order=item.get('order', 0),
                rate=float(item.get('rate', 0)),
                rise_and_fall=float(item.get('rise_and_fall', 0)),
                lead_stock=item.get('lead_stock', {}).get('name') if item.get('lead_stock') else None
            )
            result.append(plate)

        return result

    def get_hourly_hot(self, limit: int = 100) -> List[THSStockHot]:
        """获取小时热门榜（普通）"""
        return self.get_hot_stocks(time_type="hour", list_type="normal", limit=limit)

    def get_hourly_skyrocket(self, limit: int = 100) -> List[THSStockHot]:
        """获取小时飙升榜"""
        return self.get_hot_stocks(time_type="hour", list_type="skyrocket", limit=limit)

    def get_24h_hot(self, limit: int = 100) -> List[THSStockHot]:
        """获取24小时热榜（日榜-普通）"""
        return self.get_hot_stocks(time_type="day", list_type="normal", limit=limit)

    def get_daily_hot(self, limit: int = 100) -> List[THSStockHot]:
        """获取日热门榜（技术面）"""
        return self.get_hot_stocks(time_type="day", list_type="tech", limit=limit)

    def get_daily_hot(self, limit: int = 100) -> List[THSStockHot]:
        """获取日热门榜（技术面）"""
        return self.get_hot_stocks(time_type="day", list_type="tech", limit=limit)

    def get_concept_plates(self, limit: int = 50) -> List[THSPlateHot]:
        """获取热门概念板块"""
        return self.get_hot_plates(plate_type="concept", limit=limit)

    def get_industry_plates(self, limit: int = 50) -> List[THSPlateHot]:
        """获取热门行业板块"""
        return self.get_hot_plates(plate_type="industry", limit=limit)


# 测试代码
if __name__ == "__main__":
    provider = THSHotProvider()

    print("=" * 60)
    print("同花顺热门榜单数据测试")
    print("=" * 60)

    # 获取小时热门榜 Top 10
    print("\n[小时热门榜 Top 10]")
    stocks = provider.get_hourly_hot(limit=10)
    for stock in stocks:
        print(f"  {stock.order}. {stock.code} {stock.name} "
              f"- 热度: {stock.rate:.0f} 涨跌: {stock.rise_and_fall:+.2f}% "
              f"{'↑' if stock.hot_rank_chg > 0 else '↓' if stock.hot_rank_chg < 0 else '-'}")

    # 获取小时飙升榜 Top 10
    print("\n[小时飙升榜 Top 10]")
    stocks = provider.get_hourly_skyrocket(limit=10)
    for stock in stocks:
        print(f"  {stock.order}. {stock.code} {stock.name} "
              f"- 热度: {stock.rate:.0f} 涨跌: {stock.rise_and_fall:+.2f}%")

    # 获取热门概念板块 Top 10
    print("\n[热门概念板块 Top 10]")
    plates = provider.get_concept_plates(limit=10)
    for plate in plates:
        print(f"  {plate.order}. {plate.name} "
              f"- 热度: {plate.rate:.0f} 涨跌: {plate.rise_and_fall:+.2f}%"
              f"{f' 领涨: {plate.lead_stock}' if plate.lead_stock else ''}")