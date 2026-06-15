"""Scraper pro JOSEPHINE – josephine.proebiz.com

Prochází všechny zakázky od nejnovějších a filtruje podle:
1. Klíčové slovo v NÁZVU zakázky
2. Datum zveřejnění max. 30 dní staré
3. Pouze české zakázky (ne SK/PL)

Zastaví se jakmile narazí na stránku kde jsou VŠECHNY zakázky starší než 30 dní.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"
    max_pages = 60   # Pojistka – v praxi se zastaví dříve
    max_tenders = 200

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        now = datetime.now()
        cutoff = now - timedelta(days=30)  # hledáme zakázky max 30 dní staré

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
                logger.warning("JOSEPHINE str. %s: timeout", page_num + 1)
                break

            rows = await page.locator(
                "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
            ).all()
            rows = [r for r in rows if len(await r.locator("td").all()) >= 7]
            logger.info("JOSEPHINE page=%s rows=%s", page.url, len(rows))

            if not rows:
                break

            page_newest_date = None  # nejnovější datum na této stránce

            for row in rows:
                if len(tenders) >= self.max_tenders:
                    break

                cells = [self._clean(await c.inner_text()) for c in await row.locator("td").all()]
                if len(cells) < 7:
                    continue

                link = row.locator("a[href*='/tender/'][href*='/summary']").first
                href = await link.get_attribute("href") if await link.count() else None
                tender = self._build(cells, href, page.url, now)
                if tender is None:
                    continue

                # Sledujeme nejnovější datum na stránce pro rozhodnutí o zastavení
                if tender.published_at:
                    try:
                        d = datetime.strptime(tender.published_at[:10], "%d.%m.%Y")
                        if page_newest_date is None or d > page_newest_date:
                            page_newest_date = d
                    except Exception:
                        pass

                # Odmítneme SK/PL zakázky
                if _is_foreign(tender):
                    continue

                # Ověříme klíčová slova v názvu
                matches = self._keyword_matches(tender)
                if not matches:
                    continue

                # Kontrola data zveřejnění
                if tender.published_at:
                    try:
                        pub = datetime.strptime(tender.published_at[:10], "%d.%m.%Y")
                        if pub < cutoff:
                            continue
                    except Exception:
                        pass

                tender.matched_keywords = matches
                logger.info("JOSEPHINE ✅ '%s' published=%s", tender.title[:50], tender.published_at)
                tenders.append(tender)

            if len(tenders) >= self.max_tenders:
                break

            # Zastavíme se pokud jsou VŠECHNY zakázky na stránce starší než cutoff
            if page_newest_date is not None and page_newest_date < cutoff:
                logger.info("JOSEPHINE: zakázky příliš staré (nejnovější %s) – zastavuji str. %s",
                           page_newest_date.date(), page_num + 1)
                break

            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        logger.info("JOSEPHINE total=%s", len(tenders))
        return self.deduplicate_tenders(tenders)

    async def _next_url(self, page: Page) -> str | None:
        link = page.locator("a:has-text('Další'), a:has-text('Next')").last
        if not await link.count():
            return None
        href = await link.get_attribute("href")
        if not href or href in {"#", page.url}:
            return None
        return urljoin(page.url, href)

    @classmethod
    def _build(cls, cells: list[str], href: str | None, current_url: str,
               now: datetime) -> Tender | None:
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[5]) if len(cells) > 5 else ""
        deadline = cls._date(cells[8]) if len(cells) > 8 else None

        # Datum v minulosti = datum zveřejnění
        published = None
        for cell in cells:
            d = cls._date(cell)
            if d:
                try:
                    if datetime.strptime(d[:10], "%d.%m.%Y") <= now:
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
