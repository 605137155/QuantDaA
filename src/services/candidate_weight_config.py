from __future__ import annotations

from pathlib import Path

from src.config_loader import load_toml


DEFAULT_PROFILE_NAME = "default"
METRIC_WEIGHT_KEYS = (
    "metric_monitor_rank_weight",
    "metric_ths_rank_weight",
    "metric_vol_ratio_5_weight",
    "metric_red_green_ratio_5_weight",
    "metric_close_strength_weight",
    "metric_day_pct_weight",
    "metric_ths_value_rank_weight",
    "metric_day_amplitude_weight",
    "metric_body_ratio_weight",
    "metric_signed_body_pct_weight",
    "metric_breakout_20_weight",
    "metric_breakout_gap_20_weight",
    "metric_bias_ma5_weight",
    "metric_pos60_weight",
    "metric_upper_shadow_ratio_weight",
    "metric_pct3_weight",
    "metric_amount_continuity_2d_weight",
    "metric_float_market_cap_weight",
    "metric_kpl_rank_weight",
)
DEFAULT_PROFILE_WEIGHTS = {
    "heat_weight": 1.0,
    "market_cap_weight": 1.0,
    "volume_price_weight": 1.0,
    "position_weight": 1.0,
    "risk_weight": 1.0,
    **{key: 0.0 for key in METRIC_WEIGHT_KEYS},
}


def default_weight_config() -> dict:
    return {
        "active_profile": DEFAULT_PROFILE_NAME,
        "model_paths": {},
        "profiles": {DEFAULT_PROFILE_NAME: dict(DEFAULT_PROFILE_WEIGHTS)},
    }


def load_candidate_weight_config(path: Path) -> dict:
    if not path.exists():
        return default_weight_config()

    raw = load_toml(path)
    active_profile = str(raw.get("meta", {}).get("active_profile", DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME)
    profiles: dict[str, dict[str, float]] = {}
    model_paths: dict[str, str] = {}

    for section_name, values in raw.items():
        if not section_name.startswith("profile_") or not isinstance(values, dict):
            continue
        profile_name = section_name[len("profile_") :]
        model_path = str(values.get("model_path", "") or "")
        if model_path:
            model_paths[profile_name] = model_path
        profiles[profile_name] = {
            "heat_weight": float(values.get("heat_weight", 1.0) or 1.0),
            "market_cap_weight": float(values.get("market_cap_weight", 1.0) or 1.0),
            "volume_price_weight": float(values.get("volume_price_weight", 1.0) or 1.0),
            "position_weight": float(values.get("position_weight", 1.0) or 1.0),
            "risk_weight": float(values.get("risk_weight", 1.0) or 1.0),
            **{key: float(values.get(key, 0.0) or 0.0) for key in METRIC_WEIGHT_KEYS},
        }

    if DEFAULT_PROFILE_NAME not in profiles:
        profiles[DEFAULT_PROFILE_NAME] = dict(DEFAULT_PROFILE_WEIGHTS)
    if active_profile not in profiles:
        active_profile = DEFAULT_PROFILE_NAME

    return {
        "active_profile": active_profile,
        "model_paths": model_paths,
        "profiles": profiles,
    }


def save_candidate_weight_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    active_profile = config.get("active_profile", DEFAULT_PROFILE_NAME)
    model_paths = dict(config.get("model_paths", {}))
    profiles = dict(config.get("profiles", {}))
    if DEFAULT_PROFILE_NAME not in profiles:
        profiles[DEFAULT_PROFILE_NAME] = dict(DEFAULT_PROFILE_WEIGHTS)

    lines = [
        "[meta]",
        f'active_profile = "{active_profile}"',
        "",
    ]

    for profile_name in sorted(profiles):
        weights = profiles[profile_name]
        lines.extend(
            [
                f"[profile_{profile_name}]",
                f"heat_weight = {float(weights.get('heat_weight', 1.0)):.6f}",
                f"market_cap_weight = {float(weights.get('market_cap_weight', 1.0)):.6f}",
                f"volume_price_weight = {float(weights.get('volume_price_weight', 1.0)):.6f}",
                f"position_weight = {float(weights.get('position_weight', 1.0)):.6f}",
                f"risk_weight = {float(weights.get('risk_weight', 1.0)):.6f}",
                *( [f'model_path = "{model_paths[profile_name]}"'] if model_paths.get(profile_name) else [] ),
                *[f"{key} = {float(weights.get(key, 0.0)):.6f}" for key in METRIC_WEIGHT_KEYS],
                "",
            ]
        )

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
