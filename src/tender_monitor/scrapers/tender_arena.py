"""Scraper pro Tender Arena - tenderarena.cz

TenderArena vyzaduje platnou session pro API volani.
Reseni: Playwright nejdrive nacte stranku (ziska cookies/session),
pak zavola API pres page.request.post() ktery pouziva stejnou session.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani"
<<<<<<< HEAD
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?\b")
_ID_RE = re.compile(r"\bVZ\d+\b")

=======
_API_URL = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani/vyhledat"
>>>>>>> 06dc5ef35514d19904d483263eebc8fe8ce74e5b
MAX_PER_KEYWORD = 10


class TenderArenaScraper(BaseScraper):
    source = "TenderArena"
    url = _SEARCH_URL
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

        try:
            # Načteme stránku - získáme cookies a session
            logger.info("TenderArena: načítám stránku pro session...")
            await page.goto(_SEARCH_URL, wait_until="domcontentloaded",
                            timeout=self.per_keyword_timeout_ms)
            await page.wait_for_timeout(3_000)
            logger.info("TenderArena: stránka načtena, URL=%s", page.url)

            for keyword in self.keywords:
                logger.info("TenderArena hledam: '%s'", keyword)
                try:
                    payload = json.dumps({
                        "dotaz": keyword,
                        "strankovani": {"stranka": 1, "pocetNaStranku": MAX_PER_KEYWORD},
                    })

<<<<<<< HEAD
                    # Input je uvnitř divu .search-box__input
                    field = page.locator(".search-box__input input").first
                    if not await field.count():
                        # Fallback - zkusíme přímo input
                        field = page.locator("input[type='text'], input[type='search']").first
                    if not await field.count():
                        logger.warning("TenderArena: input nenalezen")
                        break

                    await field.fill(keyword)
                    await field.press("Enter")

                    # Počkáme na načtení výsledků (Angular)
                    try:
                        await page.wait_for_selector(
                            "app-chytre-vyhledavani-seznam",
                            timeout=10_000
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(3_000)

                    body_text = await page.locator("body").inner_text()
                    logger.info("TenderArena '%s': body_len=%s", keyword, len(body_text))

                    # Zkusíme různé selektory pro výsledky
                    result_items = await page.locator(
                        "app-chytre-vyhledavani-seznam > div, "
                        "[class*='vyhledavani-seznam'] > *, "
                        ".search-result-item"
                    ).all()

                    # Fallback - nadpisy s odkazem
                    if not result_items:
                        result_items = await page.locator("h3 a, h4 a, strong a").all()
                        logger.info("TenderArena '%s': fallback links=%s", keyword, len(result_items))

                    logger.info("TenderArena '%s': items=%s", keyword, len(result_items))
=======
                    # page.request.post() používá cookies ze session stránky
                    response = await page.request.post(
                        _API_URL,
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/plain, */*",
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": _SEARCH_URL,
                        },
                    )

                    status = response.status
                    raw = await response.text()
                    logger.info("TenderArena '%s': status=%s body_start=%s",
                                keyword, status, raw[:80])

                    if not raw.strip().startswith("{"):
                        logger.warning("TenderArena '%s': není JSON", keyword)
                        continue

                    data = json.loads(raw)
                    polozky = data.get("polozky", [])
                    logger.info("TenderArena '%s': polozky=%s", keyword, len(polozky))
>>>>>>> 06dc5ef35514d19904d483263eebc8fe8ce74e5b

                    found_kw = 0
                    for item in polozky:
                        if found_kw >= MAX_PER_KEYWORD:
                            break

<<<<<<< HEAD
                        try:
                            text = (await item.inner_text()).strip()
                        except Exception:
                            continue

                        if not text or len(text) < 5:
                            continue

                        # Název - z odkazu nebo první řádek
                        link = item.locator("a").first
                        href = await link.get_attribute("href") if await link.count() else None
                        title = (await link.inner_text()).strip() if await link.count() else ""
                        if not title:
                            title = text.splitlines()[0].strip()

                        if not title or len(title) < 5:
                            # Pokud je item samotný odkaz
                            if await item.evaluate("el => el.tagName") == "A":
                                href = await item.get_attribute("href")
                                title = text

                        if not title or len(title) < 5:
=======
                        title = (item.get("nazev") or "").strip()
                        if not title:
>>>>>>> 06dc5ef35514d19904d483263eebc8fe8ce74e5b
                            continue

                        if normalize_text(keyword) not in normalize_text(title):
                            continue

<<<<<<< HEAD
                        row_url = f"https://www.tenderarena.cz{href}" if href and href.startswith("/") else href

                        # ID zakázky z textu
                        external_id = None
                        deadline = None
                        id_m = _ID_RE.search(text)
                        if id_m:
                            external_id = id_m.group(0)
                            if not row_url:
                                row_url = f"https://www.tenderarena.cz/dodavatel/zakazka/detail/{external_id}"

                        date_m = _DATE_RE.search(text)
                        if date_m:
                            deadline = date_m.group(0)

                        # Zadavatel - druhý řádek
                        lines = [l.strip() for l in text.splitlines() if l.strip()]
                        authority = None
                        for line in lines[1:]:
                            if not _ID_RE.search(line) and not _DATE_RE.search(line) and len(line) > 3:
                                authority = line
                                break

=======
                        external_id = item.get("idProZadavatele", "")
                        row_url = (
                            f"https://www.tenderarena.cz/dodavatel/zakazka/detail/{external_id}"
                            if external_id else ""
                        )
>>>>>>> 06dc5ef35514d19904d483263eebc8fe8ce74e5b
                        if not row_url:
                            continue

                        authority = (item.get("nazevZadavatele") or "").strip() or None

                        deadline = None
                        lhuta_raw = item.get("lhutaProPodaniNabidek")
                        if lhuta_raw:
                            try:
                                dt = datetime.fromisoformat(lhuta_raw.replace("Z", "+00:00"))
                                deadline = dt.strftime("%d.%m.%Y %H:%M")
                            except Exception:
                                deadline = lhuta_raw[:16]

                        t = Tender(
                            source=self.source,
                            title=title,
                            url=row_url,
                            authority=authority,
                            published_at=None,
                            deadline_at=deadline,
                            external_id=external_id or None,
                        )

                        if _is_foreign(t):
                            continue

                        t.matched_keywords = [keyword]
                        logger.info("TenderArena [%s] nalezena: '%s'", keyword, t.title[:60])
                        all_tenders.append(t)
                        found_kw += 1

                    logger.info("TenderArena '%s': found=%s", keyword, found_kw)

                except Exception as exc:
                    logger.warning("TenderArena keyword='%s' chyba: %s", keyword, exc)
                    error_msg = str(exc)

                await asyncio.sleep(0.5)

        except Exception as exc:
            logger.warning("TenderArena chyba: %s", exc)
            error_msg = str(exc)
        finally:
            await context.close()

        unique = self.deduplicate_tenders(all_tenders)
        filtered = self._filter(unique)
        logger.info("TenderArena: scraped=%s after_filter=%s", len(unique), len(filtered))

        return ScrapeResult(
            source=self.source,
            tenders=filtered,
            error=error_msg if not all_tenders else None,
        )

    async def scrape_page(self, page: Page) -> list[Tender]:
        return []
