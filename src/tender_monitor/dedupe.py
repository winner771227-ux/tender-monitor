from __future__ import annotations

import hashlib
import re
import unicodedata

from tender_monitor.models import Tender

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().strip()
    return _WHITESPACE_RE.sub(" ", normalized)


def tender_fingerprint(tender: Tender) -> str:
    stable_parts = [
        normalize_text(tender.source),
        normalize_text(tender.external_id),
        normalize_text(tender.url),
        normalize_text(tender.title),
        normalize_text(tender.authority),
    ]
    raw_key = "|".join(part for part in stable_parts if part)
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def remove_duplicates(tenders: list[Tender]) -> list[Tender]:
    seen: set[str] = set()
    unique: list[Tender] = []
    for tender in tenders:
        fingerprint = tender_fingerprint(tender)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(tender)
    return unique
