"""Scraper pro NEN - nen.nipez.cz

OPRAVA (18.8.2026): NEN mezitím přešel na nový frontend (SPA, bundle
"nen-lightweb", data se tahají přes POST /api/datarows?className=...).
Živě ověřeno proti produkci - HTML tabulka výsledků vyhledávání
(`table tr`) pořád existuje a sloupce odpovídají tomu, co scraper čekal
(0=Detail, 1=Systémové číslo NEN, 2=Název zadávacího postupu,
3=Aktuální stav, 4=Zadavatel, 5=Lhůta podání nabídek, 6=Detail), takže
samotné parsování řádků fungovalo dál.

Chyba byla jinde: seznam vrací VŠECHNY zakázky odpovídající fulltextu
bez ohledu na stav (Neukončen/Zadán/Zrušen/Ukončení plnění), a hodně už
zadaných/zrušených zakázek nemá v tabulce vyplněnou lhůtu podání. Scraper
"published_at" vůbec neplní (v seznamu není sloupec s datem zveřejnění),
takže taková zakázka měla published_at=None i deadline_at=None -
a BaseScraper._filter() v tom případě zakázku PONECHÁ (viz komentář
"Když nemáme ani lhůtu, ani datum zveřejnění, zakázku ponecháme").
Výsledek: uživateli se hlásily už zadané/zrušené zakázky jako aktivní
příležitosti. Příklad ověřený živě: N006/26/V00023907 "Demolice Českého
pavilonu EXPO 2025..." má stav "Zadán" a prázdnou lhůtu - beze změny by
prošla filtrem jako otevřená.

Oprava: čteme sloupec "Aktuální stav" (cells[3]) a bereme jen zakázky se
stavem "Neukončen" - stejný princip jako u zakazky_gov.py (tam se
filtruje `stav.startswith("AKTIVNI")`).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://nen.nipez.cz/verejne-zakazky"
    "/p:vz:query={keyword}"
)

MAX_ROWS_PER_KEYWORD = 40  # původně 5 — kvůli tomu unikaly zakázky

# Stavy zakázky, které bereme jako "pořád běžící" - vše ostatní (Zadán,
# Zrušen, Ukončení plnění, ...) přeskočíme, i kdyby v tabulce chyběla lhůta.
_OPEN_STAV = "neukoncen"


class NenScraper(BaseScraper):
    source = "NEN"
    url = "https://nen.nipez.cz/verejne-zakazky"
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
        all_tenders: list[Tender] = []
        error_msg = None
        current_year = datetime.now().year % 100  # např. 26

        try:
            for keyword in self.keywords:
                search_url = _SEARCH_URL.format(keyword=quote(keyword))
                logger.info("NEN hledam: '%s'", keyword)
                try:
                    await page.goto(
                        search_url, wait_until="domcontentloaded",
                        timeout=self.per_keyword_timeout_ms,
                    )
                    await page.wait_for_timeout(6_000)

                    tables = await page.locator("table").count()
                    text_len = len(await page.locator("body").inner_text())
                    logger.info("NEN '%s': tables=%s text_len=%s", keyword, tables, text_len)

                    rows_all = await page.locator("table tr").all()
                    logger.info("NEN '%s': radky=%s", keyword, len(rows_all))

                    found_kw = 0
                    skipped_stav = 0
                    for row in rows_all:
                        if found_kw >= MAX_ROWS_PER_KEYWORD:
                            break

                        cells = [
                            (await c.inner_text()).strip()
                            for c in await row.locator("td").all()
                        ]
                        if len(cells) < 4:
                            continue

                        title = cells[2] if len(cells) > 2 and len(cells[2]) > 5 else ""
                        if not title or cells[2].startswith("N006"):
                            for idx in [3, 4, 1]:
                                if idx < len(cells) and len(cells[idx]) > 5:
                                    if not cells[idx].startswith("N006"):
                                        title = cells[idx]
                                        break

                        if not title:
                            continue

                        # Sloupec "Aktuální stav" (index 3) - bereme jen běžící
                        # zakázky. Bez tohohle filtru procházely i už zadané/
                        # zrušené zakázky bez lhůty (viz docstring nahoře).
                        stav = cells[3].strip() if len(cells) > 3 else ""
                        if stav and normalize_text(stav) != _OPEN_STAV:
                            skipped_stav += 1
                            continue

                        external_id = cells[1] if len(cells) > 1 else ""
                        if external_id and "/" in external_id:
                            id_slug = external_id.replace("/", "-")
                            row_url = f"https://nen.nipez.cz/verejne-zakazky/detail-zakazky/{id_slug}"
                        else:
                            any_link = row.locator("a[href]").first
                            if not await any_link.count():
                                continue
                            href = await any_link.get_attribute("href")
                            if not href:
                                continue
                            row_url = f"https://nen.nipez.cz{href}" if href.startswith("/") else href

                        # Odfiltrovat staré zakázky podle roku v čísle zakázky
                        # N006/25/V00036754 = rok 2025, N006/26/... = rok 2026
                        if external_id:
                            year_match = re.search(r'N006[/-](\d{2})[/-]', external_id)
                            if year_match and int(year_match.group(1)) < current_year:
                                logger.debug("NEN skip stará zakázka %s", external_id)
                                continue

                        t = Tender(
                            source="NEN",
                            title=title,
                            url=row_url,
                            authority=cells[4] if len(cells) > 4 else None,
                            published_at=None,
                            deadline_at=cells[5] if len(cells) > 5 else None,
                            external_id=external_id or None,
                        )

                        if _is_foreign(t):
                            continue

                        t.matched_keywords = [keyword]
                        logger.info("NEN [%s] nalezena: '%s'", keyword, t.title[:60])
                        all_tenders.append(t)
                        found_kw += 1

                    logger.info(
                        "NEN '%s': found=%s skip_stav=%s", keyword, found_kw, skipped_stav
                    )

                except Exception as exc:
                    logger.warning("NEN keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)

                await asyncio.sleep(1)

        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("NEN: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
