from __future__ import annotations

import argparse
import os
from pathlib import Path

from target_treasury_monitor_clean.inventory_planner_server import serve_inventory_planner
from target_treasury_monitor_clean.settings import (
    DEFAULT_IB_ACCOUNT,
    IBSettings,
    market_data_type_from_label,
)


def parse_contract_months(*spec_values: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in spec_values:
        for part in str(value or "").split(";"):
            text = part.strip()
            if not text:
                continue
            separator = "=" if "=" in text else ":"
            if separator not in text:
                continue
            root, months = (item.strip() for item in text.split(separator, 1))
            if root and months:
                out[root.upper()] = months
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve and open sell_side_inventory_planner.html with auto-discovered CSV inputs.")
    parser.add_argument("--directory", default=Path(__file__).resolve().parent, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--no-open", action="store_true", help="Start the local server without opening the browser.")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", default=4001, type=int)
    parser.add_argument("--account", default=os.environ.get("IB_ACCOUNT", DEFAULT_IB_ACCOUNT))
    parser.add_argument("--stream-client-id", default=int(os.environ.get("IB_STREAM_CLIENT_ID", "7318")), type=int)
    parser.add_argument("--market-data-type", default="delayed")
    parser.add_argument("--chain-specs", default="ZF=202609;ZN=202609")
    parser.add_argument("--zc-chain-specs", default="ZC=202609")
    args = parser.parse_args()
    stream_settings = IBSettings(
        host=args.ib_host,
        port=args.ib_port,
        client_id=args.stream_client_id,
        account=args.account,
        market_data_type=market_data_type_from_label(args.market_data_type),
        readonly=True,
    )
    serve_inventory_planner(
        args.directory,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        stream_settings=stream_settings,
        stream_future_months=parse_contract_months(args.chain_specs, args.zc_chain_specs),
    )


if __name__ == "__main__":
    main()
