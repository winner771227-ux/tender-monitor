"""Scraper pro BASE PROEBIZ – https://baseproebiz.com

Katalog probíhajících eAukcí a poptávek platformy PROEBIZ. Seznam běžících
zakázek se renderuje serverově v jedné HTML tabulce na úvodní stránce.

Zvláštnosti portálu:
- Vyhlašovatel je u neplaceného účtu skrytý (zobrazuje se jako "******").
- Detail zakázky i žádost o účast jsou zamčené za placený účet, veřejná URL
  detailu tedy neexistuje. Ukládáme proto název, lhůtu a odkaz na katalog.
- Země zakázky je v CSS třídě řádku ("country-113" = ČR, 157 = Polsko,
  162 = Slovensko, 159/none = ostatní). Filtrujeme jen české (country-113).

Filtr:
1. Pouze české zakázky (řádek má třídu 'country-113').
2. Alespoň jedno klíčové slovo v názvu (demolice, bourání, odstranění ...).
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# CSS třída české republiky na baseproebiz.com (zjištěno z HTML řádků tabulky)
_COUNTRY_CZ = "113"

_COUNTRY_RE = re.compile(r"country-(\d+)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class BaseProebizScraper(BaseScraper):
    source = "BASE PROEBIZ"
    url = "https://baseproebiz.com/"
    max_pages = 1        # celý seznam probíhajících zakázek je na jedné stránce
    max_tenders = 500

    async def scrape_page(self, page: Page) -> list[Tender]:
        try:
            await page.wait_for_selector(
                "table tbody tr", state="attached", timeout=self.timeout_ms
            )
        except Exception:
            logger.warning("BASE PROEBIZ: tabulka se seznamem zakázek se nenačetla")
            return []

        rows = await page.locator("table tbody tr").all()
        logger.info("BASE PROEBIZ: řádků v tabulce=%s", len(rows))

        tenders: list[Tender] = []
        for row in rows:
            cls = await row.get_attribute("class") or ""
            countries = _COUNTRY_RE.findall(cls)
            # Bereme jen české zakázky
            if _COUNTRY_CZ not in countries:
                continue

            cells = [self.clean_text(await c.inner_text()) for c in await row.locator("td").all()]
            if len(cells) < 4:
                continue

            title = self.first_line(cells[1])
            if not title:
                continue

            # Klíčové slovo musí být v názvu
            norm_title = normalize_text(title)
            matches = [kw for kw in self.keywords if normalize_text(kw) in norm_title]
            if not matches:
                continue

            deadline = self.first_date(cells[3])

            # Vyhlašovatel bývá skrytý (******) – v tom případě necháme prázdné
            authority = self.first_line(cells[2])
            if not authority or set(authority) <= {"*"}:
                authority = None

            # Detail nemá veřejnou URL, vytvoříme stabilní odkaz na katalog
            slug = _SLUG_RE.sub("-", norm_title).strip("-")[:80]
            url = f"https://baseproebiz.com/#{slug}" if slug else "https://baseproebiz.com/"

            tender = Tender(
                source=self.source,
                title=title,
                url=url,
                authority=authority,
                published_at=None,        # portál datum zveřejnění v seznamu neuvádí
                deadline_at=deadline,
                external_id=slug or None,
                matched_keywords=matches,
            )
            logger.info("BASE PROEBIZ ✅ '%s' lhůta=%s", title[:50], deadline)
            tenders.append(tender)

        logger.info("BASE PROEBIZ total=%s", len(tenders))
        return self.deduplicate_tenders(tenders)
