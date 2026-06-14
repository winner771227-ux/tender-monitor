"""Scraper pro JOSEPHINE – josephine.proebiz.com

Místo procházení VŠECH zakázek používáme vyhledávání pro každé klíčové slovo.
Tím dostaneme relevantní výsledky bez nutnosti procházet 596 stránek.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, quote

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

# Štítky pro datum zveřejnění na detailní stránce
_PUB_LABELS = (
    "Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno",
    "Datum prvního uveřejnění", "Datum zahájení", "Publication date", "Vytvořeno",
)

# Základní URL pro vyhledávání – ?query= přidáme pro každé klíčové slovo
_SEARCH_URL = "https://josephine.proebiz.com/cs/public-tenders/all?query={keyword}"


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"
    max_pages = 5     # max 5 stránek na jedno klíčové slovo
    max_tenders = 200

    async def scrape_page(self, page: Page) -> list[Tender]:
        """Prohledá JOSEPHINE pro každé klíčové slovo zvlášť."""
        all_tenders: list[Tender] = []

        for keyword in self.keywords:
            search_url = _SEARCH_URL.format(keyword=quote(keyword))
            logger.info("JOSEPHINE hledám: %s -> %s", keyword, search_url)
            try:
                await page.goto(search_url, wait_until="domcontentloaded")
                batch = await self._scrape_keyword(page, keyword)
                logger.info("JOSEPHINE keyword='%s' nalezeno=%s", keyword, len(batch))
                all_tenders.extend(batch)
            except Exception as exc:
                logger.warning("JOSEPHINE keyword='%s' chyba: %s", keyword, exc)
            # Krátká pauza mezi vyhledáváními aby nás server neblokl
            await asyncio.sleep(1)

        return self.deduplicate_tenders(all_tenders)

    async def _scrape_keyword(self, page: Page, keyword: str) -> list[Tender]:
        """Prochází stránky výsledků pro jedno klíčové slovo."""
        tenders: list[Tender] = []
        visited_urls: set[str] = set()

        for page_num in range(self.max_pages):
            if page.url in visited_urls:
                break
            visited_urls.add(page.url)

            try:
                await page.wait_for_selector(
                    "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
                    state="attached",
                    timeout=30_000,
                )
            except Exception:
                # Žádné výsledky pro toto klíčové slovo
                logger.info("JOSEPHINE keyword='%s' stránka %s: žádná tabulka", keyword, page_num + 1)
                break

            rows = await page.locator(
                "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
            ).all()
            rows = [r for r in rows if len(await r.locator("td").all()) >= 7]
            logger.info("JOSEPHINE keyword='%s' stránka %s: %s řádků", keyword, page_num + 1, len(rows))

            for row in rows:
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

                # Datum z detailu pokud není v tabulce
                # Klíčové slovo musí být v NÁZVU zakázky (ne jen fulltextově)
                # JOSEPHINE fulltext vrací i zakázky kde je slovo jen v dokumentech
                from tender_monitor.dedupe import normalize_text
                if normalize_text(keyword) not in normalize_text(tender.title):
                    logger.debug("JOSEPHINE [%s] SKIP (slovo není v názvu): %s", keyword, tender.title[:50])
                    continue

                if not tender.published_at:
                    tender.published_at = await self._get_pub_date(page, tender.url)

                logger.info(
                    "JOSEPHINE [%s] tender=%s published=%s",
                    keyword, tender.title[:45], tender.published_at,
                )
                tenders.append(tender)

            # Přejdi na další stránku
            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        return tenders

    async def _get_pub_date(self, page: Page, tender_url: str) -> str | None:
        ctx = await page.context.browser.new_context()
        detail = await ctx.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            await detail.goto(tender_url, wait_until="domcontentloaded")
            await detail.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = await detail.locator("body").inner_text()

            for label in _PUB_LABELS:
                pattern = re.compile(
                    rf"{re.escape(label)}\s*:?\s*(\d{{2}}\.\d{{2}}\.\d{{4}}(?:\s+\d{{2}}:\d{{2}}:\d{{2}})?)",
                    re.IGNORECASE,
                )
                m = pattern.search(text)
                if m:
                    return m.group(1).strip()

                lines = text.splitlines()
                for i, line in enumerate(lines[:-3]):
                    if label.lower() in line.lower():
                        for next_line in lines[i + 1:i + 4]:
                            m2 = _DATE_RE.search(next_line)
                            if m2:
                                return m2.group(0).strip()

            return None
        except Exception as exc:
            logger.warning("JOSEPHINE detail error %s: %s", tender_url, exc)
            return None
        finally:
            await ctx.close()

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
        # Zkusíme najít datum zveřejnění přímo v buňkách tabulky
        published = None
        for i in [3, 4, 6, 7]:
            if i < len(cells):
                d = cls._date(cells[i])
                if d:
                    published = d
                    break

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
