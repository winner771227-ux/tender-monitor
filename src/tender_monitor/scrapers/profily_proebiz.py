from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper


class ProfilyProebizScraper(BaseScraper):
    source = "Profily PROEBIZ"
    url = "https://profily.proebiz.com/verejne-zakazky"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = "//table[.//th[contains(normalize-space(.), 'Název') or contains(normalize-space(.), 'Zadavatel')]]"
        detail_selector = "a[href*='verejne-zakazky'], a[href*='zakazka'], a[href*='detail'], a:has-text('Detail')"

        for _ in range(self.max_pages):
            visited_urls.add(page.url)
            await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            tenders.extend(await self.collect_table_tenders(page, table_xpath, detail_selector=detail_selector))
            tenders.extend(
                await self.collect_card_tenders(
                    page,
                    ".tender, .zakazka, .verejna-zakazka, article, .card, .list-item",
                    detail_selector=detail_selector,
                )
            )
            if not await self.goto_next_page(page, visited_urls):
                self.logger.warning(
                    "PROEBIZ NEXT PAGE FAILED: %s",
                    page.url,
                )
                break

        return self.deduplicate_tenders(tenders)