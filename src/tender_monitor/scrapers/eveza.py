"""Scraper pro eVeZa – eveza.cz

eVeZa používá ASP.NET WebForms se stránkováním přes __doPostBack – 
proto nelze použít normální goto_next_page. Načteme jen první stránku výsledků.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.eveza.cz/verejne-zakazky/?s={keyword}"


class EvezaScraper(BaseScraper):
    source = "eVeZa"
    url = "https://www.eveza.cz/verejne-zakazky/"
    max_pages = 1  # eVeZa používá JS stránkování – bereme jen první stránku

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []

        for keyword in self.keywords:
            url = _SEARCH_URL.format(keyword=quote(keyword))
            logger.info("eVeZa hledám: '%s'", keyword)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                await page.wait_for_selector("body", state="attached", timeout=20_000)
                table_xpath = "//table[.//th]"
                batch = await self.collect_table_tenders(page, table_xpath)
                card_batch = await self.collect_card_tenders(
                    page, ".zakazka, article, .card, .tender, .list-item"
                )
                found = []
                for t in batch + card_batch:
                    if _is_foreign(t):
                        continue
                    if normalize_text(keyword) not in normalize_text(t.title):
                        continue
                    t.matched_keywords = [keyword]
                    found.append(t)
                logger.info("eVeZa keyword='%s' nalezeno=%s", keyword, len(found))
                all_tenders.extend(found)
            except Exception as exc:
                logger.warning("eVeZa keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)
