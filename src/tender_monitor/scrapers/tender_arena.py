"""Scraper pro Tender Arena - tenderarena.cz

Tender Arena ma verejne REST API:
POST https://www.tenderarena.cz/dodavatel/chytre-vyhledavani/vyhledat
Vraci JSON s polem 'polozky'.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from datetime import datetime

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_API_URL = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani/vyhledat"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani",
    "Origin": "https://www.tenderarena.cz",
}

MAX_PER_KEYWORD = 10


def _api_search(keyword: str) -> list[dict]:
    """Synchronní volání TenderArena API."""
    payload = json.dumps({
        "dotaz": keyword,
        "strankovani": {"stranka": 1, "pocetNaStranku": MAX_PER_KEYWORD},
    }).encode("utf-8")

    req = urllib.request.Request(_API_URL, data=payload, headers=_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("polozky", [])


class TenderArenaScraper(BaseScraper):
    source = "TenderArena"
    url = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani"

    async def scrape(self, browser: Browser) -> ScrapeResult:
        all_tenders: list[Tender] = []
        error_msg = None

        for keyword in self.keywords:
            logger.info("TenderArena hledam: '%s'", keyword)
            try:
                # Voláme API v thread poolu aby neblokovalo event loop
                loop = asyncio.get_event_loop()
                polozky = await loop.run_in_executor(None, _api_search, keyword)

                logger.info("TenderArena '%s': polozky=%s", keyword, len(polozky))

                found_kw = 0
                for item in polozky:
                    if found_kw >= MAX_PER_KEYWORD:
                        break

                    title = item.get("nazev", "").strip()
                    if not title:
                        continue

                    # Klíčové slovo musí být v názvu
                    if normalize_text(keyword) not in normalize_text(title):
                        continue

                    external_id = item.get("idProZadavatele", "")
                    row_url = (
                        f"https://www.tenderarena.cz/dodavatel/zakazka/detail/{external_id}"
                        if external_id else ""
                    )
                    if not row_url:
                        continue

                    authority = item.get("nazevZadavatele", "").strip() or None

                    deadline = None
                    lhuta_raw = item.get("lhutaProPodaniNabidek")
                    if lhuta_raw:
                        try:
                            dt = datetime.fromisoformat(lhuta_raw.replace("Z", "+00:00"))
                            deadline = dt.strftime("%d.%m.%Y %H:%M")
                        except Exception:
                            deadline = lhuta_raw[:16]

                    t = Tender(
                        source=self.source,
                        title=title,
                        url=row_url,
                        authority=authority,
                        published_at=None,
                        deadline_at=deadline,
                        external_id=external_id or None,
                    )

                    if _is_foreign(t):
                        continue

                    t.matched_keywords = [keyword]
                    logger.info("TenderArena [%s] nalezena: '%s'", keyword, t.title[:60])
                    all_tenders.append(t)
                    found_kw += 1

                logger.info("TenderArena '%s': found=%s", keyword, found_kw)

            except Exception as exc:
                logger.warning("TenderArena keyword='%s' chyba: %s", keyword, exc)
                error_msg = str(exc)

            await asyncio.sleep(0.5)

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("TenderArena: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
