from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class Tender:
    """Normalized public tender record."""

    source: str
    title: str
    url: str
    authority: str | None = None
    published_at: str | None = None
    deadline_at: str | None = None
    description: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    external_id: str | None = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class ScrapeResult:
    """Result returned by one scraper run."""

    source: str
    tenders: list[Tender]
    error: str | None = None
