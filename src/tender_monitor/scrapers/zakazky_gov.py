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

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import DATE_RE, BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

MAX_ROWS_PER_KEYWORD = 12

# Klíče v JSON odpovědi API, které pravděpodobně obsahují datum zveřejnění / lhůtu
# (zkoušíme české i anglické varianty - moderní portál mohl převzít mezinárodní
# názvosloví, např. podle standardu OCDS)
_PUBLISHED_JSON_KEYS = (
    "uverejn", "zverejn", "zahajeni", "publikov", "publish", "datepublished", "startdate",
    "datumvyhlaseni", "vyhlaseni", "datumzahajeni", "datumpublikace", "created", "vlozeni",
    "datumzadani", "zadani",
)
_DEADLINE_JSON_KEYS = (
    "lhuta", "koneclhuty", "terminpodani", "deadline", "enddate", "submissiondeadline",
)

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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Ať do logu vypíšeme ukázku skutečných dat jen jednou za běh, ne
        # pro každou jednotlivou zakázku (log by byl nečitelný).
        self._sample_logged = False
        self._keys_logged = False

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
        # Zachytíme JSON odpovědi, které portál vrací během vyhledávání -
        # obsahují data zakázek (včetně dat), spolehlivěji než vykreslený text.
        captured_json: list = []
        all_urls: list = []  # jen pro diagnostiku - seznam URL požadavků

        async def _on_response(response) -> None:
            try:
                url = response.url
                # Datové požadavky poznáme podle přípony/cesty, ne podle domény
                # (API může běžet na jiné subdoméně než zakazky.gov.cz).
                if any(x in url for x in (".js", ".css", ".woff", ".svg", ".png", ".webp", ".ico")):
                    return
                all_urls.append(url)
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype.lower():
                    return
                data = await response.json()
                # verze aplikace ({'hash': ...}) nás nezajímá
                if isinstance(data, dict) and set(data.keys()) == {"hash"}:
                    return
                captured_json.append((url, data))
            except Exception:
                pass

        page.on("response", _on_response)

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

        # Datum z JSONu vyhledávacího API napárujeme na zakázky podle čísla (RVZ...)
        date_map = self._build_date_map(captured_json)

        # Jednorázová diagnostika názvů polí v API - ať víme, jak se jmenuje
        # datum zveřejnění (published), které se zatím nedaří napárovat.
        if not self._keys_logged:
            self._keys_logged = True
            sample_keys = self._sample_record_keys(captured_json)
            if sample_keys:
                logger.info("Zakázky GOV DIAGNOSTIKA – klíče záznamu zakázky v API: %s", sample_keys)

        # Jednorázová diagnostika - ukážeme, co síť během hledání vrací, ať víme,
        # kde jsou data uložená (pro případné doladění).
        if not date_map and not self._sample_logged:
            self._sample_logged = True
            # 1) seznam URL požadavků (zkrácený) - hledáme, kde je datové API
            url_list = " || ".join(u[:120] for u in all_urls[:40])
            logger.info("Zakázky GOV DIAGNOSTIKA – URL požadavků (%s): %s", len(all_urls), url_list)
            # 2) obsah zachycených JSON odpovědí (začátek každé)
            if captured_json:
                for i, (u, d) in enumerate(captured_json[:5]):
                    logger.info("Zakázky GOV DIAGNOSTIKA – JSON #%s z %s: %s", i, u[:120], str(d)[:600])
            else:
                logger.info("Zakázky GOV DIAGNOSTIKA – žádná datová JSON odpověď se nezachytila")

        try:
            return await self._extract_results(page, keyword, date_map)
        finally:
            page.remove_listener("response", _on_response)

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

    async def _extract_results(self, page: Page, keyword: str, date_map: dict) -> list[Tender]:
        tenders: list[Tender] = []
        links = await page.locator(_DETAIL_LINK_SELECTOR).all()
        logger.info("Zakázky GOV [%s]: nalezeno odkazů=%s (dat z API=%s)",
                    keyword, len(links), len(date_map))

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
            # Číslo zakázky je i v URL (…/detail-zakazky/RVZ2600110661) - vytáhneme ho
            if not external_id:
                m = re.search(r"(RVZ\d{6,}|N\d{3}[/-]\d{2}[/-][A-Z]\d+)", url)
                if m:
                    external_id = m.group(1)

            # Datum z karty (pokud tam náhodou je)
            published = self.value_after_label(context_text, _PUBLISHED_LABELS) or \
                self._fuzzy_date_after_label(context_text, _PUBLISHED_LABELS)
            deadline = self.value_after_label(context_text, _DEADLINE_LABELS) or \
                self._fuzzy_date_after_label(context_text, _DEADLINE_LABELS)

            # Datum z JSON odpovědi vyhledávacího API (napárováno podle čísla zakázky)
            if external_id and external_id in date_map:
                api_pub, api_dl = date_map[external_id]
                published = published or api_pub
                deadline = deadline or api_dl

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

            tender.matched_keywords = [keyword]
            tenders.append(tender)
            if len(tenders) >= MAX_ROWS_PER_KEYWORD:
                break

        return self.deduplicate_tenders(tenders)

    @classmethod
    def _sample_record_keys(cls, captured_json: list) -> str | None:
        """Najde v API první objekt, který vypadá jako záznam zakázky
        (obsahuje číslo RVZ…), a vrátí seznam jeho klíčů. Slouží jen k tomu,
        abychom v logu jednou viděli, jak se pole (hlavně datum) jmenují."""
        found: list[str] = []

        def _walk(obj, depth=0):
            if found or depth > 8:
                return
            if isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, str) and re.search(r"RVZ\d{6,}", v):
                        found.extend(obj.keys())
                        return
                for v in obj.values():
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)

        for _url, data in captured_json:
            _walk(data)
            if found:
                break
        return ", ".join(found) if found else None

    @classmethod
    def _build_date_map(cls, captured_json: list) -> dict:
        """Z JSON odpovědí vyhledávacího API sestaví mapu:
        číslo_zakázky (RVZ…) -> (datum_zveřejnění, lhůta_podání).

        Nezná přesnou strukturu API předem, proto rekurzivně hledá objekty,
        které mají zároveň něco jako číslo zakázky (RVZ…) a nějaké datum.
        """
        date_map: dict = {}
        for _url, data in captured_json:
            cls._harvest_records(data, date_map)
        return date_map

    @classmethod
    def _harvest_records(cls, obj, date_map: dict, _depth: int = 0) -> None:
        if _depth > 8:
            return
        if isinstance(obj, dict):
            # Najdeme v tomto objektu číslo zakázky
            ext_id = None
            for k, v in obj.items():
                if isinstance(v, str):
                    m = re.search(r"(RVZ\d{6,}|N\d{3}[/-]\d{2}[/-][A-Z]\d+)", v)
                    if m:
                        ext_id = m.group(1)
                        break
            if ext_id:
                published = cls._find_date_value(obj, _PUBLISHED_JSON_KEYS)
                deadline = cls._find_date_value(obj, _DEADLINE_JSON_KEYS)
                if (published or deadline) and ext_id not in date_map:
                    date_map[ext_id] = (
                        BaseScraper.first_date(published) if published else None,
                        BaseScraper.first_date(deadline) if deadline else None,
                    )
            for v in obj.values():
                cls._harvest_records(v, date_map, _depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                cls._harvest_records(item, date_map, _depth + 1)

    @staticmethod
    def _find_date_value(obj, key_substrings: tuple[str, ...], _depth: int = 0) -> str | None:
        """Rekurzivně projde JSON strukturu a vrátí první textovou hodnotu
        u klíče, jehož (normalizovaný) název obsahuje jeden z key_substrings."""
        if _depth > 6:
            return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = normalize_text(k).replace(" ", "")
                if isinstance(v, str) and v.strip() and any(p in kl for p in key_substrings):
                    return v
            for v in obj.values():
                found = ZakazkyGovScraper._find_date_value(v, key_substrings, _depth + 1)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj[:50]:
                found = ZakazkyGovScraper._find_date_value(item, key_substrings, _depth + 1)
                if found:
                    return found
        return None

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
