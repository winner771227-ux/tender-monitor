"""Scraper pro JOSEPHINE – josephine.proebiz.com

Prochází všechny zakázky od nejnovějších, ale zastaví se jakmile
narazí na zakázku starší než 180 dní – starší zakázky nás nezajímají.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Page

from tender_monitor.models import Tender
from tender_monitor.scrapers.base import BaseScraper, _is_foreign

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")

_PUB_LABELS = (
    "Datum uveřejnění", "Datum zveřejnění", "Zveřejněno", "Uveřejněno",
    "Datum prvního uveřejnění", "Datum zahájení", "Vytvořeno",
)

# JOSEPHINE zobrazuje v tabulce datum "zveřejnění" v posledním sloupci každého řádku
# Zjistili jsme: cells[8] = deadline, published_at je jen na detailu
# Ale detailní stránka JOSEPHINE datum zveřejnění má pod štítkem (viz _PUB_LABELS výše)


class JosephineScraper(BaseScraper):
    source = "JOSEPHINE"
    url = "https://josephine.proebiz.com/cs/public-tenders/all"
    # Procházíme stránky od nejnovějších – zastavíme se automaticky
    # jakmile nenajdeme žádné nové relevantní zakázky na 3 stránkách za sebou
    # JOSEPHINE má ~600 stránek, demoliční zakázky jsou rozmístěné řídce
    # Zastavíme se buď po 30 stránkách NEBO když narazíme na staré zakázky
    max_pages = 30
    max_tenders = 200

    async def scrape_page(self, page: Page) -> list[Tender]:
        tenders: list[Tender] = []
        visited_urls: set[str] = set()
        empty_pages = 0   # počet stránek bez jediné relevantní zakázky

        for page_num in range(self.max_pages):
            if page.url in visited_urls:
                break
            visited_urls.add(page.url)

            try:
                await page.wait_for_selector(
                    "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]",
                    state="attached",
                    timeout=30_000,
                )
            except Exception:
                logger.warning("JOSEPHINE stránka %s: timeout čekání na tabulku", page_num + 1)
                break

            rows = await page.locator(
                "xpath=//table[.//th[contains(normalize-space(.), 'Název zakázky')]]//tr[td]"
            ).all()
            rows = [r for r in rows if len(await r.locator("td").all()) >= 7]
            logger.info("JOSEPHINE page=%s rows=%s", page.url, len(rows))

            found_on_page = 0
            stop_crawling = False

            for row in rows:
                if len(tenders) >= self.max_tenders:
                    stop_crawling = True
                    break

                cells = [
                    self._clean(await cell.inner_text())
                    for cell in await row.locator("td").all()
                ]
                if len(cells) < 7:
                    continue

                link = row.locator("a[href*='/tender/'][href*='/summary']").first
                href = await link.get_attribute("href") if await link.count() else None
                tender = self._build(cells, href, page.url)
                if tender is None:
                    continue

                # Odmítneme SK/PL zakázky okamžitě
                if _is_foreign(tender):
                    continue

                # Ověříme klíčová slova (base._keyword_matches hledá v title+authority)
                matches = self._keyword_matches(tender)
                if not matches:
                    continue

                # Načteme datum zveřejnění z detailní stránky
                tender.published_at = await self._get_pub_date(page, tender.url)
                tender.matched_keywords = matches

                logger.info(
                    "JOSEPHINE ✅ tender=%s published=%s",
                    tender.title[:50], tender.published_at,
                )
                tenders.append(tender)
                found_on_page += 1

            if stop_crawling:
                break

            if found_on_page == 0:
                empty_pages += 1
                if empty_pages >= 20:
                    logger.info("JOSEPHINE: 20 prázdných stránek za sebou – zastavuji na stránce %s", page_num + 1)
                    break
            else:
                empty_pages = 0

            next_url = await self._next_url(page)
            if not next_url or next_url in visited_urls:
                break
            await page.goto(next_url, wait_until="domcontentloaded")

        logger.info("JOSEPHINE total=%s", len(tenders))
        return self.deduplicate_tenders(tenders)

    async def _get_pub_date(self, page: Page, tender_url: str) -> str | None:
        """Načte datum zveřejnění z detailní stránky zakázky."""
        ctx = await page.context.browser.new_context()
        detail = await ctx.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            await detail.goto(tender_url, wait_until="domcontentloaded")
            await detail.wait_for_selector("body", state="attached", timeout=self.timeout_ms)
            text = await detail.locator("body").inner_text()

            for label in _PUB_LABELS:
                # Vzor: "Datum uveřejnění: 02.06.2026" na stejném řádku
                m = re.compile(
                    rf"{re.escape(label)}\s*:?\s*(\d{{2}}\.\d{{2}}\.\d{{4}}(?:\s+\d{{2}}:\d{{2}}:\d{{2}})?)",
                    re.IGNORECASE,
                ).search(text)
                if m:
                    return m.group(1).strip()

                # Vzor: štítek na jednom řádku, datum na dalším
                lines = text.splitlines()
                for i, line in enumerate(lines[:-3]):
                    if label.lower() in line.lower():
                        for next_line in lines[i + 1:i + 4]:
                            m2 = _DATE_RE.search(next_line)
                            if m2:
                                return m2.group(0).strip()

            return None
        except Exception as exc:
            logger.warning("JOSEPHINE detail error %s: %s", tender_url, exc)
            return None
        finally:
            await ctx.close()

    async def _next_url(self, page: Page) -> str | None:
        link = page.locator("a:has-text('Další'), a:has-text('Next')").last
        if not await link.count():
            return None
        href = await link.get_attribute("href")
        if not href or href in {"#", page.url}:
            return None
        return urljoin(page.url, href)

    @classmethod
    def _build(cls, cells: list[str], href: str | None, current_url: str) -> Tender | None:
        external_id = cls._line(cells[0])
        title = cls._line(cells[2])
        authority = cls._line(cells[5]) if len(cells) > 5 else ""
        deadline = cls._date(cells[8]) if len(cells) > 8 else None
        url = (
            urljoin(current_url, href)
            if href
            else (
                f"https://josephine.proebiz.com/cs/tender/{external_id}/summary"
                if external_id.isdigit()
                else None
            )
        )
        if not title or not url:
            return None
        return Tender(
            source=JosephineScraper.source,
            title=title,
            url=url,
            authority=authority or None,
            deadline_at=deadline,
            external_id=external_id or None,
        )

    @staticmethod
    def _date(value: str) -> str | None:
        m = _DATE_RE.search(value)
        return m.group(0) if m else None

    @staticmethod
    def _line(value: str) -> str:
        return next((ln.strip() for ln in value.splitlines() if ln.strip()), "")

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"[ \t]+", " ", value.replace("\xa0", " ")).strip()
