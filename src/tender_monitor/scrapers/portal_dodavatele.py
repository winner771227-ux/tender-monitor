"""Scraper pro Portál Dodavatele – portaldodavatele.cz

Nástupce Vhodného uveřejnění – agreguje všechny CZ veřejné zakázky.
Vyhledávání podle klíčových slov s filtrem data.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

# Vyhledávací URL – zkusíme různé varianty parametrů
_SEARCH_URLS = [
    "https://portaldodavatele.cz/verejne-zakazky?search={keyword}&date_from={date_from}",
    "https://portaldodavatele.cz/verejne-zakazky?q={keyword}&dateFrom={date_from}",
    "https://portaldodavatele.cz/verejne-zakazky?nazev={keyword}",
]


class PortalDodavateleScraper(BaseScraper):
    source = "Portál Dodavatele"
    url = "https://portaldodavatele.cz/verejne-zakazky"
    max_pages = 3

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        # Nejdřív zjistíme jak stránka funguje
        try:
            r = await page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)
            text = await page.locator("body").inner_text()
            tables = await page.locator("table").count()
            inputs = await page.locator("input[type='text'], input[type='search']").count()
            logger.info("PortalDodavatele homepage: status=%s tables=%s inputs=%s text_len=%s",
                       r.status if r else "?", tables, inputs, len(text))
            logger.info("PortalDodavatele preview: %s", text[:400].replace("\n", " "))
        except Exception as exc:
            logger.warning("PortalDodavatele: homepage nedostupná: %s", exc)
            return []

        for keyword in self.keywords:
            # Zkusíme první URL variantu
            url = _SEARCH_URLS[0].format(keyword=quote(keyword), date_from=date_from)
            logger.info("PortalDodavatele hledám: '%s'", keyword)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(4_000)

                tables = await page.locator("table").count()
                text_len = len(await page.locator("body").inner_text())
                logger.info("PortalDodavatele '%s': tables=%s text_len=%s url=%s",
                           keyword, tables, text_len, page.url[:80])

                batch = await self.collect_table_tenders(page, "//table[.//th or .//td]")
                card_batch = await self.collect_card_tenders(
                    page,
                    ".contract-item, .tender-item, .zakazka, article, .card, "
                    ".list-item, .search-result, [class*='contract'], [class*='tender']"
                )

                found = []
                for t in batch + card_batch:
                    if _is_foreign(t): continue
                    if normalize_text(keyword) not in normalize_text(t.title): continue
                    t.matched_keywords = [keyword]
                    logger.info("PortalDodavatele ✅ [%s] '%s'", keyword, t.title[:50])
                    found.append(t)

                logger.info("PortalDodavatele keyword='%s' nalezeno=%s", keyword, len(found))
                all_tenders.extend(found)
            except Exception as exc:
                logger.warning("PortalDodavatele keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)
