"""Scraper pro eVeZa – eveza.cz

eVeZa ma HTML formular pro vyhledavani zakazek.
Playwright vyplni formular a odeslat ho pro kazde klicove slovo.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

_BASE_URL = "https://eveza.cz/verejne-zakazky/"


class EvezaScraper(BaseScraper):
    source = "eVeZa"
    url = _BASE_URL

    async def scrape(self, browser: Browser) -> ScrapeResult:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
        )
        page = await context.new_page()
        all_tenders: list[Tender] = []

        try:
            for keyword in self.keywords:
                logger.info("eVeZa hledam: '%s'", keyword)
                try:
                    # Nacti stranku s formularem
                    await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(2_000)

                    # Najdi pole "Nazev verejne zakazky" a vyplnime ho
                    # eVeZa ma input s name nebo id obsahujici "nazev" nebo "zakazky"
                    filled = False
                    for selector in [
                        "input[name*='nazev']",
                        "input[name*='zakazk']",
                        "input[name*='Nazev']",
                        "input[id*='nazev']",
                        "input[id*='zakazk']",
                        "input[placeholder*='azev']",
                        "input[type='text']:nth-of-type(3)",  # treti textove pole
                    ]:
                        try:
                            el = page.locator(selector).first
                            if await el.count() and await el.is_visible():
                                await el.fill(keyword)
                                filled = True
                                logger.info("eVeZa vyplneno pole '%s' hodnotou '%s'",
                                           selector, keyword)
                                break
                        except Exception:
                            continue

                    if not filled:
                        # Pokud formulár nenajdeme, zkusíme URL s parametrem
                        logger.warning("eVeZa: formular nenalezen, zkousim URL")
                        await page.goto(
                            f"{_BASE_URL}?nazev_zakazky={keyword}",
                            wait_until="domcontentloaded", timeout=45_000
                        )
                    else:
                        # Odeslame formular
                        submit = page.locator("input[type='submit'], button[type='submit'], button:has-text('Vyhledat')").first
                        if await submit.count():
                            await submit.click()
                            await page.wait_for_load_state("domcontentloaded")
                        else:
                            await page.keyboard.press("Enter")
                            await page.wait_for_load_state("domcontentloaded")

                    await page.wait_for_timeout(2_000)

                    # Parsujeme vysledky
                    tables = await page.locator("table").count()
                    text_len = len(await page.locator("body").inner_text())
                    logger.info("eVeZa '%s': tables=%s text_len=%s url=%s",
                               keyword, tables, text_len, page.url[:80])

                    batch = await self.collect_table_tenders(page, "//table[.//th or .//td]")
                    for t in batch:
                        if _is_foreign(t):
                            continue
                        if normalize_text(keyword) not in normalize_text(t.title):
                            continue
                        t.matched_keywords = [keyword]
                        logger.info("eVeZa [%s] nalezena: '%s'", keyword, t.title[:50])
                        all_tenders.append(t)

                except Exception as exc:
                    logger.warning("eVeZa keyword='%s' chyba: %s", keyword, exc)

                await asyncio.sleep(1)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("eVeZa: scraped=%s after_filter=%s", len(unique), len(filtered))
        return ScrapeResult(source=self.source, tenders=filtered)

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
