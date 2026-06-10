from __future__ import annotations

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = "//table[.//th[contains(normalize-space(.), 'Název zadávacího postupu')]]"

        for _ in range(self.max_pages):
            visited_urls.add(page.url)
            await page.wait_for_selector(
                "xpath=//table[.//th[contains(normalize-space(.), 'Systémové číslo NEN')]]//tr[td]",
                state="attached",
                timeout=self.timeout_ms,
            )
            tenders.extend(await self.collect_table_tenders(page, table_xpath, detail_selector="a:has-text('Detail')"))
            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
