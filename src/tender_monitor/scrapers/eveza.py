"""Scraper pro eVeZa - eveza.cz

eVeZa ma fulltext vyhledavani bez prihlaseni na homepage.
Filtr se odesila POST requestem - pouzivame Playwright pro vyplneni formulare.
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?\b")

_BASE_URL = "https://eveza.cz/"
_FULLTEXT_ID = "MiddleContent_vzZakazkyPrehled_txtFultext"
_SUBMIT_SELECTOR = "input[type='submit'][value='Vyhledat'], button:has-text('Vyhledat')"


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
            # Pro každé klíčové slovo zvlášť - eVeZa hledá fulltext
            for keyword in self.keywords:
                try:
                    await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(2_000)

                    # Vyplníme fulltext pole a odešleme
                    field = page.locator(f"#{_FULLTEXT_ID}")
                    if not await field.count():
                        logger.warning("eVeZa: fulltext pole nenalezeno, preskakuji")
                        break

                    await field.fill(keyword)
                    await page.locator(_SUBMIT_SELECTOR).click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(2_000)

                    rows = await page.locator("table tr").all()
                    logger.info("eVeZa '%s': radky=%s url=%s", keyword, len(rows), page.url)

                    found_kw = 0
                    for row in rows:
                        # Název zakázky je v <a> odkazu
                        link = row.locator("a[href]").first
                        if not await link.count():
                            continue

                        href = await link.get_attribute("href")
                        if not href:
                            continue

                        title = (await link.inner_text()).strip()
                        if not title or len(title) < 5:
                            continue

                        row_url = f"https://eveza.cz{href}" if href.startswith("/") else href

                        # Zadavatel a lhůta jsou v dalším řádku tabulky (podřádek)
                        cells = [
                            (await c.inner_text()).strip()
                            for c in await row.locator("td").all()
                        ]

                        deadline = None
                        authority = None
                        for cell in cells:
                            d = _DATE_RE.search(cell)
                            if d and not deadline:
                                deadline = d.group(0)
                            elif len(cell) > 5 and not _DATE_RE.search(cell) and cell != title:
                                authority = cell

                        t = Tender(
                            source=self.source,
                            title=title,
                            url=row_url,
                            authority=authority,
                            published_at=None,  # eVeZa zobrazuje jen lhůtu, ne datum zveřejnění
                            deadline_at=deadline,
                        )

                        if _is_foreign(t):
                            continue

                        # Keyword byl použit pro vyhledávání = relevantní
                        t.matched_keywords = [keyword]
                        logger.info("eVeZa [%s] nalezena: '%s'", keyword, t.title[:60])
                        all_tenders.append(t)
                        found_kw += 1

                    logger.info("eVeZa '%s': found=%s", keyword, found_kw)

                except Exception as exc:
                    logger.warning("eVeZa keyword='%s' chyba: %s", keyword, exc)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("eVeZa: scraped=%s after_filter=%s", len(unique), len(filtered))
        return ScrapeResult(source=self.source, tenders=filtered)

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
