"""Scraper for JOSEPHINE – josephine.proebiz.com"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}:\d{2})?\b")

# Štítky, pod kterými JOSEPHINE zobrazuje datum zveřejnění zakázky
_PUBLICATION_LABELS = (
    "Datum uveřejnění",
    "Datum zveřejnění",
    "Zveřejněno",
    "Uveřejněno",
    "Datum prvního uveřejnění",
    "Datum zahájení",
    "Publication date",
    "Published",
)


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()

        while page.url not in visited_urls:
            visited_urls.add(page.url)
            await self._wait_table(page)
            rows = await self._rows(page)
            logger.info("JOSEPHINE page=%s rows=%s", page.url, len(rows))

            for row in rows:
                if len(tenders) >= self.max_tenders:
                    break
                cells = [
                    self._clean(await cell.inner_text())
                    for cell in await row.locator("td").all()
                ]
                if len(cells) < 7:
                    continue

                link = row.locator("a[href*='/tender/'][href*='/summary']").first
                href = await link.get_attribute("href") if await link.count() else None

                tender = self._build(cells, href, page.url)
                if tender is None:
                    continue

                # Předběžná kontrola klíčových slov (bez data)
                if not self._keyword_matches(tender):
                    continue

                # Načteme datum zveřejnění z detailní stránky zakázky
                tender.published_at = await self._get_publication_date(page, tender.url)
                logger.info(
                    "JOSEPHINE tender=%s published=%s",
                    tender.title[:60],
                    tender.published_at,
                )
                tenders.append(tender)

            if len(tenders) >= self.max_tenders:
                break

            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        logger.info("JOSEPHINE total=%s", len(tenders))
        return tenders

    # ------------------------------------------------------------------
    # Načtení data zveřejnění z detailní stránky
    # ------------------------------------------------------------------

    async def _get_publication_date(self, page: Page, tender_url: str) -> str | None:
        ctx = await page.context.browser.new_context()
        detail = await ctx.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            await detail.goto(tender_url, wait_until="domcontentloaded")
            await detail.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = await detail.locator("body").inner_text()

            # 1. Zkusíme najít datum u konkrétního štítku (nejspolehlivější)
            for label in _PUBLICATION_LABELS:
                pattern = re.compile(
                    rf"{re.escape(label)}\s*:?\s*(\d{{2}}\.\d{{2}}\.\d{{4}}(?:\s+\d{{2}}:\d{{2}}:\d{{2}})?)",
                    re.IGNORECASE,
                )
                m = pattern.search(text)
                if m:
                    logger.info("JOSEPHINE date via label '%s': %s", label, m.group(1))
                    return m.group(1).strip()

            # 2. Záložní: nejstarší datum v celém textu stránky
            #    (datum zveřejnění bývá nejstarší ze všech dat)
            all_dates = _DATE_RE.findall(text)
            if all_dates:
                oldest = min(all_dates, key=self._date_key)
                logger.info("JOSEPHINE date via oldest fallback: %s", oldest)
                return oldest

            return None

        except Exception as exc:
            logger.warning("JOSEPHINE detail fetch failed for %s: %s", tender_url, exc)
            return None
        finally:
            await ctx.close()

    # ------------------------------------------------------------------
    # Pomocné metody
    # ------------------------------------------------------------------

    async def _wait_table(self, page: Page) -> None:
        await page.wait_for_selector(
            "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
            state="attached",
            timeout=self.timeout_ms,
        )

    async def _rows(self, page: Page):
        rows = await page.locator(
            "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
        ).all()
        return [r for r in rows if len(await r.locator("td").all()) >= 7]

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
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[5]) if len(cells) > 5 else ""
        deadline = cls._date(cells[8]) if len(cells) > 8 else None
        url = (
            urljoin(current_url, href)
            if href
            else (
                f"https://josephine.proebiz.com/cs/tender/{external_id}/summary"
                if external_id.isdigit()
                else None
            )
        )
        if not title or not url:
            return None
        return Tender(
            source=JosephineScraper.source,
            title=title,
            url=url,
            authority=authority or None,
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

    @staticmethod
    def _date_key(v: str) -> tuple[int, int, int, str]:
        d, m, y = v[:10].split(".")
        return int(y), int(m), int(d), v[11:]
