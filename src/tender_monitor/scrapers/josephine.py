"""Scraper pro JOSEPHINE – josephine.proebiz.com

Hledá zakázky zveřejněné v posledních 14 dnech s klíčovými slovy v NÁZVU.
Používá filtr dateFrom a kontroluje že klíčové slovo je přímo v názvu zakázky.
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
    "https://josephine.proebiz.com/cs/public-tenders/all"
    "?query={keyword}&dateFrom={date_from}"
)


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"
    max_pages = 5
    max_tenders = 200

    async def scrape_page(self, page: Page) -> list[Tender]:
        all_tenders: list[Tender] = []
        # Pouze zakázky zveřejněné v posledních 14 dnech
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        for keyword in self.keywords:
            search_url = _SEARCH_URL.format(keyword=quote(keyword), date_from=date_from)
            logger.info("JOSEPHINE hledám: '%s' od %s", keyword, date_from)
            try:
                await page.goto(search_url, wait_until="domcontentloaded")
                batch = await self._scrape_keyword(page, keyword, date_from)
                logger.info("JOSEPHINE keyword='%s' nalezeno=%s", keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("JOSEPHINE keyword='%s' chyba: %s", keyword, exc)
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_keyword(self, page: Page, keyword: str, date_from: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        cutoff = datetime.strptime(date_from, "%Y-%m-%d")

        for page_num in range(self.max_pages):
            if page.url in visited_urls:
                break
            visited_urls.add(page.url)

            try:
                await page.wait_for_selector(
                    "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
                    state="attached", timeout=30_000,
                )
            except Exception:
                logger.info("JOSEPHINE '%s' str. %s: žádná tabulka", keyword, page_num + 1)
                break

            rows = await page.locator(
                "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
            ).all()
            rows = [r for r in rows if len(await r.locator("td").all()) >= 7]
            logger.info("JOSEPHINE '%s' str. %s: %s řádků", keyword, page_num + 1, len(rows))

            if not rows:
                break

            for row in rows:
                cells = [self._clean(await c.inner_text()) for c in await row.locator("td").all()]
                if len(cells) < 7:
                    continue

                link = row.locator("a[href*='/tender/'][href*='/summary']").first
                href = await link.get_attribute("href") if await link.count() else None
                tender = self._build(cells, href, page.url)
                if tender is None:
                    continue

                # 1. Odmítneme SK/PL zakázky
                if _is_foreign(tender):
                    logger.debug("JOSEPHINE SKIP SK/PL: %s", tender.title[:60])
                    continue

                # 2. Klíčové slovo MUSÍ být přímo v názvu zakázky
                if normalize_text(keyword) not in normalize_text(tender.title):
                    logger.debug("JOSEPHINE SKIP (kw ne v názvu) [%s]: %s", keyword, tender.title[:60])
                    continue

                # 3. Datum zveřejnění musí být v posledních 14 dnech
                pub = tender.published_at
                if pub:
                    try:
                        pub_date = datetime.strptime(pub[:10], "%d.%m.%Y")
                        if pub_date < cutoff:
                            logger.debug("JOSEPHINE SKIP stará (%s): %s", pub[:10], tender.title[:60])
                            continue
                    except Exception:
                        pass

                tender.matched_keywords = [keyword]
                logger.info("JOSEPHINE ✅ [%s] '%s' published=%s", keyword, tender.title[:50], tender.published_at)
                tenders.append(tender)

            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        return tenders

    async def _next_url(self, page: Page) -> str | None:
        link = page.locator("a:has-text('Další'), a:has-text('Next')").last
        if not await link.count():
            return None
        href = await link.get_attribute("href")
        if not href or href in {"#", page.url}:
            return None
        return urljoin(page.url, href)

    @classmethod
    def _build(cls, cells: list[str], href: str | None, current_url: str) -> Tender | None:
        now = datetime.now()
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[5]) if len(cells) > 5 else ""
        deadline = cls._date(cells[8]) if len(cells) > 8 else None

        # Datum v MINULOSTI = datum zveřejnění (NIKOLI deadline)
        published = None
        for cell in cells:
            d = cls._date(cell)
            if d:
                try:
                    parsed = datetime.strptime(d[:10], "%d.%m.%Y")
                    if parsed <= now:
                        published = d
                        break
                except Exception:
                    pass

        url = (
            urljoin(current_url, href) if href
            else (f"https://josephine.proebiz.com/cs/tender/{external_id}/summary"
                  if external_id.isdigit() else None)
        )
        if not title or not url:
            return None
        return Tender(
            source=JosephineScraper.source,
            title=title, url=url,
            authority=authority or None,
            published_at=published,
            deadline_at=deadline,
            external_id=external_id or None,
        )

    @staticmethod
    def _date(value: str) -> str | None:
        m = _DATE_RE.search(value)
        return m.group(0) if m else None

    @staticmethod
    def _line(value: str) -> str:
        return next((ln.strip() for ln in value.splitlines() if ln.strip()), "")

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"[ \t]+", " ", value.replace("\xa0", " ")).strip()
