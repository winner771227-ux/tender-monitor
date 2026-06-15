"""Scraper pro Vhodné uveřejnění – vhodne-uverejneni.cz

Agregátor 100 % veřejných zakázek ze všech CZ profilů zadavatelů.
Má funkční fulltextové vyhledávání s filtrem data.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

# Vyhledávání na Vhodné uveřejnění
_SEARCH_URL = (
    "https://vhodne-uverejneni.cz/katalog/zakazky"
    "?q={keyword}&date_from={date_from}&order=date_desc"
)


class VhodneUverejneniScraper(BaseScraper):
    source = "Vhodné uveřejnění"
    url = "https://vhodne-uverejneni.cz/katalog/zakazky"
    max_pages = 5

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        for keyword in self.keywords:
            url = _SEARCH_URL.format(keyword=quote(keyword), date_from=date_from)
            logger.info("VhodneUverejneni hledám: '%s' od %s", keyword, date_from)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2_000)
                batch = await self._scrape_keyword(page, keyword)
                logger.info("VhodneUverejneni keyword='%s' nalezeno=%s", keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("VhodneUverejneni keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_keyword(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited: set[str] = set()

        for page_num in range(self.max_pages):
            if page.url in visited:
                break
            visited.add(page.url)

            tables = await page.locator("table").count()
            text_len = len(await page.locator("body").inner_text())
            logger.info("VhodneUverejneni str.%s: tables=%s text_len=%s",
                       page_num + 1, tables, text_len)

            # Tabulkový výpis
            batch = await self.collect_table_tenders(page, "//table[.//th or .//td]")
            # Kartový výpis
            card_batch = await self.collect_card_tenders(
                page,
                ".contract-item, .tender-item, .zakazka, article, "
                ".list-item, .search-result, .result"
            )

            for t in batch + card_batch:
                if _is_foreign(t):
                    continue
                if normalize_text(keyword) not in normalize_text(t.title):
                    continue
                t.matched_keywords = [keyword]
                logger.info("VhodneUverejneni ✅ [%s] '%s'", keyword, t.title[:50])
                tenders.append(t)

            if not await self.goto_next_page(page, visited):
                break

        return tenders
