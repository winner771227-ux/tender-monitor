"""Scraper pro eVeZa - eveza.cz

eVeZa ma fulltext vyhledavani bez prihlaseni na homepage.
Filtr se odesila POST requestem - pouzivame Playwright pro vyplneni formulare.
Fulltext hleda i v textu dokumentu, proto kontrolujeme ze klicove slovo
je primo v nazvu zakazky.

Struktura tabulky eVeZa:
  Řádek 1 (název): [Název zakázky jako odkaz]
  Řádek 2 (detail): [Stav] [Lhůta podání] [Zadavatel]
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?\b")

_BASE_URL = "https://eveza.cz/"
_FULLTEXT_ID = "MiddleContent_vzZakazkyPrehled_txtFultext"
_SUBMIT_SELECTOR = "input[type='submit'][value='Vyhledat'], button:has-text('Vyhledat')"

# Zadavatel je v URL: /profil-zadavatele/SLUG/zakazka/ID
_AUTHORITY_RE = re.compile(r"/profil-zadavatele/([^/]+)/zakazka/(\d+)")


def _slug_to_name(slug: str) -> str:
    """Převede URL slug na čitelné jméno: lesy-ceske-republiky-sp -> Lesy české republiky s.p."""
    return slug.replace("-", " ").title()


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
                try:
                    await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(2_000)

                    field = page.locator(f"#{_FULLTEXT_ID}")
                    if not await field.count():
                        logger.warning("eVeZa: fulltext pole nenalezeno, preskakuji")
                        break

                    await field.fill(keyword)
                    await page.locator(_SUBMIT_SELECTOR).click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(2_000)

                    rows = await page.locator("table tr").all()
                    logger.info("eVeZa '%s': radky=%s", keyword, len(rows))

                    found_kw = 0
                    i = 0
                    while i < len(rows):
                        row = rows[i]
                        link = row.locator("a[href]").first
                        if not await link.count():
                            i += 1
                            continue

                        href = await link.get_attribute("href")
                        if not href:
                            i += 1
                            continue

                        title = (await link.inner_text()).strip()
                        if not title or len(title) < 5:
                            i += 1
                            continue

                        # Klíčové slovo musí být v názvu
                        if normalize_text(keyword) not in normalize_text(title):
                            i += 1
                            continue

                        row_url = f"https://eveza.cz{href}" if href.startswith("/") else href

                        # Zadavatel z URL slug
                        authority = None
                        m = _AUTHORITY_RE.search(href)
                        if m:
                            authority = _slug_to_name(m.group(1))

                        # Lhůta a stav z následujícího řádku (detail řádek)
                        deadline = None
                        if i + 1 < len(rows):
                            next_cells = [
                                (await c.inner_text()).strip()
                                for c in await rows[i + 1].locator("td").all()
                            ]
                            for cell in next_cells:
                                d = _DATE_RE.search(cell)
                                if d and not deadline:
                                    deadline = d.group(0)

                        t = Tender(
                            source=self.source,
                            title=title,
                            url=row_url,
                            authority=authority,
                            published_at=None,
                            deadline_at=deadline,
                        )

                        if _is_foreign(t):
                            i += 1
                            continue

                        t.matched_keywords = [keyword]
                        logger.info("eVeZa [%s] nalezena: '%s' lhůta=%s zadavatel=%s",
                                   keyword, t.title[:50], deadline, authority)
                        all_tenders.append(t)
                        found_kw += 1
                        i += 1  # přeskočíme jen tento řádek, detail řádek přečteme příště

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
