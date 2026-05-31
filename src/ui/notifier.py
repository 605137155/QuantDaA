from __future__ import annotations


class ConsoleNotifier:
    def __init__(self, logger):
        self.logger = logger

    def notify(self, signal) -> None:
        self.logger.warning("[%s] %s - %s", signal.signal_level, signal.title, signal.message)
