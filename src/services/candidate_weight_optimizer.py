from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
import random

from src.services.candidate_scoring_service import CandidateScoringService
from src.services.candidate_weight_config import DEFAULT_PROFILE_NAME, DEFAULT_PROFILE_WEIGHTS, METRIC_WEIGHT_KEYS, load_candidate_weight_config, save_candidate_weight_config


SECTION_WEIGHT_FIELDS = (
    "heat_weight",
    "market_cap_weight",
    "volume_price_weight",
    "position_weight",
    "risk_weight",
)


@dataclass
class ReplayWeightOptimizationResult:
    profile_name: str
    sample_count: int
    base_spearman: float
    optimized_spearman: float
    base_top10_avg_pct: float
    optimized_top10_avg_pct: float
    weights: dict[str, float]
    model_path: str = ""


class CandidateWeightOptimizer:
    def optimize_replay_weights(
        self,
        csv_path: str | Path,
        weight_config_path: str | Path,
        profile_name: str = "optimized_latest",
        activate_profile: bool = True,
    ) -> ReplayWeightOptimizationResult:
        samples = self._load_samples(csv_path)
        if len(samples) < 5:
            raise ValueError("可用于优化的样本不足，至少需要 5 条带 label_live_pct 的记录")

        config_path = Path(weight_config_path)
        labels = [s["label_pct"] for s in samples]
        raw_labels = [s["label_raw_pct"] for s in samples]

        # --- Baseline (default linear weights) ---
        base_weights = dict(DEFAULT_PROFILE_WEIGHTS)
        base_scores = [self._weighted_total(s, base_weights) for s in samples]
        base_spearman = self._spearman(base_scores, labels)
        base_top10_avg = self._topn_avg_from_scores(base_scores, raw_labels, 10)

        # --- Search for optimal weights (Top-N focused) ---
        best_weights = self._search_optimal_weights(samples, labels)

        model_rel_path = f"config/candidate_model_{profile_name}.pkl"
        project_root = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
        model_abs_path = project_root / model_rel_path
        trained_model = self._train_model(samples, labels)
        self._save_model(trained_model, model_abs_path)
        model_scores = self._predict_model_scores(trained_model, samples)
        optimized_spearman = self._spearman(model_scores, labels)
        optimized_top10_avg = self._topn_avg_from_scores(model_scores, raw_labels, 10)

        # --- Save weights ---
        config = load_candidate_weight_config(config_path)
        config["profiles"][DEFAULT_PROFILE_NAME] = config["profiles"].get(DEFAULT_PROFILE_NAME, dict(base_weights))
        config["profiles"][profile_name] = best_weights
        config.setdefault("model_paths", {})
        config["model_paths"][profile_name] = model_rel_path
        if activate_profile:
            config["active_profile"] = profile_name
        save_candidate_weight_config(config_path, config)

        return ReplayWeightOptimizationResult(
            profile_name=profile_name,
            sample_count=len(samples),
            base_spearman=round(base_spearman, 4),
            optimized_spearman=round(optimized_spearman, 4),
            base_top10_avg_pct=round(base_top10_avg, 4),
            optimized_top10_avg_pct=round(optimized_top10_avg, 4),
            weights=best_weights,
            model_path=model_rel_path,
        )

    # ------------------------------------------------------------------
    # Weight search
    # ------------------------------------------------------------------

    def _search_optimal_weights(self, samples: list[dict], labels: list[float]) -> dict[str, float]:
        """Search for weights that maximize Top-N average return.

        Uses a combined objective: top10_avg + spearman_bonus.
        The Spearman component ensures the rest of the ranking is reasonable.
        """
        rng = random.Random(20260602)
        best_weights = dict(DEFAULT_PROFILE_WEIGHTS)
        best_objective = -999.0

        # Expanded search values (includes higher weights)
        section_values = (0.0, 0.4, 0.7, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0)
        metric_values = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0)

        # Phase 1: Grid search on section weights (metric weights = 0)
        for heat_w in section_values:
            for mktcap_w in section_values:
                for volprice_w in section_values:
                    for pos_w in section_values:
                        for risk_w in section_values:
                            weights = dict(DEFAULT_PROFILE_WEIGHTS)
                            weights.update({
                                "heat_weight": heat_w,
                                "market_cap_weight": mktcap_w,
                                "volume_price_weight": volprice_w,
                                "position_weight": pos_w,
                                "risk_weight": risk_w,
                            })
                            scores = [self._weighted_total(s, weights) for s in samples]
                            obj = self._topn_objective(scores, labels, topn=10)
                            if obj > best_objective:
                                best_objective = obj
                                best_weights = dict(weights)

        # Phase 2: Random search with metric weights
        for _ in range(8000):
            weights = dict(DEFAULT_PROFILE_WEIGHTS)
            weights.update({
                "heat_weight": rng.choice(section_values),
                "market_cap_weight": rng.choice(section_values),
                "volume_price_weight": rng.choice(section_values),
                "position_weight": rng.choice(section_values),
                "risk_weight": rng.choice(section_values),
            })
            for metric_name in METRIC_WEIGHT_KEYS:
                weights[metric_name] = rng.choice(metric_values)

            scores = [self._weighted_total(s, weights) for s in samples]
            obj = self._topn_objective(scores, labels, topn=10)
            if obj > best_objective:
                best_objective = obj
                best_weights = dict(weights)

        return best_weights

    def _topn_objective(self, scores: list[float], labels: list[float], topn: int = 10) -> float:
        """Combined objective: Top-N average return + Spearman bonus.

        Prioritizes getting the top picks right, while penalizing
        rankings that are completely random (Spearman < 0.15).
        """
        n = len(scores)
        top_indices = sorted(range(n), key=lambda i: -scores[i])[:topn]
        top10_avg = sum(labels[i] for i in top_indices) / max(len(top_indices), 1)

        spearman = self._spearman(scores, labels)
        # Spearman bonus: reward correlation above 0.15, penalize below
        spearman_bonus = (spearman - 0.15) * 20

        return top10_avg + spearman_bonus

    # ------------------------------------------------------------------
    # Dataset construction
    # ------------------------------------------------------------------

    def _load_samples(self, csv_path: str | Path) -> list[dict]:
        path = Path(csv_path)
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file))

        samples = []
        for row in rows:
            label_value = row.get("next_day_pct", "")
            if label_value in ("", None):
                label_value = row.get("label_live_pct", "")
            if label_value in ("", None):
                continue
            try:
                stock_code = str(row.get("stock_code", "") or "")
                stock_name = str(row.get("stock_name", "") or "")
                raw_label_pct = float(label_value)
                sample = {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "label_raw_pct": raw_label_pct,
                    "label_pct": self._normalize_label_pct(raw_label_pct, stock_code, stock_name),
                    "heat_score": float(row.get("heat_score", 0) or 0),
                    "market_cap_score": float(row.get("market_cap_score", 0) or 0),
                    "volume_price_score": float(row.get("volume_price_score", 0) or 0),
                    "position_score": float(row.get("position_score", 0) or 0),
                    "risk_penalty": float(row.get("risk_penalty", 0) or 0),
                    "metrics": {
                        "monitor_rank_yesterday": int(float(row.get("metric_monitor_rank_yesterday", 0) or 0)),
                        "ths_rank_yesterday": int(float(row.get("metric_ths_rank_yesterday", 0) or 0)),
                        "vol_ratio_5": float(row.get("metric_vol_ratio_5", 0) or 0),
                        "red_green_ratio_5": float(row.get("metric_red_green_ratio_5", 0) or 0),
                        "close_strength": float(row.get("metric_close_strength", 0) or 0),
                        "day_pct": float(row.get("metric_day_pct", 0) or 0),
                        "day_amplitude": float(row.get("metric_day_amplitude", 0) or 0),
                        "body_ratio": float(row.get("metric_body_ratio", 0) or 0),
                        "signed_body_pct": float(row.get("metric_signed_body_pct", 0) or 0),
                        "breakout_20": str(row.get("metric_breakout_20", "")).strip().lower() in {"1", "true", "yes"},
                        "breakout_gap_20": float(row.get("metric_breakout_gap_20", 0) or 0),
                        "bias_ma5": float(row.get("metric_bias_ma5", 0) or 0),
                        "pos60": float(row.get("metric_pos60", 0) or 0),
                        "upper_shadow_ratio": float(row.get("metric_upper_shadow_ratio", 0) or 0),
                        "pct3": float(row.get("metric_pct3", 0) or 0),
                        "amount_continuity_2d": float(row.get("metric_amount_continuity_2d", 0) or 0),
                        "float_market_cap_est": float(row.get("metric_float_market_cap_est", 0) or 0),
                        "ths_value_rank_yesterday": int(float(row.get("metric_ths_value_rank_yesterday", 0) or 0)),
                        "kpl_rank_yesterday": int(float(row.get("metric_kpl_rank_yesterday", 0) or 0)),
                    },
                }
            except ValueError:
                continue
            samples.append(sample)
        normalized_feature_map = CandidateScoringService._normalize_factor_feature_maps(
            [CandidateScoringService._build_factor_features(sample["metrics"]) for sample in samples]
        )
        for sample, normalized_factor_features in zip(samples, normalized_feature_map):
            sample["normalized_factor_features"] = normalized_factor_features
        return samples

    @staticmethod
    def _normalize_label_pct(label_pct: float, stock_code: str, stock_name: str) -> float:
        limit_pct = CandidateScoringService._resolve_limit_up_pct(stock_code, stock_name)
        if limit_pct <= 0:
            return round(label_pct, 4)
        return round(label_pct * 10.0 / limit_pct, 4)

    def _build_feature_matrix(self, samples: list[dict]) -> list[list[float]]:
        rows = []
        for sample in samples:
            row = [float(sample.get(key, 0.0)) for key in SECTION_WEIGHT_FIELDS]
            normalized = sample.get("normalized_factor_features") or {}
            row.extend(float(normalized.get(metric_name.removesuffix("_weight"), 0.0)) for metric_name in METRIC_WEIGHT_KEYS)
            rows.append(row)
        return rows

    def _train_model(self, samples: list[dict], labels: list[float]):
        from sklearn.ensemble import RandomForestRegressor

        features = self._build_feature_matrix(samples)
        model = RandomForestRegressor(
            n_estimators=300,
            random_state=20260604,
            min_samples_leaf=1,
            n_jobs=-1,
        )
        model.fit(features, labels)
        return model

    def _predict_model_scores(self, model, samples: list[dict]) -> list[float]:
        features = self._build_feature_matrix(samples)
        return [float(value) for value in model.predict(features)]

    @staticmethod
    def _save_model(model, model_path: Path) -> None:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import joblib

            joblib.dump(model, model_path)
        except ImportError:
            import pickle

            with model_path.open("wb") as file_obj:
                pickle.dump(model, file_obj)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _weighted_total(self, sample: dict, weights: dict[str, float]) -> float:
        base_total = (
            sample["heat_score"] * weights["heat_weight"]
            + sample["market_cap_score"] * weights["market_cap_weight"]
            + sample["volume_price_score"] * weights["volume_price_weight"]
            + sample["position_score"] * weights["position_weight"]
            + sample["risk_penalty"] * weights["risk_weight"]
        )
        factor_features = sample.get("normalized_factor_features") or CandidateScoringService._build_factor_features(sample["metrics"])
        factor_total = sum(
            factor_features[metric_name.removesuffix("_weight")] * float(weights.get(metric_name, 0.0))
            for metric_name in METRIC_WEIGHT_KEYS
        )
        return base_total + factor_total

    @staticmethod
    def _topn_avg_from_scores(scores: list[float], labels: list[float], topn: int) -> float:
        n = len(scores)
        top_indices = sorted(range(n), key=lambda i: -scores[i])[:topn]
        if not top_indices:
            return 0.0
        return sum(labels[i] for i in top_indices) / len(top_indices)

    # ------------------------------------------------------------------
    # Stats utilities
    # ------------------------------------------------------------------

    def _spearman(self, xs: list[float], ys: list[float]) -> float:
        if len(xs) != len(ys) or len(xs) < 2:
            return 0.0
        x_ranks = self._rank_data(xs)
        y_ranks = self._rank_data(ys)
        return self._pearson(x_ranks, y_ranks)

    @staticmethod
    def _rank_data(values: list[float]) -> list[float]:
        indexed = sorted(enumerate(values), key=lambda item: item[1])
        ranks = [0.0] * len(values)
        start = 0
        while start < len(indexed):
            end = start
            while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[start][1]:
                end += 1
            average_rank = (start + end) / 2 + 1
            for idx in range(start, end + 1):
                ranks[indexed[idx][0]] = average_rank
            start = end + 1
        return ranks

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float:
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        var_x = sum((x - mean_x) ** 2 for x in xs)
        var_y = sum((y - mean_y) ** 2 for y in ys)
        if var_x <= 0 or var_y <= 0:
            return 0.0
        return cov / math.sqrt(var_x * var_y)
