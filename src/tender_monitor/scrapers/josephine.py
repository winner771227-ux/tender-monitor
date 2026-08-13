"""Scraper pro JOSEPHINE – josephine.proebiz.com

Pro každé klíčové slovo použije serverový fulltext filtr portálu
(`filter[search]={dotaz}&filter[state]=executed`), stejně jako to dělají
scrapery pro eVeZa, NEN a TenderArena.

PŮVODNÍ PŘÍSTUP (do 2026-08) procházel neomezenou stránku "Všechny soutěže"
bez jakéhokoli filtru a spoléhal na to, že je řazená od nejnovějších zakázek.
To byl omyl: neřazený výpis mísí zakázky z ČR/SK/PL/UK, má stovky stránek
(v době psaní 616) a scraper procházel jen prvních 60 – zakázky mimo tento
výsek se nikdy nedostaly do výsledků, bez ohledu na to, jak nové byly.
Typický případ: "Šternberk, odstranění komplexu budov v areálu zimního
stadionu" (ID 80404) – validní běžící zakázka, kterou starý scraper
nikdy nenašel, protože ležela mimo prvních 60 stránek neřazeného výpisu.

Filtr:
1. Klíčové slovo v NÁZVU zakázky (fulltext portálu hledá šířeji, proto
   ještě ověřujeme shodu v titulku).
2. Pouze běžící zakázky (`filter[state]=executed` = "Probíhající" v UI).
3. Pouze české zakázky (ne SK/PL/UK).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

_SEARCH_URL = (
    "https://josephine.proebiz.com/cs/public-tenders/all"
    "?filter[search]={keyword}&filter[state]=executed"
)

MAX_PAGES_PER_KEYWORD = 5   # výsledky jednoho klíčového slova bývají řádově desítky
MAX_ROWS_PER_KEYWORD = 100


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"

    async def scrape(self, browser: Browser) -> ScrapeResult:
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
        all_tenders: list[Tender] = []
        error_msg: str | None = None

        try:
            for keyword in self.keywords:
                try:
                    found = await self._scrape_keyword(page, keyword)
                    logger.info("JOSEPHINE '%s': nalezeno=%s", keyword, len(found))
                    all_tenders.extend(found)
                except Exception as exc:
                    logger.warning("JOSEPHINE keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)
        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("JOSEPHINE: scraped=%s after_filter=%s", len(unique), len(filtered))
        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def _scrape_keyword(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        search_url = _SEARCH_URL.format(keyword=quote(keyword))
        norm_keyword = normalize_text(keyword)

        current_url = search_url
        for page_num in range(MAX_PAGES_PER_KEYWORD):
            if current_url in visited_urls:
                break
            visited_urls.add(current_url)

            await page.goto(current_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

            try:
                await page.wait_for_selector(
                    "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
                    state="attached", timeout=15_000,
                )
            except Exception:
                logger.info("JOSEPHINE '%s' str. %s: žádné výsledky / timeout", keyword, page_num + 1)
                break

            rows = await page.locator(
                "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
            ).all()
            rows = [r for r in rows if len(await r.locator("td").all()) >= 7]
            logger.info("JOSEPHINE '%s' str. %s: řádků=%s", keyword, page_num + 1, len(rows))

            if not rows:
                break

            for row in rows:
                if len(tenders) >= MAX_ROWS_PER_KEYWORD:
                    break

                cells = [self._clean(await c.inner_text()) for c in await row.locator("td").all()]
                if len(cells) < 7:
                    continue

                link = row.locator("a[href*='/tender/'][href*='/summary']").first
                href = await link.get_attribute("href") if await link.count() else None
                tender = self._build(cells, href, page.url)
                if tender is None:
                    continue

                # Klíčové slovo musí být přímo v názvu (portálový fulltext hledá šířeji)
                if norm_keyword not in normalize_text(tender.title):
                    continue

                # Odmítneme SK/PL/UK zakázky
                if _is_foreign(tender):
                    continue

                tender.matched_keywords = [keyword]
                logger.info("JOSEPHINE ✅ [%s] '%s' lhůta=%s", keyword, tender.title[:50], tender.deadline_at)
                tenders.append(tender)

            if len(tenders) >= MAX_ROWS_PER_KEYWORD:
                break

            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            current_url = next_url

        return tenders

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []

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
        # Sloupce tabulky (ověřeno na živých datech): 0=ID, 1=Číslo spisu VZ,
        # 2=Název zakázky, 3=ikona (prázdné), 4=Zadavatel, 5=Předpokládaná
        # hodnota, 6=Lhůta pro podávání, 7=odkaz na detail.
        now = datetime.now()
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[4]) if len(cells) > 4 else ""
        deadline = cls._date(cells[6]) if len(cells) > 6 else None

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
