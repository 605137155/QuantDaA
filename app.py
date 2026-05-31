from __future__ import annotations

import argparse
from pathlib import Path

from src.bootstrap import bootstrap_app
from src.ui.main_window import launch_desktop_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuantDaA hot-stock monitor")
    parser.add_argument("--once", action="store_true", help="run one refresh cycle and exit")
    parser.add_argument("--cli", action="store_true", help="run background loop in console mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    app = bootstrap_app(project_root=project_root)

    if args.once:
        app.run_once()
        return

    if args.cli:
        app.run_forever()
        return

    launch_desktop_app(app)


if __name__ == "__main__":
    main()
