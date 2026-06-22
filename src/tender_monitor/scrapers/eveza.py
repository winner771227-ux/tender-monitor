"""Scraper pro eVeZa - eveza.cz

eVeZa zobrazuje nove uverejnene zakazky na homepage bez prihlaseni.
Vyhledavani vyzaduje prihlaseni - proto cteme cely seznam a filtrujeme lokalne.
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

_BASE_URL = "https://eveza.cz/"


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
            await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(3_000)

            tables = await page.locator("table").count()
            text_len = len(await page.locator("body").inner_text())
            logger.info("eVeZa homepage: tables=%s text_len=%s url=%s",
                       tables, text_len, page.url)

            # Cteme vsechny radky tabulky zakazek
            rows = await page.locator("table tr").all()
            logger.info("eVeZa: celkem radku=%s", len(rows))

            for row in rows:
                cells = [
                    (await c.inner_text()).strip()
                    for c in await row.locator("td").all()
                ]
                if len(cells) < 2:
                    continue

                # Najdeme odkaz na zakazku
                link = row.locator("a[href]").first
                href = await link.get_attribute("href") if await link.count() else None
                if not href:
                    continue

                url = f"https://eveza.cz{href}" if href.startswith("/") else href
                title = (await link.inner_text()).strip()
                if not title or len(title) < 3:
                    # zkusime vsechny bunky
                    for cell in cells:
                        if len(cell) > 10 and not cell[0].isdigit():
                            title = cell
                            break

                if not title:
                    continue

                # Datum a stav z bunek
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
                    url=url,
                    authority=authority,
                    published_at=published,
                    deadline_at=deadline,
                )

                if _is_foreign(t):
                    continue

                # Zkontrolujeme klicova slova
                matches = self._keyword_matches(t)
                if not matches:
                    continue

                t.matched_keywords = matches
                logger.info("eVeZa nalezena: '%s'", t.title[:60])
                all_tenders.append(t)

        except Exception as exc:
            logger.warning("eVeZa chyba: %s", exc)
        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("eVeZa: scraped=%s after_filter=%s", len(unique), len(filtered))
        return ScrapeResult(source=self.source, tenders=filtered)

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
