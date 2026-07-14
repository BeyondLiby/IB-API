from __future__ import annotations

import argparse
from pathlib import Path

from .config import (
    DEFAULT_EVENT_EXCHANGES,
    DEFAULT_EVENT_SEC_TYPES,
    DEFAULT_EVENT_SYMBOL_SEEDS,
    IBConnectionSettings,
    ScanSettings,
)
from .fed_ib_discovery import DiscoverySettings, discovery_frames, discovery_summary, run_fed_ib_discovery
from .ibkr import attach_error_collector, connect_ib, detach_error_collector
from .quotes import fetch_quote_frame, load_contracts
from .scanner import read_local_symbols, scan_event_contracts, split_csv, split_float_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="IBKR ForecastEx / event-contract scanner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=601)
    parser.add_argument("--market-data-type", type=int, default=1, help="1 live, 2 frozen, 3 delayed, 4 delayed frozen")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Probe contract details for event-like contracts")
    scan_parser.add_argument("--symbols", default=",".join(DEFAULT_EVENT_SYMBOL_SEEDS))
    scan_parser.add_argument("--local-symbols", default="", help="Comma-separated exact localSymbol values")
    scan_parser.add_argument("--local-symbol-file", default="", help="CSV containing localSymbol/local_symbol/symbol")
    scan_parser.add_argument("--expirations", default="", help="Comma-separated YYYYMMDD/contract month values")
    scan_parser.add_argument("--strikes", default="", help="Comma-separated strike values")
    scan_parser.add_argument("--rights", default="", help="Comma-separated C/P values")
    scan_parser.add_argument("--sec-types", default=",".join(DEFAULT_EVENT_SEC_TYPES))
    scan_parser.add_argument("--exchanges", default=",".join(DEFAULT_EVENT_EXCHANGES))
    scan_parser.add_argument("--keep-all-matches", action="store_true")
    scan_parser.add_argument("--out", default="data/prediction_market_contracts.csv")

    quote_parser = subparsers.add_parser("quote", help="Subscribe briefly and snapshot top-of-book liquidity")
    quote_parser.add_argument("--contracts", default="data/prediction_market_contracts.csv")
    quote_parser.add_argument("--wait", type=float, default=5.0)
    quote_parser.add_argument("--batch-size", type=int, default=25)
    quote_parser.add_argument("--generic-ticks", default="100,101,104,106,165,233,293,294,295")
    quote_parser.add_argument("--no-qualify", action="store_true")
    quote_parser.add_argument("--out", default="data/prediction_market_quotes.csv")

    discover_parser = subparsers.add_parser("fed-discover", help="Diagnose IB access to ZQ and Fed/Kalshi event contracts")
    discover_parser.add_argument("--wait", type=float, default=5.0)
    discover_parser.add_argument("--out-prefix", default="data/fed_ib_discovery")

    args = parser.parse_args()
    settings = IBConnectionSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        market_data_type=args.market_data_type,
    )
    ib = connect_ib(settings)
    errors, handler = attach_error_collector(ib)
    try:
        if args.command == "scan":
            local_symbols = split_csv(args.local_symbols) + read_local_symbols(args.local_symbol_file)
            frame = scan_event_contracts(
                ib,
                ScanSettings(
                    symbols=split_csv(args.symbols),
                    local_symbols=local_symbols,
                    expirations=split_csv(args.expirations),
                    strikes=split_float_csv(args.strikes),
                    rights=split_csv(args.rights),
                    sec_types=split_csv(args.sec_types),
                    exchanges=split_csv(args.exchanges),
                    keep_all_matches=args.keep_all_matches,
                ),
            )
            _write_frame(frame, args.out)
            print(f"wrote {len(frame)} contract rows to {args.out}")
        elif args.command == "quote":
            contracts = load_contracts(args.contracts)
            frame = fetch_quote_frame(
                ib,
                contracts,
                wait_seconds=args.wait,
                generic_ticks=args.generic_ticks,
                qualify=not args.no_qualify,
                batch_size=args.batch_size,
            )
            _write_frame(frame, args.out)
            print(f"wrote {len(frame)} quote rows to {args.out}")
        elif args.command == "fed-discover":
            result = run_fed_ib_discovery(ib, DiscoverySettings(wait_seconds=args.wait))
            for name, frame in discovery_frames(result).items():
                path = f"{args.out_prefix}_{name}.csv"
                _write_frame(frame, path)
                print(f"wrote {len(frame)} {name} rows to {path}")
            print(discovery_summary(result))

        if errors:
            error_path = Path("data/prediction_market_ib_errors.csv")
            _write_frame(__import__("pandas").DataFrame(errors), str(error_path))
            print(f"wrote {len(errors)} IB messages to {error_path}")
    finally:
        detach_error_collector(ib, handler)
        if ib.isConnected():
            ib.disconnect()


def _write_frame(frame, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
