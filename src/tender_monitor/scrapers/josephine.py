from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import re
from urllib.parse import urljoin

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}:\d{2})?\b")


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"

    async def scrape_page(self, page: Page) -> list[Tender]:
        
        logger.warning("JOSEPHINE START")
        
        tenders: list[Tender] = []
        visited_urls: set[str] = set()

        while page.url not in visited_urls:

            
            
            visited_urls.add(page.url)
            await self._wait_for_tender_table(page)
            rows = await self._tender_rows(page)
            
            logger.warning(
                "JOSEPHINE PAGE %s ROWS=%s",
                page.url,
                len(rows),
            )

            for row in rows:
                cells = [
                    self._clean_text(await cell.inner_text()) 
                    for cell in await row.locator("td").all()
                ]
                
                logger.warning("JOSEPHINE CELLS %s", cells)
                
                tender_link = row.locator(
                    "a[href*='/tender/'][href*='/summary']"
                ).first

                href = (
                    await tender_link.get_attribute("href")
                    if await tender_link.count()
                    else None
                )
                                   
                tender = self._build_tender_from_cells(
                    cells,
                    href,
                    page.url,
                )

                logger.warning(
                    "TENDER=%s",
                    tender,
                )

                if tender is None or not self.keyword_matches(tender):
                    continue

                tender.published_at = await self._extract_publication_date(
                    page,
                    tender.url,
                )

                tenders.append(tender)
              

            next_url = await self._next_page_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        return tenders

    def keyword_matches(self, tender: Tender) -> list[str]:
        haystack = normalize_text(" ".join(filter(None, (tender.title, tender.description, tender.authority))))
        return [keyword for keyword in self.keywords if normalize_text(keyword) in haystack]

    async def _wait_for_tender_table(self, page: Page) -> None:
        await page.wait_for_selector(
            "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
            state="attached",
            timeout=self.timeout_ms,
        )

    async def _tender_rows(self, page: Page):
        rows = await page.locator(
            "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
        ).all()
        return [row for row in rows if len(await row.locator("td").all()) >= 7]

    async def _next_page_url(self, page: Page) -> str | None:
        next_link = page.locator("a:has-text('Další'), a:has-text('Next')").last

        
        if not await next_link.count():
            return None

        href = await next_link.get_attribute("href")

        
        if not href or href in {"#", page.url}:
            return None

        return urljoin(page.url, href)

    async def _extract_publication_date(self, page: Page, tender_url: str) -> str | None:
        context = await page.context.browser.new_context()
        detail_page = await context.new_page()
        detail_page.set_default_timeout(self.timeout_ms)
        try:
            await detail_page.goto(tender_url, wait_until="domcontentloaded")
            await detail_page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = await detail_page.locator("body").inner_text()
            document_section = self._text_after_first_heading(text, ("Dokumenty", "Documents"))
            dates = _DATE_RE.findall(document_section)
            return min(dates, default=None, key=self._date_sort_key)
        finally:
            await context.close()

    @classmethod
    def _build_tender_from_cells(
        cls, 
        cells: list[str], 
        href: str | None, 
        current_url: str
    ) -> Tender | None:

        logger.warning(
            "BUILD LEN=%s CELLS=%s",
            en(cells),
            cells,
        )
        
        if len(cells) < 7:
            logger.warning("RETURN NONE: TOO FEW CELLS")
            return None
    try:
        external_id = cls._first_line(cells[0])
        title = cls._first_line(cells[2])

        authority = (
            cls._first_line(cells[5])
            if len(cells) > 5
            else ""
        )

        deadline = (
            cls._first_date(cells[8])
            if len(cells) > 8
            else None
        )

        logger.warning(
            "PARSED id=%s title=%s authority=%s",
            external_id,
            title,
            authority,
            deadline,
        )

                
        tender_url = ( 
            urljoin(current_url, href) 
            if href 
            else cls._summary_url_from_id(current_url, external_id)
        )

        logger.warning(
            "URL=%s",
            tender_url,
       )

        if not title:
            logger.warning("RETURN NONE: EMPTY TITLE")
            return None

        if not tender_url:
            logger.warning("RETURN NONE: EMPTY URL")
            return None

        tender = Tender(
            source=cls.source,
            title=title,
            url=tender_url,
            authority=authority or None,
            deadline_at=deadline,
            external_id=external_id or None,
        )

        logger.warning(
            "BUILD SUCCESS ID=%s",
            external_id,
        )

        return tender

    except Exception as exc:
        logger.exception(
            "BUILD FAILED EXCEPTION: %s",
            exc,
        )
        return None        

    @staticmethod
    def _summary_url_from_id(current_url: str, external_id: str) -> str | None:
        if not external_id.isdigit():
            return None
        return urljoin(current_url, f"/cs/tender/{external_id}/summary")

    @staticmethod
    def _first_date(value: str) -> str | None:
        match = _DATE_RE.search(value)
        return match.group(0) if match else None

    @staticmethod
    def _first_line(value: str) -> str:
        return next((line.strip() for line in value.splitlines() if line.strip()), "")

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"[ \t]+", " ", value.replace("\xa0", " ")).strip()

    @staticmethod
    def _text_after_first_heading(text: str, headings: tuple[str, ...]) -> str:
        for heading in headings:
            marker = f"\n{heading}\n"
            if marker in text:
                return text.split(marker, 1)[1]
        return ""

    @staticmethod
    def _date_sort_key(value: str) -> tuple[int, int, int, str]:
        day, month, year = value[:10].split(".")
        return int(year), int(month), int(day), value[11:]
