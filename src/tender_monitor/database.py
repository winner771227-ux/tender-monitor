from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tender_monitor.dedupe import tender_fingerprint
from tender_monitor.models import ScrapeResult, Tender

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    total_found INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    authority TEXT,
    published_at TEXT,
    deadline_at TEXT,
    description TEXT,
    matched_keywords TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tender_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    FOREIGN KEY (tender_id) REFERENCES tenders(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_tenders_source ON tenders(source);
CREATE INDEX IF NOT EXISTS idx_tender_history_run_id ON tender_history(run_id);
"""


class TenderDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def start_run(self) -> int:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, ?)",
                (now, "running"),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, total_found: int, error: str | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, total_found = ?, error = ?
                WHERE id = ?
                """,
                (now, status, total_found, error, run_id),
            )

    def save_results(self, run_id: int, results: list[ScrapeResult]) -> list[Tender]:
        saved: list[Tender] = []
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            for result in results:
                for tender in result.tenders:
                    fingerprint = tender_fingerprint(tender)
                    matched_keywords = ", ".join(tender.matched_keywords)
                    connection.execute(
                        """
                        INSERT INTO tenders (
                            fingerprint, source, external_id, title, url, authority,
                            published_at, deadline_at, description, matched_keywords,
                            first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(fingerprint) DO UPDATE SET
                            title = excluded.title,
                            url = excluded.url,
                            authority = excluded.authority,
                            published_at = excluded.published_at,
                            deadline_at = excluded.deadline_at,
                            description = excluded.description,
                            matched_keywords = excluded.matched_keywords,
                            last_seen_at = excluded.last_seen_at
                        """,
                        (
                            fingerprint,
                            tender.source,
                            tender.external_id,
                            tender.title,
                            tender.url,
                            tender.authority,
                            tender.published_at,
                            tender.deadline_at,
                            tender.description,
                            matched_keywords,
                            now,
                            now,
                        ),
                    )
                    row = connection.execute(
                        "SELECT id FROM tenders WHERE fingerprint = ?",
                        (fingerprint,),
                    ).fetchone()
                    connection.execute(
                        "INSERT INTO tender_history (tender_id, run_id, seen_at) VALUES (?, ?, ?)",
                        (row["id"], run_id, now),
                    )
                    saved.append(tender)
        return saved

    def list_tenders(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM tenders
                    ORDER BY COALESCE(deadline_at, published_at, last_seen_at) DESC
                    """
                )
            )
