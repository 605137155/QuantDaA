from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.bootstrap import bootstrap_app
from src.ui.main_window import launch_desktop_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuantDaA hot-stock monitor")
    parser.add_argument("--once", action="store_true", help="run one refresh cycle and exit")
    parser.add_argument("--cli", action="store_true", help="run background loop in console mode")
    parser.add_argument(
        "--export-candidate-csv",
        choices=("replay", "intraday"),
        help="export replay/intraday candidate review rows for a trade date",
    )
    parser.add_argument("--optimize-replay-weights-csv", help="optimize replay candidate weights from an exported CSV file")
    parser.add_argument("--profile-name", default="optimized_latest", help="profile name for optimized replay weights")
    parser.add_argument("--candidate-profile", default="", help="candidate scoring profile to use when launching or exporting")
    parser.add_argument("--trade-date", help="trade date for candidate CSV export, e.g. 2026-06-01")
    parser.add_argument("--output", help="optional CSV output path")
    args = parser.parse_args()
    if args.export_candidate_csv and not args.trade_date:
        parser.error("--trade-date is required when using --export-candidate-csv")
    return args


def resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    args = parse_args()
    project_root = resolve_project_root()
    if args.optimize_replay_weights_csv:
        from src.services.candidate_weight_optimizer import CandidateWeightOptimizer

        weight_config_path = project_root / "config" / "candidate_weights.toml"
        result = CandidateWeightOptimizer().optimize_replay_weights(
            csv_path=args.optimize_replay_weights_csv,
            weight_config_path=weight_config_path,
            profile_name=args.profile_name,
            activate_profile=True,
        )
        print("=== 复盘候选权重优化完成 ===")
        print(f"  profile:     {result.profile_name}")
        print(f"  samples:     {result.sample_count}")
        print(f"  model:       {result.model_path or '(无)'}")
        print(f"  spearman:    {result.base_spearman:.4f} -> {result.optimized_spearman:.4f}")
        print(f"  top10_avg:   {result.base_top10_avg_pct:.2f}% -> {result.optimized_top10_avg_pct:.2f}%")
        return

    app = bootstrap_app(project_root=project_root, candidate_profile_override=args.candidate_profile)

    if args.export_candidate_csv:
        app.refresh_pools()
        output_path = args.output or project_root / "exports" / f"{args.export_candidate_csv}_{args.trade_date}.csv"
        exported = app.export_candidate_review_csv(args.export_candidate_csv, args.trade_date, output_path)
        print(f"已导出: {exported}")
        return

    if args.once:
        app.run_once()
        return

    if args.cli:
        app.run_forever()
        return

    launch_desktop_app(app)


if __name__ == "__main__":
    main()
