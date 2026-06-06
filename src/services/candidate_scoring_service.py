from __future__ import annotations

import math
from pathlib import Path

from src.services.candidate_weight_config import DEFAULT_PROFILE_NAME, DEFAULT_PROFILE_WEIGHTS, METRIC_WEIGHT_KEYS

SECTION_SCORE_KEYS = ("heat_score", "market_cap_score", "volume_price_score", "position_score", "risk_penalty")
METRIC_FEATURE_KEYS = tuple(key.removesuffix("_weight") for key in METRIC_WEIGHT_KEYS)


class CandidateScoringService:
    def __init__(
        self,
        weight_profiles: dict[str, dict[str, float]] | None = None,
        active_profile: str = DEFAULT_PROFILE_NAME,
        model_path: str | Path | None = None,
        model_paths: dict[str, str] | None = None,
        model_root: str | Path | None = None,
    ):
        self.weight_profiles = {
            DEFAULT_PROFILE_NAME: dict(DEFAULT_PROFILE_WEIGHTS),
            **(weight_profiles or {}),
        }
        self.model_paths = dict(model_paths or {})
        self.model_root = Path(model_root) if model_root is not None else None
        self.active_profile = active_profile if active_profile in self.weight_profiles else DEFAULT_PROFILE_NAME
        self._model = self._load_model(model_path) if model_path else None

    def get_available_profiles(self) -> list[str]:
        return sorted(self.weight_profiles)

    def set_active_profile(self, profile_name: str) -> bool:
        if profile_name not in self.weight_profiles:
            return False
        self.active_profile = profile_name
        model_path = self.model_paths.get(profile_name, "")
        resolved_path = self._resolve_model_path(model_path)
        self._model = self._load_model(resolved_path) if resolved_path else None
        return True

    def _resolve_model_path(self, model_path_str: str) -> Path | None:
        if not model_path_str:
            return None
        path = Path(model_path_str)
        if path.is_absolute():
            return path
        if self.model_root is not None:
            return self.model_root / path
        return path

    @staticmethod
    def _load_model(model_path: str | Path | None):
        if model_path is None:
            return None
        path = Path(model_path)
        if not path.exists():
            return None
        try:
            import joblib
            return joblib.load(path)
        except ImportError:
            import pickle
            with path.open("rb") as f:
                return pickle.load(f)

    def _predict_with_model(self, section_scores: dict, normalized_factor_features: dict) -> float | None:
        """Use trained nonlinear model to predict return. Returns None if model unavailable."""
        if self._model is None:
            return None
        features = self._build_model_features(section_scores, normalized_factor_features)
        try:
            return float(self._model.predict([features])[0])
        except Exception:
            return None

    @staticmethod
    def _build_model_features(section_scores: dict, normalized_factor_features: dict) -> list[float]:
        features = [float(section_scores.get(k, 0.0)) for k in SECTION_SCORE_KEYS]
        features.extend(float(normalized_factor_features.get(k, 0.0)) for k in METRIC_FEATURE_KEYS)
        return features

    def _predict_many_with_model(self, feature_rows: list[list[float]]) -> list[float] | None:
        if self._model is None:
            return None
        try:
            predictions = self._model.predict(feature_rows)
        except Exception:
            return None
        return [float(value) for value in predictions]

    def score_replay_candidate(self, stock_code: str, stock_name: str, daily_bars: list, rank_context: dict) -> dict | None:
        metrics = self._build_metrics(daily_bars)
        if metrics is None:
            return None
        float_market_cap_est = self._resolve_float_market_cap(rank_context)
        metrics["float_market_cap_est"] = float_market_cap_est
        metrics["reference_price"] = daily_bars[-1].close
        metrics["monitor_rank_yesterday"] = int(rank_context.get("monitor_rank_yesterday") or 0)
        metrics["ths_rank_yesterday"] = int(rank_context.get("ths_rank_yesterday") or 0)
        metrics["ths_value_rank_yesterday"] = int(rank_context.get("ths_value_rank_yesterday") or 0)
        metrics["kpl_rank_yesterday"] = int(rank_context.get("kpl_rank_yesterday") or 0)

        heat_score, heat_flags = self._calc_heat_score(
            rank_context.get("monitor_rank_yesterday"),
            rank_context.get("ths_rank_yesterday"),
            rank_context.get("ths_value_rank_yesterday"),
            rank_context.get("kpl_rank_yesterday"),
        )
        market_cap_score, market_cap_flags = self._calc_market_cap_score(float_market_cap_est)
        volume_price_score, vp_flags = self._calc_volume_price_score(metrics)
        position_score, position_flags = self._calc_position_score(metrics)
        risk_penalty, risk_flags = self._calc_risk_penalty(metrics)

        factor_features = self._build_factor_features(metrics)
        section_scores = {
            "heat_score": heat_score,
            "market_cap_score": market_cap_score,
            "volume_price_score": volume_price_score,
            "position_score": position_score,
            "risk_penalty": risk_penalty,
        }

        # Try nonlinear model first; fall back to linear weighted sum
        model_prediction = self._predict_with_model(section_scores, factor_features)
        if model_prediction is not None:
            # Map predicted return to 0-100 score via percentile-like scaling
            total_score = max(0, min(100, round(self._pct_to_score(model_prediction))))
            weighted_total_raw = model_prediction
        else:
            total_score, weighted_total_raw = self._combine_weighted_total(
                heat_score,
                market_cap_score,
                volume_price_score,
                position_score,
                risk_penalty,
                metrics,
                normalized_factor_features=None,
            )

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
            "metrics": {
                **metrics,
                "factor_features": factor_features,
                "weight_profile": self.active_profile,
                "weighted_total_raw": weighted_total_raw,
                "model_prediction": model_prediction,
            },
        }

    def has_raw_metric_weights(self) -> bool:
        weights = self.weight_profiles.get(self.active_profile, DEFAULT_PROFILE_WEIGHTS)
        return any(abs(float(weights.get(key, 0.0))) > 1e-9 for key in METRIC_WEIGHT_KEYS)

    @staticmethod
    def _pct_to_score(predicted_pct: float) -> float:
        """Map a predicted return (%) to a 0-100 score using a sigmoid-like curve.

        Typical A-stock daily returns fall in [-10%, +10%]:
          -10% → ~20, 0% → 50, +10% → ~80
        """
        return 100.0 / (1.0 + math.exp(-predicted_pct * 0.3))

    def rerank_replay_rows(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return rows

        # If nonlinear model is available, use it for reranking
        if self._model is not None:
            return self._rerank_with_model(rows)


        normalized_feature_map = self._normalize_factor_feature_maps([row.get("metrics", {}).get("factor_features", {}) for row in rows])
        rescored_rows = []
        weighted_totals = []
        for row, normalized_factor_features in zip(rows, normalized_feature_map):
            _total_score, weighted_total_raw = self._combine_weighted_total(
                int(row.get("heat_score", 0)),
                int(row.get("market_cap_score", 0)),
                int(row.get("volume_price_score", 0)),
                int(row.get("position_score", 0)),
                int(row.get("risk_penalty", 0)),
                row.get("metrics", {}),
                normalized_factor_features=normalized_factor_features,
            )
            rescored_rows.append(
                (
                    row,
                    normalized_factor_features,
                    weighted_total_raw,
                )
            )
            weighted_totals.append(weighted_total_raw)

        mapped_scores = self._percentile_normalize_to_score(weighted_totals)
        reranked = []
        for (row, normalized_factor_features, weighted_total_raw), mapped_score in zip(rescored_rows, mapped_scores):
            total_score = max(0, min(100, round(mapped_score)))
            reranked.append(
                {
                    **row,
                    "total_score": total_score,
                    "grade": self._to_grade(total_score),
                    "metrics": {
                        **row.get("metrics", {}),
                        "normalized_factor_features": normalized_factor_features,
                        "weighted_total_raw": weighted_total_raw,
                    },
                }
            )
        return sorted(reranked, key=lambda item: (-item["total_score"], item["stock_code"]))

    def _rerank_with_model(self, rows: list[dict]) -> list[dict]:
        """Rerank rows using the trained nonlinear model with batch normalization."""
        # 如果没有 factor_features，就重新计算
        feature_maps = []
        for row in rows:
            factor_features = row.get("metrics", {}).get("factor_features", {})
            if not factor_features:
                factor_features = self._build_factor_features(row.get("metrics", {}))
            feature_maps.append(factor_features)
        normalized_feature_map = self._normalize_factor_feature_maps(feature_maps)

        feature_rows: list[list[float]] = []
        for row, nf in zip(rows, normalized_feature_map):
            section_scores = {
                "heat_score": int(row.get("heat_score", 0)),
                "market_cap_score": int(row.get("market_cap_score", 0)),
                "volume_price_score": int(row.get("volume_price_score", 0)),
                "position_score": int(row.get("position_score", 0)),
                "risk_penalty": int(row.get("risk_penalty", 0)),
            }
            feature_rows.append(self._build_model_features(section_scores, nf))

        predictions = self._predict_many_with_model(feature_rows)
        if predictions is None:
            predictions = [0.0] * len(rows)

        # Percentile-normalize predictions to [0, 100] for fair scoring
        scores = self._percentile_normalize_to_score(predictions)

        reranked = []
        for row, score, pred, nf in zip(rows, scores, predictions, normalized_feature_map):
            reranked.append(
                {
                    **row,
                    "total_score": max(0, min(100, round(score))),
                    "grade": self._to_grade(max(0, min(100, round(score)))),
                    "metrics": {
                        **row.get("metrics", {}),
                        "normalized_factor_features": nf,
                        "weighted_total_raw": pred,
                        "model_prediction": pred,
                    },
                }
            )
        return sorted(reranked, key=lambda item: (-item["total_score"], item["stock_code"]))

    @staticmethod
    def _percentile_normalize_to_score(values: list[float]) -> list[float]:
        """Percentile-rank values and map to [0, 100]."""
        n = len(values)
        if n <= 1:
            return [50.0] * n
        indexed = sorted(enumerate(values), key=lambda x: x[1])
        scores = [0.0] * n
        start = 0
        while start < n:
            end = start
            while end + 1 < n and indexed[end + 1][1] == indexed[start][1]:
                end += 1
            percentile = ((start + end) / 2) / (n - 1)
            mapped = percentile * 100.0
            for i in range(start, end + 1):
                scores[indexed[i][0]] = mapped
            start = end + 1
        return scores

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
            "ths_value_rank_yesterday": rank_context.get("ths_value_rank_today"),
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
            rank_context.get("ths_value_rank_today"),
            rank_context.get("kpl_rank_today"),
        )
        intraday_bonus, intraday_flags, intraday_risks, intraday_metrics = self._calc_intraday_confirmation(
            stock_code,
            stock_name,
            daily_bars,
            snapshot,
        )
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
                **intraday_metrics,
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
        day_amplitude = ((today.high - today.low) / yesterday.close * 100) if yesterday.close else 0.0
        body_ratio = abs(today.close - today.open) / max(today.high - today.low, 1e-6)
        signed_body_pct = ((today.close - today.open) / today.open * 100) if today.open else 0.0

        prev20 = daily_bars[-21:-1] if len(daily_bars) >= 21 else daily_bars[:-1]
        prev20_high = max((bar.high for bar in prev20), default=today.high)
        breakout_20 = today.close > prev20_high
        breakout_gap_20 = ((today.close - prev20_high) / prev20_high * 100) if prev20_high else 0.0

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

        previous_amount_bars = daily_bars[-7:-2] if len(daily_bars) >= 7 else daily_bars[:-2]
        avg_prev_amount = (
            sum(self._resolve_bar_amount(bar) for bar in previous_amount_bars) / len(previous_amount_bars)
            if previous_amount_bars
            else 0.0
        )
        today_amount = self._resolve_bar_amount(today)
        yesterday_amount = self._resolve_bar_amount(yesterday)
        amount_continuity_2d = (min(today_amount, yesterday_amount) / avg_prev_amount) if avg_prev_amount else 0.0

        return {
            "vol_ratio_5": round(vol_ratio_5, 4),
            "red_green_ratio_5": round(red_green_ratio_5, 4),
            "close_strength": round(close_strength, 4),
            "day_pct": round(day_pct, 4),
            "day_amplitude": round(day_amplitude, 4),
            "body_ratio": round(body_ratio, 4),
            "signed_body_pct": round(signed_body_pct, 4),
            "breakout_20": breakout_20,
            "breakout_gap_20": round(breakout_gap_20, 4),
            "bias_ma5": round(bias_ma5, 4),
            "pos60": round(pos60, 4),
            "upper_shadow_ratio": round(upper_shadow_ratio, 4),
            "pct3": round(pct3, 4),
            "amount_continuity_2d": round(amount_continuity_2d, 4),
        }

    def _calc_heat_score(
        self,
        monitor_rank: int | None,
        ths_rank: int | None,
        ths_value_rank: int | None,
        kpl_rank: int | None,
    ) -> tuple[int, list[str]]:
        score = 0
        flags = []
        if monitor_rank:
            score += round(self._rank_to_feature(int(monitor_rank)) * 12)
            if monitor_rank <= 20:
                flags.append(f"成交额榜#{int(monitor_rank)}")
        if ths_rank:
            score += round(self._rank_to_feature(int(ths_rank)) * 8)
            if ths_rank <= 20:
                flags.append(f"同花顺热榜#{int(ths_rank)}")
        if ths_value_rank:
            score += round(self._rank_to_feature(ths_value_rank) * 4)
            if ths_value_rank <= 20:
                flags.append(f"同花顺价值榜#{ths_value_rank}")
        # 开盘啦热榜功能已禁用，此处不再对其进行加分
        return min(score, 25), flags

    def _calc_volume_price_score(self, metrics: dict) -> tuple[int, list[str]]:
        score = 0
        flags = []
        vol_ratio = metrics["vol_ratio_5"]
        day_amplitude = metrics["day_amplitude"]
        day_pct = metrics["day_pct"]

        if 1.5 <= vol_ratio <= 3.0:
            score += 10
            flags.append("温和放量")
        elif 1.2 <= vol_ratio < 1.5:
            score += 6
        elif vol_ratio > 3.0:
            # 爆量优化逻辑：
            # 如果振幅小且涨幅没有大跌，说明主力资金在窄幅区间内有极强的承接和吸筹动作
            if day_amplitude < 4.0 and day_pct >= -1.0:
                score += 10
                flags.append("爆量窄幅吸筹")
            # 如果振幅大但是涨幅大，说明是强劲的放量突破
            elif day_amplitude >= 4.0 and day_pct >= 5.0:
                score += 8
                flags.append("爆量强劲突破")
            else:
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

        day_pct_val = metrics["day_pct"]
        if 2.0 <= day_pct_val <= 7.0:
            score += 6
        elif 0.0 <= day_pct_val < 2.0:
            score += 3
        elif day_pct_val > 9.0:
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
        
        # 爆量滞涨优化逻辑：必须是高振幅（>=4.0%）且涨幅小于 2.0% 的情况下，才视为滞涨风险进行扣分
        if metrics["vol_ratio_5"] > 3.0 and metrics["day_pct"] < 2.0 and metrics["day_amplitude"] >= 4.0:
            penalty -= 10
            risks.append("爆量滞涨")
            
        if metrics["pct3"] > 18.0:
            penalty -= 8
            risks.append("连续加速")
        if metrics["close_strength"] < 0.4:
            penalty -= 6
            risks.append("收盘偏弱")
        return penalty, risks

    def _calc_intraday_confirmation(
        self,
        stock_code: str,
        stock_name: str,
        daily_bars: list,
        snapshot: dict,
    ) -> tuple[int, list[str], list[str], dict]:
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
        upside_metrics = self._calc_intraday_upside_metrics(stock_code, stock_name, daily_bars, snapshot)
        upside_room_pct = upside_metrics["upside_room_pct"]
        if upside_room_pct >= 6.0:
            score += 8
            flags.append("上行空间充足")
        elif upside_room_pct >= 3.0:
            score += 4
            flags.append("上行空间尚可")
        elif upside_room_pct < 1.5:
            risks.append("上行空间偏低")

        return score, flags, risks, upside_metrics

    def _calc_intraday_upside_metrics(self, stock_code: str, stock_name: str, daily_bars: list, snapshot: dict) -> dict:
        pct_chg = float(snapshot.get("pct_chg", 0.0) or 0.0)
        last_price = float(snapshot.get("last_price", 0.0) or 0.0)
        limit_pct = self._resolve_limit_up_pct(stock_code, stock_name)
        limit_up_room_pct = round(max(limit_pct - pct_chg, 0.0), 4)

        resistance_room_pct = limit_up_room_pct
        if daily_bars and last_price > 0:
            recent20 = daily_bars[-20:] if len(daily_bars) >= 20 else daily_bars
            prev20_high = max((bar.high for bar in recent20), default=0.0)
            if prev20_high > last_price:
                resistance_room_pct = round(max((prev20_high - last_price) / last_price * 100, 0.0), 4)

        upside_room_pct = round(min(limit_up_room_pct, resistance_room_pct), 4)
        reward_risk_ratio = 0.0
        day_low = float(snapshot.get("low", 0.0) or 0.0)
        if last_price > 0 and day_low > 0 and last_price > day_low:
            downside_risk_pct = (last_price - day_low) / last_price * 100
            reward_risk_ratio = round(upside_room_pct / max(downside_risk_pct, 0.5), 4)

        return {
            "limit_up_pct": limit_pct,
            "limit_up_room_pct": limit_up_room_pct,
            "resistance_room_pct": resistance_room_pct,
            "upside_room_pct": upside_room_pct,
            "reward_risk_ratio": reward_risk_ratio,
        }

    @staticmethod
    def _resolve_limit_up_pct(stock_code: str, stock_name: str) -> float:
        name = (stock_name or "").upper()
        if "ST" in name:
            return 5.0
        if stock_code.startswith(("30", "68")):
            return 20.0
        if stock_code.startswith(("8", "4")):
            return 30.0
        return 10.0

    def _combine_weighted_total(
        self,
        heat_score: int,
        market_cap_score: int,
        volume_price_score: int,
        position_score: int,
        risk_penalty: int,
        metrics: dict,
        normalized_factor_features: dict | None,
    ) -> tuple[int, float]:
        weights = self.weight_profiles.get(self.active_profile, DEFAULT_PROFILE_WEIGHTS)
        factor_features = normalized_factor_features or self._build_factor_features(metrics)
        weighted_total_raw = (
            heat_score * float(weights.get("heat_weight", 1.0))
            + market_cap_score * float(weights.get("market_cap_weight", 1.0))
            + volume_price_score * float(weights.get("volume_price_weight", 1.0))
            + position_score * float(weights.get("position_weight", 1.0))
            + risk_penalty * float(weights.get("risk_weight", 1.0))
            + float(factor_features.get("metric_vol_ratio_5", 0.0)) * float(weights.get("metric_vol_ratio_5_weight", 0.0))
            + float(factor_features.get("metric_red_green_ratio_5", 0.0)) * float(weights.get("metric_red_green_ratio_5_weight", 0.0))
            + float(factor_features.get("metric_close_strength", 0.0)) * float(weights.get("metric_close_strength_weight", 0.0))
            + float(factor_features.get("metric_day_pct", 0.0)) * float(weights.get("metric_day_pct_weight", 0.0))
            + float(factor_features.get("metric_day_amplitude", 0.0)) * float(weights.get("metric_day_amplitude_weight", 0.0))
            + float(factor_features.get("metric_body_ratio", 0.0)) * float(weights.get("metric_body_ratio_weight", 0.0))
            + float(factor_features.get("metric_signed_body_pct", 0.0)) * float(weights.get("metric_signed_body_pct_weight", 0.0))
            + float(factor_features.get("metric_breakout_20", 0.0)) * float(weights.get("metric_breakout_20_weight", 0.0))
            + float(factor_features.get("metric_breakout_gap_20", 0.0)) * float(weights.get("metric_breakout_gap_20_weight", 0.0))
            + float(factor_features.get("metric_bias_ma5", 0.0)) * float(weights.get("metric_bias_ma5_weight", 0.0))
            + float(factor_features.get("metric_pos60", 0.0)) * float(weights.get("metric_pos60_weight", 0.0))
            + float(factor_features.get("metric_upper_shadow_ratio", 0.0)) * float(weights.get("metric_upper_shadow_ratio_weight", 0.0))
            + float(factor_features.get("metric_pct3", 0.0)) * float(weights.get("metric_pct3_weight", 0.0))
            + float(factor_features.get("metric_amount_continuity_2d", 0.0)) * float(weights.get("metric_amount_continuity_2d_weight", 0.0))
            + float(factor_features.get("metric_float_market_cap", 0.0)) * float(weights.get("metric_float_market_cap_weight", 0.0))
            + float(factor_features.get("metric_monitor_rank", 0.0)) * float(weights.get("metric_monitor_rank_weight", 0.0))
            + float(factor_features.get("metric_ths_rank", 0.0)) * float(weights.get("metric_ths_rank_weight", 0.0))
            + float(factor_features.get("metric_ths_value_rank", 0.0)) * float(weights.get("metric_ths_value_rank_weight", 0.0))
            + float(factor_features.get("metric_kpl_rank", 0.0)) * float(weights.get("metric_kpl_rank_weight", 0.0))
        )
        return max(0, min(100, round(weighted_total_raw))), round(weighted_total_raw, 4)

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _rank_to_feature(rank_value: int) -> float:
        if rank_value <= 0:
            return 0.0
        clipped_rank = max(1, min(rank_value, 100))
        return 1.0 - clipped_rank / 100.0

    @classmethod
    def _build_factor_features(cls, metrics: dict) -> dict[str, float]:
        float_market_cap_est = float(metrics.get("float_market_cap_est", 0.0) or 0.0)
        cap_yi = float_market_cap_est / 100000000 if float_market_cap_est > 0 else 0.0
        cap_feature = math.log10(max(cap_yi, 1.0)) if cap_yi > 0 else 0.0
        monitor_rank = int(metrics.get("monitor_rank_yesterday", 0) or 0)
        ths_rank = int(metrics.get("ths_rank_yesterday", 0) or 0)
        ths_value_rank = int(metrics.get("ths_value_rank_yesterday", 0) or 0)
        # 开盘啦热榜功能已禁用，恒定为 0 以防影响模型加载的特征维度
        kpl_rank = 0
        return {
            "metric_monitor_rank": round(cls._rank_to_feature(monitor_rank), 4),
            "metric_ths_rank": round(cls._rank_to_feature(ths_rank), 4),
            "metric_vol_ratio_5": round(cls._clip(float(metrics.get("vol_ratio_5", 0.0) or 0.0) - 1.0, -2.0, 4.0), 4),
            "metric_red_green_ratio_5": round(cls._clip(float(metrics.get("red_green_ratio_5", 0.0) or 0.0) - 1.0, -1.0, 4.0), 4),
            "metric_close_strength": round(cls._clip((float(metrics.get("close_strength", 0.0) or 0.0) - 0.5) * 10, -5.0, 5.0), 4),
            "metric_day_pct": round(cls._clip(float(metrics.get("day_pct", 0.0) or 0.0) / 2.0, -5.0, 5.0), 4),
            "metric_day_amplitude": round(cls._clip(float(metrics.get("day_amplitude", 0.0) or 0.0) / 2.0, 0.0, 5.0), 4),
            "metric_body_ratio": round(cls._clip((float(metrics.get("body_ratio", 0.0) or 0.0) - 0.5) * 10.0, -5.0, 5.0), 4),
            "metric_signed_body_pct": round(cls._clip(float(metrics.get("signed_body_pct", 0.0) or 0.0) / 2.0, -5.0, 5.0), 4),
            "metric_breakout_20": 1.0 if metrics.get("breakout_20") else 0.0,
            "metric_breakout_gap_20": round(cls._clip(float(metrics.get("breakout_gap_20", 0.0) or 0.0) / 2.0, -5.0, 5.0), 4),
            "metric_bias_ma5": round(cls._clip(float(metrics.get("bias_ma5", 0.0) or 0.0) / 2.0, -5.0, 5.0), 4),
            "metric_pos60": round(cls._clip((float(metrics.get("pos60", 0.0) or 0.0) - 0.5) * 10.0, -5.0, 5.0), 4),
            "metric_upper_shadow_ratio": round(cls._clip(float(metrics.get("upper_shadow_ratio", 0.0) or 0.0) / 2.0, 0.0, 5.0), 4),
            "metric_pct3": round(cls._clip(float(metrics.get("pct3", 0.0) or 0.0) / 4.0, -5.0, 5.0), 4),
            "metric_amount_continuity_2d": round(cls._clip(float(metrics.get("amount_continuity_2d", 0.0) or 0.0) - 1.0, -2.0, 4.0), 4),
            "metric_float_market_cap": round(cls._clip(cap_feature, 0.0, 4.5), 4),
            "metric_ths_value_rank": round(cls._rank_to_feature(ths_value_rank), 4),
            "metric_kpl_rank": round(cls._rank_to_feature(kpl_rank), 4),
        }

    @staticmethod
    def _normalize_factor_feature_maps(feature_maps: list[dict]) -> list[dict]:
        if not feature_maps:
            return []
        keys = sorted({key for feature_map in feature_maps for key in feature_map.keys()})
        normalized_rows = [dict() for _ in feature_maps]
        for key in keys:
            values = [float(feature_map.get(key, 0.0) or 0.0) for feature_map in feature_maps]
            normalized_values = CandidateScoringService._percentile_normalize(values)
            for index, normalized_value in enumerate(normalized_values):
                normalized_rows[index][key] = normalized_value
        return normalized_rows

    @staticmethod
    def _percentile_normalize(values: list[float]) -> list[float]:
        if not values:
            return []
        if len(values) == 1:
            return [0.0]
        indexed = sorted(enumerate(values), key=lambda item: item[1])
        ranks = [0.0] * len(values)
        start = 0
        while start < len(indexed):
            end = start
            while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[start][1]:
                end += 1
            percentile = ((start + end) / 2) / (len(indexed) - 1)
            normalized_value = round(percentile * 2.0 - 1.0, 4)
            for idx in range(start, end + 1):
                ranks[indexed[idx][0]] = normalized_value
            start = end + 1
        return ranks

    @staticmethod
    def _resolve_bar_amount(bar) -> float:
        amount = float(getattr(bar, "amount", 0.0) or 0.0)
        if amount > 0:
            return amount
        close = float(getattr(bar, "close", 0.0) or 0.0)
        volume = float(getattr(bar, "volume", 0.0) or 0.0)
        return close * volume if close > 0 and volume > 0 else 0.0

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
