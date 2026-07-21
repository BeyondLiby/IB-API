from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ResearchItem


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portal TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    status TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    items_seen INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    portal TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_date TEXT NOT NULL,
    url TEXT NOT NULL,
    pages INTEGER,
    authors_json TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (portal, external_id)
);

CREATE TABLE IF NOT EXISTS document_subscriptions (
    portal TEXT NOT NULL,
    external_id TEXT NOT NULL,
    subscription TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    PRIMARY KEY (portal, external_id, subscription),
    FOREIGN KEY (portal, external_id)
        REFERENCES documents (portal, external_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_files (
    portal TEXT NOT NULL,
    external_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    downloaded_at_utc TEXT NOT NULL,
    PRIMARY KEY (portal, external_id),
    FOREIGN KEY (portal, external_id)
        REFERENCES documents (portal, external_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS download_failures (
    portal TEXT NOT NULL,
    external_id TEXT NOT NULL,
    error TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_attempt_at_utc TEXT NOT NULL,
    PRIMARY KEY (portal, external_id),
    FOREIGN KEY (portal, external_id)
        REFERENCES documents (portal, external_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_published_date
    ON documents (portal, published_date);
CREATE INDEX IF NOT EXISTS idx_document_subscriptions_name
    ON document_subscriptions (portal, subscription);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def start_run(self, portal: str, parameters: dict[str, Any]) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO crawl_runs (portal, started_at_utc, status, parameters_json)
            VALUES (?, ?, 'running', ?)
            """,
            (portal, _now(), json.dumps(parameters, ensure_ascii=False)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self, run_id: int, status: str, items_seen: int, error: str | None = None
    ) -> None:
        self.connection.execute(
            """
            UPDATE crawl_runs
            SET finished_at_utc = ?, status = ?, items_seen = ?, error = ?
            WHERE id = ?
            """,
            (_now(), status, items_seen, error, run_id),
        )
        self.connection.commit()

    def upsert_items(self, items: list[ResearchItem]) -> None:
        now = _now()
        with self.connection:
            for item in items:
                raw = json.dumps(item.as_dict(), ensure_ascii=False)
                self.connection.execute(
                    """
                    INSERT INTO documents (
                        portal, external_id, title, summary, published_date, url,
                        pages, authors_json, first_seen_at_utc, last_seen_at_utc,
                        raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (portal, external_id) DO UPDATE SET
                        title = excluded.title,
                        summary = excluded.summary,
                        published_date = excluded.published_date,
                        url = excluded.url,
                        pages = excluded.pages,
                        authors_json = excluded.authors_json,
                        last_seen_at_utc = excluded.last_seen_at_utc,
                        raw_json = excluded.raw_json
                    """,
                    (
                        item.portal,
                        item.external_id,
                        item.title,
                        item.summary,
                        item.published_date,
                        item.url,
                        item.pages,
                        json.dumps(item.authors, ensure_ascii=False),
                        now,
                        now,
                        raw,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO document_subscriptions (
                        portal, external_id, subscription,
                        first_seen_at_utc, last_seen_at_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (portal, external_id, subscription) DO UPDATE SET
                        last_seen_at_utc = excluded.last_seen_at_utc
                    """,
                    (item.portal, item.external_id, item.subscription, now, now),
                )

    def list_documents(
        self,
        portal: str,
        from_date: str | None,
        to_date: str | None,
        subscriptions: list[str] | None,
    ) -> list[dict[str, Any]]:
        clauses = ["d.portal = ?"]
        parameters: list[Any] = [portal]
        join = ""
        if from_date:
            clauses.append("d.published_date >= ?")
            parameters.append(from_date)
        if to_date:
            clauses.append("d.published_date <= ?")
            parameters.append(to_date)
        if subscriptions:
            join = (
                "JOIN document_subscriptions ds "
                "ON ds.portal = d.portal AND ds.external_id = d.external_id"
            )
            placeholders = ", ".join("?" for _ in subscriptions)
            clauses.append(f"ds.subscription IN ({placeholders})")
            parameters.extend(subscriptions)

        rows = self.connection.execute(
            f"""
            SELECT DISTINCT d.external_id, d.title, d.published_date, d.url
            FROM documents d
            {join}
            WHERE {" AND ".join(clauses)}
            ORDER BY d.published_date DESC, d.external_id
            """,
            parameters,
        ).fetchall()
        return [
            {
                "external_id": row[0],
                "title": row[1],
                "published_date": row[2],
                "url": row[3],
            }
            for row in rows
        ]

    def record_file(
        self,
        portal: str,
        external_id: str,
        file_path: str,
        byte_size: int,
        sha256: str,
        content_type: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO document_files (
                portal, external_id, file_path, byte_size, sha256,
                content_type, downloaded_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (portal, external_id) DO UPDATE SET
                file_path = excluded.file_path,
                byte_size = excluded.byte_size,
                sha256 = excluded.sha256,
                content_type = excluded.content_type,
                downloaded_at_utc = excluded.downloaded_at_utc
            """,
            (
                portal,
                external_id,
                file_path,
                byte_size,
                sha256,
                content_type,
                _now(),
            ),
        )
        self.connection.execute(
            "DELETE FROM download_failures WHERE portal = ? AND external_id = ?",
            (portal, external_id),
        )
        self.connection.commit()

    def record_download_failure(
        self, portal: str, external_id: str, error: str
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO download_failures (
                portal, external_id, error, attempt_count, last_attempt_at_utc
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT (portal, external_id) DO UPDATE SET
                error = excluded.error,
                attempt_count = download_failures.attempt_count + 1,
                last_attempt_at_utc = excluded.last_attempt_at_utc
            """,
            (portal, external_id, error, _now()),
        )
        self.connection.commit()

    def get_file(self, portal: str, external_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT file_path, byte_size, sha256, content_type, downloaded_at_utc
            FROM document_files
            WHERE portal = ? AND external_id = ?
            """,
            (portal, external_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "file_path": row[0],
            "byte_size": row[1],
            "sha256": row[2],
            "content_type": row[3],
            "downloaded_at_utc": row[4],
        }

    def file_owner(self, portal: str, file_path: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT external_id
            FROM document_files
            WHERE portal = ? AND file_path = ?
            """,
            (portal, file_path),
        ).fetchone()
        return str(row[0]) if row else None
