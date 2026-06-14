"""Scraper pro VVZ – Věstník veřejných zakázek (vvz.nipez.cz)."""
from __future__ import annotations

import logging

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, DETAIL_LINK_SELECTOR

logger = logging.getLogger(__name__)


class VvzScraper(BaseScraper):
    source = "VVZ"
    # Hlavní stránka VVZ s výpisem zakázek
    url = "https://vvz.nipez.cz/verejne-zakazky?druh=VS&stav=aktivni"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()

        # VVZ má tabulku se sloupci Název, Zadavatel, Datum uveřejnění atd.
        table_xpath = (
            "//table[.//th[contains(normalize-space(.), 'Název') "
            "or contains(normalize-space(.), 'Předmět') "
            "or contains(normalize-space(.), 'Zadavatel')]]"
        )
        detail_selector = (
            "a[href*='/verejne-zakazky/'], a[href*='detail'], "
            "a[href*='Detail'], a:has-text('Detail'), a:has-text('Zobrazit')"
        )

        for _ in range(self.max_pages):
            if len(tenders) >= self.max_tenders:
                break
            visited_urls.add(page.url)

            try:
                await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            except Exception:
                logger.warning("VVZ: timeout na %s", page.url)
                break

            batch = await self.collect_table_tenders(
                page, table_xpath, detail_selector=detail_selector, open_detail=False
            )
            tenders.extend(batch)

            card_batch = await self.collect_card_tenders(
                page,
                ".vz-row, .contract-row, .list-item, article, .card, .tender",
                detail_selector=detail_selector,
                open_detail=False,
            )
            tenders.extend(card_batch)

            logger.info("VVZ page=%s batch=%s total=%s", page.url, len(batch) + len(card_batch), len(tenders))

            if not await self.goto_next_page(page, visited_urls):
                break

        return self.deduplicate_tenders(tenders)
