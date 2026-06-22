from __future__ import annotations

import html
import json
import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


STATUS_CODES = {2104, 2106, 2107, 2108, 2158}


@dataclass
class NewsRecord:
    symbol: str
    con_id: int
    time_utc: str
    time_local: str
    provider: str
    article_id: str

    headline_raw: str
    headline: str
    publisher: str = ""
    language: str = ""

    article_type: int | None = None
    article_html: str = ""
    article_text: str = ""
    article_error: str = ""

    duplicate_count: int = 1
    duplicate_items: list[dict[str, str]] = field(default_factory=list)


def parse_ib_news_time(
    value: str,
    local_timezone: str = "Asia/Taipei",
) -> tuple[str, str]:
    """
    IBKR 新闻时间通常是 UTC，例如：
    '2026-06-13 01:31:00.0'
    """
    raw = value.strip()
    dt: datetime | None = None

    formats = (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    )

    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue

    if dt is None:
        # 解析失败时不阻断主流程
        return raw, raw

    dt_utc = dt.replace(tzinfo=timezone.utc)
    dt_local = dt_utc.astimezone(ZoneInfo(local_timezone))

    return (
        dt_utc.isoformat(),
        dt_local.isoformat(),
    )


def parse_headline_metadata(raw_headline: str) -> dict[str, Any]:
    """
    将：
    {A:800015:L:en}Oracle ... -- Barron's

    解析为：
    headline / publisher / language / metadata
    """
    decoded = html.unescape(raw_headline or "").strip()

    blocks = re.findall(r"^\{([^{}]+)\}", decoded)
    metadata: dict[str, str] = {}

    for block in blocks:
        parts = block.split(":")
        for i in range(0, len(parts) - 1, 2):
            metadata[parts[i]] = parts[i + 1]

    cleaned = re.sub(r"^(?:\{[^{}]*\})+", "", decoded).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    publisher = ""
    headline = cleaned

    if " -- " in cleaned:
        possible_headline, possible_publisher = cleaned.rsplit(" -- ", 1)
        if possible_headline.strip() and possible_publisher.strip():
            headline = possible_headline.strip()
            publisher = possible_publisher.strip()

    return {
        "headline": headline,
        "publisher": publisher,
        "language": metadata.get("L", ""),
        "metadata": metadata,
    }


def normalize_headline(value: str) -> str:
    """
    用于精确及近似去重的标准化标题。
    """
    text = html.unescape(value or "").lower()
    text = re.sub(r"^(?:\{[^{}]*\})+", "", text)
    text = re.sub(r"\s+--\s+.+$", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def headline_similarity(left: str, right: str) -> tuple[float, float]:
    """
    返回：
    1. 字符序列相似度
    2. Token Jaccard 相似度
    """
    left_norm = normalize_headline(left)
    right_norm = normalize_headline(right)

    if not left_norm or not right_norm:
        return 0.0, 0.0

    sequence_ratio = SequenceMatcher(
        None,
        left_norm,
        right_norm,
    ).ratio()

    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())

    union = left_tokens | right_tokens
    jaccard = (
        len(left_tokens & right_tokens) / len(union)
        if union
        else 0.0
    )

    return sequence_ratio, jaccard


def clean_article_html(
    raw_html: str,
    strip_boilerplate: bool = True,
) -> str:
    """
    将 IBKR 返回的 HTML 正文转换为干净文本。
    """
    if not raw_html:
        return ""

    decoded = html.unescape(raw_html)
    soup = BeautifulSoup(decoded, "html.parser")

    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    text = soup.get_text(separator="\n", strip=True)

    lines: list[str] = []
    previous = None

    stop_patterns = (
        r"^\(END\)\b",
        r"^Copyright \(c\)",
        r"^The statements in this document shall not be considered",
    )

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()

        if not line:
            continue

        if strip_boilerplate and any(
            re.search(pattern, line, flags=re.IGNORECASE)
            for pattern in stop_patterns
        ):
            break

        if line == previous:
            continue

        lines.append(line)
        previous = line

    return "\n\n".join(lines)


def deduplicate_records(
    records: Iterable[NewsRecord],
    sequence_threshold: float = 0.90,
    jaccard_threshold: float = 0.84,
) -> list[NewsRecord]:
    """
    按返回顺序去重。IBKR 历史新闻通常按最新到最旧返回，
    所以相似稿件会保留最新一条。
    """
    unique: list[NewsRecord] = []

    for record in records:
        duplicate_of: NewsRecord | None = None

        for existing in unique:
            sequence_ratio, jaccard = headline_similarity(
                record.headline,
                existing.headline,
            )

            if (
                sequence_ratio >= sequence_threshold
                or jaccard >= jaccard_threshold
            ):
                duplicate_of = existing
                break

        if duplicate_of is None:
            unique.append(record)
            continue

        duplicate_of.duplicate_count += 1
        duplicate_of.duplicate_items.append(
            {
                "time_utc": record.time_utc,
                "provider": record.provider,
                "article_id": record.article_id,
                "headline": record.headline,
            }
        )

    return unique


class IBKRNewsPipeline(EWrapper, EClient):
    """
    IBKR 新闻抓取与清洗流水线。

    功能：
    1. 查询股票 conId
    2. 拉取历史新闻标题
    3. 清洗标题元数据
    4. 精确及近似去重
    5. 拉取新闻正文
    6. 清洗 HTML
    7. 输出 DataFrame / CSV / JSONL / SQLite
    """

    def __init__(
        self,
        symbol: str,
        primary_exchange: str,
        provider_codes: str,
        *,
        host: str = "127.0.0.1",
        port: int = 4001,
        client_id: int = 91,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        total_results: int = 100,
        max_articles: int | None = 30,
        local_timezone: str = "Asia/Taipei",
        article_request_interval: float = 0.25,
        verbose: bool = True,
    ) -> None:
        EClient.__init__(self, self)

        self.symbol = symbol.upper()
        self.primary_exchange = primary_exchange
        self.provider_codes = provider_codes

        self.host = host
        self.port = port
        self.client_id = client_id

        self.sec_type = sec_type
        self.exchange = exchange
        self.currency = currency

        self.total_results = total_results
        self.max_articles = max_articles
        self.local_timezone = local_timezone
        self.article_request_interval = article_request_interval
        self.verbose = verbose

        self.contract_candidates: list[Any] = []
        self.raw_records: list[NewsRecord] = []
        self.records: list[NewsRecord] = []

        self.con_id: int | None = None
        self.has_more: bool | None = None

        self._article_queue: deque[int] = deque()
        self._article_req_map: dict[int, int] = {}
        self._next_article_req_id = 3000

        self._done = threading.Event()
        self._api_thread: threading.Thread | None = None

    def log(self, message: str) -> None:
        if self.verbose:
            print(message)

    # ---------- IBKR 回调 ----------

    def nextValidId(self, order_id: int) -> None:
        self.log(f"连接成功，nextValidId={order_id}")

        contract = Contract()
        contract.symbol = self.symbol
        contract.secType = self.sec_type
        contract.exchange = self.exchange
        contract.currency = self.currency
        contract.primaryExchange = self.primary_exchange

        self.reqContractDetails(
            reqId=1001,
            contract=contract,
        )

    def contractDetails(
        self,
        req_id: int,
        contract_details: Any,
    ) -> None:
        if req_id == 1001:
            self.contract_candidates.append(contract_details)

    def contractDetailsEnd(self, req_id: int) -> None:
        if req_id != 1001:
            return

        if not self.contract_candidates:
            self.log(f"未找到合约：{self.symbol}")
            self._done.set()
            return

        selected = None

        for candidate in self.contract_candidates:
            contract = candidate.contract

            if (
                contract.symbol.upper() == self.symbol
                and (
                    not self.primary_exchange
                    or contract.primaryExchange.upper()
                    == self.primary_exchange.upper()
                )
            ):
                selected = candidate
                break

        if selected is None:
            selected = self.contract_candidates[0]

        contract = selected.contract
        self.con_id = int(contract.conId)

        self.log(
            f"找到合约：{contract.symbol}, "
            f"conId={self.con_id}, "
            f"primaryExchange={contract.primaryExchange}"
        )

        self.reqHistoricalNews(
            reqId=2001,
            conId=self.con_id,
            providerCodes=self.provider_codes,
            startDateTime="",
            endDateTime="",
            totalResults=self.total_results,
            historicalNewsOptions=[],
        )

    def historicalNews(
        self,
        req_id: int,
        news_time: str,
        provider_code: str,
        article_id: str,
        headline: str,
    ) -> None:
        if req_id != 2001:
            return

        parsed = parse_headline_metadata(headline)
        time_utc, time_local = parse_ib_news_time(
            news_time,
            self.local_timezone,
        )

        record = NewsRecord(
            symbol=self.symbol,
            con_id=self.con_id or 0,
            time_utc=time_utc,
            time_local=time_local,
            provider=provider_code,
            article_id=article_id,
            headline_raw=headline,
            headline=parsed["headline"],
            publisher=parsed["publisher"],
            language=parsed["language"],
        )

        self.raw_records.append(record)

    def historicalNewsEnd(
        self,
        req_id: int,
        has_more: bool,
    ) -> None:
        if req_id != 2001:
            return

        self.has_more = bool(has_more)

        self.records = deduplicate_records(self.raw_records)

        self.log(
            f"历史新闻返回 {len(self.raw_records)} 条；"
            f"去重后 {len(self.records)} 条；"
            f"hasMore={self.has_more}"
        )

        article_count = len(self.records)

        if self.max_articles is not None:
            article_count = min(article_count, self.max_articles)

        self._article_queue = deque(range(article_count))

        if not self._article_queue:
            self._done.set()
            return

        self._request_next_article()

    def _request_next_article(self) -> None:
        if not self._article_queue:
            self._done.set()
            return

        record_index = self._article_queue.popleft()
        record = self.records[record_index]

        req_id = self._next_article_req_id
        self._next_article_req_id += 1

        self._article_req_map[req_id] = record_index

        self.reqNewsArticle(
            reqId=req_id,
            providerCode=record.provider,
            articleId=record.article_id,
            newsArticleOptions=[],
        )

    def _schedule_next_article(self) -> None:
        timer = threading.Timer(
            self.article_request_interval,
            self._request_next_article,
        )
        timer.daemon = True
        timer.start()

    def newsArticle(
        self,
        req_id: int,
        article_type: int,
        article_text: str,
    ) -> None:
        record_index = self._article_req_map.pop(req_id, None)

        if record_index is None:
            return

        record = self.records[record_index]
        record.article_type = article_type

        if article_type == 0:
            record.article_html = article_text or ""
            record.article_text = clean_article_html(article_text)
        else:
            # 非 0 类型可能是其他格式，例如二进制/Base64 数据。
            record.article_text = article_text or ""

        self.log(
            f"正文完成 {record_index + 1}/"
            f"{min(len(self.records), self.max_articles or len(self.records))}: "
            f"{record.headline[:80]}"
        )

        self._schedule_next_article()

    def error(self, req_id: int, *args: Any) -> None:
        if len(args) == 2:
            error_code, error_string = args
        elif len(args) == 3:
            if isinstance(args[1], int):
                _, error_code, error_string = args
            else:
                error_code, error_string, _ = args
        elif len(args) >= 4:
            _, error_code, error_string, _ = args[:4]
        else:
            error_code = -1
            error_string = "Unknown IBKR error callback payload"

        if error_code in STATUS_CODES:
            if self.verbose:
                self.log(f"IBKR状态：{error_code} - {error_string}")
            return

        message = (
            f"IBKR错误：reqId={req_id}, "
            f"code={error_code}, "
            f"message={error_string}"
        )
        self.log(message)

        # 某篇正文读取失败时，记录错误并继续下一篇。
        record_index = self._article_req_map.pop(req_id, None)

        if record_index is not None:
            self.records[record_index].article_error = (
                f"{error_code}: {error_string}"
            )
            self._schedule_next_article()

    # ---------- 对外接口 ----------

    def run_pipeline(self, timeout: float = 120.0) -> pd.DataFrame:
        """
        连接 IB Gateway/TWS，运行完整流水线并返回 DataFrame。
        """
        if self.isConnected():
            raise RuntimeError("当前实例已经连接。")

        self.connect(
            self.host,
            self.port,
            clientId=self.client_id,
        )

        self._api_thread = threading.Thread(
            target=self.run,
            daemon=True,
        )
        self._api_thread.start()

        completed = self._done.wait(timeout=timeout)

        if self.isConnected():
            self.disconnect()

        if not completed:
            raise TimeoutError(
                f"新闻抓取在 {timeout} 秒内未完成。"
                "可以提高 timeout 或减少 max_articles。"
            )

        return self.to_dataframe()

    def to_dataframe(self) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []

        for record in self.records:
            row = asdict(record)
            row["duplicate_items_json"] = json.dumps(
                row.pop("duplicate_items"),
                ensure_ascii=False,
            )
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        dataframe = pd.DataFrame(rows)

        preferred_columns = [
            "symbol",
            "con_id",
            "time_utc",
            "time_local",
            "provider",
            "publisher",
            "language",
            "article_id",
            "headline",
            "duplicate_count",
            "article_type",
            "article_error",
            "article_text",
            "headline_raw",
            "article_html",
            "duplicate_items_json",
        ]

        existing_columns = [
            column
            for column in preferred_columns
            if column in dataframe.columns
        ]

        return dataframe[existing_columns]

    def save_outputs(
        self,
        output_dir: str | Path = "ibkr_news_output",
        *,
        save_csv: bool = True,
        save_jsonl: bool = True,
        save_sqlite: bool = True,
    ) -> dict[str, Path]:
        """
        保存 CSV、JSONL 和 SQLite。
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{self.symbol}_news_{timestamp}"

        result: dict[str, Path] = {}
        dataframe = self.to_dataframe()

        if save_csv:
            csv_path = output_path / f"{stem}.csv"

            # CSV 不保存体积较大的原始 HTML。
            csv_df = dataframe.drop(
                columns=["article_html"],
                errors="ignore",
            )

            csv_df.to_csv(
                csv_path,
                index=False,
                encoding="utf-8-sig",
            )
            result["csv"] = csv_path

        if save_jsonl:
            jsonl_path = output_path / f"{stem}.jsonl"

            with jsonl_path.open("w", encoding="utf-8") as file:
                for record in self.records:
                    file.write(
                        json.dumps(
                            asdict(record),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            result["jsonl"] = jsonl_path

        if save_sqlite:
            sqlite_path = output_path / "ibkr_news.sqlite"

            sqlite_df = dataframe.copy()
            sqlite_df["ingested_at"] = datetime.now(
                timezone.utc
            ).isoformat()

            with sqlite3.connect(sqlite_path) as connection:
                sqlite_df.to_sql(
                    "news",
                    connection,
                    if_exists="append",
                    index=False,
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_news_symbol_time
                    ON news(symbol, time_utc)
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_news_article_id
                    ON news(article_id)
                    """
                )

            result["sqlite"] = sqlite_path

        return result


def main() -> None:
    provider_codes = (
        "BRFG+BRFUPDN+DJ-N+DJ-RTA+"
        "DJ-RTE+DJ-RTG+DJ-RTPRO+DJNL"
    )

    pipeline = IBKRNewsPipeline(
        symbol="ORCL",
        primary_exchange="NYSE",
        provider_codes=provider_codes,
        host="127.0.0.1",
        port=4001,
        client_id=91,
        total_results=100,
        max_articles=30,
        local_timezone="Asia/Taipei",
        verbose=True,
    )

    dataframe = pipeline.run_pipeline(timeout=180)

    if dataframe.empty:
        print("没有取得新闻。")
        return

    print(
        dataframe[
            [
                "time_local",
                "provider",
                "publisher",
                "headline",
                "duplicate_count",
            ]
        ]
        .head(20)
        .to_string(index=False)
    )

    paths = pipeline.save_outputs()

    print("\n输出文件：")
    for file_type, path in paths.items():
        print(f"{file_type}: {path.resolve()}")


if __name__ == "__main__":
    main()
