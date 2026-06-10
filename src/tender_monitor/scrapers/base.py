from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from playwright.async_api import Browser, Page

from tender_monitor.models import ScrapeResult, Tender

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Base class for Playwright tender scrapers."""

    source: str
    url: str

    def __init__(self, keywords: tuple[str, ...], timeout_ms: int) -> None:
        self.keywords = keywords
        self.timeout_ms = timeout_ms

    async def scrape(self, browser: Browser) -> ScrapeResult:
        page = await browser.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            await page.goto(self.url, wait_until="domcontentloaded")
            tenders = await self.scrape_page(page)
            return ScrapeResult(source=self.source, tenders=self.filter_by_keywords(tenders))
        except Exception as exc:  # noqa: BLE001 - scraper failures are isolated per portal
            logger.exception("Scraper %s failed", self.source)
            return ScrapeResult(source=self.source, tenders=[], error=str(exc))
        finally:
            await page.close()

    @abstractmethod
    async def scrape_page(self, page: Page) -> list[Tender]:
        """Extract tenders from an already opened portal page."""

    def keyword_matches(self, tender: Tender) -> list[str]:
        haystack = " ".join(
            part or ""
            for part in (tender.title, tender.description, tender.authority)
        ).lower()
        return [keyword for keyword in self.keywords if keyword.lower() in haystack]

    def filter_by_keywords(self, tenders: list[Tender]) -> list[Tender]:
        filtered: list[Tender] = []
        for tender in tenders:
            matches = self.keyword_matches(tender)
            if matches:
                tender.matched_keywords = matches
                filtered.append(tender)
        return filtered

    async def collect_link_tenders(self, page: Page, link_selector: str) -> list[Tender]:
        """Generic placeholder helper for portals where a tender is represented by a link."""
        items: list[Tender] = []
        links = await page.locator(link_selector).all()
        for link in links:
            title = (await link.inner_text()).strip()
            href = await link.get_attribute("href")
            if not title or not href:
                continue
            items.append(
                Tender(
                    source=self.source,
                    title=title,
                    url=href if href.startswith("http") else page.url.rstrip("/") + "/" + href.lstrip("/"),
                )
            )
        return items
