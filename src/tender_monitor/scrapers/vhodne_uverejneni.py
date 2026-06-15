"""Scraper pro Vhodné uveřejnění – vhodne-uverejneni.cz

Agregátor 100 % veřejných zakázek ze všech CZ profilů zadavatelů.
Stránka používá JavaScript lazy loading – čekáme na plné načtení.
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

_SEARCH_URL = (
    "https://vhodne-uverejneni.cz/katalog/zakazky"
    "?q={keyword}&date_from={date_from}&order=date_desc"
)


class VhodneUverejneniScraper(BaseScraper):
    source = "Vhodné uveřejnění"
    url = "https://vhodne-uverejneni.cz/katalog/zakazky"
    max_pages = 3

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        for keyword in self.keywords:
            url = _SEARCH_URL.format(keyword=quote(keyword), date_from=date_from)
            logger.info("VhodneUverejneni hledám: '%s' od %s", keyword, date_from)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                # Čekáme déle – stránka načítá výsledky Javascriptem
                await page.wait_for_timeout(5_000)

                batch = await self._scrape_keyword(page, keyword)
                logger.info("VhodneUverejneni keyword='%s' nalezeno=%s", keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("VhodneUverejneni keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_keyword(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited: set[str] = set()

        for page_num in range(self.max_pages):
            current_url = page.url
            if current_url in visited:
                break
            visited.add(current_url)

            text = await page.locator("body").inner_text()
            tables = await page.locator("table").count()
            logger.info("VhodneUverejneni str.%s: tables=%s text_len=%s url=%s",
                       page_num + 1, tables, len(text), current_url[:80])

            # Vypíšeme prvních 500 znaků pro debug
            if page_num == 0:
                logger.info("VhodneUverejneni preview: %s", text[:500].replace("\n", " "))

            # Různé selektory podle toho co stránka používá
            card_selectors = [
                # Běžné kartové layouty
                ".contract-item", ".tender-item", ".zakazka-item",
                # Generické
                "article", ".card", ".item",
                # Seznam
                ".list-group-item", ".search-result", ".result-item",
                # Možné třídy na vhodne-uverejneni.cz
                "[class*='contract']", "[class*='zakazk']", "[class*='tender']",
                # Fallback – jakýkoliv blok s odkazem
                "li:has(a[href*='/zakazk'])", "div:has(a[href*='/zakazk'])",
            ]

            # Zkusíme tabulky
            if tables > 0:
                batch = await self.collect_table_tenders(page, "//table[.//th or .//td]")
                for t in self._filter_keyword(batch, keyword):
                    tenders.append(t)

            # Zkusíme karty
            for sel in card_selectors:
                try:
                    count = await page.locator(sel).count()
                    if count > 0:
                        batch = await self.collect_card_tenders(page, sel)
                        logger.info("VhodneUverejneni selector='%s' count=%s batch=%s",
                                   sel, count, len(batch))
                        for t in self._filter_keyword(batch, keyword):
                            tenders.append(t)
                        if batch:
                            break  # Našli jsme funkční selektor
                except Exception:
                    continue

            # Stránkování
            next_link = page.locator(
                "a[rel='next'], a:has-text('Další'), a:has-text('Next'), "
                ".pagination a:last-child, [aria-label*='Další']"
            ).last
            if not await next_link.count():
                break
            href = await next_link.get_attribute("href")
            if not href or href == "#":
                break
            next_url = urljoin(current_url, href)
            if next_url in visited:
                break
            await page.goto(next_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)

        return self.deduplicate_tenders(tenders)

    def _filter_keyword(self, tenders: list[Tender], keyword: str) -> list[Tender]:
        result = []
        for t in tenders:
            if _is_foreign(t):
                continue
            if normalize_text(keyword) not in normalize_text(t.title):
                continue
            t.matched_keywords = [keyword]
            logger.info("VhodneUverejneni ✅ [%s] '%s'", keyword, t.title[:50])
            result.append(t)
        return result
