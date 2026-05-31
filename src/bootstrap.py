from __future__ import annotations

from pathlib import Path
import os

from src.config_loader import load_toml
from src.core.alert_manager import AlertManager
from src.core.app_runner import AppRunner
from src.core.pool_manager import PoolManager
from src.core.strategy_runner import StrategyRunner
from src.data_providers.akshare_provider import AkshareHistoricalMinuteProvider
from src.data_providers.mock_provider import MockMarketProvider
from src.data_providers.resilient_provider import ResilientMarketProvider
from src.data_providers.sina_tencent_provider import SinaTencentMarketProvider
from src.repositories.database import Database
from src.repositories.candidate_score_repo import CandidateScoreRepository
from src.repositories.daily_repo import DailyBarRepository
from src.repositories.minute_repo import MinuteBarRepository
from src.repositories.rank_snapshot_repo import RankSnapshotRepository
from src.repositories.signal_repo import SignalRepository
from src.repositories.stock_repo import StockRepository
from src.repositories.watchlist_repo import WatchlistRepository
from src.services.candidate_scoring_service import CandidateScoringService
from src.services.hot_score_service import HotScoreService
from src.data_providers.ths_hot_provider import THSHotProvider
from src.services.signal_dedupe_service import SignalDedupeService
from src.services.stock_filter_service import StockFilterService
from src.strategies.double_bottom import DoubleBottomStrategy
from src.strategies.momentum_probe import MomentumProbeStrategy
from src.ui.notifier import ConsoleNotifier
from src.utils.logger import get_logger


def _build_provider(settings: dict):
    if settings["app"].get("demo_mode", True):
        provider = MockMarketProvider()
        provider.source_name = "mock-demo"
        return provider

    fallback = MockMarketProvider()
    fallback.source_name = "mock-fallback"
    try:
        provider = SinaTencentMarketProvider()
        provider.source_name = "sina-tencent"
        return ResilientMarketProvider(primary=provider, fallback=fallback)
    except Exception:
        return fallback


def _build_historical_minute_provider(settings: dict, logger):
    if not settings.get("scan", {}).get("enable_historical_minute_extension", False):
        return None
    if settings["app"].get("demo_mode", True):
        return None
    try:
        return AkshareHistoricalMinuteProvider()
    except Exception as e:
        logger.warning(f"[Bootstrap] 鍘嗗彶鍒嗛挓鏁版嵁鎻愪緵鑰呭垵濮嬪寲澶辫触: {e}")
        return None


def bootstrap_app(project_root: Path) -> AppRunner:
    settings = load_toml(project_root / "config" / "settings.toml")
    strategy_params = load_toml(project_root / "config" / "strategy_params.toml")

    logger = get_logger()
    db_path = project_root / settings["database"]["path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(db_path)
    database.initialize()

    stock_repo = StockRepository(database)
    daily_repo = DailyBarRepository(database)
    minute_repo = MinuteBarRepository(database)
    signal_repo = SignalRepository(database)
    watchlist_repo = WatchlistRepository(database)
    rank_snapshot_repo = RankSnapshotRepository(database)
    candidate_score_repo = CandidateScoreRepository(database)
    candidate_scoring_service = CandidateScoringService()

    provider = _build_provider(settings)
    stock_filter = StockFilterService(settings["market"])
    historical_minute_provider = _build_historical_minute_provider(settings, logger)

    # 初始化同花顺热门榜单提供者
    ths_provider = None
    hot_score_settings = settings.get("hot_score", {})
    enable_ths = hot_score_settings.get("enable_ths", True)
    if enable_ths:
        try:
            ths_provider = THSHotProvider()
            logger.info("[Bootstrap] 同花顺热门榜单提供者已初始化")
        except Exception as e:
            logger.warning(f"[Bootstrap] 同花顺热门榜单提供者初始化失败: {e}")
            enable_ths = False

    hot_score_service = HotScoreService(
        ths_provider=ths_provider,
        enable_ths=enable_ths,
        ths_hourly_hot_bonus=int(hot_score_settings.get("ths_hourly_hot_bonus", 15)),
        ths_value_hot_bonus=int(hot_score_settings.get("ths_value_hot_bonus", 10))
    )
    pool_manager = PoolManager(
        provider=provider,
        stock_filter=stock_filter,
        hot_score_service=hot_score_service,
        stock_repo=stock_repo,
        scan_settings=settings["scan"],
    )
    dedupe_service = SignalDedupeService(signal_repo)
    alert_manager = AlertManager(ConsoleNotifier(logger))

    strategies = [
        DoubleBottomStrategy(strategy_params["double_bottom"]),
    ]
    if strategy_params.get("momentum_probe", {}).get("enabled", False):
        strategies.append(MomentumProbeStrategy(strategy_params["momentum_probe"]))

    strategy_runner = StrategyRunner(
        provider=provider,
        daily_repo=daily_repo,
        minute_repo=minute_repo,
        signal_repo=signal_repo,
        watchlist_repo=watchlist_repo,
        dedupe_service=dedupe_service,
        alert_manager=alert_manager,
        strategies=strategies,
        logger=logger,
    )

    return AppRunner(
        logger=logger,
        provider=provider,
        pool_manager=pool_manager,
        strategy_runner=strategy_runner,
        daily_repo=daily_repo,
        settings=settings,
        historical_minute_provider=historical_minute_provider,
        rank_snapshot_repo=rank_snapshot_repo,
        candidate_score_repo=candidate_score_repo,
        candidate_scoring_service=candidate_scoring_service,
    )
