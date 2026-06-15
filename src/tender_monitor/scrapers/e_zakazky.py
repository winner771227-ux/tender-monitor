"""Scraper pro E-ZAKAZKY – e-zakazky.cz"""
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

_SEARCH_URL = "https://www.e-zakazky.cz/Vyhledavani?Predmet={keyword}&DatumUverejneniOd={date_from}"


class EZakazkyScraper(BaseScraper):
    source = "E-ZAKAZKY"
    url = "https://www.e-zakazky.cz/Vyhledavani"
    max_pages = 5

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        date_from = (datetime.now() - timedelta(days=30)).strftime("%d.%m.%Y")

        for keyword in self.keywords:
            url = _SEARCH_URL.format(keyword=quote(keyword), date_from=date_from)
            logger.info("E-ZAKAZKY hledám: '%s' od %s", keyword, date_from)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                batch = await self._scrape_keyword(page, keyword)
                logger.info("E-ZAKAZKY keyword='%s' nalezeno=%s", keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("E-ZAKAZKY keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_keyword(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited: set[str] = set()
        table_xpath = "//table[.//th]"

        for _ in range(self.max_pages):
            if page.url in visited:
                break
            visited.add(page.url)
            try:
                await page.wait_for_selector("body", state="attached", timeout=20_000)
            except Exception:
                break

            batch = await self.collect_table_tenders(page, table_xpath)
            logger.info("%s page=%s batch=%s", self.source, page.url, len(batch))
            for t in batch:
                if _is_foreign(t):
                    continue
                if normalize_text(keyword) not in normalize_text(t.title):
                    continue
                t.matched_keywords = [keyword]
                tenders.append(t)

            if not await self.goto_next_page(page, visited):
                break

        return tenders
