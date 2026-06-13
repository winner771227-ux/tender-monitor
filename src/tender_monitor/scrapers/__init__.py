from tender_monitor.scrapers.e_zakazky import EZakazkyScraper
from tender_monitor.scrapers.eveza import EvezaScraper
from tender_monitor.scrapers.josephine import JosephineScraper
from tender_monitor.scrapers.nen import NenScraper
from tender_monitor.scrapers.profil_zadavatele_vz import ProfilZadavateleVzScraper
from tender_monitor.scrapers.profily_proebiz import ProfilyProebizScraper
from tender_monitor.scrapers.tender_arena import TenderArenaScraper
from tender_monitor.scrapers.tender_market import TenderMarketScraper

SCRAPER_CLASSES = (
    JosephineScraper,
    #TenderMarketScraper,
    #TenderArenaScraper,
    #EZakazkyScraper,
    #ProfilyProebizScraper,
    #ProfilZadavateleVzScraper,
)

__all__ = [
    "SCRAPER_CLASSES",
    "EZakazkyScraper",
    "EvezaScraper",
    "JosephineScraper",
    "NenScraper",
    "ProfilZadavateleVzScraper",
    "ProfilyProebizScraper",
    "TenderArenaScraper",
    "TenderMarketScraper",
]
