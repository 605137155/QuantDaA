from __future__ import annotations

import unittest

from src.models.signal import Signal
from src.services.signal_dedupe_service import SignalDedupeService


class FakeSignalRepo:
    def __init__(self, last_trigger=None):
        self.last_trigger = last_trigger

    def get_last_trigger(self, dedupe_key: str):
        return self.last_trigger


class SignalDedupeServiceTests(unittest.TestCase):
    def test_allows_first_alert(self):
        service = SignalDedupeService(FakeSignalRepo())
        signal = Signal(True, "double_bottom", "000001", "平安银行", "watch", 70, "t", "m", timestamp="2026-05-30 10:00:00")
        self.assertTrue(service.should_alert(signal))

    def test_blocks_alert_inside_cooldown(self):
        service = SignalDedupeService(FakeSignalRepo(last_trigger="2026-05-30 09:50:00"))
        signal = Signal(True, "double_bottom", "000001", "平安银行", "watch", 70, "t", "m", cooldown_minutes=20, timestamp="2026-05-30 10:00:00")
        self.assertFalse(service.should_alert(signal))


if __name__ == "__main__":
    unittest.main()
