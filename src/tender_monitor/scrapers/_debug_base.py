"""Společná základna pro portálové scrapery s vyhledáváním a debug logováním."""
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

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


class SearchScraper(BaseScraper):
    """Základní třída pro scrapery používající URL vyhledávání."""
    search_url_template: str = ""  # Přepište v podtřídě, {keyword} bude nahrazeno
    max_pages = 3

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        date_from = (datetime.now() - timedelta(days=30)).strftime("%d.%m.%Y")

        for keyword in self.keywords:
            url = self.search_url_template.format(
                keyword=quote(keyword), date_from=date_from
            )
            logger.info("%s hledám: '%s' od %s -> %s", self.source, keyword, date_from, url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2_000)  # Počkáme na JS rendering
                batch = await self._scrape_page_results(page, keyword)
                logger.info("%s keyword='%s' nalezeno=%s", self.source, keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("%s keyword='%s' chyba: %s", self.source, keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_page_results(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited: set[str] = set()

        for page_num in range(self.max_pages):
            if page.url in visited:
                break
            visited.add(page.url)

            # Debug: co je na stránce?
            tables = await page.locator("table").count()
            links = await page.locator("a[href]").count()
            text_len = len(await page.locator("body").inner_text())
            logger.info("%s str.%s: tables=%s links=%s text_len=%s url=%s",
                       self.source, page_num+1, tables, links, text_len, page.url[:80])

            # Zkusíme tabulky
            if tables > 0:
                batch = await self.collect_table_tenders(page, "//table[.//th or .//td]")
                logger.info("%s str.%s: z tabulky=%s", self.source, page_num+1, len(batch))
                for t in batch:
                    if _is_foreign(t): continue
                    if normalize_text(keyword) not in normalize_text(t.title): continue
                    t.matched_keywords = [keyword]
                    tenders.append(t)

            # Zkusíme karty/seznam
            card_batch = await self.collect_card_tenders(
                page,
                ".contract-item, .tender-item, .zakazka, .item, article, li.item, "
                ".list-group-item, .search-result, .result-item"
            )
            logger.info("%s str.%s: z karet=%s", self.source, page_num+1, len(card_batch))
            for t in card_batch:
                if _is_foreign(t): continue
                if normalize_text(keyword) not in normalize_text(t.title): continue
                t.matched_keywords = [keyword]
                tenders.append(t)

            if not await self.goto_next_page(page, visited):
                break

        return tenders
