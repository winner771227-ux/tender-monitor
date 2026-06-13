from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# VVZ uses ISO dates in its data attributes / text
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


class VvzScraper(BaseScraper):
    """Scraper for Věstník veřejných zakázek (VVZ) – https://vvz.nipez.cz."""

    source = "VVZ"
    url = "https://vvz.nipez.cz/form/SearchForm/searchPublicContracts"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()

        # VVZ má tabulkový výpis zakázek
        table_xpath = (
            "//table[.//th[contains(normalize-space(.), 'Název') "
            "or contains(normalize-space(.), 'Předmět')]]"
        )
        detail_selector = (
            "a[href*='searchPublicContracts'], "
            "a[href*='detail'], "
            "a[href*='Detail'], "
            "a:has-text('Detail')"
        )

        logger.warning("VVZ START %s", self.url)

        for _ in range(self.max_pages):
            visited_urls.add(page.url)

            try:
                await page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            except Exception:
                logger.warning("VVZ: body wait timeout on %s", page.url)
                break

            # Zkus tabulkový layout
            table_tenders = await self.collect_table_tenders(
                page,
                table_xpath,
                detail_selector=detail_selector,
                open_detail=False,
            )
            tenders.extend(table_tenders)

            # Zkus kartový layout (záložní)
            card_tenders = await self.collect_card_tenders(
                page,
                ".vz-row, .contract-row, .list-item, article, .card, .tender",
                detail_selector=detail_selector,
                open_detail=False,
            )
            tenders.extend(card_tenders)

            logger.warning(
                "VVZ PAGE %s: table=%s cards=%s",
                page.url,
                len(table_tenders),
                len(card_tenders),
            )

            if not await self.goto_next_page(page, visited_urls):
                break

        unique = self.deduplicate_tenders(tenders)
        logger.warning("VVZ TOTAL UNIQUE=%s", len(unique))
        return unique