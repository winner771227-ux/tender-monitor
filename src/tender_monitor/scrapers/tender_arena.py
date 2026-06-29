"""Scraper pro Tender Arena - tenderarena.cz

Tender Arena je Angular SPA. Vyhledavani pres JavaScript.
Pouzivame Playwright - vyplnime input, pocame na vysledky.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani"
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?\b")
_ID_RE = re.compile(r"\bVZ\d+\b")

MAX_PER_KEYWORD = 10


class TenderArenaScraper(BaseScraper):
    source = "TenderArena"
    url = _SEARCH_URL
    per_keyword_timeout_ms = 45_000

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
        error_msg = None

        try:
            for keyword in self.keywords:
                logger.info("TenderArena hledam: '%s'", keyword)
                try:
                    await page.goto(_SEARCH_URL, wait_until="domcontentloaded",
                                    timeout=self.per_keyword_timeout_ms)
                    await page.wait_for_timeout(3_000)

                    # Input je uvnitř divu .search-box__input
                    field = page.locator(".search-box__input input").first
                    if not await field.count():
                        # Fallback - zkusíme přímo input
                        field = page.locator("input[type='text'], input[type='search']").first
                    if not await field.count():
                        logger.warning("TenderArena: input nenalezen")
                        break

                    await field.fill(keyword)
                    await field.press("Enter")

                    # Počkáme na načtení výsledků (Angular)
                    try:
                        await page.wait_for_selector(
                            "app-chytre-vyhledavani-seznam",
                            timeout=10_000
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(3_000)

                    body_text = await page.locator("body").inner_text()
                    logger.info("TenderArena '%s': body_len=%s", keyword, len(body_text))

                    # Zkusíme různé selektory pro výsledky
                    result_items = await page.locator(
                        "app-chytre-vyhledavani-seznam > div, "
                        "[class*='vyhledavani-seznam'] > *, "
                        ".search-result-item"
                    ).all()

                    # Fallback - nadpisy s odkazem
                    if not result_items:
                        result_items = await page.locator("h3 a, h4 a, strong a").all()
                        logger.info("TenderArena '%s': fallback links=%s", keyword, len(result_items))

                    logger.info("TenderArena '%s': items=%s", keyword, len(result_items))

                    found_kw = 0
                    for item in result_items:
                        if found_kw >= MAX_PER_KEYWORD:
                            break

                        try:
                            text = (await item.inner_text()).strip()
                        except Exception:
                            continue

                        if not text or len(text) < 5:
                            continue

                        # Název - z odkazu nebo první řádek
                        link = item.locator("a").first
                        href = await link.get_attribute("href") if await link.count() else None
                        title = (await link.inner_text()).strip() if await link.count() else ""
                        if not title:
                            title = text.splitlines()[0].strip()

                        if not title or len(title) < 5:
                            # Pokud je item samotný odkaz
                            if await item.evaluate("el => el.tagName") == "A":
                                href = await item.get_attribute("href")
                                title = text

                        if not title or len(title) < 5:
                            continue

                        # Klíčové slovo musí být v názvu
                        if normalize_text(keyword) not in normalize_text(title):
                            continue

                        row_url = f"https://www.tenderarena.cz{href}" if href and href.startswith("/") else href

                        # ID zakázky z textu
                        external_id = None
                        deadline = None
                        id_m = _ID_RE.search(text)
                        if id_m:
                            external_id = id_m.group(0)
                            if not row_url:
                                row_url = f"https://www.tenderarena.cz/dodavatel/zakazka/detail/{external_id}"

                        date_m = _DATE_RE.search(text)
                        if date_m:
                            deadline = date_m.group(0)

                        # Zadavatel - druhý řádek
                        lines = [l.strip() for l in text.splitlines() if l.strip()]
                        authority = None
                        for line in lines[1:]:
                            if not _ID_RE.search(line) and not _DATE_RE.search(line) and len(line) > 3:
                                authority = line
                                break

                        if not row_url:
                            continue

                        t = Tender(
                            source=self.source,
                            title=title,
                            url=row_url,
                            authority=authority,
                            published_at=None,
                            deadline_at=deadline,
                            external_id=external_id,
                        )

                        if _is_foreign(t):
                            continue

                        t.matched_keywords = [keyword]
                        logger.info("TenderArena [%s] nalezena: '%s'", keyword, t.title[:60])
                        all_tenders.append(t)
                        found_kw += 1

                    logger.info("TenderArena '%s': found=%s", keyword, found_kw)

                except Exception as exc:
                    logger.warning("TenderArena keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)

                await asyncio.sleep(1)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("TenderArena: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
