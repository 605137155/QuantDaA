from __future__ import annotations

import types
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

import app


class AppEntryTests(unittest.TestCase):
    def test_main_refreshes_pools_before_candidate_export(self):
        exported_paths = []
        fake_app = types.SimpleNamespace(
            refresh_pools=lambda: exported_paths.append("refreshed"),
            export_candidate_review_csv=lambda session_type, trade_date, output_path: exported_paths.append(
                (session_type, trade_date, output_path)
            )
            or Path("exports/replay_2026-06-01.csv"),
        )
        fake_args = types.SimpleNamespace(
            once=False,
            cli=False,
            export_candidate_csv="replay",
            optimize_replay_weights_csv=None,
            profile_name="optimized_latest",
            candidate_profile=None,
            trade_date="2026-06-01",
            output=None,
        )

        with patch.object(app, "parse_args", return_value=fake_args), patch.object(
            app, "resolve_project_root", return_value=Path("D:/giteeCloneProject/QuantDaA")
        ), patch.object(app, "bootstrap_app", return_value=fake_app), patch("builtins.print"):
            app.main()

        self.assertEqual("refreshed", exported_paths[0])
        self.assertEqual("replay", exported_paths[1][0])
        self.assertEqual("2026-06-01", exported_paths[1][1])

    def test_main_optimizes_replay_weights_without_bootstrapping_app(self):
        fake_args = types.SimpleNamespace(
            once=False,
            cli=False,
            export_candidate_csv=None,
            trade_date=None,
            output=None,
            optimize_replay_weights_csv="exports\\replay_2026-06-01.csv",
            profile_name="optimized_latest",
            candidate_profile=None,
        )
        optimizer_calls = []

        class FakeOptimizer:
            def optimize_replay_weights(self, **kwargs):
                optimizer_calls.append(kwargs)
                return types.SimpleNamespace(
                    profile_name="optimized_latest",
                    sample_count=88,
                    base_spearman=0.11,
                    optimized_spearman=0.42,
                    base_top10_avg_pct=1.20,
                    optimized_top10_avg_pct=3.40,
                    weights={"heat_weight": 0.7},
                    model_path="config/candidate_model.pkl",
                )

        with patch.object(app, "parse_args", return_value=fake_args), patch.object(
            app, "resolve_project_root", return_value=Path("D:/giteeCloneProject/QuantDaA")
        ), patch("src.services.candidate_weight_optimizer.CandidateWeightOptimizer", return_value=FakeOptimizer()), patch.object(
            app, "bootstrap_app"
        ) as bootstrap_app_mock, patch("builtins.print"):
            app.main()

        bootstrap_app_mock.assert_not_called()
        self.assertEqual("exports\\replay_2026-06-01.csv", optimizer_calls[0]["csv_path"])
        self.assertEqual("optimized_latest", optimizer_calls[0]["profile_name"])

    def test_parse_args_requires_trade_date_for_candidate_export(self):
        with patch.object(sys, "argv", ["app.py", "--export-candidate-csv", "replay"]):
            with self.assertRaises(SystemExit):
                app.parse_args()

    def test_parse_args_accepts_candidate_export_arguments(self):
        with patch.object(
            sys,
            "argv",
            [
                "app.py",
                "--export-candidate-csv",
                "replay",
                "--trade-date",
                "2026-06-01",
                "--output",
                "exports\\replay_2026-06-01.csv",
                "--candidate-profile",
                "replay_2026_06_01_strict_norm",
            ],
        ):
            args = app.parse_args()

        self.assertEqual("replay", args.export_candidate_csv)
        self.assertEqual("2026-06-01", args.trade_date)
        self.assertEqual("exports\\replay_2026-06-01.csv", args.output)
        self.assertEqual("replay_2026_06_01_strict_norm", args.candidate_profile)

    def test_main_passes_candidate_profile_to_bootstrap(self):
        fake_args = types.SimpleNamespace(
            once=True,
            cli=False,
            export_candidate_csv=None,
            optimize_replay_weights_csv=None,
            profile_name="optimized_latest",
            candidate_profile="replay_2026_06_01_strict_norm",
            trade_date=None,
            output=None,
        )
        fake_app = types.SimpleNamespace(run_once=lambda: None)

        with patch.object(app, "parse_args", return_value=fake_args), patch.object(
            app, "resolve_project_root", return_value=Path("D:/giteeCloneProject/QuantDaA")
        ), patch.object(app, "bootstrap_app", return_value=fake_app) as bootstrap_app_mock:
            app.main()

        bootstrap_app_mock.assert_called_once_with(
            project_root=Path("D:/giteeCloneProject/QuantDaA"),
            candidate_profile_override="replay_2026_06_01_strict_norm",
        )

    def test_resolve_project_root_uses_source_dir_when_not_frozen(self):
        root = app.resolve_project_root()

        self.assertEqual(Path(app.__file__).resolve().parent, root)

    def test_resolve_project_root_uses_executable_dir_when_frozen(self):
        fake_sys = types.SimpleNamespace(frozen=True, executable=r"D:\dist\QuantDaA\QuantDaA.exe")

        with patch.object(app, "sys", fake_sys):
            root = app.resolve_project_root()

        self.assertEqual(Path(r"D:\dist\QuantDaA"), root)

    def test_resolve_project_root_prefers_meipass_when_frozen(self):
        fake_sys = types.SimpleNamespace(
            frozen=True,
            executable=r"H:\QuantDaA\dist\QuantDaA\QuantDaA.exe",
            _MEIPASS=r"H:\QuantDaA\dist\QuantDaA\_internal",
        )

        with patch.object(app, "sys", fake_sys):
            root = app.resolve_project_root()

        self.assertEqual(Path(r"H:\QuantDaA\dist\QuantDaA\_internal"), root)


if __name__ == "__main__":
    unittest.main()
