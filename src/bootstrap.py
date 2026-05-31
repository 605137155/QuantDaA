from __future__ import annotations

from pathlib import Path
import os

from src.config_loader import load_toml
from src.core.alert_manager import AlertManager
from src.core.app_runner import AppRunner
from src.core.pool_manager import PoolManager
from src.core.strategy_runner import StrategyRunner
from src.data_providers.mock_provider import MockMarketProvider
from src.data_providers.resilient_provider import ResilientMarketProvider
from src.data_providers.sina_tencent_provider import SinaTencentMarketProvider
from src.repositories.database import Database
from src.repositories.daily_repo import DailyBarRepository
from src.repositories.minute_repo import MinuteBarRepository
from src.repositories.signal_repo import SignalRepository
from src.repositories.stock_repo import StockRepository
from src.repositories.watchlist_repo import WatchlistRepository
from src.services.hot_score_service import HotScoreService
from src.services.signal_dedupe_service import SignalDedupeService
from src.services.stock_filter_service import StockFilterService
from src.strategies.double_bottom import DoubleBottomStrategy
from src.strategies.momentum_probe import MomentumProbeStrategy
from src.ui.notifier import ConsoleNotifier
from src.utils.logger import get_logger


def _build_provider(settings: dict):
    if settings["app"].get("ignore_env_proxy", False):
        for key in (
            "http_proxy",
            "https_proxy",
            "ftp_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "FTP_PROXY",
            "ALL_PROXY",
            "all_proxy",
        ):
            os.environ.pop(key, None)

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

    provider = _build_provider(settings)
    stock_filter = StockFilterService(settings["market"])
    hot_score_service = HotScoreService()
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
    )
