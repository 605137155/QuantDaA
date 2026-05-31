from __future__ import annotations

from datetime import datetime, timedelta


class SignalDedupeService:
    def __init__(self, signal_repo):
        self.signal_repo = signal_repo

    def should_alert(self, signal) -> bool:
        last_trigger = self.signal_repo.get_last_trigger(signal.dedupe_key)
        if last_trigger is None:
            return True

        last_dt = datetime.strptime(last_trigger, "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.strptime(signal.timestamp, "%Y-%m-%d %H:%M:%S")
        return now_dt - last_dt >= timedelta(minutes=signal.cooldown_minutes)
