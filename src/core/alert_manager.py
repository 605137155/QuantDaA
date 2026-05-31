from __future__ import annotations

from src.models.signal import Signal


class AlertManager:
    def __init__(self, notifier):
        self.notifier = notifier

    def send(self, signal: Signal) -> None:
        self.notifier.notify(signal)
