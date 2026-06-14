"""Scraper for E-ZAKAZKY."""
from __future__ import annotations

import logging

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, DETAIL_LINK_SELECTOR

logger = logging.getLogger(__name__)


class EZakazkyScraper(BaseScraper):
    source = "E-ZAKAZKY"
    url = "https://www.e-zakazky.cz"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = "//table[.//th[contains(normalize-space(.), 'Název') or contains(normalize-space(.), 'Zadavatel')]]"
        detail_selector = "a[href*='verejne-zakazky'], a[href*='zakazka'], a[href*='detail'], a:has-text('Detail')"

        for _ in range(self.max_pages):
            if len(tenders) >= self.max_tenders:
                break
            visited_urls.add(page.url)
            await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            batch = await self.collect_table_tenders(page, table_xpath, detail_selector=detail_selector)
            tenders.extend(batch)
            card_batch = await self.collect_card_tenders(
                page, ".zakazka, .verejna-zakazka, .public-contract, article, .card, .list-item", detail_selector=detail_selector
            )
            tenders.extend(card_batch)
            logger.info("E-ZAKAZKY page=%s batch=%s total=%s", page.url, len(batch) + len(card_batch), len(tenders))
            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
