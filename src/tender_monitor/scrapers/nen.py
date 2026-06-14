"""Scraper pro NEN – Národní elektronický nástroj (nen.nipez.cz)."""
from __future__ import annotations

import logging

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = "//table[.//th[contains(normalize-space(.), 'Název zadávacího postupu')]]"

        # NEN používá JavaScript – počkáme na tabulku (max 60s)
        try:
            await page.wait_for_selector(
                f"xpath={table_xpath}",
                state="attached",
                timeout=60_000,
            )
        except Exception:
            logger.warning("NEN: tabulka se nenačetla – přeskakuji")
            return []

        for _ in range(self.max_pages):
            if len(tenders) >= self.max_tenders:
                break
            visited_urls.add(page.url)

            batch = await self.collect_table_tenders(
                page, table_xpath, detail_selector="a:has-text('Detail')", open_detail=False
            )
            tenders.extend(batch)
            logger.info("NEN page=%s batch=%s total=%s", page.url, len(batch), len(tenders))

            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
