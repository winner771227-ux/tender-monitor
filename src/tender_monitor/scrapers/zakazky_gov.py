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
import re

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import DATE_RE, BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

MAX_ROWS_PER_KEYWORD = 12

# Řádek s číslem zakázky vypadá typicky jako "RVZ2600110661" nebo "N006/25/V..."
_ID_LINE_RE = re.compile(r"^(RVZ\d{6,}|N\d{3}/\d{2}/[A-Z]\d+|[A-Z]{2,6}\d{4,})$")

# Odkaz na detail zakázky - zkoušíme víc obvyklých vzorů URL najednou
_DETAIL_LINK_SELECTOR = (
    "a[href*='/verejne-zakazky/'], a[href*='/zakazka/'], "
    "a[href*='/detail'], a[href*='/vz/']"
)

_PUBLISHED_LABELS = (
    "Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno",
    "Datum zahájení", "Datum uveřejnění výzvy", "Zahájení řízení",
)
_DEADLINE_LABELS = (
    "Lhůta pro podání nabídek", "Lhůta podání nabídek", "Konec lhůty",
    "Termín podání", "Lhůta pro podání žádostí", "Konec lhůty pro podání nabídek",
)


class ZakazkyGovScraper(BaseScraper):
    source = "Zakázky GOV"
    # Z logu prvního běhu: na /verejne-zakazky Playwright narazil na SKRYTOU
    # kopii vyhledávacího pole (stejný placeholder existuje 2× v DOM – zřejmě
    # kvůli responzivnímu layoutu). Startujeme proto z úvodní stránky, kde je
    # velké vyhledávací pole vidět rovnou bez jakékoli interakce.
    url = "https://zakazky.gov.cz/"
    max_pages = 1
    per_keyword_timeout_ms = 45_000

    async def scrape(self, browser: Browser) -> ScrapeResult:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
            viewport={"width": 1440, "height": 900},
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
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        await page.wait_for_timeout(1_500)
        await self._dismiss_cookie_banner(page)

        # Přepneme na fulltextové vyhledávání (výchozí je "Chytré AI vyhledávání").
        # Pokud viditelný přepínač nenajdeme, pokračujeme i tak - fulltext bývá
        # výchozí chování při psaní přesné fráze.
        fulltext_switch = await self._first_visible(
            page.get_by_text("Fulltextové vyhledávání", exact=False)
        )
        if fulltext_switch is not None:
            try:
                await fulltext_switch.click(timeout=5_000)
                await page.wait_for_timeout(500)
            except Exception:
                logger.debug("Zakázky GOV: klik na přepínač 'Fulltextové vyhledávání' selhal")
        else:
            logger.debug("Zakázky GOV: viditelný přepínač 'Fulltextové vyhledávání' nenalezen")

        # Najdeme VIDITELNÉ vyhledávací pole (log z prvního běhu ukázal, že na
        # stránce existuje i skrytá kopie se stejným placeholderem, proto tady
        # výslovně filtrujeme na is_visible()).
        search_box = await self._first_visible(
            page.get_by_placeholder("Zeptejte se Zakázek GOV", exact=False)
        )
        if search_box is None:
            search_box = await self._first_visible(page.locator("input[type='search']"))
        if search_box is None:
            search_box = await self._first_visible(page.locator("input[type='text']"))
        if search_box is None:
            raise RuntimeError("viditelné vyhledávací pole na stránce nenalezeno")

        await search_box.scroll_into_view_if_needed()
        await search_box.click(timeout=8_000)
        await search_box.fill(keyword)

        # Odešleme hledání - přednostně tlačítkem "Hledat zakázku", jinak Enter
        clicked = False
        search_btn = await self._first_visible(page.get_by_role("button", name="Hledat zakázku"))
        if search_btn is not None:
            try:
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

    @staticmethod
    async def _first_visible(locator):
        """Vrátí první VIDITELNOU shodu z locatoru, nebo None.

        Nutné proto, že stránka umí mít stejný prvek (např. vyhledávací pole)
        vykreslený vícekrát – jednu viditelnou verzi a jednu skrytou (typicky
        kvůli responzivnímu layoutu) – a obyčejné `.first` může trefit tu
        skrytou, na kterou pak Playwright nikdy nedokáže kliknout.
        """
        try:
            count = await locator.count()
        except Exception:
            return None
        for i in range(count):
            candidate = locator.nth(i)
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    @staticmethod
    async def _dismiss_cookie_banner(page: Page) -> None:
        for text in ("Souhlasím", "Přijmout", "Rozumím", "Povolit vše", "Přijmout vše"):
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=2_000)
                    await page.wait_for_timeout(300)
                    return
            except Exception:
                continue

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

            # Karta zakázky - zkusíme najít celý řádek/kartu (obsahuje víc
            # informací než jen samotný odkaz, který někdy obaluje pouze
            # číslo zakázky).
            container = link.locator(
                "xpath=ancestor::tr[1] | ancestor::*[contains(@class,'card')][1] "
                "| ancestor::li[1] | ancestor::article[1]"
            ).first
            context_text = ""
            if await container.count():
                context_text = self.clean_text(await container.inner_text())
            if not context_text:
                context_text = self.clean_text(await link.inner_text())

            lines = [ln.strip() for ln in context_text.splitlines() if ln.strip()]
            # Název bereme z první řádky, která nevypadá jen jako číslo zakázky
            title = next((ln for ln in lines if not _ID_LINE_RE.match(ln) and len(ln) > 4), "")
            if not title and lines:
                title = lines[0]
            if not title or len(title) < 5:
                continue

            # Zadavatel = první další řádka, co není číslo zakázky
            authority = next(
                (ln for ln in lines if ln != title and not _ID_LINE_RE.match(ln) and len(ln) > 2),
                None,
            )
            external_id = next((ln for ln in lines if _ID_LINE_RE.match(ln)), None)

            published = self.value_after_label(context_text, _PUBLISHED_LABELS) or \
                self._fuzzy_date_after_label(context_text, _PUBLISHED_LABELS)
            deadline = self.value_after_label(context_text, _DEADLINE_LABELS) or \
                self._fuzzy_date_after_label(context_text, _DEADLINE_LABELS)

            tender = Tender(
                source=self.source,
                title=title,
                url=url,
                authority=authority,
                published_at=published,
                deadline_at=deadline,
                external_id=external_id,
                description=context_text or None,
            )
            if _is_foreign(tender):
                continue

            # Karta výsledků hledání datum obvykle neukazuje - doplníme ho
            # z detailu zakázky, jinak by filtr na 14denní okno neplatil
            # a do reportu by propadly i staré/neaktuální zakázky.
            if not tender.published_at and not tender.deadline_at:
                await self._enrich_zakazky_gov_detail(page, tender)

            tender.matched_keywords = [keyword]
            tenders.append(tender)
            if len(tenders) >= MAX_ROWS_PER_KEYWORD:
                break

        return self.deduplicate_tenders(tenders)

    async def _enrich_zakazky_gov_detail(self, page: Page, tender: Tender) -> None:
        """Doplní datum zveřejnění/lhůtu z detailu zakázky.

        Na rozdíl od obecné BaseScraper._enrich_from_detail():
        - otevírá novou záložku VE STEJNÉM kontextu (page.context.new_page()),
          takže se zachovají cookies/session ze stránky, na které jsme spustili
          vyhledávání - v novém "prázdném" kontextu portál někdy vrací jinou
          (neúplnou) stránku,
        - čeká na "networkidle", ne jen na to, že <body> existuje v DOM - u
          Angular aplikace se obsah dopisuje až po doběhnutí JS požadavků,
        - chybu loguje na úrovni INFO, aby byla vidět v GitHub Actions logu.
        """
        try:
            detail = await page.context.new_page()
        except Exception as exc:
            logger.info("Zakázky GOV: nepodařilo se otevřít záložku pro detail (%s)", exc)
            return

        try:
            await detail.goto(tender.url, wait_until="domcontentloaded", timeout=20_000)
            try:
                await detail.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            await detail.wait_for_timeout(1_500)

            text = self.clean_text(await detail.locator("body").inner_text())
            if not text:
                logger.info("Zakázky GOV: detail %s se nevykreslil (prázdné tělo stránky)", tender.url)
                return

            if not tender.published_at:
                tender.published_at = self.value_after_label(text, _PUBLISHED_LABELS) or \
                    self._fuzzy_date_after_label(text, _PUBLISHED_LABELS)
            if not tender.deadline_at:
                tender.deadline_at = self.value_after_label(text, _DEADLINE_LABELS) or \
                    self._fuzzy_date_after_label(text, _DEADLINE_LABELS)
            if not tender.authority:
                tender.authority = self.value_after_text_label(text, ("Zadavatel", "Název zadavatele"))
            if not tender.description:
                tender.description = text

            if not tender.published_at and not tender.deadline_at:
                logger.info("Zakázky GOV: v detailu %s se datum nenašlo", tender.url)
        except Exception as exc:
            logger.info("Zakázky GOV: chyba při čtení detailu %s (%s)", tender.url, exc)
        finally:
            await detail.close()

    @staticmethod
    def _fuzzy_date_after_label(text: str, labels: tuple[str, ...]) -> str | None:
        """Najde datum do 40 znaků za popiskem, i když mezi nimi je další text
        (např. "Datum uveřejnění výzvy do systému: 15. 7. 2026")."""
        for label in labels:
            pattern = re.compile(
                rf"{re.escape(label)}.{{0,40}}?({DATE_RE.pattern})",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(text)
            if match:
                return BaseScraper.first_date(match.group(1))
        return None

    async def scrape_page(self, page: Page) -> list[Tender]:
        # Nepoužívá se – veškerá logika je v scrape()/_search_keyword(), protože
        # tento portál potřebuje pro každé klíčové slovo vlastní vyhledávání.
        return []
