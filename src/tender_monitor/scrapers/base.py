from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import urljoin

from playwright.async_api import Browser, Locator, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO
)

DATE_RE = re.compile(
    r"\b(?:\d{1,2}[.]\s*\d{1,2}[.]\s*\d{4}|\d{4}-\d{2}-\d{2})"
    r"(?:\s*(?:do|v|at)?\s*\d{1,2}:\d{2}(?::\d{2})?)?\b",
    re.IGNORECASE,
)

NEXT_PAGE_SELECTOR = (
    "a[rel='next'], "
    "a[aria-label*='Další'], a[aria-label*='Next'], "
    "button[aria-label*='Další'], button[aria-label*='Next'], "
    "a:has-text('Další'), a:has-text('Next'), "
    "button:has-text('Další'), button:has-text('Next'), "
    "li.next:not(.disabled) a, .pagination .next:not(.disabled) a"
)

DETAIL_LINK_SELECTOR = (
    "a[href*='detail'], a[href*='Detail'], a[href*='zakaz'], a[href*='Zakaz'], "
    "a[href*='tender'], a[href*='verejne-zakazky'], a[href*='verejna-zakazka'], "
    "a:has-text('Detail'), a:has-text('Zobrazit')"
)

TITLE_HEADER_ALIASES = (
    "nazev zakazky",
    "nazev verejne zakazky",
    "nazev zadavaciho postupu",
    "verejna zakazka",
    "zakazka",
    "predmet",
)
AUTHORITY_HEADER_ALIASES = ("zadavatel", "nazev zadavatele", "organizace", "verejny zadavatel")
PUBLICATION_HEADER_ALIASES = (
    "zverejneno",
    "datum zverejneni",
    "datum uverejneni",
    "uverejneno",
    "datum prvniho uverejneni",
)
DEADLINE_HEADER_ALIASES = (
    "lhuta",
    "lhuta pro podani",
    "lhuta podani nabidek",
    "konec lhuty",
    "termin",
    "deadline",
    "podani nabidek",
)
ID_HEADER_ALIASES = ("systemove cislo", "evidencni cislo", "id", "cislo zakazky", "kod")


class BaseScraper(ABC):
    """Base class for Playwright tender scrapers."""

    source: str
    url: str
    max_pages: int = 50

    def __init__(self, keywords: tuple[str, ...], timeout_ms: int) -> None:
        self.keywords = keywords
        self.timeout_ms = timeout_ms

    async def scrape(self, browser: Browser) -> ScrapeResult:
        page = await browser.new_page()

        page.set_default_timeout(
            max(self.timeout_ms, 120000)
        )

        try:
            await page.goto(
                self.url,
                wait_until="domcontentloaded",
                timeout=max(self.timeout_ms, 120000),
            )

            tenders = await self.scrape_page(page)

            filtered = self.filter_by_keywords(
                tenders
            )

            logger.warning(
                "%s: loaded=%s filtered=%s",
                self.source,
                len(tenders),
                len(filtered),
            )

            for tender in filtered:
                logger.warning(
                     "MATCH %s | %s | %s",
                    self.source,
                    tender.title,
                    tender.published_at,
                )

            return ScrapeResult(
                source=self.source,
                tenders=filtered,
            )

        except Exception as exc:
            logger.exception(
                "Scraper %s failed",
                self.source,
            )

            return ScrapeResult(
                source=self.source,
                tenders=[],
                error=str(exc),
            )

        finally:
            await page.close()

    @abstractmethod
    async def scrape_page(self, page: Page) -> list[Tender]:
        """Extract tenders from an already opened portal page."""

    def keyword_matches(self, tender: Tender) -> list[str]:
        haystack = normalize_text(
            " ".join(part or "" for part in (tender.title, tender.description, tender.authority))
        )
        return [keyword for keyword in self.keywords if normalize_text(keyword) in haystack]


    def filter_by_keywords(self, tenders: list[Tender]) -> list[Tender]:
        from datetime import datetime, timedelta

        filtered: list[Tender] = []

        cutoff = datetime.now() - timedelta(days=14)

        for tender in tenders:
            matches = self.keyword_matches(tender)

            if "archivu nemocnice" in tender.title.lower():
                logger.warning(
                    "DEBUG STERNBERK title=%s published=%s matches=%s",
                    tender.title,
                    tender.published_at,
                    matches,
            )

            if not matches:
                continue

            if not tender.published_at:
                tender.matched_keywords = matches
                filtered.append(tender)
                continue

            date_text = tender.published_at.strip()

            published = None

            formats = [
                "%d.%m.%Y",
                "%d.%m.%Y %H:%M",
                "%d.%m.%Y %H:%M:%S",
                "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S",
            ]

            for fmt in formats:
                try:
                    published = datetime.strptime(date_text[:19], fmt)
                    break
                except ValueError:
                    pass

            if not published:
                continue

            logger.warning(
                "DATECHECK %s | published=%s | cutoff=%s",
                tender.title,
                published,
                cutoff,
            )

            if "archivu nemocnice" in tender.title.lower():
                logger.warning(
                "DEBUG STERNBERK DATE published=%s cutoff=%s",
                published,
                cutoff,
            )

            if published < cutoff:
                logger.warning("SKIPPING OLD %s", tender.title)
                continue

            if not published:
                logger.warning("NO DATE %s", tender.title)

            tender.matched_keywords = matches
            filtered.append(tender)

        return filtered

    async def collect_link_tenders(self, page: Page, link_selector: str) -> list[Tender]:
        """Collect visible tender links from a page."""
        items: list[Tender] = []
        links = await page.locator(link_selector).all()
        for link in links:
            title = self.clean_text(await link.inner_text())
            href = await link.get_attribute("href")
            if not title or not href:
                continue
            items.append(
                Tender(
                    source=self.source,
                    title=title,
                    url=self.absolute_url(page.url, href),
                )
            )
        return self.deduplicate_tenders(items)

    async def collect_table_tenders(
        self,
        page: Page,
        table_xpath: str,
        *,
        detail_selector: str = DETAIL_LINK_SELECTOR,
        open_detail: bool = True,
    ) -> list[Tender]:
        tenders: list[Tender] = []
        rows = await page.locator(f"xpath={table_xpath}//tr[td]").all()
        headers = await self.table_headers(page, table_xpath)
        for row in rows:
            cells = [self.clean_text(await cell.inner_text()) for cell in await row.locator("td").all()]
            if not any(cells):
                continue
            tender = await self.tender_from_cells(
                row=row,
                cells=cells,
                headers=headers,
                current_url=page.url,
                detail_selector=detail_selector,
            )
            if tender is None:
                continue
            if open_detail:
                await self.enrich_from_detail(page, tender)
            logger.warning(
                "RAW %s | %s | %s",
                self.source,
                tender.title,
                tender.published_at,
            )
            tenders.append(tender)
        return self.deduplicate_tenders(tenders)


    async def collect_card_tenders(
        self,
        page: Page,
        container_selector: str,
        *,
        detail_selector: str = DETAIL_LINK_SELECTOR,
        open_detail: bool = True,
    ) -> list[Tender]:
        tenders: list[Tender] = []
        containers = await page.locator(container_selector).all()
        for container in containers:
            text = self.clean_text(await container.inner_text())
            if not text:
                continue
            url = await self.first_row_url(container, page.url, detail_selector)
            title = await self.first_row_link_text(container, detail_selector)
            if not title:
                title = self.first_line(text)
            if not title or not url:
                continue
            tender = Tender(
                source=self.source,
                title=title,
                url=url,
                authority=self.value_after_text_label(text, ("Zadavatel", "Název zadavatele", "Organizace")),
                published_at=self.value_after_label(
                    text,
                    ("Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno"),
                ),
                deadline_at=self.value_after_label(
                    text,
                    ("Lhůta pro podání nabídek", "Lhůta", "Termín podání", "Konec lhůty"),
                ),
                external_id=self.detect_external_id(text.splitlines()),
                description=text,
            )
            if open_detail:
                await self.enrich_from_detail(page, tender)
            tenders.append(tender)
        return self.deduplicate_tenders(tenders)

    async def table_headers(self, page: Page, table_xpath: str) -> list[str]:
        header_cells = await page.locator(f"xpath={table_xpath}//tr[th][1]/th").all()
        return [normalize_text(await cell.inner_text()) for cell in header_cells]

    async def tender_from_cells(
        self,
        *,
        row: Locator,
        cells: list[str],
        headers: list[str],
        current_url: str,
        detail_selector: str = DETAIL_LINK_SELECTOR,
    ) -> Tender | None:
        title = self.value_by_header(cells, headers, TITLE_HEADER_ALIASES)
        authority = self.value_by_header(cells, headers, AUTHORITY_HEADER_ALIASES)
        publication_date = self.first_date(self.value_by_header(cells, headers, PUBLICATION_HEADER_ALIASES))
        deadline = self.first_date(self.value_by_header(cells, headers, DEADLINE_HEADER_ALIASES))
        external_id = self.first_line(self.value_by_header(cells, headers, ID_HEADER_ALIASES))
        url = await self.first_row_url(row, current_url, detail_selector)

        if not title:
            title = await self.first_row_link_text(row, detail_selector)
        if not authority:
            authority = self.detect_authority_from_cells(cells, title)
        if not deadline:
            deadline = self.detect_deadline_from_cells(cells)
        if not publication_date:
            publication_date = self.detect_publication_from_cells(cells)
        if not external_id:
            external_id = self.detect_external_id(cells)

        if not title or not url:
            return None

        return Tender(
            source=self.source,
            title=self.first_line(title),
            authority=self.first_line(authority) or None,
            published_at=publication_date,
            deadline_at=deadline,
            url=url,
            external_id=external_id or None,
            description=" ".join(cells),
        )

    async def enrich_from_detail(self, page: Page, tender: Tender) -> None:
        context = await page.context.browser.new_context()
        detail_page = await context.new_page()
        detail_page.set_default_timeout(self.timeout_ms)
        try:
            await detail_page.goto(tender.url, wait_until="domcontentloaded")
            await detail_page.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = self.clean_text(await detail_page.locator("body").inner_text())
            if not tender.published_at:
                tender.published_at = self.value_after_label(
                    text,
                    ("Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno", "Datum prvního uveřejnění"),
                )
            if not tender.deadline_at:
                tender.deadline_at = self.value_after_label(
                    text,
                    ("Lhůta pro podání nabídek", "Lhůta podání nabídek", "Konec lhůty", "Termín podání"),
                )
            if not tender.authority:
                tender.authority = self.value_after_text_label(text, ("Zadavatel", "Název zadavatele"))
            if not tender.description:
                tender.description = text
        finally:
            await context.close()

    async def goto_next_page(self, page: Page, visited_urls: set[str]) -> bool:
        next_link = page.locator(NEXT_PAGE_SELECTOR).last
        if not await next_link.count():
            return False
        disabled = await next_link.get_attribute("disabled")
        aria_disabled = await next_link.get_attribute("aria-disabled")
        class_name = await next_link.get_attribute("class")
        if disabled is not None or aria_disabled == "true" or "disabled" in (class_name or "").lower():
            return False
        href = await next_link.get_attribute("href")
        if href and href != "#":
            next_url = self.absolute_url(page.url, href)
            if next_url in visited_urls:
                return False
            await page.goto(next_url, wait_until="domcontentloaded")
            return True
        await next_link.click()
        await page.wait_for_load_state("domcontentloaded")
        return page.url not in visited_urls

    @staticmethod
    def value_by_header(cells: list[str], headers: list[str], aliases: tuple[str, ...]) -> str:
        normalized_aliases = tuple(normalize_text(alias) for alias in aliases)
        for index, header in enumerate(headers):
            if index >= len(cells):
                continue
            if any(alias in header for alias in normalized_aliases):
                return cells[index]
        return ""

    @classmethod
    def detect_deadline_from_cells(cls, cells: list[str]) -> str | None:
        for cell in reversed(cells):
            if any(label in normalize_text(cell) for label in ("lhuta", "termin", "deadline", "nabidek")):
                date = cls.first_date(cell)
                if date:
                    return date
        for cell in reversed(cells):
            date = cls.first_date(cell)
            if date:
                return date
        return None

    @classmethod
    def detect_publication_from_cells(cls, cells: list[str]) -> str | None:
        for cell in cells:
            if any(label in normalize_text(cell) for label in ("zverej", "uverej", "publik")):
                date = cls.first_date(cell)
                if date:
                    return date
        return None

    @staticmethod
    def detect_authority_from_cells(cells: list[str], title: str) -> str:
        for cell in cells:
            if cell and cell != title and not DATE_RE.search(cell) and len(cell) > 3:
                return BaseScraper.first_line(cell)
        return ""

    @staticmethod
    def detect_external_id(cells: list[str]) -> str:
        for cell in cells:
            line = BaseScraper.first_line(cell)
            if re.search(r"\b[A-Z]{1,4}\d{2,}|N\d{3}/\d{2}/[A-Z]\d+|\b\d{4,}\b", line):
                return line
        return ""

    @staticmethod
    async def first_row_url(row: Locator, current_url: str, detail_selector: str) -> str | None:
        link = row.locator(detail_selector).first
        if not await link.count():
            link = row.locator("a[href]").first
        href = await link.get_attribute("href") if await link.count() else None
        return BaseScraper.absolute_url(current_url, href) if href else None

    @staticmethod
    async def first_row_link_text(row: Locator, detail_selector: str) -> str:
        link = row.locator(detail_selector).first
        if not await link.count():
            link = row.locator("a[href]").first
        return BaseScraper.clean_text(await link.inner_text()) if await link.count() else ""

    @staticmethod
    def value_after_label(text: str, labels: tuple[str, ...]) -> str | None:
        normalized = BaseScraper.clean_text(text)
        for label in labels:
            pattern = re.compile(rf"{re.escape(label)}\s*:?\s*({DATE_RE.pattern})", re.IGNORECASE)
            match = pattern.search(normalized)
            if match:
                return BaseScraper.first_date(match.group(1))
        return None

    @staticmethod
    def value_after_text_label(text: str, labels: tuple[str, ...]) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        normalized_labels = tuple(normalize_text(label) for label in labels)
        for index, line in enumerate(lines[:-1]):
            if any(label == normalize_text(line).rstrip(":") for label in normalized_labels):
                return lines[index + 1]
        return None

    @staticmethod
    def first_date(value: str | None) -> str | None:
        if not value:
            return None
        match = DATE_RE.search(value.replace("\xa0", " "))
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group(0)).strip()

    @staticmethod
    def first_line(value: str | None) -> str:
        if not value:
            return ""
        return next((line.strip() for line in value.splitlines() if line.strip()), "")

    @staticmethod
    def clean_text(value: str | None) -> str:
        if not value:
            return ""
        text = value.replace("\xa0", " ").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def absolute_url(current_url: str, href: str) -> str:
        return urljoin(current_url, href)

    @staticmethod
    def deduplicate_tenders(tenders: list[Tender]) -> list[Tender]:
        seen: set[str] = set()
        unique: list[Tender] = []
        for tender in tenders:
            key = tender.url or f"{tender.source}|{tender.title}|{tender.authority}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(tender)
        return unique
