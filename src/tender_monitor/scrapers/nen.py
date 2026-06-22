"""Scraper pro NEN - nen.nipez.cz

NEN je React SPA - nacita obsah JavaScriptem.
Prepisujeme scrape() abychom se nezasekli na uvodnim goto()
pokud je portal prave nedostupny - kazde klicove slovo ma vlastni pokus.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://nen.nipez.cz/verejne-zakazky"
    "/p:vz:query={keyword}"
)


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"
    max_pages = 3
    per_keyword_timeout_ms = 45_000

    async def scrape(self, browser: Browser) -> ScrapeResult:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
        )
        page = await context.new_page()
        all_tenders: list[Tender] = []
        error_msg = None

        try:
            for keyword in self.keywords:
                url = _SEARCH_URL.format(keyword=quote(keyword))
                logger.info("NEN hledam: '%s'", keyword)
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded",
                        timeout=self.per_keyword_timeout_ms,
                    )
                    await page.wait_for_timeout(6_000)

                    tables = await page.locator("table").count()
                    text_len = len(await page.locator("body").inner_text())
                    logger.info(
                        "NEN '%s': tables=%s text_len=%s", keyword, tables, text_len
                    )

                    # Debug: zobrazime prvni 3 radky tabulky
                    rows_debug = await page.locator("table tr").all_inner_texts()
                    logger.info("NEN '%s': radky tabulky=%s", keyword, len(rows_debug))
                    for i, row in enumerate(rows_debug[:3]):
                        logger.info("NEN radek %s: %s", i, row[:150])

                    # NEN pouziva React - tabulka nemusi mit <th>, zkusime oba varianty
                    batch = await self.collect_table_tenders(page, "//table[.//td]")
                    if not batch:
                        batch = await self.collect_table_tenders(page, "//table")
                    for t in batch:
                        if _is_foreign(t):
                            continue
                        if normalize_text(keyword) not in normalize_text(t.title):
                            continue
                        t.matched_keywords = [keyword]
                        all_tenders.append(t)

                except Exception as exc:
                    logger.warning(
                        "NEN keyword='%s' chyba (pokracuji dal): %s", keyword, exc
                    )
                    error_msg = str(exc)

                await asyncio.sleep(1)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("NEN: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
