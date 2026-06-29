"""Scraper pro Tender Arena - tenderarena.cz

TenderArena blokuje prime requesty z GitHub Actions IP.
Reseni: pouzivame ScraperAPI proxy.
ScraperAPI pro POST: posílame jako form data s parametrem 'body'.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.async_api import Browser, Page

from tender_monitor.dedupe import normalize_text
from tender_monitor.models import ScrapeResult, Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_API_URL = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani/vyhledat"
MAX_PER_KEYWORD = 10


def _api_search(keyword: str, scraper_api_key: str) -> list[dict]:
    """Volání TenderArena API přes ScraperAPI proxy."""
    # ScraperAPI POST: posíláme na jejich endpoint s parametry
    # url= cílová URL
    # body= JSON payload jako string
    # Samotný request na ScraperAPI je POST s form-encoded daty
    params = urllib.parse.urlencode({
        "api_key": scraper_api_key,
        "url": _API_URL,
        "keep_headers": "true",
    })
    scraper_url = f"https://api.scraperapi.com/?{params}"

    json_body = json.dumps({
        "dotaz": keyword,
        "strankovani": {"stranka": 1, "pocetNaStranku": MAX_PER_KEYWORD},
    })

    req = urllib.request.Request(
        scraper_url,
        data=json_body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://www.tenderarena.cz",
            "Referer": "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    logger.debug("TenderArena raw response: %s", raw[:200])

    # Zkontrolujeme jestli jsme dostali JSON
    if not raw.strip().startswith("{"):
        logger.warning("TenderArena: odpověď není JSON: %s", raw[:100])
        return []

    data = json.loads(raw)
    return data.get("polozky", [])


class TenderArenaScraper(BaseScraper):
    source = "TenderArena"
    url = "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani"

    async def scrape(self, browser: Browser) -> ScrapeResult:
        scraper_api_key = os.environ.get("SCRAPERAPI_KEY", "")
        if not scraper_api_key:
            logger.warning("TenderArena: SCRAPERAPI_KEY není nastavený, přeskakuji")
            return ScrapeResult(source=self.source, tenders=[],
                                error="SCRAPERAPI_KEY není nastavený")

        logger.info("TenderArena: ScraperAPI klíč nalezen, délka=%s", len(scraper_api_key))
        all_tenders: list[Tender] = []
        error_msg = None

        for keyword in self.keywords:
            logger.info("TenderArena hledam: '%s'", keyword)
            try:
                loop = asyncio.get_event_loop()
                polozky = await loop.run_in_executor(
                    None, _api_search, keyword, scraper_api_key
                )
                logger.info("TenderArena '%s': polozky=%s", keyword, len(polozky))

                found_kw = 0
                for item in polozky:
                    if found_kw >= MAX_PER_KEYWORD:
                        break

                    title = (item.get("nazev") or "").strip()
                    if not title:
                        continue

                    if normalize_text(keyword) not in normalize_text(title):
                        continue

                    external_id = item.get("idProZadavatele", "")
                    row_url = (
                        f"https://www.tenderarena.cz/dodavatel/zakazka/detail/{external_id}"
                        if external_id else ""
                    )
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
