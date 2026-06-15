from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import ArticleContent, NewsAnalysis, NewsHeadline, utc_now_iso


class SQLiteNewsStorage:
    """SQLite 存储。第一版够轻，后续可替换成 PostgreSQL。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_raw (
                    unique_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    ticker_id INTEGER,
                    con_id INTEGER,
                    symbol TEXT NOT NULL,
                    published_at TEXT,
                    received_at TEXT,
                    headline_raw TEXT,
                    headline TEXT NOT NULL,
                    publisher TEXT,
                    extra_data TEXT
                );

                CREATE TABLE IF NOT EXISTS news_articles (
                    unique_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    article_html TEXT,
                    article_text TEXT,
                    publisher TEXT,
                    fetch_status TEXT,
                    fetched_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS news_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unique_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    headline TEXT,
                    event_type TEXT,
                    event_subtypes TEXT,
                    relevance_score INTEGER,
                    event_score INTEGER,
                    novelty_score INTEGER,
                    source_score INTEGER,
                    market_impact_score INTEGER,
                    importance_score INTEGER,
                    sentiment REAL,
                    summary_zh TEXT,
                    reason_important TEXT,
                    story_id TEXT,
                    should_push INTEGER,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS news_push_log (
                    push_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unique_key TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    pushed_at TEXT,
                    push_status TEXT,
                    response TEXT
                );

                CREATE TABLE IF NOT EXISTS news_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_news_raw_symbol_time
                ON news_raw(symbol, published_at);

                CREATE INDEX IF NOT EXISTS idx_news_events_story
                ON news_events(story_id);
                """
            )
            self._ensure_column(connection, "news_events", "headline", "TEXT")

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        """简单迁移：老库缺少新字段时自动补列。"""
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {row["name"] for row in rows}
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
            )

    def save_raw(self, event: NewsHeadline) -> bool:
        """保存原始标题。返回 False 表示 provider+article_id 已存在。"""
        with self.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO news_raw (
                        unique_key, provider, article_id, ticker_id, con_id, symbol,
                        published_at, received_at, headline_raw, headline, publisher, extra_data
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.unique_key,
                        event.provider,
                        event.article_id,
                        event.ticker_id,
                        event.con_id,
                        event.symbol,
                        event.published_at,
                        event.received_at,
                        event.headline_raw,
                        event.headline,
                        event.publisher,
                        event.extra_data,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def save_article(self, article: ArticleContent) -> None:
        unique_key = f"{article.provider}:{article.article_id}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO news_articles (
                    unique_key, provider, article_id, article_html, article_text,
                    publisher, fetch_status, fetched_at, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unique_key,
                    article.provider,
                    article.article_id,
                    article.article_html,
                    article.article_text,
                    article.publisher,
                    article.fetch_status,
                    article.fetched_at,
                    article.error,
                ),
            )

    def save_event(self, analysis: NewsAnalysis) -> int:
        unique_key = f"{analysis.provider}:{analysis.article_id}"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO news_events (
                    unique_key, symbol, provider, article_id, headline,
                    event_type, event_subtypes, relevance_score, event_score, novelty_score, source_score,
                    market_impact_score, importance_score, sentiment, summary_zh,
                    reason_important, story_id, should_push, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unique_key,
                    analysis.symbol,
                    analysis.provider,
                    analysis.article_id,
                    analysis.headline,
                    analysis.event_type,
                    json.dumps(analysis.event_subtypes, ensure_ascii=False),
                    analysis.relevance_score,
                    analysis.event_score,
                    analysis.novelty_score,
                    analysis.source_score,
                    analysis.market_impact_score,
                    analysis.importance_score,
                    analysis.sentiment,
                    json.dumps(analysis.summary_zh, ensure_ascii=False),
                    analysis.reason_important,
                    analysis.story_id,
                    int(analysis.should_push),
                    utc_now_iso(),
                ),
            )
            return int(cursor.lastrowid)

    def save_push_log(
        self,
        unique_key: str,
        channel: str,
        status: str,
        response: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO news_push_log (
                    unique_key, channel, pushed_at, push_status, response
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (unique_key, channel, utc_now_iso(), status, response),
            )

    def set_state(self, key: str, value: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO news_state (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM news_state WHERE key = ?",
                (key,),
            ).fetchone()
        return json.loads(row["value"]) if row else default

    def fetch_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM news_events
                ORDER BY event_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
