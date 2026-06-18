"""Scraper pro ASPO – Armádní Servisní, příspěvková organizace

ASPO zadává zakázky výhradně přes NEN (nen.nipez.cz/profil/ASPO).
Tento scraper prohledává přímo jejich NEN profil a XML feed.
"""
from __future__ import annotations

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender, ScrapeResult
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})|\d{4}-\d{2}-\d{2}\b")

# XML feed ASPO z NEN (veřejný, bez přihlášení)
_XML_URL = "https://nen.nipez.cz/profil/ASPO/xmldatavz?Typ=1"
# Záložní – profil ASPO přímo v NEN
_NEN_PROFILE_URL = "https://nen.nipez.cz/profil/ASPO"


class AspoScraper(BaseScraper):
    source = "ASPO"
    url = _NEN_PROFILE_URL
    max_pages = 1

    async def scrape(self, browser) -> ScrapeResult:
        """Nejdřív zkusíme XML feed, pak Playwright jako zálohu."""
        # 1. Pokus: XML feed (rychlý, spolehlivý)
        tenders = self._scrape_xml()
        if tenders is not None:
            filtered = self._filter(tenders)
            logger.info("ASPO XML: scraped=%s after_filter=%s", len(tenders), len(filtered))
            return ScrapeResult(source=self.source, tenders=filtered)

        # 2. Záloha: Playwright scraping NEN profilu (s kratším timeoutem,
        #    aby výpadek NEN nezdržel celý běh)
        logger.info("ASPO XML nedostupný, zkouším Playwright (krátký timeout)")
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
            logger.info("ASPO Playwright: scraped=%s after_filter=%s", len(raw), len(filtered))
            return ScrapeResult(source=self.source, tenders=filtered)
        except Exception as exc:
            logger.warning("ASPO: NEN profil nedostupný (%s) – přeskakuji bez chyby", exc)
            # Vracíme prázdný výsledek BEZ chyby – NEN výpadek nemá shazovat celý běh
            return ScrapeResult(source=self.source, tenders=[], error=None)
        finally:
            await context.close()

    def _scrape_xml(self) -> list[Tender] | None:
        """Stáhne a parsuje XML feed zakázek ASPO."""
        try:
            req = urllib.request.Request(_XML_URL, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
                "Accept": "application/xml, text/xml, */*",
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()

            root = ET.fromstring(data)
            tenders: list[Tender] = []

            # XML struktura NEN: <VEREJNA_ZAKAZKA> nebo podobné
            # Projdeme všechny elementy a hledáme zakázky
            ns = {"": ""}  # namespace handling
            for zakazka in root.iter():
                tag = zakazka.tag.split("}")[-1].lower()  # odstranit namespace
                if tag not in ("zakazka", "verejna_zakazka", "contract", "item"):
                    continue

                # Extrahujeme pole
                def get(names):
                    for name in names:
                        el = zakazka.find(f".//{name}")
                        if el is None:
                            # bez namespace
                            for child in zakazka.iter():
                                if child.tag.split("}")[-1].lower() == name.lower():
                                    return (child.text or "").strip()
                    return ""

                title = get(["NAZEV", "NAZEV_ZP", "PREDMET", "nazev", "title"])
                url = get(["URL", "ODKAZ", "ADRESA", "url", "link"])
                published = get(["DATUM_UVEREJNENI", "ZVEREJNENO", "DATE_PUBLISHED", "datum"])
                deadline = get(["LHUTA_PODANI", "DEADLINE", "lhuta"])
                authority = "Armádní Servisní, p.o."
                external_id = get(["ID", "CISLO", "EVIDENCNI_CISLO", "id"])

                if not title:
                    continue
                if not url:
                    url = f"{_NEN_PROFILE_URL}#{external_id}" if external_id else _NEN_PROFILE_URL

                tenders.append(Tender(
                    source=self.source,
                    title=title,
                    url=url,
                    authority=authority,
                    published_at=published or None,
                    deadline_at=deadline or None,
                    external_id=external_id or None,
                ))

            logger.info("ASPO XML: nalezeno %s zakázek", len(tenders))
            return tenders

        except Exception as exc:
            logger.warning("ASPO XML chyba: %s", exc)
            return None

    async def scrape_page(self, page: Page) -> list[Tender]:
        """Záložní Playwright scraping NEN profilu ASPO."""
        tenders: list[Tender] = []
        visited: set[str] = set()
        table_xpath = "//table[.//th]"

        for _ in range(self.max_pages):
            if page.url in visited:
                break
            visited.add(page.url)
            try:
                await page.wait_for_selector("body", state="attached", timeout=60_000)
                await page.wait_for_timeout(5_000)  # NEN načítá JS
            except Exception:
                break

            tables = await page.locator("table").count()
            text_len = len(await page.locator("body").inner_text())
            logger.info("ASPO NEN str: tables=%s text_len=%s", tables, text_len)

            batch = await self.collect_table_tenders(page, table_xpath)
            for t in batch:
                t.authority = "Armádní Servisní, p.o."
                tenders.append(t)

            if not await self.goto_next_page(page, visited):
                break

        return self.deduplicate_tenders(tenders)
