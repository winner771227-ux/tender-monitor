"""Scraper pro Zakázky GOV – nový centrální portál NIPEZ (zakazky.gov.cz).

Portál je moderní SPA (obsah se vykresluje až po spuštění JavaScriptu,
stažené HTML je prázdná "kostra"), takže tabulku/karty zakázek nejde
najít staticky – musíme přes Playwright:
  1. otevřít https://zakazky.gov.cz/verejne-zakazky
  2. přepnout na "Fulltextové vyhledávání" (ne AI vyhledávání)
  3. pro každé klíčové slovo zvlášť vyplnit vyhledávací pole a odeslat
     (stejný princip jako u NEN – portál neumožňuje hledání přes URL parametr)
  4. počkat na vykreslení výsledků a vytáhnout odkazy na detail + okolní text

POZOR – toto je PRVNÍ verze scraperu:
Selektory jsou postavené na viditelném textu stránky (placeholder pole,
text tlačítka, text přepínače), protože skutečnou strukturu vykresleného
DOM nešlo předem ověřit (portál běží na JS frameworku, statické stažení
stránky vrátí jen prázdnou kostru). Po prvním běhu je potřeba zkontrolovat
log v GitHub Actions (Actions → run → job log, hledej řádky "Zakázky GOV")
a podle toho selektory doladit – stejně jako u ostatních portálů v tomto
projektu (NEN, eVeZa apod. procházely stejným laděním).
"""
from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

MAX_ROWS_PER_KEYWORD = 15

# Odkaz na detail zakázky - zkoušíme víc obvyklých vzorů URL najednou
_DETAIL_LINK_SELECTOR = (
    "a[href*='/verejne-zakazky/'], a[href*='/zakazka/'], "
    "a[href*='/detail'], a[href*='/vz/']"
)


class ZakazkyGovScraper(BaseScraper):
    source = "Zakázky GOV"
    url = "https://zakazky.gov.cz/verejne-zakazky"
    max_pages = 1
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
        page.set_default_timeout(self.per_keyword_timeout_ms)
        all_tenders: list[Tender] = []
        error_msg: str | None = None

        try:
            for keyword in self.keywords:
                try:
                    found = await self._search_keyword(page, keyword)
                    logger.info("Zakázky GOV [%s]: nalezeno=%s", keyword, len(found))
                    all_tenders.extend(found)
                except Exception as exc:
                    logger.warning("Zakázky GOV keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)
                await asyncio.sleep(1)
        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("Zakázky GOV: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def _search_keyword(self, page: Page, keyword: str) -> list[Tender]:
        await page.goto(self.url, wait_until="domcontentloaded", timeout=self.per_keyword_timeout_ms)
        # necháme JS aplikaci "nastartovat" a stáhnout data
        await page.wait_for_timeout(3_000)

        # Přepneme na fulltextové vyhledávání (výchozí je "Chytré AI vyhledávání")
        try:
            fulltext_switch = page.get_by_text("Fulltextové vyhledávání", exact=False).first
            if await fulltext_switch.count():
                await fulltext_switch.click(timeout=5_000)
                await page.wait_for_timeout(500)
        except Exception:
            logger.debug("Zakázky GOV: přepínač 'Fulltextové vyhledávání' nenalezen")

        # Najdeme vyhledávací pole - zkusíme víc variant podle typu/placeholderu
        search_box = None
        for locator_fn in (
            lambda: page.get_by_placeholder("Zeptejte se Zakázek GOV", exact=False),
            lambda: page.locator("input[type='search']"),
            lambda: page.locator("input[type='text']"),
        ):
            candidate = locator_fn().first
            if await candidate.count():
                search_box = candidate
                break

        if search_box is None:
            raise RuntimeError("vyhledávací pole na stránce nenalezeno")

        await search_box.click()
        await search_box.fill(keyword)

        # Odešleme hledání - přednostně tlačítkem "Hledat zakázku", jinak Enter
        clicked = False
        try:
            search_btn = page.get_by_role("button", name="Hledat zakázku")
            if await search_btn.count():
                await search_btn.click(timeout=5_000)
                clicked = True
        except Exception:
            pass
        if not clicked:
            await search_box.press("Enter")

        # Počkáme na vykreslení výsledků hledání
        await page.wait_for_timeout(4_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        return await self._extract_results(page, keyword)

    async def _extract_results(self, page: Page, keyword: str) -> list[Tender]:
        tenders: list[Tender] = []
        links = await page.locator(_DETAIL_LINK_SELECTOR).all()
        logger.info("Zakázky GOV [%s]: nalezeno odkazů=%s", keyword, len(links))

        seen_urls: set[str] = set()
        for link in links[: MAX_ROWS_PER_KEYWORD * 3]:
            href = await link.get_attribute("href")
            if not href:
                continue
            url = self.absolute_url(page.url, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = self.clean_text(await link.inner_text())
            if not title or len(title) < 5:
                continue

            # Zkusíme najít okolní řádek/kartu kvůli datu a zadavateli
            container = link.locator(
                "xpath=ancestor::tr[1] | ancestor::*[contains(@class,'card')][1] "
                "| ancestor::li[1] | ancestor::article[1]"
            ).first
            context_text = ""
            if await container.count():
                context_text = self.clean_text(await container.inner_text())

            published = self.value_after_label(
                context_text,
                ("Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno"),
            ) or self.first_date(context_text)
            deadline = self.value_after_label(
                context_text,
                ("Lhůta pro podání nabídek", "Lhůta", "Termín podání", "Konec lhůty"),
            )
            authority = self.value_after_text_label(
                context_text, ("Zadavatel", "Název zadavatele")
            )

            tender = Tender(
                source=self.source,
                title=title,
                url=url,
                authority=authority,
                published_at=published,
                deadline_at=deadline,
                description=context_text or None,
            )
            if _is_foreign(tender):
                continue
            tender.matched_keywords = [keyword]
            tenders.append(tender)
            if len(tenders) >= MAX_ROWS_PER_KEYWORD:
                break

        return self.deduplicate_tenders(tenders)

    async def scrape_page(self, page: Page) -> list[Tender]:
        # Nepoužívá se – veškerá logika je v scrape()/_search_keyword(), protože
        # tento portál potřebuje pro každé klíčové slovo vlastní vyhledávání.
        return []
