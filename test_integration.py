"""
测试同花顺热门榜单集成
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.data_providers.ths_hot_provider import THSHotProvider
from src.services.hot_score_service import HotScoreService


def test_integration():
    """测试同花顺热门榜单集成"""
    print("=" * 60)
    print("测试同花顺热门榜单集成")
    print("=" * 60)

    # 1. 测试同花顺数据提供者
    print("\n[1] 测试同花顺数据提供者")
    try:
        ths_provider = THSHotProvider()
        hourly_stocks = ths_provider.get_hourly_hot(limit=10)
        print(f"  [OK] 24小时热榜获取成功，共 {len(hourly_stocks)} 只股票")
        for stock in hourly_stocks[:5]:
            print(f"    {stock.order}. {stock.code} {stock.name} - 热度: {stock.rate:.0f}")

        value_stocks = ths_provider.get_hot_stocks(time_type="day", list_type="value", limit=10)
        print(f"  [OK] 价值投资热榜获取成功，共 {len(value_stocks)} 只股票")
        for stock in value_stocks[:5]:
            print(f"    {stock.order}. {stock.code} {stock.name} - 热度: {stock.rate:.0f}")
    except Exception as e:
        print(f"  [FAIL] 同花顺数据提供者测试失败: {e}")
        return False

    # 2. 测试热度评分服务
    print("\n[2] 测试热度评分服务")
    try:
        hot_score_service = HotScoreService(ths_provider=ths_provider, enable_ths=True)

        # 模拟股票快照
        class MockSnapshot:
            def __init__(self, code, name, amount, pct_chg, turnover_rate, last_price, high, low):
                self.code = code
                self.name = name
                self.amount = amount
                self.pct_chg = pct_chg
                self.turnover_rate = turnover_rate
                self.last_price = last_price
                self.high = high
                self.low = low

        # 测试股票：假设在同花顺热榜中的股票
        test_stock = MockSnapshot(
            code=hourly_stocks[0].code if hourly_stocks else "000001",
            name=hourly_stocks[0].name if hourly_stocks else "测试股票",
            amount=3_000_000_000,  # 30亿成交额
            pct_chg=5.0,  # 5% 涨幅
            turnover_rate=4.0,  # 4% 换手率
            last_price=10.0,
            high=10.5,
            low=9.5
        )

        score = hot_score_service.score(test_stock)
        print(f"  [OK] 热度评分计算成功")
        print(f"    股票: {test_stock.code} {test_stock.name}")
        print(f"    评分: {score}")

        # 获取同花顺榜单信息
        ths_info = hot_score_service.get_ths_hot_info(test_stock.code)
        print(f"    24小时热榜: {'是' if ths_info['in_hourly_hot'] else '否'}")
        print(f"    价值投资热榜: {'是' if ths_info['in_value_hot'] else '否'}")

    except Exception as e:
        print(f"  [FAIL] 热度评分服务测试失败: {e}")
        return False

    # 3. 测试配置加载
    print("\n[3] 测试配置加载")
    try:
        from src.config_loader import load_toml
        settings = load_toml(project_root / "config" / "settings.toml")
        hot_score_settings = settings.get("hot_score", {})
        print(f"  [OK] 配置加载成功")
        print(f"    enable_ths: {hot_score_settings.get('enable_ths', True)}")
        print(f"    ths_hourly_hot_bonus: {hot_score_settings.get('ths_hourly_hot_bonus', 15)}")
        print(f"    ths_value_hot_bonus: {hot_score_settings.get('ths_value_hot_bonus', 10)}")
    except Exception as e:
        print(f"  [FAIL] 配置加载测试失败: {e}")
        return False

    print("\n" + "=" * 60)
    print("[SUCCESS] 所有测试通过！同花顺热门榜单集成成功！")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_integration()
    sys.exit(0 if success else 1)