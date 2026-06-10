from __future__ import annotations

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper


class EvezaScraper(BaseScraper):
    source = "eVeZa"
    url = "https://www.eveza.cz"

    async def scrape_page(self, page: Page) -> list[Tender]:
        # TODO: doplnit přesné selektory eVeZa po ověření aktuální struktury portálu.
        return await self.collect_link_tenders(page, "a[href]")
