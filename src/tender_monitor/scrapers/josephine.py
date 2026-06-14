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

                # keyword pre-check (no date yet – date fetched from detail)
                if not self._keyword_matches(tender):
                    continue

                tender.published_at = await self._earliest_doc_date(page, tender.url)
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

    async def _earliest_doc_date(self, page: Page, tender_url: str) -> str | None:
        ctx = await page.context.browser.new_context()
        detail = await ctx.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            await detail.goto(tender_url, wait_until="domcontentloaded")
            await detail.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = await detail.locator("body").inner_text()
            section = self._after_heading(text, ("Dokumenty", "Documents"))
            dates = _DATE_RE.findall(section)
            return min(dates, default=None, key=self._date_key)
        except Exception:
            return None
        finally:
            await ctx.close()

    # ------------------------------------------------------------------

    @classmethod
    def _build(cls, cells: list[str], href: str | None, current_url: str) -> Tender | None:
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[5]) if len(cells) > 5 else ""
        deadline = cls._date(cells[8]) if len(cells) > 8 else None
        url = (
            urljoin(current_url, href)
            if href
            else (f"https://josephine.proebiz.com/cs/tender/{external_id}/summary" if external_id.isdigit() else None)
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
        return next((l.strip() for l in value.splitlines() if l.strip()), "")

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"[ \t]+", " ", value.replace("\xa0", " ")).strip()

    @staticmethod
    def _after_heading(text: str, headings: tuple[str, ...]) -> str:
        for h in headings:
            if f"\n{h}\n" in text:
                return text.split(f"\n{h}\n", 1)[1]
        return ""

    @staticmethod
    def _date_key(v: str) -> tuple[int, int, int, str]:
        d, m, y = v[:10].split(".")
        return int(y), int(m), int(d), v[11:]
