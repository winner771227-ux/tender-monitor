"""Scraper pro Profil zadavatele VZ – profilzadavatele-vz.cz"""
from __future__ import annotations

import logging

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, DETAIL_LINK_SELECTOR

logger = logging.getLogger(__name__)


class ProfilZadavateleVzScraper(BaseScraper):
    source = "Profil zadavatele VZ"
    url = "https://www.profilzadavatele-vz.cz/seznam-profilu/"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        table_xpath = (
            "//table[.//th[contains(normalize-space(.), 'Název') "
            "or contains(normalize-space(.), 'Zadavatel')]]"
        )

        for _ in range(self.max_pages):
            if len(tenders) >= self.max_tenders:
                break
            visited_urls.add(page.url)
            try:
                await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            except Exception:
                break

            batch = await self.collect_table_tenders(page, table_xpath, detail_selector=DETAIL_LINK_SELECTOR)
            tenders.extend(batch)
            card_batch = await self.collect_card_tenders(
                page,
                ".tender, .zakazka, .verejna-zakazka, .contract, article, .card, .list-item",
                detail_selector=DETAIL_LINK_SELECTOR,
            )
            tenders.extend(card_batch)
            logger.info("Profil VZ page=%s batch=%s total=%s", page.url, len(batch)+len(card_batch), len(tenders))
            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
