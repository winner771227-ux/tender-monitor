"""SQLite storage for tender runs and results."""
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
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def start_run(self) -> int:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            cursor = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, ?)", (now, "running"))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, total_found: int, error: str | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, status=?, total_found=?, error=? WHERE id=?",
                (now, status, total_found, error, run_id),
            )

    def save_results(self, run_id: int, results: list[ScrapeResult]) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            for result in results:
                for tender in result.tenders:
                    fp = tender_fingerprint(tender)
                    conn.execute(
                        """
                        INSERT INTO tenders (
                            fingerprint, source, external_id, title, url, authority,
                            published_at, deadline_at, description, matched_keywords,
                            first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(fingerprint) DO UPDATE SET
                            title=excluded.title, url=excluded.url, authority=excluded.authority,
                            published_at=excluded.published_at, deadline_at=excluded.deadline_at,
                            description=excluded.description, matched_keywords=excluded.matched_keywords,
                            last_seen_at=excluded.last_seen_at
                        """,
                        (
                            fp, tender.source, tender.external_id, tender.title,
                            tender.url, tender.authority, tender.published_at,
                            tender.deadline_at, tender.description,
                            ", ".join(tender.matched_keywords), now, now,
                        ),
                    )
                    row = conn.execute("SELECT id FROM tenders WHERE fingerprint=?", (fp,)).fetchone()
                    conn.execute(
                        "INSERT INTO tender_history (tender_id, run_id, seen_at) VALUES (?, ?, ?)",
                        (row["id"], run_id, now),
                    )

    def list_tenders_for_run(self, run_id: int) -> list[sqlite3.Row]:
        """Return only tenders found in this specific run (for the daily report)."""
        with self.connect() as conn:
            return list(conn.execute(
                """
                SELECT DISTINCT t.*
                FROM tenders t
                JOIN tender_history h ON h.tender_id = t.id
                WHERE h.run_id = ?
                ORDER BY COALESCE(t.published_at, t.last_seen_at) DESC
                """,
                (run_id,),
            ))

    def list_all_tenders(self) -> list[sqlite3.Row]:
        """Return all tenders ever stored (historical view)."""
        with self.connect() as conn:
            return list(conn.execute(
                "SELECT * FROM tenders ORDER BY COALESCE(published_at, last_seen_at) DESC"
            ))
