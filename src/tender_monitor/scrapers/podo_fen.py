"""Scraper pro Portál Dodavatele FEN – podo.fen.cz"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign, ScrapeResult

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

# Alternativní přístupy k FEN
_URLS = [
    "https://podo.fen.cz/verejne-zakazky?nazev={keyword}&stav=aktivni",
    "https://www.vhodne-uverejneni.cz/katalog/zakazky?q={keyword}",
]


class PodoFenScraper(BaseScraper):
    source = "Portál FEN"
    url = "https://podo.fen.cz/verejne-zakazky"
    max_pages = 3

    async def scrape(self, browser) -> ScrapeResult:
        """Přepíšeme scrape() aby při timeout vrátil prázdný výsledek místo chyby."""
        from playwright.async_api import TimeoutError as PWTimeout
        try:
            return await super().scrape(browser)
        except (PWTimeout, Exception) as exc:
            logger.warning("FEN: přeskakuji kvůli chybě: %s", exc)
            return ScrapeResult(source=self.source, tenders=[], error=str(exc))

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []

        for keyword in self.keywords:
            url = _URLS[0].format(keyword=quote(keyword))
            logger.info("FEN hledám: '%s'", keyword)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_selector("body", state="attached", timeout=30_000)

                table_xpath = "//table[.//th]"
                batch = await self.collect_table_tenders(page, table_xpath)
                card_batch = await self.collect_card_tenders(
                    page, ".contract-item, .tender-item, .zakazka, article, .card, .list-item"
                )
                found = []
                for t in batch + card_batch:
                    if _is_foreign(t):
                        continue
                    if normalize_text(keyword) not in normalize_text(t.title):
                        continue
                    t.matched_keywords = [keyword]
                    found.append(t)
                logger.info("FEN keyword='%s' nalezeno=%s", keyword, len(found))
                all_tenders.extend(found)
            except Exception as exc:
                logger.warning("FEN keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)
