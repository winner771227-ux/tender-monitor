from __future__ import annotations

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, DETAIL_LINK_SELECTOR


class EZakazkyScraper(BaseScraper):
    source = "E-ZAKAZKY"
    url = "https://www.e-zakazky.cz"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = "//table[.//th[contains(normalize-space(.), 'Název') or contains(normalize-space(.), 'Zadavatel')]]"

        for _ in range(self.max_pages):
            visited_urls.add(page.url)
            await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            tenders.extend(await self.collect_table_tenders(page, table_xpath, detail_selector=DETAIL_LINK_SELECTOR))
            tenders.extend(
                await self.collect_card_tenders(
                    page,
                    ".zakazka, .verejna-zakazka, .public-contract, article, .card, .list-item",
                    detail_selector=DETAIL_LINK_SELECTOR,
                )
            )
            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
