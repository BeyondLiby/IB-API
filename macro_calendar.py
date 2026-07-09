from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DB_PATH = Path("data/macro_calendar.sqlite")
DEFAULT_FMP_API_KEY = "Ypbwgmj79wKCZPIECYRCQJKZRPtfOoVL"
FMP_URLS = (
    "https://financialmodelingprep.com/stable/economic-calendar",
    "https://financialmodelingprep.com/api/v3/economic_calendar",
)
FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse_dt(value: object) -> str:
    text = str(value or "").replace("Z", "+00:00").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19] if " " in text or "T" in text else text[:10], fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return text


def clean_num(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().replace(",", "")


def treasury_importance(event: str, impact: str = "") -> int:
    text = event.lower()
    high = ("fomc", "fed interest rate", "nonfarm", "payroll", "cpi", "core cpi", "pce", "core pce")
    medium = ("retail sales", "ism", "jobless claims", "gdp", "jolts", "ppi", "auction", "treasury")
    if any(word in text for word in high):
        return 5
    if any(word in text for word in medium):
        return 4
    if str(impact).lower() in {"high", "3"}:
        return 3
    if str(impact).lower() in {"medium", "2"}:
        return 2
    return 1


def normalize(row: dict, source: str) -> dict:
    event = str(row.get("event") or row.get("Event") or row.get("title") or "").strip()
    country = str(row.get("country") or row.get("Country") or "").strip()
    actual = clean_num(row.get("actual", row.get("Actual")))
    consensus = clean_num(
        row.get(
            "consensus",
            row.get("Consensus", row.get("forecast", row.get("estimate", row.get("Forecast")))),
        )
    )
    previous = clean_num(row.get("previous", row.get("Previous", row.get("prev"))))
    release_time = parse_dt(row.get("date", row.get("time", row.get("Date"))))
    period = str(row.get("period") or row.get("reference") or row.get("Reference") or "").strip()
    unit = str(row.get("unit") or row.get("Unit") or "").strip()
    impact = str(row.get("impact") or row.get("importance") or row.get("Importance") or "").strip()
    return {
        "event_id": f"{source}:{country}:{event}:{release_time}:{period}",
        "country": country,
        "event_name": event,
        "release_time_utc": release_time,
        "period": period,
        "actual": actual,
        "consensus": consensus,
        "previous": previous,
        "unit": unit,
        "importance_treasury": treasury_importance(event, impact),
        "source": source,
        "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }


def in_range(row: dict, start: str, end: str) -> bool:
    release_time = row["release_time_utc"]
    return bool(release_time) and parse_dt(start) <= release_time < parse_dt(end)


def request_json(url: str, params: dict) -> object:
    full_url = f"{url}?{urlencode(params)}" if params else url
    req = Request(full_url, headers={"User-Agent": "IB-API macro-calendar/1.0"})
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_fmp(start: str, end: str, api_key: str) -> list[dict]:
    params = {"from": start, "to": end, "apikey": api_key}
    last_error: Exception | None = None
    for url in FMP_URLS:
        try:
            payload = request_json(url, params)
            if isinstance(payload, dict) and "Error Message" in payload:
                raise RuntimeError(payload["Error Message"])
            rows = payload if isinstance(payload, list) else payload.get("economicCalendar", [])
            return [normalize(row, "fmp") for row in rows if isinstance(row, dict)]
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"FMP calendar fetch failed: {last_error}")


def fetch_forex_factory(start: str, end: str) -> list[dict]:
    payload = request_json(FOREX_FACTORY_URL, {})
    if not isinstance(payload, list):
        raise RuntimeError("ForexFactory calendar returned non-list payload")
    rows = [normalize(row, "forexfactory") for row in payload if isinstance(row, dict)]
    return [row for row in rows if in_range(row, start, end)]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_events (
            event_id TEXT PRIMARY KEY,
            country TEXT,
            event_name TEXT,
            release_time_utc TEXT,
            period TEXT,
            actual TEXT,
            consensus TEXT,
            previous TEXT,
            unit TEXT,
            importance_treasury INTEGER,
            source TEXT,
            raw_json TEXT,
            last_checked_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_events_time ON macro_events(release_time_utc)")
    return conn


def save_events(db_path: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    fields = tuple(rows[0])
    sql = f"""
        INSERT INTO macro_events ({",".join(fields)})
        VALUES ({",".join("?" for _ in fields)})
        ON CONFLICT(event_id) DO UPDATE SET
        {",".join(f"{field}=excluded.{field}" for field in fields if field != "event_id")}
    """
    with connect(db_path) as conn:
        conn.executemany(sql, [[row[field] for field in fields] for row in rows])
    return len(rows)


def load_events(db_path: Path, start: str, end: str) -> list[sqlite3.Row]:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT release_time_utc, country, event_name, actual, consensus, previous, unit, importance_treasury, source
        FROM macro_events
        WHERE release_time_utc >= ? AND release_time_utc < ?
        ORDER BY release_time_utc, importance_treasury DESC, country, event_name
        """,
        (parse_dt(start), parse_dt(end)),
    ).fetchall()


def print_rows(rows: list[sqlite3.Row]) -> None:
    for row in rows:
        print(
            f"{row['release_time_utc'][:16]:16} "
            f"P{row['importance_treasury']} {row['country']:<3} "
            f"{row['event_name'][:42]:42} "
            f"cons={row['consensus'] or '-':>8} "
            f"act={row['actual'] or '-':>8} "
            f"prev={row['previous'] or '-':>8} "
            f"{row['unit'] or ''} [{row['source']}]"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Small macro calendar fetcher with consensus/actual storage.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--from", dest="start", required=True)
    fetch.add_argument("--to", dest="end", required=True)
    fetch.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", DEFAULT_FMP_API_KEY))

    imp = sub.add_parser("import-json")
    imp.add_argument("path", type=Path)
    imp.add_argument("--source", default="manual")

    ls = sub.add_parser("list")
    ls.add_argument("--from", dest="start", default=date.today().isoformat())
    ls.add_argument("--to", dest="end")
    ls.add_argument("--days", type=int, default=7)

    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        try:
            rows = fetch_fmp(args.start, args.end, args.api_key) if args.api_key else []
        except Exception as exc:
            print(f"FMP unavailable, falling back to ForexFactory: {exc}", file=sys.stderr)
            rows = fetch_forex_factory(args.start, args.end)
        print(f"saved {save_events(args.db, rows)} events to {args.db}")
    elif args.cmd == "import-json":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        source_rows = payload if isinstance(payload, list) else payload.get("economicCalendar", [])
        rows = [normalize(row, args.source) for row in source_rows]
        print(f"saved {save_events(args.db, rows)} events to {args.db}")
    elif args.cmd == "list":
        end = args.end or (date.fromisoformat(args.start) + timedelta(days=args.days)).isoformat()
        print_rows(load_events(args.db, args.start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
