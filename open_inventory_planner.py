from __future__ import annotations

import argparse
from pathlib import Path

from target_treasury_monitor_clean.inventory_planner_server import serve_inventory_planner


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve and open sell_side_inventory_planner.html with auto-discovered CSV inputs.")
    parser.add_argument("--directory", default=Path(__file__).resolve().parent, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--no-open", action="store_true", help="Start the local server without opening the browser.")
    args = parser.parse_args()
    serve_inventory_planner(args.directory, host=args.host, port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
