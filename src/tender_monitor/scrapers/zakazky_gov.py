"""Scraper pro Zakázky GOV – centrální portál NIPEZ (zakazky.gov.cz).

VERZE 2 – přímé volání API (bez klikání v prohlížeči).

Z prvního běhu (UI scraping) a následné analýzy sítě portálu víme:
  * Vyhledávání volá POST https://api.isd.nipez.cz/isd/seznam/zakazek/hlavni-seznam
    s tělem: {"filtr": {"klicova_slova": ["demolice"], "skupinaZakazek": "VSE"},
              "strankovani": {"stranka": 1, "pocet_zaznamu": N},
              "razeni": {"atribut": "DATUM_UVEREJNENI_NA_ZAKAZKY_GOV",
                         "typ_razeni": "SESTUPNE"}}
    Odpověď: {"polozky": [...], "posledni_stranka": bool}. Záznam obsahuje
    identifikator_NIPEZ, nazev_verejne_zakazky, nazev_zadavatele,
    popis_predmetu, lhuta_pro_podani (ISO), stav, typ_zadavaciho_postupu.
    POZOR: datum uveřejnění v odpovědi NENÍ (proto v 1. verzi chybělo).
  * Datum uveřejnění vrací detail zakázky:
    GET https://api.isd.nipez.cz/isd/detail/zakazky/verejna-zakazka/{id}
    v poli "uverejneniNaZakazkyGov" (ISO, např. "2026-07-17T06:47:51.85Z").
  * Webový detail pro uživatele:
    https://zakazky.gov.cz/verejne-zakazky/detail-zakazky/{id}
  * Předpokládaná hodnota zakázky: v detailu je pole
    "predpokladana_hodnota_bude_uverejnena" (bool) - u obou ověřených vzorků
    (RVZ2600110843, RVZ2600108692) bylo FALSE, tedy zadavatel hodnotu
    nezveřejnil (běžná praxe u českých veřejných zakázek, není chyba
    scraperu). Pokud je TRUE, přesnou strukturu čísla jsme naživo neověřili
    (nebyl po ruce vzorek) - scraper proto hodnotu hledá obecně, podle
    libovolného klíče obsahujícího "hodnota" s číselnou částkou, a když ji
    nenajde, aspoň doplní kategorii "typ_verejne_zakazky_dle_vyse_
    predpokladane_hodnoty" (nadlimitní/podlimitní/malého rozsahu) do popisu -
    at je z karty v CRM aspoň vidět řádová velikost zakázky.

Řazení SESTUPNE podle data uveřejnění znamená, že bereme nejnovější zakázky,
což přesně odpovídá 14dennímu filtru v BaseScraper._filter().
"""
from __future__ import annotations

import asyncio
import logging

from playwright.async_api import APIRequestContext, Browser, Page

from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

MAX_ROWS_PER_KEYWORD = 12
MAX_DESCRIPTION_CHARS = 800

API_SEARCH_URL = "https://api.isd.nipez.cz/isd/seznam/zakazek/hlavni-seznam"
API_DETAIL_URL = "https://api.isd.nipez.cz/isd/detail/zakazky/verejna-zakazka/{id}"
WEB_DETAIL_URL = "https://zakazky.gov.cz/verejne-zakazky/detail-zakazky/{id}"

# Reportujeme jen aktivní zakázky. Pozorované stavy v API: AKTIVNI_NEUKONCEN,
# DOKONCEN_ZADAN, UKONCENO_PLNENI_SMLOUVY - POZOR, "AKTIVNI_NEUKONCEN" obsahuje
# substring "UKONCEN", proto se testuje prefix "AKTIVNI", ne substring.

# Čitelné popisky kategorie zakázky dle výše předpokládané hodnoty (fallback,
# když portál přesnou částku nezveřejní).
_KATEGORIE_HODNOTY = {
    "NADLIMITNI_VEREJNA_ZAKAZKA": "nadlimitní veřejná zakázka",
    "PODLIMITNI_VEREJNA_ZAKAZKA": "podlimitní veřejná zakázka",
    "VEREJNA_ZAKAZKA_MALEHO_ROZSAHU": "veřejná zakázka malého rozsahu",
}

# Klíče v detailu zakázky, které jsou o hodnotě, ale NEJSOU číslo v Kč
# (booleovský příznak "bude zveřejněna" apod.) - vylučujeme je z hledání čísla.
_HODNOTA_KEY_EXCLUDE = ("bude_uverejnena", "bude_zverejnena")


class ZakazkyGovScraper(BaseScraper):
    source = "Zakázky GOV"
    url = "https://zakazky.gov.cz/"
    request_timeout_ms = 30_000

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Cache detailů: stejná zakázka se objevuje pod více klíčovými slovy,
        # detail stahujeme jen jednou za běh. Hodnoty: (published, hodnota_radek).
        self._detail_cache: dict[str, tuple[str | None, str | None]] = {}

    async def scrape(self, browser: Browser) -> ScrapeResult:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="cs-CZ",
        )
        all_tenders: list[Tender] = []
        error_msg: str | None = None

        try:
            request = context.request
            for keyword in self.keywords:
                try:
                    found = await self._search_keyword(request, keyword)
                    logger.info("Zakázky GOV [%s]: nalezeno=%s", keyword, len(found))
                    all_tenders.extend(found)
                except Exception as exc:
                    logger.warning("Zakázky GOV keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)
                await asyncio.sleep(0.5)
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

    async def _search_keyword(self, request: APIRequestContext, keyword: str) -> list[Tender]:
        payload = {
            "filtr": {"klicova_slova": [keyword], "skupinaZakazek": "VSE"},
            "strankovani": {"stranka": 1, "pocet_zaznamu": MAX_ROWS_PER_KEYWORD},
            "razeni": {
                "atribut": "DATUM_UVEREJNENI_NA_ZAKAZKY_GOV",
                "typ_razeni": "SESTUPNE",
            },
        }
        response = await request.post(
            API_SEARCH_URL, data=payload, timeout=self.request_timeout_ms
        )
        if not response.ok:
            body = (await response.text())[:300]
            raise RuntimeError(f"API vyhledávání HTTP {response.status}: {body}")
        data = await response.json()
        records = data.get("polozky") or []
        logger.info("Zakázky GOV [%s]: API vrátilo záznamů=%s", keyword, len(records))

        tenders: list[Tender] = []
        for rec in records[:MAX_ROWS_PER_KEYWORD]:
            if not isinstance(rec, dict):
                continue
            ext_id = rec.get("identifikator_NIPEZ")
            title = self.clean_text(rec.get("nazev_verejne_zakazky"))
            if not ext_id or not title:
                continue

            stav = (rec.get("stav") or "").upper()
            if stav and not stav.startswith("AKTIVNI"):
                logger.debug("Zakázky GOV skip stav=%s: %s", stav, title[:60])
                continue

            deadline = rec.get("lhuta_pro_podani") or None
            if isinstance(deadline, str):
                # "2026-08-19T08:00:00Z" -> "2026-08-19T08:00:00"
                # (formát, který umí BaseScraper._parse_date)
                deadline = deadline.strip()[:19] or None

            published, hodnota_radek = await self._fetch_detail_extra(request, ext_id)

            description = self.clean_text(rec.get("popis_predmetu"))
            if len(description) > MAX_DESCRIPTION_CHARS:
                description = description[:MAX_DESCRIPTION_CHARS] + "…"
            if hodnota_radek:
                description = f"{hodnota_radek}\n\n{description}" if description else hodnota_radek

            tender = Tender(
                source=self.source,
                title=title,
                url=WEB_DETAIL_URL.format(id=ext_id),
                authority=self.clean_text(rec.get("nazev_zadavatele")) or None,
                published_at=published,
                deadline_at=deadline,
                external_id=ext_id,
                description=description or None,
            )
            if _is_foreign(tender):
                continue
            tender.matched_keywords = [keyword]
            tenders.append(tender)

        return self.deduplicate_tenders(tenders)

    async def _fetch_detail_extra(
        self, request: APIRequestContext, ext_id: str
    ) -> tuple[str | None, str | None]:
        """Stáhne detail zakázky a vrátí (datum_uverejneni, radek_o_hodnote).

        Detail se pro každou zakázku stahuje jen jednou za běh (cache),
        chyba detailu zakázku nezahazuje - jen zůstane bez data/hodnoty.
        """
        if ext_id in self._detail_cache:
            return self._detail_cache[ext_id]

        published: str | None = None
        hodnota_radek: str | None = None
        try:
            response = await request.get(
                API_DETAIL_URL.format(id=ext_id), timeout=self.request_timeout_ms
            )
            if response.ok:
                data = await response.json()
                if isinstance(data, dict):
                    # Datum uveřejnění - přesný název pole (stav k 07/2026)
                    value = data.get("uverejneniNaZakazkyGov")
                    if not isinstance(value, str):
                        # Záloha: kdyby portál pole přejmenoval, vezmeme první
                        # textové pole, jehož název obsahuje "uverejnen"
                        value = next(
                            (
                                v for k, v in data.items()
                                if isinstance(v, str) and "uverejnen" in k.lower()
                            ),
                            None,
                        )
                    published = self.first_date(value) if value else None
                    hodnota_radek = self._hodnota_radek(data)
            else:
                logger.debug("Zakázky GOV detail %s: HTTP %s", ext_id, response.status)
        except Exception as exc:
            logger.debug("Zakázky GOV detail %s chyba: %s", ext_id, exc)

        if published is None:
            logger.info("Zakázky GOV: detail %s bez data uveřejnění", ext_id)
        self._detail_cache[ext_id] = (published, hodnota_radek)
        return published, hodnota_radek

    @classmethod
    def _hodnota_radek(cls, detail: dict) -> str | None:
        """Zkusí najít předpokládanou hodnotu zakázky v detailu (Kč).

        Portál ji u naživo ověřených zakázek nezveřejnil (pole
        "predpokladana_hodnota_bude_uverejnena": false) - to je zákonná
        praxe zadavatele, ne chyba scraperu. Když by ji přesto zveřejnil,
        hledáme obecně libovolný číselný klíč obsahující "hodnota". Když
        číslo nenajdeme, doplníme aspoň kategorii dle výše hodnoty
        (nadlimitní/podlimitní/malého rozsahu), ať je z karty v CRM vidět
        aspoň řádová velikost zakázky.
        """
        castka = cls._najdi_cislo_hodnoty(detail)
        if castka is not None:
            return f"Předpokládaná hodnota: {castka:,} Kč".replace(",", " ")

        kategorie_kod = detail.get("typ_verejne_zakazky_dle_vyse_predpokladane_hodnoty")
        if isinstance(kategorie_kod, str) and kategorie_kod:
            popisek = _KATEGORIE_HODNOTY.get(
                kategorie_kod, kategorie_kod.replace("_", " ").lower()
            )
            return f"Kategorie dle předpokládané hodnoty: {popisek} (přesná částka nezveřejněna)"
        return None

    @classmethod
    def _najdi_cislo_hodnoty(cls, obj, _depth: int = 0) -> int | None:
        if _depth > 6:
            return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = k.lower()
                if "hodnota" not in kl or any(ex in kl for ex in _HODNOTA_KEY_EXCLUDE):
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1000:
                    return int(v)
            for v in obj.values():
                found = cls._najdi_cislo_hodnoty(v, _depth + 1)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj[:20]:
                found = cls._najdi_cislo_hodnoty(item, _depth + 1)
                if found is not None:
                    return found
        return None

    async def scrape_page(self, page: Page) -> list[Tender]:
        # Nepoužívá se - scraper jde přímo přes API v scrape().
        return []
