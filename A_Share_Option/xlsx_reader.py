from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

HEADER_ALIASES = {
    "代码": "code",
    "证券代码": "code",
    "合约代码": "code",
    "最新价": "last",
    "Mid": "mid",
    "MID": "mid",
    "中间价": "mid",
    "Bid": "bid",
    "BID": "bid",
    "买价": "bid",
    "Ask": "ask",
    "ASK": "ask",
    "卖价": "ask",
    "涨跌": "change",
    "涨跌幅": "changePct",
    "成交量": "volume",
    "持仓量": "openInterest",
    "IV": "iv",
    "MIV": "miv",
    "Delta": "delta",
    "Gamma": "gamma",
    "Vega": "vega",
    "Theta": "theta",
    "到期日": "expiry",
    "到期": "expiry",
    "DTE": "dte",
    "剩余天数": "dte",
    "方向": "optionType",
    "类型": "optionType",
    "认购认沽": "optionType",
    "保证金": "margin",
    "现券价格": "underlyingPrice",
    "LastPrice": "underlyingPrice",
    "LASTPRICE": "underlyingPrice",
    "现券Bid": "underlyingBid",
    "现券Ask": "underlyingAsk",
    "标的Bid": "underlyingBid",
    "标的Ask": "underlyingAsk",
    "ETF Bid": "underlyingBid",
    "ETF Ask": "underlyingAsk",
    "ETF买价": "underlyingBid",
    "ETF卖价": "underlyingAsk",
    "Bid_Price2": "underlyingBid",
    "bid_price2": "underlyingBid",
    "Ask_Price2": "underlyingAsk",
    "ask_price2": "underlyingAsk",
    "行权价": "strike",
    "执行价": "strike",
    "行权价格": "strike",
    "Strike": "strike",
    "STRIKE": "strike",
}


class XlsxReadError(RuntimeError):
    """Raised when the option workbook cannot be read."""


@dataclass(frozen=True)
class WorkbookSheet:
    name: str
    path: str


def read_option_chain(excel_path: str | Path, sheet_name: str = "创业板") -> dict[str, object]:
    """Read and normalize an option-chain sheet from an xlsx workbook.

    This intentionally uses only the Python standard library so the dashboard
    can run inside the project's existing .venv without adding packages.
    """

    path = Path(excel_path).expanduser()
    if not path.exists():
        raise XlsxReadError(f"Excel file not found: {path}")

    last_error: Exception | None = None
    for attempt in range(3):
        snapshot: Path | None = None
        try:
            snapshot = _snapshot_workbook(path)
            return _read_option_chain_from_snapshot(snapshot, path, sheet_name)
        except (OSError, BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
            last_error = exc
            time.sleep(0.12 * (attempt + 1))
        finally:
            if snapshot is not None and snapshot != path:
                try:
                    snapshot.unlink(missing_ok=True)
                except OSError:
                    pass
    raise XlsxReadError(f"Could not read {path}: {last_error}") from last_error


def _snapshot_workbook(path: Path) -> Path:
    suffix = path.suffix or ".xlsx"
    handle = tempfile.NamedTemporaryFile(prefix="a_share_option_", suffix=suffix, delete=False)
    handle.close()
    target = Path(handle.name)
    try:
        shutil.copy2(path, target)
        return target
    except OSError:
        target.unlink(missing_ok=True)
        return path


def _read_option_chain_from_snapshot(snapshot: Path, source_path: Path, sheet_name: str) -> dict[str, object]:
    with ZipFile(snapshot) as archive:
        sheets = _workbook_sheets(archive)
        if not sheets:
            raise XlsxReadError("Workbook has no visible sheets")
        selected = next((sheet for sheet in sheets if sheet.name == sheet_name), None)
        if selected is None:
            selected = sheets[0]
        shared_strings = _shared_strings(archive)
        matrix = _sheet_matrix(archive, selected.path, shared_strings)

    return read_option_chain_from_matrix(
        matrix=matrix,
        source_path=source_path,
        sheet_name=selected.name,
        requested_sheet=sheet_name,
        available_sheets=[sheet.name for sheet in sheets],
        source_mode="saved",
    )


def read_option_chain_from_matrix(
    matrix: list[list[object | None]],
    source_path: str | Path,
    sheet_name: str,
    requested_sheet: str | None = None,
    available_sheets: list[str] | None = None,
    source_mode: str = "saved",
) -> dict[str, object]:
    source_path = Path(source_path)
    header_index = _find_header_row(matrix)
    if header_index is None:
        raise XlsxReadError(f"Could not find a header row in sheet {sheet_name}")

    headers = [str(value).strip() if value is not None else "" for value in matrix[header_index]]
    mapped_headers = [_map_header(header) for header in headers]
    raw_rows = matrix[header_index + 1 :]
    records = _normalize_rows(raw_rows, headers, mapped_headers, sheet_name)
    try:
        updated_at = datetime.fromtimestamp(source_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        updated_at = ""
    return {
        "ok": True,
        "source": str(source_path),
        "sourceMode": source_mode,
        "sheet": sheet_name,
        "requestedSheet": requested_sheet or sheet_name,
        "availableSheets": available_sheets or [sheet_name],
        "headers": headers,
        "mappedHeaders": mapped_headers,
        "rowCount": len(records),
        "updatedAt": updated_at,
        "readAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": records,
    }


def _workbook_sheets(archive: ZipFile) -> list[WorkbookSheet]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("pkgrel:Relationship", NS)
        if "Id" in rel.attrib and "Target" in rel.attrib
    }
    sheets: list[WorkbookSheet] = []
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        rel_id = sheet.attrib.get(f"{{{NS['rel']}}}id")
        target = rel_targets.get(rel_id or "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = f"xl/{target}"
        sheets.append(WorkbookSheet(name=sheet.attrib.get("name", ""), path=path.replace("\\", "/")))
    return sheets


def _shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: list[str] = []
    for item in root.findall("main:si", NS):
        texts = [node.text or "" for node in item.findall(".//main:t", NS)]
        out.append("".join(texts))
    return out


def _sheet_matrix(archive: ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[object | None]]:
    root = ET.fromstring(archive.read(sheet_path))
    rows: list[list[object | None]] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        values: dict[int, object | None] = {}
        for cell in row.findall("main:c", NS):
            ref = cell.attrib.get("r", "")
            column = _column_index(ref)
            if column is None:
                continue
            values[column] = _cell_value(cell, shared_strings)
        if values:
            width = max(values) + 1
            rows.append([values.get(i) for i in range(width)])
        else:
            rows.append([])
    return rows


def _column_index(cell_ref: str) -> int | None:
    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        return None
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    return number - 1


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> object | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        texts = [node.text or "" for node in cell.findall(".//main:t", NS)]
        return "".join(texts).strip()

    value_node = cell.find("main:v", NS)
    if value_node is None or value_node.text is None:
        return None
    raw = value_node.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "b":
        return raw == "1"
    if cell_type in {"str", "e"}:
        return raw
    return _coerce_number(raw)


def _coerce_number(raw: str) -> object:
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer():
        return int(value)
    return value


def _find_header_row(matrix: list[list[object | None]]) -> int | None:
    for index, row in enumerate(matrix[:20]):
        labels = {str(value).strip() for value in row if value is not None}
        score = len(labels.intersection(HEADER_ALIASES))
        if score >= 5 and "代码" in labels:
            return index
    return None


def _map_header(header: str) -> str:
    clean = header.strip()
    return HEADER_ALIASES.get(clean, clean)


def _normalize_rows(
    rows: list[list[object | None]],
    headers: list[str],
    mapped_headers: list[str],
    sheet_name: str,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    first_underlying = _first_number(rows, mapped_headers, "underlyingPrice")
    first_underlying_bid = _first_number(rows, mapped_headers, "underlyingBid")
    first_underlying_ask = _first_number(rows, mapped_headers, "underlyingAsk")
    for row_number, values in enumerate(rows, start=2):
        raw = {
            headers[i]: values[i] if i < len(values) else None
            for i in range(len(headers))
            if headers[i]
        }
        row = {
            mapped_headers[i]: values[i] if i < len(values) else None
            for i in range(len(mapped_headers))
            if mapped_headers[i]
        }
        code = _text(row.get("code"))
        if not code:
            continue
        option_type = _normalize_option_type(row.get("optionType"), row.get("delta"))
        expiry_label, dte = _normalize_expiry(row.get("expiry"), row.get("dte"))
        strike = _number(row.get("strike"))
        last = _number(row.get("last"))
        bid = _number(row.get("bid"))
        ask = _number(row.get("ask"))
        mid = _number(row.get("mid"))
        mark = _first_present_number(mid, last, _mid_from_bid_ask(bid, ask), bid, ask)
        underlying = _first_present_number(_number(row.get("underlyingPrice")), first_underlying)
        underlying_bid = _first_present_number(_number(row.get("underlyingBid")), first_underlying_bid)
        underlying_ask = _first_present_number(_number(row.get("underlyingAsk")), first_underlying_ask)
        record = {
            "rowNumber": row_number,
            "code": code,
            "product": _product_from_sheet(sheet_name),
            "sheet": sheet_name,
            "last": last,
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "mark": mark,
            "change": _number(row.get("change")),
            "changePct": _number(row.get("changePct")),
            "volume": _number(row.get("volume")),
            "openInterest": _number(row.get("openInterest")),
            "iv": _number(row.get("iv")),
            "miv": _number(row.get("miv")),
            "delta": _number(row.get("delta")),
            "gamma": _number(row.get("gamma")),
            "vega": _number(row.get("vega")),
            "theta": _number(row.get("theta")),
            "expiry": expiry_label,
            "dte": dte,
            "optionType": option_type,
            "optionSide": "call" if option_type == "认购" else "put",
            "strike": strike,
            "strikeLabel": _format_number(strike) if strike is not None else "",
            "margin": _number(row.get("margin")),
            "underlyingPrice": underlying,
            "underlyingBid": underlying_bid,
            "underlyingAsk": underlying_ask,
            "raw": raw,
        }
        record["rowKey"] = _row_key(record)
        records.append(record)
    return records


def _product_from_sheet(sheet_name: str) -> str:
    if "科创" in sheet_name:
        return "科创"
    if "创业" in sheet_name:
        return "创业"
    return sheet_name


def _first_number(rows: list[list[object | None]], mapped_headers: list[str], key: str) -> float | None:
    try:
        index = mapped_headers.index(key)
    except ValueError:
        return None
    for row in rows:
        value = _number(row[index] if index < len(row) else None)
        if value is not None:
            return value
    return None


def _normalize_option_type(value: object, delta: object = None) -> str:
    text = _text(value).lower()
    if "沽" in text or "put" in text or text == "p":
        return "认沽"
    if "购" in text or "call" in text or text == "c":
        return "认购"
    delta_value = _number(delta)
    if delta_value is not None and delta_value < 0:
        return "认沽"
    return "认购"


def _normalize_expiry(expiry: object, dte: object = None) -> tuple[str, float | None]:
    explicit_dte = _number(dte)
    if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
        if 0 <= float(expiry) < 10000:
            return _format_number(float(expiry)), float(expiry)
        date = datetime(1899, 12, 30) + timedelta(days=float(expiry))
        return date.strftime("%Y-%m-%d"), explicit_dte
    text = _text(expiry)
    if text:
        inferred = explicit_dte
        number = _number(text)
        if inferred is None and number is not None and 0 <= number < 10000:
            inferred = number
        return text, inferred
    if explicit_dte is not None:
        return _format_number(explicit_dte), explicit_dte
    return "", None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _first_present_number(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _mid_from_bid_ask(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _row_key(record: dict[str, object]) -> str:
    code = _text(record.get("code"))
    if code:
        return code
    return "|".join(
        _text(record.get(key))
        for key in ("product", "optionType", "expiry", "strikeLabel", "rowNumber")
    )
