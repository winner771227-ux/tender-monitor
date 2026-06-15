"""Scraper pro Tender Arena – tenderarena.cz"""
from tender_monitor.scrapers._debug_base import SearchScraper

class TenderArenaScraper(SearchScraper):
    source = "Tender Arena"
    url = "https://tenderarena.cz/dodavatel/seznam-zakazek/"
    search_url_template = "https://tenderarena.cz/dodavatel/seznam-zakazek/?stav=aktivni&nazev={keyword}&datumUverejneniOd={date_from}"
