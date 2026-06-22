"""Scraper pro eVeZa - eveza.cz

eVeZa vyzaduje prihlaseni pro vyhledavani.
Homepage zobrazuje posledni zakazky ze vsech oboru - prilis maly vzorek
pro spolehlivy zachyt demolici. Scraper proto zkusi prochazet vice stranek
seznamu novych zakazek (pokud existuji) a filtruje lokalne.
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

# eVeZa - seznam novych zakazek (verejny, bez prihlaseni)
_BASE_URL = "https://eveza.cz/"
_LIST_URLS = [
    "https://eveza.cz/",
    "https://eveza.cz/verejne-zakazky/",
    "https://eveza.cz/zakazky/",
]


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
            # Zkusíme homepage i případné další stránky seznamu
            for list_url in _LIST_URLS:
                try:
                    await page.goto(list_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(3_000)

                    tables = await page.locator("table").count()
                    text_len = len(await page.locator("body").inner_text())
                    logger.info(
                        "eVeZa %s: tables=%s text_len=%s url=%s",
                        list_url, tables, text_len, page.url,
                    )

                    if tables == 0 and text_len < 500:
                        logger.info("eVeZa: %s - prázdná stránka, zkouším další", list_url)
                        continue

                    rows = await page.locator("table tr").all()
                    logger.info("eVeZa %s: celkem radku=%s", list_url, len(rows))

                    found_on_page = 0
                    for row in rows:
                        cells = [
                            (await c.inner_text()).strip()
                            for c in await row.locator("td").all()
                        ]
                        if len(cells) < 2:
                            continue

                        link = row.locator("a[href]").first
                        href = await link.get_attribute("href") if await link.count() else None
                        if not href:
                            continue

                        row_url = f"https://eveza.cz{href}" if href.startswith("/") else href
                        title = (await link.inner_text()).strip()
                        if not title or len(title) < 3:
                            for cell in cells:
                                if len(cell) > 10 and not cell[0].isdigit():
                                    title = cell
                                    break

                        if not title:
                            continue

                        published = None
                        deadline = None
                        authority = None
                        for cell in cells:
                            d = _DATE_RE.search(cell)
                            if d and not published:
                                published = d.group(0)
                            elif d and not deadline:
                                deadline = d.group(0)
                            elif len(cell) > 5 and not any(c.isdigit() for c in cell[:5]):
                                if cell != title:
                                    authority = cell

                        t = Tender(
                            source=self.source,
                            title=title,
                            url=row_url,
                            authority=authority,
                            published_at=published,
                            deadline_at=deadline,
                        )

                        if _is_foreign(t):
                            continue

                        matches = self._keyword_matches(t)
                        if not matches:
                            continue

                        t.matched_keywords = matches
                        logger.info("eVeZa nalezena: '%s'", t.title[:60])
                        all_tenders.append(t)
                        found_on_page += 1

                    logger.info("eVeZa %s: nalezeno %s odpovídajících zakázek", list_url, found_on_page)

                    # Pokud jsme na homepage a nenašli nic, nemá smysl zkoušet další
                    # (jsou to stejná data nebo prázdné stránky)
                    if list_url == _BASE_URL and found_on_page == 0 and len(rows) > 0:
                        logger.info("eVeZa: homepage má %s řádků ale žádná demoliční zakázka - normální stav", len(rows))
                        break

                except Exception as exc:
                    logger.warning("eVeZa %s chyba: %s", list_url, exc)
                    continue

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("eVeZa: scraped=%s after_filter=%s", len(unique), len(filtered))
        return ScrapeResult(source=self.source, tenders=filtered)

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
