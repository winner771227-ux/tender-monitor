"""Scraper pro ASPO – Armádní Servisní, příspěvková organizace

ASPO zadává zakázky výhradně přes NEN (nen.nipez.cz/profil/ASPO).

OPRAVA (18.8.2026) - obě dosavadní cesty byly rozbité, živě ověřeno proti
produkci:

1. XML feed `https://nen.nipez.cz/profil/ASPO/xmldatavz?Typ=1` na novém
   NEN (viz níže) neodpovídá vůbec - přímý fetch z reálného prohlížeče
   visel 45+ sekund bez jakékoli odpovědi (ne timeout/404, prostě nic).
   Feed patrně pochází ze starého NEN a na nové platformě neexistuje.
   Navíc `_scrape_xml()` hádala názvy XML tagů ("zakazka",
   "verejna_zakazka", "contract", "item") bez ověření proti reálné
   odpovědi - i kdyby feed odpovídal, nebylo jisté, že by cokoliv našla.
   XML cestu proto vypínáme, dokud nemáme ověřený vzorek skutečné
   odpovědi.

2. Playwright záloha mířila na `https://nen.nipez.cz/profil/ASPO` -
   tahle URL sice funguje (přesměruje se na nový profil), ale skončí na
   záložce "Základní informace" (kontakty, IČO...), ŽÁDNÁ tabulka
   zakázek tam není. NEN je teď SPA (bundle "nen-lightweb", data přes
   POST /api/datarows?className=...) a seznam zahájených zakázek je na
   samostatné podstránce, kterou předchozí kód nikdy nenačetl - proto
   scraper spolehlivě vracel 0 zakázek, ať feed fungoval nebo ne.

Oprava: Playwright teď jde přímo na
`https://nen.nipez.cz/profily-zadavatelu-platne/detail-profilu/ASPO/zahajene-zakazky`
(živě ověřeno - HTML tabulka s sloupci Systémové číslo NEN / Název
zadávacího postupu / Aktuální stav / Zadavatel / Lhůta podání nabídek,
stejná struktura jako u obecného vyhledávání na NEN, viz nen.py).
Bereme jen řádky se stavem "Neukončen" (stejný princip jako
`stav.startswith("AKTIVNI")` v zakazky_gov.py).
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender, ScrapeResult
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Podstránka profilu ASPO se seznamem zahájených (běžících) zakázek.
# Živě ověřeno 18.8.2026 - vrací HTML tabulku se stejnou strukturou
# sloupců jako obecné vyhledávání na nen.py.
_ZAHAJENE_ZAKAZKY_URL = (
    "https://nen.nipez.cz/profily-zadavatelu-platne/detail-profilu/ASPO/zahajene-zakazky"
)

_AUTHORITY_NAME = "Armádní Servisní, příspěvková organizace"

# Stav, který znamená "zakázka pořád běží" - stejný princip jako u NEN
# vyhledávání (nen.py) a zakazky_gov.py.
_OPEN_STAV = "neukoncen"


class AspoScraper(BaseScraper):
    source = "ASPO"
    url = _ZAHAJENE_ZAKAZKY_URL
    max_pages = 1

    async def scrape(self, browser) -> ScrapeResult:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
        )
        page = await context.new_page()
        try:
            await page.goto(self.url, wait_until="domcontentloaded", timeout=45_000)
            raw = await self.scrape_page(page)
            filtered = self._filter(raw)
            logger.info("ASPO: scraped=%s after_filter=%s", len(raw), len(filtered))
            return ScrapeResult(source=self.source, tenders=filtered)
        except Exception as exc:
            logger.warning("ASPO: NEN profil nedostupný (%s) – přeskakuji bez chyby", exc)
            # Vracíme prázdný výsledek BEZ chyby – NEN výpadek nemá shazovat celý běh
            return ScrapeResult(source=self.source, tenders=[], error=None)
        finally:
            await context.close()

    async def scrape_page(self, page: Page) -> list[Tender]:
        """Přečte tabulku 'Zahájené zakázky' z profilu ASPO na NEN.

        NEN je SPA - tabulka se dorenderuje až po dotažení dat přes
        /api/datarows, proto čekáme na řádky, ne jen na domcontentloaded.
        """
        try:
            await page.wait_for_selector("table tr", state="attached", timeout=45_000)
            await page.wait_for_timeout(3_000)  # doběhnutí případných dalších API volání
        except Exception:
            logger.warning("ASPO: tabulka zahájených zakázek se nenačetla")
            return []

        rows = await page.locator("table tr").all()
        logger.info("ASPO: řádků v tabulce=%s", len(rows))

        tenders: list[Tender] = []
        skipped_stav = 0
        for row in rows:
            cells = [self.clean_text(await c.inner_text()) for c in await row.locator("td").all()]
            if len(cells) < 4:
                continue

            # Sloupce (ověřeno živě 18.8.2026): 0=Detail, 1=Systémové číslo NEN,
            # 2=Název zadávacího postupu, 3=Aktuální stav, 4=Zadavatel,
            # 5=Lhůta podání nabídek, 6=Detail.
            external_id = cells[1] if len(cells) > 1 else ""
            title = cells[2] if len(cells) > 2 else ""
            stav = cells[3] if len(cells) > 3 else ""
            deadline = cells[5] if len(cells) > 5 else None

            if not title or not external_id:
                continue

            # Jen běžící zakázky - stejný princip jako u NEN vyhledávání
            # a zakazky_gov.py. Bez tohohle filtru by prošly i už zadané/
            # zrušené zakázky bez lhůty.
            if stav and normalize_text(stav) != _OPEN_STAV:
                skipped_stav += 1
                continue

            id_slug = external_id.replace("/", "-")
            url = f"https://nen.nipez.cz/verejne-zakazky/detail-zakazky/{id_slug}"

            tenders.append(Tender(
                source=self.source,
                title=title,
                url=url,
                authority=_AUTHORITY_NAME,
                published_at=None,
                deadline_at=deadline or None,
                external_id=external_id or None,
            ))

        logger.info("ASPO: nalezeno=%s skip_stav=%s", len(tenders), skipped_stav)
        return self.deduplicate_tenders(tenders)
