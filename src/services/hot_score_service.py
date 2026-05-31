from __future__ import annotations


class HotScoreService:
    def pick_top(self, monitor_pool: list, top_n: int) -> list:
        ranked = sorted(monitor_pool, key=self.score, reverse=True)
        return ranked[:top_n]

    def score(self, snapshot) -> int:
        score = 0
        if snapshot.amount >= 5_000_000_000:
            score += 40
        elif snapshot.amount >= 3_000_000_000:
            score += 30
        elif snapshot.amount >= 1_000_000_000:
            score += 20
        else:
            score += 10

        if snapshot.pct_chg >= 3:
            score += 15
        elif snapshot.pct_chg >= 1:
            score += 8

        if snapshot.turnover_rate >= 3:
            score += 10
        elif snapshot.turnover_rate >= 1:
            score += 5

        amplitude = 0.0
        if snapshot.last_price:
            amplitude = max(snapshot.high - snapshot.low, 0.0) / snapshot.last_price

        if amplitude >= 0.05:
            score += 10
        elif amplitude >= 0.03:
            score += 5

        return score
