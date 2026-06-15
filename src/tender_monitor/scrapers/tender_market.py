"""Scraper pro Tender Market – tendermarket.cz"""
from tender_monitor.scrapers._debug_base import SearchScraper

class TenderMarketScraper(SearchScraper):
    source = "Tender Market"
    url = "https://tendermarket.cz/zakazky.html"
    search_url_template = "https://tendermarket.cz/zakazky.html?nazev={keyword}&zverejnenoOd={date_from}"
