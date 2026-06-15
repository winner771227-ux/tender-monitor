"""Scraper pro Profil zadavatele VZ – profilzadavatele-vz.cz"""
from tender_monitor.scrapers._debug_base import SearchScraper

class ProfilZadavateleVzScraper(SearchScraper):
    source = "Profil zadavatele VZ"
    url = "https://www.profilzadavatele-vz.cz/vyhledavani/"
    search_url_template = "https://www.profilzadavatele-vz.cz/vyhledavani/?co={keyword}&datum_od={date_from}"
