from __future__ import annotations


class CandidateScoringService:
    def score_replay_candidate(self, stock_code: str, stock_name: str, daily_bars: list, rank_context: dict) -> dict | None:
        metrics = self._build_metrics(daily_bars)
        if metrics is None:
            return None
        float_market_cap_est = self._resolve_float_market_cap(rank_context)
        metrics["float_market_cap_est"] = float_market_cap_est
        metrics["reference_price"] = daily_bars[-1].close

        heat_score, heat_flags = self._calc_heat_score(
            rank_context.get("monitor_rank_yesterday"),
            rank_context.get("ths_rank_yesterday"),
            rank_context.get("kpl_rank_yesterday"),
        )
        market_cap_score, market_cap_flags = self._calc_market_cap_score(float_market_cap_est)
        volume_price_score, vp_flags = self._calc_volume_price_score(metrics)
        position_score, position_flags = self._calc_position_score(metrics)
        risk_penalty, risk_flags = self._calc_risk_penalty(metrics)
        total_score = max(0, min(100, heat_score + market_cap_score + volume_price_score + position_score + risk_penalty))

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "total_score": total_score,
            "grade": self._to_grade(total_score),
            "heat_score": heat_score,
            "market_cap_score": market_cap_score,
            "volume_price_score": volume_price_score,
            "position_score": position_score,
            "risk_penalty": risk_penalty,
            "flags": heat_flags + market_cap_flags + vp_flags + position_flags,
            "risks": risk_flags,
            "metrics": metrics,
        }

    def score_intraday_candidate(
        self,
        stock_code: str,
        stock_name: str,
        daily_bars: list,
        rank_context: dict,
        snapshot: dict | None = None,
    ) -> dict | None:
        snapshot = snapshot or {}
        replay_rank_context = {
            "monitor_rank_yesterday": rank_context.get("monitor_rank_yesterday"),
            "ths_rank_yesterday": rank_context.get("ths_rank_today"),
            "kpl_rank_yesterday": rank_context.get("kpl_rank_today"),
            "amount": snapshot.get("amount", 0.0),
            "turnover_rate": snapshot.get("turnover_rate", 0.0),
            "float_market_cap_est": rank_context.get("float_market_cap_est", 0.0),
        }
        replay_score = self.score_replay_candidate(stock_code, stock_name, daily_bars, replay_rank_context)
        if replay_score is None:
            return None

        today_heat, today_heat_flags = self._calc_heat_score(
            rank_context.get("monitor_rank_today"),
            rank_context.get("ths_rank_today"),
            rank_context.get("kpl_rank_today"),
        )
        intraday_bonus, intraday_flags, intraday_risks = self._calc_intraday_confirmation(snapshot)
        total_score = max(0, min(100, int(replay_score["total_score"] * 0.55) + today_heat + intraday_bonus))
        risk_penalty = replay_score["risk_penalty"] + sum(-4 for _ in intraday_risks)

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "total_score": total_score,
            "grade": self._to_grade(total_score),
            "heat_score": replay_score["heat_score"] + today_heat,
            "market_cap_score": replay_score["market_cap_score"],
            "volume_price_score": replay_score["volume_price_score"] + intraday_bonus,
            "position_score": replay_score["position_score"],
            "risk_penalty": risk_penalty,
            "flags": replay_score["flags"] + today_heat_flags + intraday_flags,
            "risks": replay_score["risks"] + intraday_risks,
            "metrics": {
                **replay_score["metrics"],
                "intraday_pct_chg": float(snapshot.get("pct_chg", 0.0) or 0.0),
                "reference_price": float(snapshot.get("last_price", 0.0) or replay_score["metrics"].get("reference_price", 0.0)),
            },
        }

    def build_intraday_ranking(self, context: dict) -> list[dict]:
        candidates: dict[str, dict] = {}
        for field_name, rank_key in (
            ("today_monitor_rows", "monitor_rank_today"),
            ("yesterday_monitor_rows", "monitor_rank_yesterday"),
            ("ths_rows", "ths_rank_today"),
            ("kpl_rows", "kpl_rank_today"),
        ):
            for row in context.get(field_name, []):
                code = row["code"]
                item = candidates.setdefault(code, {"stock_code": code, "stock_name": row["name"]})
                item[rank_key] = row.get("rank_no") or row.get("rank") or row.get("rank_no")

        results = []
        bars_map = context.get("daily_bars_map", {})
        snapshot_map = context.get("snapshot_map", {})
        for code, rank_context in candidates.items():
            daily_bars = bars_map.get(code, [])
            score = self.score_intraday_candidate(
                code,
                rank_context["stock_name"],
                daily_bars,
                rank_context,
                snapshot_map.get(code),
            )
            if score is not None:
                results.append(score)

        return sorted(results, key=lambda item: (-item["total_score"], item["stock_code"]))

    def _build_metrics(self, daily_bars: list) -> dict | None:
        if len(daily_bars) < 5:
            return None

        today = daily_bars[-1]
        yesterday = daily_bars[-2]
        recent5 = daily_bars[-5:]
        previous5 = daily_bars[-6:-1] or recent5[:-1]
        avg5_volume = sum(bar.volume for bar in previous5) / max(len(previous5), 1)
        vol_ratio_5 = (today.volume / avg5_volume) if avg5_volume else 0.0

        up_vol = sum(bar.volume for bar in recent5 if bar.close >= bar.open)
        down_vol = sum(bar.volume for bar in recent5 if bar.close < bar.open)
        red_green_ratio_5 = (up_vol / down_vol) if down_vol else 999.0

        close_strength = (today.close - today.low) / max(today.high - today.low, 1e-6)
        day_pct = ((today.close - yesterday.close) / yesterday.close * 100) if yesterday.close else 0.0

        prev20 = daily_bars[-21:-1] if len(daily_bars) >= 21 else daily_bars[:-1]
        prev20_high = max((bar.high for bar in prev20), default=today.high)
        breakout_20 = today.close > prev20_high

        ma5 = sum(bar.close for bar in recent5) / len(recent5)
        bias_ma5 = ((today.close - ma5) / ma5 * 100) if ma5 else 0.0

        recent60 = daily_bars[-60:]
        high60 = max(bar.high for bar in recent60)
        low60 = min(bar.low for bar in recent60)
        pos60 = (today.close - low60) / max(high60 - low60, 1e-6)

        upper_shadow_ratio = ((today.high - max(today.open, today.close)) / today.close * 100) if today.close else 0.0
        pct3 = 0.0
        if len(daily_bars) >= 4 and daily_bars[-4].close:
            pct3 = (today.close - daily_bars[-4].close) / daily_bars[-4].close * 100

        return {
            "vol_ratio_5": round(vol_ratio_5, 4),
            "red_green_ratio_5": round(red_green_ratio_5, 4),
            "close_strength": round(close_strength, 4),
            "day_pct": round(day_pct, 4),
            "breakout_20": breakout_20,
            "bias_ma5": round(bias_ma5, 4),
            "pos60": round(pos60, 4),
            "upper_shadow_ratio": round(upper_shadow_ratio, 4),
            "pct3": round(pct3, 4),
        }

    def _calc_heat_score(self, monitor_rank: int | None, ths_rank: int | None, kpl_rank: int | None) -> tuple[int, list[str]]:
        score = 0
        flags = []
        if monitor_rank:
            if monitor_rank <= 10:
                score += 12
                flags.append("成交额榜前10")
            elif monitor_rank <= 30:
                score += 8
                flags.append("成交额榜前30")
            elif monitor_rank <= 60:
                score += 4
        if ths_rank:
            if ths_rank <= 20:
                score += 8
                flags.append("同花顺热榜前20")
            elif ths_rank <= 50:
                score += 5
        if kpl_rank:
            if kpl_rank <= 20:
                score += 5
                flags.append("开盘啦热榜前20")
            elif kpl_rank <= 50:
                score += 3
        return min(score, 25), flags

    def _calc_volume_price_score(self, metrics: dict) -> tuple[int, list[str]]:
        score = 0
        flags = []
        vol_ratio = metrics["vol_ratio_5"]
        if 1.5 <= vol_ratio <= 3.0:
            score += 10
            flags.append("温和放量")
        elif 1.2 <= vol_ratio < 1.5:
            score += 6
        elif vol_ratio > 3.0:
            score += 3

        red_green_ratio = metrics["red_green_ratio_5"]
        if red_green_ratio >= 1.3:
            score += 8
            flags.append("红肥绿瘦")
        elif red_green_ratio >= 1.0:
            score += 4

        close_strength = metrics["close_strength"]
        if close_strength >= 0.7:
            score += 6
            flags.append("收盘接近高点")
        elif close_strength >= 0.55:
            score += 3

        day_pct = metrics["day_pct"]
        if 2.0 <= day_pct <= 7.0:
            score += 6
        elif 0.0 <= day_pct < 2.0:
            score += 3
        elif day_pct > 9.0:
            score += 1

        return score, flags

    def _calc_market_cap_score(self, float_market_cap_est: float) -> tuple[int, list[str]]:
        if float_market_cap_est <= 0:
            return 0, []

        cap_yi = float_market_cap_est / 100000000
        if 80 <= cap_yi <= 300:
            return 10, ["流通市值适中"]
        if 50 <= cap_yi < 80 or 300 < cap_yi <= 500:
            return 6, ["流通市值可接受"]
        if 30 <= cap_yi < 50 or 500 < cap_yi <= 800:
            return 2, []
        if cap_yi < 20 or cap_yi > 1200:
            return -6, ["流通市值偏极端"]
        return -2, []

    def _calc_position_score(self, metrics: dict) -> tuple[int, list[str]]:
        score = 0
        flags = []
        if metrics["breakout_20"]:
            score += 10
            flags.append("突破近20日高点")

        bias_ma5 = metrics["bias_ma5"]
        if 0.0 <= bias_ma5 <= 6.0:
            score += 8
        elif 6.0 < bias_ma5 <= 10.0:
            score += 4

        pos60 = metrics["pos60"]
        if 0.2 <= pos60 <= 0.65:
            score += 7
        elif 0.65 < pos60 <= 0.8:
            score += 3
        return score, flags

    def _calc_risk_penalty(self, metrics: dict) -> tuple[int, list[str]]:
        penalty = 0
        risks = []
        if metrics["upper_shadow_ratio"] > 4.0:
            penalty -= 8
            risks.append("长上影")
        if metrics["vol_ratio_5"] > 3.0 and metrics["day_pct"] < 2.0:
            penalty -= 10
            risks.append("爆量滞涨")
        if metrics["pct3"] > 18.0:
            penalty -= 8
            risks.append("连续加速")
        if metrics["close_strength"] < 0.4:
            penalty -= 6
            risks.append("收盘偏弱")
        return penalty, risks

    def _calc_intraday_confirmation(self, snapshot: dict) -> tuple[int, list[str], list[str]]:
        pct_chg = float(snapshot.get("pct_chg", 0.0) or 0.0)
        amount = float(snapshot.get("amount", 0.0) or 0.0)
        score = 0
        flags = []
        risks = []

        if 0.0 <= pct_chg <= 6.0:
            score += 10
            flags.append("盘中涨幅适中")
        elif pct_chg > 8.0:
            risks.append("盘中过热")
        elif pct_chg < -2.0:
            risks.append("盘中走弱")

        if amount >= 2_000_000_000:
            score += 8
            flags.append("盘中成交额强")
        elif amount >= 1_000_000_000:
            score += 4

        return score, flags, risks

    @staticmethod
    def _resolve_float_market_cap(rank_context: dict) -> float:
        explicit = float(rank_context.get("float_market_cap_est", 0.0) or 0.0)
        if explicit > 0:
            return explicit

        amount = float(rank_context.get("amount", 0.0) or 0.0)
        turnover_rate = float(rank_context.get("turnover_rate", 0.0) or 0.0)
        if amount > 0 and turnover_rate > 0:
            return amount * 100 / turnover_rate
        return 0.0

    @staticmethod
    def _to_grade(total_score: int) -> str:
        if total_score >= 85:
            return "A"
        if total_score >= 75:
            return "B"
        if total_score >= 60:
            return "C"
        return "D"
