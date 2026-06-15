"""Scraper pro NEN – nen.nipez.cz

NEN je React SPA – načítá obsah JavaScriptem.
Hledáme přes URL s parametrem pro klíčové slovo.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

# NEN URL s vyhledáváním - seřazeno podle data zveřejnění
_SEARCH_URL = (
    "https://nen.nipez.cz/verejne-zakazky"
    "?nazevZakazky={keyword}&stavZadavacihoPostupu=zahajen"
)


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"
    max_pages = 3

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []

        for keyword in self.keywords:
            url = _SEARCH_URL.format(keyword=quote(keyword))
            logger.info("NEN hledám: '%s'", keyword)
            try:
                # NEN potřebuje delší timeout – je to React SPA
                await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                # Čekáme na načtení JavaScriptu
                await page.wait_for_timeout(8_000)

                table_xpath = "//table[.//th]"
                tables = await page.locator("table").count()
                text_len = len(await page.locator("body").inner_text())
                logger.info("NEN '%s': tables=%s text_len=%s", keyword, tables, text_len)

                batch = await self.collect_table_tenders(page, table_xpath)
                found = []
                for t in batch:
                    if _is_foreign(t): continue
                    if normalize_text(keyword) not in normalize_text(t.title): continue
                    t.matched_keywords = [keyword]
                    found.append(t)

                logger.info("NEN keyword='%s' nalezeno=%s", keyword, len(found))
                all_tenders.extend(found)
            except Exception as exc:
                logger.warning("NEN keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(2)

        return self.deduplicate_tenders(all_tenders)
