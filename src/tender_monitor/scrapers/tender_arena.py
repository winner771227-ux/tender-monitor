"""Scraper pro Tender Arena - tenderarena.cz

TenderArena blokuje prime HTTP requesty z GitHub Actions IP.
Reseni: volame API pres Playwright page.evaluate() - fetch bezi
v kontextu prohlizece primo na strance TenderArena, takze server
jej vidi jako legitimni AJAX pozadavek ze sve vlastni stranky.
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
_API_PATH = "/dodavatel/chytre-vyhledavani/vyhledat"
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
            # Nejprve načteme stránku aby prohlížeč měl správné cookies a origin
            await page.goto(_SEARCH_URL, wait_until="domcontentloaded",
                            timeout=self.per_keyword_timeout_ms)
            await page.wait_for_timeout(2_000)

            for keyword in self.keywords:
                logger.info("TenderArena hledam: '%s'", keyword)
                try:
                    # Voláme API přes fetch v kontextu prohlížeče
                    # Server vidí request jako AJAX ze své vlastní stránky
                    payload = json.dumps({
                        "dotaz": keyword,
                        "strankovani": {"stranka": 1, "pocetNaStranku": MAX_PER_KEYWORD},
                    })

                    result = await page.evaluate(f"""
                        async () => {{
                            const resp = await fetch('{_API_PATH}', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json, text/plain, */*',
                                }},
                                body: {repr(payload)},
                            }});
                            if (!resp.ok) return null;
                            return await resp.json();
                        }}
                    """)

                    if not result:
                        logger.warning("TenderArena '%s': prázdná odpověď", keyword)
                        continue

                    polozky = result.get("polozky", [])
                    logger.info("TenderArena '%s': polozky=%s", keyword, len(polozky))

                    found_kw = 0
                    for item in polozky:
                        if found_kw >= MAX_PER_KEYWORD:
                            break

                        title = (item.get("nazev") or "").strip()
                        if not title:
                            continue

                        # Klíčové slovo musí být v názvu
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

        except Exception as exc:
            logger.warning("TenderArena chyba při načítání stránky: %s", exc)
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
