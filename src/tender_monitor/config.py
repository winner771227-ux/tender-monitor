from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_KEYWORDS = (
    "demolice",
    "bourání",
    "odstranění stavby",
    "odstranění staveb",
    "odstranění budovy",
    "odstranění objektu",
    "demoliční práce",
    "demoliční",
    "likvidace stavby",
    "likvidace objektu",
)


@dataclass(frozen=True, slots=True)
class EmailConfig:
    host: str | None
    port: int
    username: str | None
    password: str | None
    sender: str | None
    recipients: tuple[str, ...]
    use_tls: bool

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender and self.recipients)


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = Path("data/tenders.sqlite3")
    report_dir: Path = Path("reports")
    headless: bool = True
    timeout_ms: int = 30_000
    keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_KEYWORDS)
    email: EmailConfig = field(
        default_factory=lambda: EmailConfig(
            host=None,
            port=587,
            username=None,
            password=None,
            sender=None,
            recipients=(),
            use_tls=True,
        )
    )


def load_settings() -> Settings:
    load_dotenv()
    recipients = tuple(
        item.strip()
        for item in os.getenv("SMTP_TO", "").split(",")
        if item.strip()
    )
    email = EmailConfig(
        host=os.getenv("SMTP_HOST") or None,
        port=int(os.getenv("SMTP_PORT", "587") or "587"),
        username=os.getenv("SMTP_USERNAME") or None,
        password=os.getenv("SMTP_PASSWORD") or None,
        sender=os.getenv("SMTP_FROM") or None,
        recipients=recipients,
        use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"},
    )
    return Settings(
        db_path=Path(os.getenv("TENDER_DB_PATH", "data/tenders.sqlite3")),
        report_dir=Path(os.getenv("TENDER_REPORT_DIR", "reports")),
        headless=os.getenv("TENDER_HEADLESS", "true").lower() in {"1", "true", "yes", "on"},
        timeout_ms=int(os.getenv("TENDER_TIMEOUT_MS", "30000") or "30000"),
        keywords=DEFAULT_KEYWORDS,
        email=email,
    )
