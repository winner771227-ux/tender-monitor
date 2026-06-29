"""Base class and shared helpers for all tender scrapers."""
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

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

DATE_RE = re.compile(
    r"\b(?:\d{1,2}[.]\s*\d{1,2}[.]\s*\d{4}|\d{4}-\d{2}-\d{2})"
    r"(?:\s*(?:do|v|at)?\s*\d{1,2}:\d{2}(?::\d{2})?)?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# CSS / XPath selectors
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Column-header aliases (used to map table columns to fields)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Slovak / Polish word lists – used to reject non-Czech tenders
# ---------------------------------------------------------------------------

_SK_WORDS = {
    # Slovenská města
    "bratislava", "košice", "prešov", "zilina", "nitra", "trnava", "trencin", "trenčín",
    "banska bystrica", "bardejov", "trebisov", "trebišov", "vranov", "poprad",
    "michalovce", "humenne", "humenné", "roznava", "rožňava",
    # Slovenský stát
    "slovensko", "slovak", "slovenska republika", "slovenská republika",
    # Slovenské výrazy ve veřejných zakázkách
    "zakazka", "zakazky", "zakaziek", "zákazka", "zákazky",
    "obstaravanie", "obstarávanie", "obstaravatel", "obstarávateľ",
    "uchadzac", "uchádzač", "sutaz", "súťaž", "verejná súťaž",
    # Slovenská gramatika – předložky a koncovky typické pro slovenštinu
    " pre zavod", " pre analyzu", " pre analyzu", " pri ms", " pri zs",
    "dns vakm", "dns mfsr",
    # Slovenská slova která se v češtině nevyskytují
    "zariadeni", "zariadenia", "zariadenie",   # zařízení
    "vodicov", "vodičov",                        # řidičů
    "rozsirovanie", "rozširenie", "rozšírenie",  # rozšíření
    "zvaranie", "zváranie",                      # svařování
    "kolajovych", "koľajových", "kolajnic", "koľajníc",
    "polne", "poľné", "polnych", "poľných",      # polní
    "lesnicke", "lesnícke",
    "namestie", "námestie", "naberezie", "nábrežie",
    "pamatnik", "pamätník",
    "brusenie", "brúsenie",
    "eurominci", "euromincí",
    "socialnych", "sociálnych",
    "detekčna", "detekčná", "detekčne", "detekčné",
}

_PL_WORDS = {
    "zamowienie", "zamówienie", "zamówień", "przetarg", "zamawiajacy", "zamawiający",
    "wykonawca", "rzeczpospolita", "warszawa", "krakow", "kraków", "wroclaw", "wrocław",
    # Polská slova z logu
    "zapytanie", "ofertowe", "jednorazowa", "dostawa",
    "czerpania", "punktow", "punktów", "budowy",
}


def _is_foreign(tender: Tender) -> bool:
    """Return True when the tender appears to be Slovak or Polish."""
    text = normalize_text(
        " ".join(part or "" for part in (tender.title, tender.authority, tender.description))
    )
    for word in _SK_WORDS | _PL_WORDS:
        if normalize_text(word) in text:
            return True
    # URL-based check (e.g. .sk or .pl domains)
    url_lower = (tender.url or "").lower()
    if re.search(r"https?://[^/]+\.(sk|pl)[/:]", url_lower):
        return True
    return False


# ---------------------------------------------------------------------------
# Base scraper
# ---------------------------------------------------------------------------

class BaseScraper(ABC):
    """Abstract base class for Playwright tender scrapers."""

    source: str
    url: str
    max_pages: int = 10          # at most 10 pages per portal
    max_tenders: int = 100       # hard limit: first 100 tenders per portal

    def __init__(self, keywords: tuple[str, ...], timeout_ms: int) -> None:
        self.keywords = keywords
        self.timeout_ms = timeout_ms

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    async def scrape(self, browser: Browser) -> ScrapeResult:
        # Vytvoříme kontext s reálným User-Agentem aby portály nás neblokly jako robota
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
            },
        )
        page = await context.new_page()
        page.set_default_timeout(max(self.timeout_ms, 120_000))
        try:
            await page.goto(self.url, wait_until="domcontentloaded", timeout=max(self.timeout_ms, 120_000))
            raw = await self.scrape_page(page)
            # Apply hard limit BEFORE keyword / date filtering
            raw = raw[: self.max_tenders]
            filtered = self._filter(raw)
            logger.info("%s: scraped=%s after_filter=%s", self.source, len(raw), len(filtered))
            return ScrapeResult(source=self.source, tenders=filtered)
        except Exception as exc:
            logger.exception("Scraper %s failed", self.source)
            return ScrapeResult(source=self.source, tenders=[], error=str(exc))
        finally:
            await context.close()

    @abstractmethod
    async def scrape_page(self, page: Page) -> list[Tender]:
        """Extract raw tenders from the already-opened portal page."""

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter(self, tenders: list[Tender]) -> list[Tender]:
        now = datetime.now()
        cutoff = now - timedelta(days=14)
        result: list[Tender] = []
        for tender in tenders:
            # 1. Odmítnout slovenské a polské zakázky
            if _is_foreign(tender):
                logger.debug("SKIP foreign: %s", tender.title)
                continue
            # 2. Musí odpovídat alespoň jedno klíčové slovo
            if tender.matched_keywords:
                matches = tender.matched_keywords
            else:
                matches = self._keyword_matches(tender)
            if not matches:
                continue
            # 3. Datum ZVEŘEJNĚNÍ (published_at) nesmí být starší než 14 dní
            published = self._parse_date(tender.published_at) if tender.published_at else None
            if published is not None and published < cutoff:
                logger.info(
                    "SKIP stará published=%s: %s",
                    tender.published_at, tender.title[:60],
                )
                continue
            # 4. Zakázky bez published_at - filtrujeme podle lhůty podání
            #    Pokud lhůta vypršela před více než 60 dny, zakázka je stará
            if published is None:
                deadline = self._parse_date(tender.deadline_at) if tender.deadline_at else None
                if deadline is not None and deadline < (now - timedelta(days=60)):
                    logger.info(
                        "SKIP stará bez data, lhůta=%s: %s",
                        tender.deadline_at, tender.title[:60],
                    )
                    continue
                if deadline is None:
                    logger.info("POZOR bez data zveřejnění: %s – %s", self.source, tender.title[:60])

            tender.matched_keywords = matches
            result.append(tender)
        return result

    def _keyword_matches(self, tender: Tender) -> list[str]:
        haystack = normalize_text(
            " ".join(part or "" for part in (tender.title, tender.description, tender.authority))
        )
        return [kw for kw in self.keywords if normalize_text(kw) in haystack]

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        if not value:
            return None
        # Normalizujeme mezery kolem teček: "18. 11. 2024" -> "18.11.2024"
        value = re.sub(r'\s*\.\s*', '.', value.strip())
        for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value.strip()[:19], fmt)
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # Page-collection helpers
    # ------------------------------------------------------------------

    async def collect_table_tenders(
        self,
        page: Page,
        table_xpath: str,
        *,
        detail_selector: str = DETAIL_LINK_SELECTOR,
        open_detail: bool = False,
    ) -> list[Tender]:
        tenders: list[Tender] = []
        rows = await page.locator(f"xpath={table_xpath}//tr[td]").all()
        headers = await self._table_headers(page, table_xpath)
        for row in rows:
            cells = [self.clean_text(await cell.inner_text()) for cell in await row.locator("td").all()]
            if not any(cells):
                continue
            tender = await self._tender_from_cells(row, cells, headers, page.url, detail_selector)
            if tender is None:
                continue
            if open_detail:
                await self._enrich_from_detail(page, tender)
            tenders.append(tender)
        return self.deduplicate_tenders(tenders)

    async def collect_card_tenders(
        self,
        page: Page,
        container_selector: str,
        *,
        detail_selector: str = DETAIL_LINK_SELECTOR,
        open_detail: bool = False,
    ) -> list[Tender]:
        tenders: list[Tender] = []
        for container in await page.locator(container_selector).all():
            text = self.clean_text(await container.inner_text())
            if not text:
                continue
            url = await self._first_href(container, page.url, detail_selector)
            title = await self._first_link_text(container, detail_selector) or self.first_line(text)
            if not title or not url:
                continue
            tender = Tender(
                source=self.source,
                title=title,
                url=url,
                authority=self.value_after_text_label(text, ("Zadavatel", "Název zadavatele", "Organizace")),
                published_at=self.value_after_label(text, ("Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno")),
                deadline_at=self.value_after_label(text, ("Lhůta pro podání nabídek", "Lhůta", "Termín podání", "Konec lhůty")),
                external_id=self.detect_external_id(text.splitlines()),
                description=text,
            )
            if open_detail:
                await self._enrich_from_detail(page, tender)
            tenders.append(tender)
        return self.deduplicate_tenders(tenders)

    async def goto_next_page(self, page: Page, visited_urls: set[str]) -> bool:
        try:
            next_link = page.locator(NEXT_PAGE_SELECTOR).last
            if not await next_link.count():
                return False
            disabled = await next_link.get_attribute("disabled")
            aria_disabled = await next_link.get_attribute("aria-disabled")
            class_attr = await next_link.get_attribute("class") or ""
            if disabled is not None or aria_disabled == "true" or "disabled" in class_attr.lower():
                return False
            href = await next_link.get_attribute("href")
            if href and href != "#":
                next_url = self.absolute_url(page.url, href)
                if next_url in visited_urls:
                    return False
                await page.goto(next_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                return True
            await next_link.click()
            await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            return page.url not in visited_urls
        except Exception as exc:
            logger.warning("%s: stránkování selhalo (%s) – končím na aktuální stránce", self.source, exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _table_headers(self, page: Page, table_xpath: str) -> list[str]:
        cells = await page.locator(f"xpath={table_xpath}//tr[th][1]/th").all()
        return [normalize_text(await c.inner_text()) for c in cells]

    async def _tender_from_cells(
        self,
        row: Locator,
        cells: list[str],
        headers: list[str],
        current_url: str,
        detail_selector: str,
    ) -> Tender | None:
        title = self._col(cells, headers, TITLE_HEADER_ALIASES)
        authority = self._col(cells, headers, AUTHORITY_HEADER_ALIASES)
        published = self.first_date(self._col(cells, headers, PUBLICATION_HEADER_ALIASES))
        deadline = self.first_date(self._col(cells, headers, DEADLINE_HEADER_ALIASES))
        external_id = self.first_line(self._col(cells, headers, ID_HEADER_ALIASES))
        url = await self._first_href(row, current_url, detail_selector)

        if not title:
            title = await self._first_link_text(row, detail_selector)
        if not authority:
            authority = self._authority_from_cells(cells, title)
        if not deadline:
            deadline = self._deadline_from_cells(cells)
        if not published:
            published = self._published_from_cells(cells)
        if not external_id:
            external_id = self.detect_external_id(cells)

        if not title or not url:
            return None

        return Tender(
            source=self.source,
            title=self.first_line(title),
            authority=self.first_line(authority) or None,
            published_at=published,
            deadline_at=deadline,
            url=url,
            external_id=external_id or None,
            description=" ".join(cells),
        )

    async def _enrich_from_detail(self, page: Page, tender: Tender) -> None:
        ctx = await page.context.browser.new_context()
        detail = await ctx.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            await detail.goto(tender.url, wait_until="domcontentloaded")
            await detail.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = self.clean_text(await detail.locator("body").inner_text())
            if not tender.published_at:
                tender.published_at = self.value_after_label(
                    text, ("Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno", "Datum prvního uveřejnění")
                )
            if not tender.deadline_at:
                tender.deadline_at = self.value_after_label(
                    text, ("Lhůta pro podání nabídek", "Lhůta podání nabídek", "Konec lhůty", "Termín podání")
                )
            if not tender.authority:
                tender.authority = self.value_after_text_label(text, ("Zadavatel", "Název zadavatele"))
            if not tender.description:
                tender.description = text
        except Exception:
            logger.debug("Could not enrich detail for %s", tender.url)
        finally:
            await ctx.close()

    # ------------------------------------------------------------------
    # Static / class helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _col(cells: list[str], headers: list[str], aliases: tuple[str, ...]) -> str:
        norm_aliases = tuple(normalize_text(a) for a in aliases)
        for i, header in enumerate(headers):
            if i >= len(cells):
                continue
            if any(a in header for a in norm_aliases):
                return cells[i]
        return ""

    @classmethod
    def _deadline_from_cells(cls, cells: list[str]) -> str | None:
        for cell in reversed(cells):
            if any(lbl in normalize_text(cell) for lbl in ("lhuta", "termin", "deadline", "nabidek")):
                d = cls.first_date(cell)
                if d:
                    return d
        for cell in reversed(cells):
            d = cls.first_date(cell)
            if d:
                return d
        return None

    @classmethod
    def _published_from_cells(cls, cells: list[str]) -> str | None:
        for cell in cells:
            if any(lbl in normalize_text(cell) for lbl in ("zverej", "uverej", "publik")):
                d = cls.first_date(cell)
                if d:
                    return d
        return None

    @staticmethod
    def _authority_from_cells(cells: list[str], title: str) -> str:
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
    async def _first_href(row: Locator, current_url: str, detail_selector: str) -> str | None:
        link = row.locator(detail_selector).first
        if not await link.count():
            link = row.locator("a[href]").first
        href = await link.get_attribute("href") if await link.count() else None
        return BaseScraper.absolute_url(current_url, href) if href else None

    @staticmethod
    async def _first_link_text(row: Locator, detail_selector: str) -> str:
        link = row.locator(detail_selector).first
        if not await link.count():
            link = row.locator("a[href]").first
        return BaseScraper.clean_text(await link.inner_text()) if await link.count() else ""

    @staticmethod
    def value_after_label(text: str, labels: tuple[str, ...]) -> str | None:
        for label in labels:
            pattern = re.compile(rf"{re.escape(label)}\s*:?\s*({DATE_RE.pattern})", re.IGNORECASE)
            match = pattern.search(BaseScraper.clean_text(text))
            if match:
                return BaseScraper.first_date(match.group(1))
        return None

    @staticmethod
    def value_after_text_label(text: str, labels: tuple[str, ...]) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        norm_labels = tuple(normalize_text(l) for l in labels)
        for i, line in enumerate(lines[:-1]):
            if any(lbl == normalize_text(line).rstrip(":") for lbl in norm_labels):
                return lines[i + 1]
        return None

    @staticmethod
    def first_date(value: str | None) -> str | None:
        if not value:
            return None
        match = DATE_RE.search(value.replace("\xa0", " "))
        return re.sub(r"\s+", " ", match.group(0)).strip() if match else None

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
        for t in tenders:
            key = t.url or f"{t.source}|{t.title}|{t.authority}"
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique
