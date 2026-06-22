"""Scraper pro NEN - nen.nipez.cz - TRACE verze"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://nen.nipez.cz/verejne-zakazky"
    "/p:vz:query={keyword}"
)


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"
    max_pages = 3
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
                # Pouze první klíčové slovo pro trace - pak normálně
                search_url = _SEARCH_URL.format(keyword=quote(keyword))
                logger.info("NEN hledam: '%s'", keyword)
                try:
                    await page.goto(
                        search_url, wait_until="domcontentloaded",
                        timeout=self.per_keyword_timeout_ms,
                    )
                    await page.wait_for_timeout(6_000)

                    rows_all = await page.locator("table tr").all()
                    logger.info("NEN '%s': radky=%s", keyword, len(rows_all))

                    found_kw = 0
                    skip_short = 0
                    skip_title = 0
                    skip_foreign = 0

                    for i, row in enumerate(rows_all):
                        cells = [
                            (await c.inner_text()).strip()
                            for c in await row.locator("td").all()
                        ]
                        if len(cells) < 4:
                            skip_short += 1
                            continue

                        # TRACE: první 2 řádky s >= 4 buňkami
                        if i < 5:
                            logger.info(
                                "NEN TRACE row[%s]: len=%s cells=%s",
                                i, len(cells), [c[:35] for c in cells]
                            )

                        title = cells[2] if len(cells) > 2 and len(cells[2]) > 5 else ""
                        if not title or cells[2].startswith("N006"):
                            for idx in [3, 4, 1]:
                                if idx < len(cells) and len(cells[idx]) > 5:
                                    if not cells[idx].startswith("N006"):
                                        title = cells[idx]
                                        break

                        if not title:
                            skip_title += 1
                            if i < 10:
                                logger.info("NEN TRACE no title: cells=%s", [c[:25] for c in cells])
                            continue

                        external_id = cells[1] if len(cells) > 1 else ""
                        if external_id and "/" in external_id:
                            id_slug = external_id.replace("/", "-")
                            row_url = f"https://nen.nipez.cz/verejne-zakazky/detail-zakazky/{id_slug}"
                        else:
                            any_link = row.locator("a[href]").first
                            if not await any_link.count():
                                skip_title += 1
                                continue
                            href = await any_link.get_attribute("href")
                            if not href:
                                skip_title += 1
                                continue
                            row_url = f"https://nen.nipez.cz{href}" if href.startswith("/") else href

                        t = Tender(
                            source="NEN",
                            title=title,
                            url=row_url,
                            authority=cells[4] if len(cells) > 4 else None,
                            published_at=None,
                            deadline_at=cells[5] if len(cells) > 5 else None,
                            external_id=external_id or None,
                        )

                        foreign = _is_foreign(t)
                        if i < 5:
                            logger.info(
                                "NEN TRACE row[%s]: title=%r url=%r foreign=%s",
                                i, title[:40], row_url[:60], foreign
                            )

                        if foreign:
                            skip_foreign += 1
                            continue

                        t.matched_keywords = [keyword]
                        logger.info("NEN [%s] nalezena: '%s'", keyword, t.title[:60])
                        all_tenders.append(t)
                        found_kw += 1

                    logger.info(
                        "NEN '%s': found=%s skip_short=%s skip_title=%s skip_foreign=%s",
                        keyword, found_kw, skip_short, skip_title, skip_foreign
                    )

                except Exception as exc:
                    logger.warning("NEN keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)

                await asyncio.sleep(1)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("NEN: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
