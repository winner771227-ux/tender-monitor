"""Scraper pro E-ZAKAZKY – e-zakazky.cz"""
from tender_monitor.scrapers._debug_base import SearchScraper

class EZakazkyScraper(SearchScraper):
    source = "E-ZAKAZKY"
    url = "https://www.e-zakazky.cz/Vyhledavani"
    search_url_template = "https://www.e-zakazky.cz/Vyhledavani?Predmet={keyword}&DatumUverejneniOd={date_from}"
